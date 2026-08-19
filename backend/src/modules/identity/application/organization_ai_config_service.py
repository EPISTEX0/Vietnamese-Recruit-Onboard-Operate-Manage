"""Application service for safe Organization AI configuration updates."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from src.modules.gmail.application.classification_rollout import (
    BusinessPolicy,
    ReleaseMetrics,
    RolloutMode,
    evaluate_release_gates,
)
from src.modules.identity.domain.entities import CredentialSource, OrganizationAIConfiguration, User
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from src.modules.identity.infrastructure.organization_ai_config_repository import (
    OrganizationAIConfigRepository,
)

# ---------------------------------------------------------------------------
# Data policy — describes what data types may be sent to the AI provider
# ---------------------------------------------------------------------------

DATA_POLICY_VERSION = "1.0"

logger = logging.getLogger(__name__)


class AIPolicyPreset(str, Enum):
    """Organization-level automation posture; thresholds stay centrally owned."""

    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    HIGH_RECALL = "high_recall"


_POLICY_PRESET_VERSIONS = {
    AIPolicyPreset.CONSERVATIVE: "conservative-v1",
    AIPolicyPreset.BALANCED: "balanced-v1",
    AIPolicyPreset.HIGH_RECALL: "high-recall-v1",
}


DATA_POLICY_ITEMS: list[dict[str, str]] = [
    {
        "category": "email_intent_classification",
        "data_types": (
            "Email subject, sender address, and body text (for inbound recruitment emails)"
        ),
        "purpose": (
            "Classify email intent (job_application/partner/event/internal/other) "
            "for automated routing"
        ),
        "retention": "Provider processes data transiently; no training or storage by provider",
    },
    {
        "category": "cv_parsing",
        "data_types": "CV/resume document text content extracted from attachments",
        "purpose": "Parse structured candidate data (name, skills, experience, education) from CVs",
        "retention": "Provider processes data transiently; no training or storage by provider",
    },
    {
        "category": "assistant_conversation",
        "data_types": "HR conversation context including candidate summaries, interview schedules, "
        "onboarding progress, and employee counts by status",
        "purpose": "Provide conversational HR assistance for recruitment and onboarding queries",
        "retention": "Provider processes data transiently; no training or storage by provider",
    },
]


# ---------------------------------------------------------------------------
# Capability state enum
# ---------------------------------------------------------------------------


class AICapabilityState(str, Enum):
    """External capability state for AI Automation and AI Assistant."""

    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    READY = "ready"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIConfigurationView:
    provider: str | None
    base_url: str | None
    model: str | None
    api_key_masked: str | None
    configured: bool
    updated_at: datetime | None
    credential_source: str = CredentialSource.ORG_API_KEY.value
    deployment_key_available: bool = False
    # True only when an api_key_enc is present but cannot be decrypted (e.g. an
    # encryption key rotation invalidated it). Distinguishes that state from
    # api_key_masked=None meaning "no key configured at all" -- the two need
    # opposite admin actions (restore the key vs enter a new one).
    api_key_decrypt_failed: bool = False
    # Consent & toggles
    data_policy_accepted: bool = False
    data_policy_accepted_at: datetime | None = None
    data_policy_version: str | None = None
    automation_enabled: bool = False
    automation_state: str = AICapabilityState.NOT_CONFIGURED.value
    assistant_enabled: bool = False
    assistant_state: str = AICapabilityState.NOT_CONFIGURED.value
    classification_policy: str = BusinessPolicy.RECALL_FIRST.value
    classification_policy_version: str = "recall-first-v1"
    stable_classifier_version: str = "classifier-v1"
    candidate_classifier_version: str | None = None
    candidate_classification_policy: str | None = None
    candidate_classification_policy_version: str | None = None
    rollout_mode: str = RolloutMode.STABLE.value
    canary_percentage: int = 0
    ai_automation_consent: bool = False
    ai_assistant_consent: bool = False
    ai_policy_preset: str = AIPolicyPreset.BALANCED.value
    ai_policy_preset_version: str = "balanced-v1"


@dataclass(frozen=True)
class AIConfigurationCandidate:
    provider: str
    base_url: str
    model: str
    api_key: str


@dataclass(frozen=True)
class AIProviderRuntimeConfig:
    """Resolved provider settings for an enabled Organization capability."""

    provider: str
    base_url: str
    model: str
    api_key: str


@dataclass(frozen=True)
class ClassificationRolloutCandidate:
    """Audited Organization selection for a candidate rollout."""

    mode: RolloutMode
    business_policy: BusinessPolicy
    policy_version: str
    classifier_version: str
    canary_percentage: int = 0
    release_metrics: ReleaseMetrics | None = None


@dataclass
class AIConfigurationUpdateResult:
    view: AIConfigurationView
    audit_details: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OrganizationAIConfigValidationError(Exception):
    pass


class OrganizationAIConfigTestError(Exception):
    pass


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OrganizationAIConfigService:
    def __init__(
        self,
        repository: OrganizationAIConfigRepository,
        crypto: CryptoUtils,
        settings: AuthSettings | None = None,
    ) -> None:
        self.repository = repository
        self.crypto = crypto
        self._settings = settings

    def _get_settings(self) -> AuthSettings:
        if self._settings is not None:
            return self._settings
        from src.modules.identity.container import get_settings

        return get_settings()

    def _get_deployment_key(self) -> str | None:
        """Return the deployment key from settings if configured, else None."""
        key = self._get_settings().ai_deployment_key
        if not key or not key.strip():
            return None
        return key

    @staticmethod
    def validate(candidate: AIConfigurationCandidate) -> None:
        if not candidate.provider.strip():
            raise OrganizationAIConfigValidationError("provider must not be empty")
        parsed = urlparse(candidate.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OrganizationAIConfigValidationError("base_url must be a valid HTTP(S) URL")
        if not candidate.model.strip():
            raise OrganizationAIConfigValidationError("model must not be empty")
        if not candidate.api_key.strip():
            raise OrganizationAIConfigValidationError("api_key must not be empty")

    @staticmethod
    def _mask_key(key: str) -> str:
        return "*" * (len(key) - 4) + key[-4:] if len(key) > 4 else "*" * len(key)

    # ------------------------------------------------------------------
    # Capability state
    # ------------------------------------------------------------------

    async def _run_health_check(self) -> bool:
        """Run a health check against the configured provider.

        Returns True if the provider responds successfully, False otherwise.
        """
        config = await self.repository.get()
        if config is None:
            return False
        try:
            key = await self._resolve_api_key(config)
        except OrganizationAIConfigValidationError:
            return False
        try:
            await self._test_completion(config.base_url, key, config.model)
        except OrganizationAIConfigTestError:
            return False
        return True

    async def _check_credential_usable(self, config: OrganizationAIConfiguration) -> bool:
        """Check if the credential can be resolved without an HTTP call.

        Returns False if the credential source is broken: missing key, empty
        encrypted key, or a key present but that fails to decrypt (e.g. after
        an encryption key rotation). The broad ``except`` matters here --
        ``_resolve_api_key`` lets a raw decrypt failure propagate as-is
        (``ValueError``, ``InvalidTag``, ...); catching only the validation
        error left that case unhandled, crashing ``get_view()`` instead of
        reporting the credential as unusable. True means the credential is at
        least present and decryptable; actual connectivity is verified during
        enable actions via health check.
        """
        try:
            await self._resolve_api_key(config)
        except Exception:
            return False
        return True

    def _compute_state(
        self,
        config: OrganizationAIConfiguration | None,
        enabled: bool,
        credential_usable: bool | None,
    ) -> str:
        """Compute the capability state.

        Args:
            config: The stored config or None.
            enabled: Whether the capability toggle is on.
            credential_usable: Whether credential can be resolved (None if not applicable).
        """
        if config is None:
            return AICapabilityState.NOT_CONFIGURED.value
        if not enabled:
            return AICapabilityState.DISABLED.value
        if credential_usable is False:
            return AICapabilityState.UNAVAILABLE.value
        return AICapabilityState.READY.value

    async def is_provider_connected(self) -> bool:
        """Return whether AI has a usable provider credential right now.

        Narrower than ``get_view()``: answers only "is a provider connected",
        without exposing provider name, base_url, model, or credential
        details. Meant for callers (HR) who may not see those.
        """
        config = await self.repository.get()
        if config is None:
            return False
        return await self._check_credential_usable(config)

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    async def get_view(self) -> AIConfigurationView:
        config = await self.repository.get()
        deployment_key = self._get_deployment_key()

        if config is None:
            return AIConfigurationView(
                None,
                None,
                None,
                None,
                False,
                None,
                deployment_key_available=deployment_key is not None,
            )

        # Run health check for state computation
        credential_usable = await self._check_credential_usable(config)

        params: dict[str, object] = dict(
            provider=config.provider,
            base_url=config.base_url,
            model=config.model,
            configured=True,
            updated_at=config.updated_at,
            credential_source=config.credential_source,
            deployment_key_available=deployment_key is not None,
            data_policy_accepted=config.data_policy_accepted,
            data_policy_accepted_at=config.data_policy_accepted_at,
            data_policy_version=config.data_policy_version,
            automation_enabled=config.ai_automation_enabled,
            automation_state=self._compute_state(
                config, config.ai_automation_enabled, credential_usable
            ),
            assistant_enabled=config.ai_assistant_enabled,
            assistant_state=self._compute_state(
                config, config.ai_assistant_enabled, credential_usable
            ),
            classification_policy=config.classification_policy,
            classification_policy_version=config.classification_policy_version,
            stable_classifier_version=config.stable_classifier_version,
            candidate_classifier_version=config.candidate_classifier_version,
            candidate_classification_policy=config.candidate_classification_policy,
            candidate_classification_policy_version=(
                config.candidate_classification_policy_version
            ),
            rollout_mode=config.rollout_mode,
            canary_percentage=config.canary_percentage,
            ai_automation_consent=config.ai_automation_consent,
            ai_assistant_consent=config.ai_assistant_consent,
            ai_policy_preset=config.ai_policy_preset,
            ai_policy_preset_version=config.ai_policy_preset_version,
        )
        if config.credential_source == CredentialSource.DEPLOYMENT_KEY.value:
            params["api_key_masked"] = "****" if deployment_key else None
        elif config.api_key_enc:
            try:
                key = self.crypto.decrypt(config.api_key_enc)
                params["api_key_masked"] = self._mask_key(key)
            except Exception:
                logger.warning(
                    "Organization API key present but failed to decrypt for masking",
                    exc_info=True,
                )
                params["api_key_masked"] = None
                params["api_key_decrypt_failed"] = True
        else:
            params["api_key_masked"] = None
        return AIConfigurationView(**params)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Health / connection
    # ------------------------------------------------------------------

    async def _test_completion(self, base_url: str, api_key: str, model: str) -> None:
        """Verify an OpenAI-compatible provider through chat completions.

        Completion-only providers are not required to expose the optional
        ``/models`` discovery endpoint, so health checks exercise the contract
        the configured model will actually use.
        """
        url = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 4,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise OrganizationAIConfigTestError(f"Unable to connect to provider: {exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise OrganizationAIConfigTestError(
                f"Provider connection test failed with status {response.status_code}"
            )

    async def test_connection(self, candidate: AIConfigurationCandidate) -> None:
        self.validate(candidate)
        await self._test_completion(candidate.base_url, candidate.api_key, candidate.model)

    async def test_deployment_key_connection(
        self, provider: str, base_url: str, model: str
    ) -> None:
        """Test connectivity using the deployment-wide key."""
        deployment_key = self._get_deployment_key()
        if not deployment_key:
            raise OrganizationAIConfigTestError("No deployment key is configured")
        candidate = AIConfigurationCandidate(
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=deployment_key,
        )
        self.validate(candidate)
        await self._test_completion(base_url, deployment_key, model)

    async def get_runtime_config(
        self,
        *,
        capability: str,
    ) -> AIProviderRuntimeConfig | None:
        """Resolve the active Organization provider for a capability.

        ``None`` means the Organization has not configured AI yet, allowing
        local bootstrap settings to remain useful before setup. Once a
        configuration exists, enabled capabilities must use its provider and
        credential; they must not silently fall back to environment values.
        """
        config = await self.repository.get()
        if config is None:
            return None

        enabled_field = {
            "assistant": "ai_assistant_enabled",
            "automation": "ai_automation_enabled",
        }.get(capability)
        if enabled_field is None:
            raise OrganizationAIConfigValidationError(f"Unknown AI capability: {capability}")
        if not getattr(config, enabled_field):
            raise OrganizationAIConfigValidationError(
                f"AI {capability} is disabled for this Organization"
            )

        api_key = await self._resolve_api_key(config)
        return AIProviderRuntimeConfig(
            provider=config.provider,
            base_url=config.base_url,
            model=config.model,
            api_key=api_key,
        )

    async def _resolve_api_key(self, config: OrganizationAIConfiguration) -> str:
        """Return the actual API key for a config based on its credential source."""
        if config.credential_source == CredentialSource.DEPLOYMENT_KEY.value:
            key = self._get_deployment_key()
            if not key:
                raise OrganizationAIConfigValidationError("Deployment key is not available")
            return key
        if not config.api_key_enc:
            raise OrganizationAIConfigValidationError(
                "Organization API key is revoked or not configured"
            )
        return self.crypto.decrypt(config.api_key_enc)

    # ------------------------------------------------------------------
    # Pre-enable validation
    # ------------------------------------------------------------------

    async def _can_enable(self, capability_name: str) -> None:
        """Validate that a capability can be safely enabled.

        Raises:
            OrganizationAIConfigValidationError: If any precondition is not met.
        """
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError(
                "Cannot enable AI capabilities: no provider configuration exists"
            )
        # The shared data policy and the capability's own consent are both
        # required, and are checked before anything about the provider: the
        # caller who accepts consent (HR) cannot see or fix a provider
        # credential/health-check failure, so their own scope must fail
        # first, not last.
        if not config.data_policy_accepted:
            raise OrganizationAIConfigValidationError(
                f"Cannot enable {capability_name}: data policy has not been accepted. "
                "Please review and accept the data policy first."
            )
        consent_field = (
            "ai_automation_consent"
            if capability_name == "AI Automation"
            else "ai_assistant_consent"
        )
        if not getattr(config, consent_field):
            raise OrganizationAIConfigValidationError(
                f"Cannot enable {capability_name}: {capability_name} consent has not been accepted."
            )
        # Must have a usable credential
        try:
            await self._resolve_api_key(config)
        except OrganizationAIConfigValidationError as exc:
            raise OrganizationAIConfigValidationError(
                f"Cannot enable {capability_name}: {exc}"
            ) from exc
        # Health check must pass
        try:
            key = await self._resolve_api_key(config)
            await self._test_completion(config.base_url, key, config.model)
        except (OrganizationAIConfigValidationError, OrganizationAIConfigTestError) as exc:
            raise OrganizationAIConfigTestError(
                f"Cannot enable {capability_name}: provider health check failed — {exc}"
            ) from exc

    def _build_toggle_audit(
        self, capability: str, enabled: bool, config: OrganizationAIConfiguration
    ) -> dict[str, object]:
        return {
            "action": "enable" if enabled else "disable",
            "capability": capability,
            "provider": config.provider,
            "model": config.model,
            "credential_source": config.credential_source,
        }

    # ------------------------------------------------------------------
    # Data policy consent
    # ------------------------------------------------------------------

    @staticmethod
    def get_data_policy() -> dict[str, object]:
        """Return the current data policy content and version."""
        return {
            "version": DATA_POLICY_VERSION,
            "items": DATA_POLICY_ITEMS,
        }

    async def accept_data_policy(self, admin: User) -> AIConfigurationUpdateResult:
        """Record that the current HR has accepted the data policy."""
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError(
                "No provider configuration exists. Configure provider and model first."
            )
        now = datetime.now(UTC)
        config.data_policy_accepted = True
        config.data_policy_accepted_at = now
        config.data_policy_accepted_by_user_id = admin.id
        config.data_policy_version = DATA_POLICY_VERSION
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "action": "data_policy_accept",
                "policy_version": DATA_POLICY_VERSION,
                "provider": config.provider,
                "model": config.model,
                "credential_source": config.credential_source,
            },
        )

    async def _accept_capability_consent(
        self, capability: str, admin: User
    ) -> AIConfigurationUpdateResult:
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError(
                "No provider configuration exists. Configure provider and model first."
            )
        field_name = (
            "ai_automation_consent" if capability == "automation" else "ai_assistant_consent"
        )
        setattr(config, field_name, True)
        now = datetime.now(UTC)
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "action": "capability_consent_accept",
                "capability": capability,
                "policy_version": DATA_POLICY_VERSION,
            },
        )

    async def accept_automation_consent(self, admin: User) -> AIConfigurationUpdateResult:
        return await self._accept_capability_consent("automation", admin)

    async def accept_assistant_consent(self, admin: User) -> AIConfigurationUpdateResult:
        return await self._accept_capability_consent("assistant", admin)

    async def set_policy_preset(
        self, preset: AIPolicyPreset, admin: User
    ) -> AIConfigurationUpdateResult:
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError("No provider configuration exists.")
        version = _POLICY_PRESET_VERSIONS[preset]
        config.ai_policy_preset = preset.value
        config.ai_policy_preset_version = version
        config.updated_at = datetime.now(UTC)
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "action": "ai_policy_preset_update",
                "policy_preset": preset.value,
                "policy_preset_version": version,
            },
        )

    # ------------------------------------------------------------------
    # AI Automation toggle
    # ------------------------------------------------------------------

    async def enable_automation(self, admin: User) -> AIConfigurationUpdateResult:
        """Enable AI Automation after validating preconditions."""
        await self._can_enable("AI Automation")
        config = await self.repository.get()
        assert config is not None  # _can_enable guarantees this
        now = datetime.now(UTC)
        config.ai_automation_enabled = True
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details=self._build_toggle_audit("automation", True, config),
        )

    async def disable_automation(self, admin: User) -> AIConfigurationUpdateResult:
        """Disable AI Automation. No preconditions needed."""
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError("No provider configuration exists.")
        now = datetime.now(UTC)
        config.ai_automation_enabled = False
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details=self._build_toggle_audit("automation", False, config),
        )

    # ------------------------------------------------------------------
    # AI Assistant toggle
    # ------------------------------------------------------------------

    async def enable_assistant(self, admin: User) -> AIConfigurationUpdateResult:
        """Enable AI Assistant after validating preconditions."""
        await self._can_enable("AI Assistant")
        config = await self.repository.get()
        assert config is not None  # _can_enable guarantees this
        now = datetime.now(UTC)
        config.ai_assistant_enabled = True
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details=self._build_toggle_audit("assistant", True, config),
        )

    async def disable_assistant(self, admin: User) -> AIConfigurationUpdateResult:
        """Disable AI Assistant. No preconditions needed."""
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError("No provider configuration exists.")
        now = datetime.now(UTC)
        config.ai_assistant_enabled = False
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details=self._build_toggle_audit("assistant", False, config),
        )

    # ------------------------------------------------------------------
    # Existing: provider config operations
    # ------------------------------------------------------------------

    async def update(
        self, candidate: AIConfigurationCandidate, admin: User
    ) -> AIConfigurationUpdateResult:
        """Test connection and update provider + model + org API key atomically."""
        await self.test_connection(candidate)
        encrypted = self.crypto.encrypt(candidate.api_key)
        now = datetime.now(UTC)
        existing = await self.repository.get()
        config = existing or OrganizationAIConfiguration(
            id=uuid4(), organization_singleton_key="default", created_at=now
        )
        config.provider = candidate.provider.strip()
        config.base_url = candidate.base_url.rstrip("/")
        config.model = candidate.model.strip()
        config.api_key_enc = encrypted
        config.credential_source = CredentialSource.ORG_API_KEY.value
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "provider": candidate.provider.strip(),
                "base_url": candidate.base_url.rstrip("/"),
                "model": candidate.model.strip(),
                "credential_source": CredentialSource.ORG_API_KEY.value,
                "action": "update",
            },
        )

    async def activate_org_api_key(self, api_key: str, admin: User) -> AIConfigurationUpdateResult:
        """Activate a new Organization API key (saves encrypted key). No test performed."""
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError(
                "No provider configuration exists. Use update to create one."
            )
        if not api_key.strip():
            raise OrganizationAIConfigValidationError("api_key must not be empty")
        encrypted = self.crypto.encrypt(api_key.strip())
        now = datetime.now(UTC)
        config.api_key_enc = encrypted
        config.credential_source = CredentialSource.ORG_API_KEY.value
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "provider": config.provider,
                "model": config.model,
                "credential_source": CredentialSource.ORG_API_KEY.value,
                "action": "rotate",
                "key_updated": True,
            },
        )

    async def set_credential_source(self, source: str, admin: User) -> AIConfigurationUpdateResult:
        """Change the credential source (org_api_key or deployment_key)."""
        if source not in {s.value for s in CredentialSource}:
            raise OrganizationAIConfigValidationError(f"Invalid credential source: {source}")
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError("No provider configuration exists.")
        if source == CredentialSource.DEPLOYMENT_KEY.value:
            deployment_key = self._get_deployment_key()
            if not deployment_key:
                raise OrganizationAIConfigValidationError(
                    "Deployment key is not configured. Set AI_DEPLOYMENT_KEY environment variable."
                )
            # Test with deployment key
            await self._test_completion(config.base_url, deployment_key, config.model)
        elif source == CredentialSource.ORG_API_KEY.value:
            if not config.api_key_enc:
                raise OrganizationAIConfigValidationError(
                    "No Organization API key is configured. Use update to set one."
                )
        now = datetime.now(UTC)
        config.credential_source = source
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "credential_source": source,
                "action": "source_change",
            },
        )

    async def revoke_org_api_key(self, admin: User) -> AIConfigurationUpdateResult:
        """Revoke the Organization API key, preserving provider/model configuration."""
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError("No provider configuration exists.")
        now = datetime.now(UTC)
        config.api_key_enc = ""
        config.credential_source = CredentialSource.ORG_API_KEY.value
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "provider": config.provider,
                "model": config.model,
                "action": "revoke",
            },
        )

    async def update_provider_config(
        self, provider: str, base_url: str, model: str, admin: User
    ) -> AIConfigurationUpdateResult:
        """Update provider/model/base_url without changing the API key."""
        from urllib.parse import urlparse

        if not provider.strip():
            raise OrganizationAIConfigValidationError("provider must not be empty")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OrganizationAIConfigValidationError("base_url must be a valid HTTP(S) URL")
        if not model.strip():
            raise OrganizationAIConfigValidationError("model must not be empty")

        existing = await self.repository.get()
        now = datetime.now(UTC)
        config = existing or OrganizationAIConfiguration(
            id=uuid4(), organization_singleton_key="default", created_at=now
        )
        config.provider = provider.strip()
        config.base_url = base_url.rstrip("/")
        config.model = model.strip()
        if existing is None:
            config.api_key_enc = ""
        config.updated_at = now
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "provider": provider.strip(),
                "base_url": base_url.rstrip("/"),
                "model": model.strip(),
                "action": "update_provider",
            },
        )

    async def configure_classification_rollout(
        self, candidate: ClassificationRolloutCandidate, admin: User
    ) -> AIConfigurationUpdateResult:
        """Select a business policy and safely stage a classifier version."""
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError("No provider configuration exists.")
        if not candidate.classifier_version.strip() or not candidate.policy_version.strip():
            raise OrganizationAIConfigValidationError(
                "classifier_version and policy_version must not be empty"
            )
        if not 0 <= candidate.canary_percentage <= 100:
            raise OrganizationAIConfigValidationError("canary_percentage must be between 0 and 100")
        if candidate.mode == RolloutMode.CANARY and candidate.canary_percentage == 0:
            raise OrganizationAIConfigValidationError(
                "canary rollout requires canary_percentage greater than zero"
            )
        if candidate.mode == RolloutMode.FULL:
            if candidate.release_metrics is None:
                raise OrganizationAIConfigValidationError(
                    "full rollout requires a release metrics report"
                )
            decision = evaluate_release_gates(candidate.release_metrics)
            if not decision.allowed:
                raise OrganizationAIConfigValidationError(
                    "full rollout blocked by release gates: " + ", ".join(decision.failures)
                )

        config.candidate_classifier_version = candidate.classifier_version.strip()
        config.candidate_classification_policy = candidate.business_policy.value
        config.candidate_classification_policy_version = candidate.policy_version.strip()
        config.rollout_mode = candidate.mode.value
        config.canary_percentage = (
            100 if candidate.mode == RolloutMode.FULL else candidate.canary_percentage
        )
        config.updated_at = datetime.now(UTC)
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "action": "classification_rollout_configure",
                "rollout_mode": candidate.mode.value,
                "business_policy": candidate.business_policy.value,
                "policy_version": candidate.policy_version,
                "stable_classifier_version": config.stable_classifier_version,
                "candidate_classifier_version": candidate.classifier_version,
                "canary_percentage": config.canary_percentage,
            },
        )

    async def enforce_classification_guardrails(
        self, metrics: ReleaseMetrics, admin: User
    ) -> AIConfigurationUpdateResult:
        """Automatically roll back an active candidate that violates guardrails."""
        decision = evaluate_release_gates(metrics)
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError("No provider configuration exists.")
        if decision.allowed or config.rollout_mode == RolloutMode.STABLE.value:
            return AIConfigurationUpdateResult(
                view=await self.get_view(),
                audit_details={
                    "action": "classification_rollout_guardrails_passed",
                    "failures": list(decision.failures),
                },
            )

        result = await self.rollback_classification_rollout(admin)
        result.audit_details = {
            "action": "classification_rollout_auto_rollback",
            "failures": list(decision.failures),
            "rolled_back_classifier_version": result.audit_details.get(
                "rolled_back_classifier_version"
            ),
            "rolled_back_policy_version": result.audit_details.get("rolled_back_policy_version"),
            "restored_classifier_version": result.audit_details.get("restored_classifier_version"),
            "restored_policy_version": result.audit_details.get("restored_policy_version"),
        }
        return result

    async def rollback_classification_rollout(self, admin: User) -> AIConfigurationUpdateResult:
        """Restore retained stable versions without touching workflow records."""
        config = await self.repository.get()
        if config is None:
            raise OrganizationAIConfigValidationError("No provider configuration exists.")
        rolled_back_classifier = config.candidate_classifier_version
        rolled_back_policy = config.candidate_classification_policy_version
        config.candidate_classifier_version = None
        config.candidate_classification_policy = None
        config.candidate_classification_policy_version = None
        config.rollout_mode = RolloutMode.STABLE.value
        config.canary_percentage = 0
        config.updated_at = datetime.now(UTC)
        config.updated_by_user_id = admin.id
        await self.repository.save(config)
        return AIConfigurationUpdateResult(
            view=await self.get_view(),
            audit_details={
                "action": "classification_rollout_rollback",
                "restored_classifier_version": config.stable_classifier_version,
                "restored_policy_version": config.classification_policy_version,
                "rolled_back_classifier_version": rolled_back_classifier,
                "rolled_back_policy_version": rolled_back_policy,
            },
        )
