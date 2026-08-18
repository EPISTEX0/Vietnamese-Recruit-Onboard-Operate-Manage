import json
from uuid import uuid4

import httpx
import pytest
import respx

from src.modules.identity.application.organization_ai_config_service import (
    AIConfigurationCandidate,
    OrganizationAIConfigService,
    OrganizationAIConfigTestError,
    OrganizationAIConfigValidationError,
)
from src.modules.identity.domain.entities import CredentialSource, OrganizationAIConfiguration, User
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils


# Stub settings with configurable deployment key
class StubSettings(AuthSettings):
    model_config = {"env_prefix": "AUTH_", "extra": "allow"}

    google_client_id: str = "test-client-id"  # type: ignore[assignment]
    google_client_secret: str = "test-client-secret"  # type: ignore[assignment]
    jwt_secret_key: str = "test-jwt-secret"  # type: ignore[assignment]
    oauth_token_encryption_key: str = "dGVzdC1rZXktMzItYnl0ZXMteHh4eHh4eA=="  # type: ignore[assignment]

    ai_deployment_key: str | None = None  # type: ignore[assignment]


class FakeRepository:
    def __init__(self) -> None:
        self.config: OrganizationAIConfiguration | None = None

    async def get(self) -> OrganizationAIConfiguration | None:
        return self.config

    async def save(self, config: OrganizationAIConfiguration) -> OrganizationAIConfiguration:
        self.config = config
        return config


@pytest.fixture
def crypto() -> CryptoUtils:
    key = __import__("base64").b64encode(b"x" * 32).decode()
    return CryptoUtils(key)


@pytest.fixture
def service(crypto: CryptoUtils) -> OrganizationAIConfigService:
    return OrganizationAIConfigService(FakeRepository(), crypto, StubSettings())


@pytest.fixture
def service_with_deployment_key(crypto: CryptoUtils) -> OrganizationAIConfigService:
    settings = StubSettings()
    settings.ai_deployment_key = "sk-deployment-key-12345678"
    return OrganizationAIConfigService(FakeRepository(), crypto, settings)


# ---------------------------------------------------------------------------
# Existing tests adapted to new return type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_connection_supports_completion_only_provider(
    service: OrganizationAIConfigService,
) -> None:
    route = respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )
    )

    await service.test_connection(
        AIConfigurationCandidate(
            "openai-completions",
            "https://api.example.test/v1",
            "custom-model",
            "secret-key",
        )
    )

    assert route.called
    request = route.calls[0].request
    assert request.url.path == "/v1/chat/completions"
    assert json.loads(request.content)["model"] == "custom-model"


@pytest.mark.asyncio
@respx.mock
async def test_update_tests_before_encrypting_and_never_returns_plaintext(
    service: OrganizationAIConfigService,
) -> None:
    route = respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    user = User(id=uuid4(), email="hr@example.com", name="HR")

    result = await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "custom", "secret-key"),
        user,
    )

    assert route.called
    assert result.view.api_key_masked == "******-key"
    assert result.view.credential_source == CredentialSource.ORG_API_KEY.value
    assert result.audit_details["action"] == "update"
    assert service.repository.config is not None
    assert service.repository.config.api_key_enc != "secret-key"
    assert service.repository.config.credential_source == CredentialSource.ORG_API_KEY.value


@pytest.mark.asyncio
@respx.mock
async def test_failed_connection_does_not_replace_existing(
    service: OrganizationAIConfigService,
) -> None:
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "no"})
    )
    user = User(id=uuid4(), email="hr@example.com", name="HR")

    with pytest.raises(OrganizationAIConfigTestError):
        await service.update(
            AIConfigurationCandidate(
                "openai", "https://api.example.test/v1", "custom", "secret-key"
            ),
            user,
        )
    assert service.repository.config is None


# ---------------------------------------------------------------------------
# New tests: deployment key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_view_shows_deployment_key_unavailable(
    service: OrganizationAIConfigService,
) -> None:
    view = await service.get_view()
    assert view.deployment_key_available is False
    assert view.credential_source == CredentialSource.ORG_API_KEY.value


@pytest.mark.asyncio
async def test_get_view_shows_deployment_key_available(
    service_with_deployment_key: OrganizationAIConfigService,
) -> None:
    view = await service_with_deployment_key.get_view()
    assert view.deployment_key_available is True


@pytest.mark.asyncio
@respx.mock
async def test_set_credential_source_to_deployment_key(
    service_with_deployment_key: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    # First create a config
    route = respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service_with_deployment_key.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "custom", "sk-test-key"),
        user,
    )
    route.reset()

    # Now switch to deployment key (mocking the deployment key test)
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    result = await service_with_deployment_key.set_credential_source(
        CredentialSource.DEPLOYMENT_KEY.value, user
    )
    assert result.view.credential_source == CredentialSource.DEPLOYMENT_KEY.value
    assert result.audit_details["action"] == "source_change"


@pytest.mark.asyncio
@respx.mock
async def test_set_credential_source_to_deployment_key_fails_when_not_available(
    service: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "custom", "sk-test-key"),
        user,
    )

    with pytest.raises(
        OrganizationAIConfigValidationError, match="Deployment key is not configured"
    ):
        await service.set_credential_source(CredentialSource.DEPLOYMENT_KEY.value, user)


# ---------------------------------------------------------------------------
# Rotation (activate) tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_activate_org_api_key(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "custom", "old-key"),
        user,
    )

    old_enc = service.repository.config.api_key_enc

    result = await service.activate_org_api_key("sk-new-key", user)
    assert result.view.api_key_masked is not None
    assert result.audit_details["action"] == "rotate"
    assert result.audit_details["key_updated"] is True
    assert service.repository.config.api_key_enc != old_enc
    assert service.repository.config.credential_source == CredentialSource.ORG_API_KEY.value


@pytest.mark.asyncio
async def test_activate_without_config_fails(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    with pytest.raises(OrganizationAIConfigValidationError, match="No provider configuration"):
        await service.activate_org_api_key("sk-new-key", user)


# ---------------------------------------------------------------------------
# Revoke tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_revoke_org_api_key_clears_key_preserves_config(
    service: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )

    result = await service.revoke_org_api_key(user)

    assert result.view.api_key_masked is None
    assert result.view.provider == "openai"
    assert result.view.model == "gpt-4o"
    assert result.view.configured is True
    assert result.audit_details["action"] == "revoke"
    assert service.repository.config.api_key_enc == ""
    assert service.repository.config.provider == "openai"
    assert service.repository.config.model == "gpt-4o"


@pytest.mark.asyncio
async def test_revoke_without_config_fails(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    with pytest.raises(OrganizationAIConfigValidationError, match="No provider configuration"):
        await service.revoke_org_api_key(user)


# ---------------------------------------------------------------------------
# Audit safety: no secrets in audit details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_update_audit_has_no_secret(service: OrganizationAIConfigService) -> None:
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    user = User(id=uuid4(), email="hr@example.com", name="HR")

    result = await service.update(
        AIConfigurationCandidate(
            "openai", "https://api.example.test/v1", "gpt-4o", "sk-secret-12345"
        ),
        user,
    )
    details = result.audit_details
    assert "provider" in details
    assert "base_url" in details
    assert "model" in details
    # Must NOT contain the actual secret key value
    assert "sk-secret-12345" not in str(details)
    # The details dict must not have a raw api_key field
    assert "api_key" not in details


@pytest.mark.asyncio
@respx.mock
async def test_rotate_audit_has_no_secret(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "old-key"),
        user,
    )

    result = await service.activate_org_api_key("sk-new-secret-key", user)
    assert "sk-new-secret-key" not in str(result.audit_details)


@pytest.mark.asyncio
@respx.mock
async def test_revoke_audit_has_no_secret(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )

    result = await service.revoke_org_api_key(user)
    assert "sk-key" not in str(result.audit_details)


# ---------------------------------------------------------------------------
# Provider config update without key change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_update_provider_config_preserves_key(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )
    old_enc = service.repository.config.api_key_enc

    result = await service.update_provider_config(
        "anthropic", "https://api.anthropic.com/v1", "claude-3.5", user
    )
    assert result.view.provider == "anthropic"
    assert result.view.model == "claude-3.5"
    assert service.repository.config.api_key_enc == old_enc
    assert result.audit_details["action"] == "update_provider"


# ---------------------------------------------------------------------------
# Data policy consent tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_data_policy_returns_static_policy(service: OrganizationAIConfigService) -> None:
    policy = service.get_data_policy()
    assert policy["version"] == "1.0"
    assert isinstance(policy["items"], list)
    assert len(policy["items"]) == 3
    assert policy["items"][0]["category"] == "email_intent_classification"


@pytest.mark.asyncio
@respx.mock
async def test_accept_data_policy_records_consent(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )

    result = await service.accept_data_policy(user)

    assert result.view.data_policy_accepted is True
    assert result.view.data_policy_version == "1.0"
    assert result.audit_details["action"] == "data_policy_accept"
    assert result.audit_details["policy_version"] == "1.0"
    assert "provider" in result.audit_details
    assert "model" in result.audit_details
    assert service.repository.config.data_policy_accepted is True
    assert service.repository.config.data_policy_version == "1.0"
    assert service.repository.config.data_policy_accepted_by_user_id == user.id


@pytest.mark.asyncio
async def test_accept_data_policy_without_config_fails(
    service: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    with pytest.raises(OrganizationAIConfigValidationError, match="No provider configuration"):
        await service.accept_data_policy(user)
        await service.accept_automation_consent(user)
        await service.accept_assistant_consent(user)


# ---------------------------------------------------------------------------
# Capability state tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_state_not_configured_when_no_config(
    service: OrganizationAIConfigService,
) -> None:
    view = await service.get_view()
    assert view.automation_state == "not_configured"
    assert view.assistant_state == "not_configured"
    assert view.automation_enabled is False
    assert view.assistant_enabled is False


@pytest.mark.asyncio
@respx.mock
async def test_capability_state_disabled_when_configured_but_not_enabled(
    service: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )

    view = await service.get_view()
    assert view.automation_state == "disabled"
    assert view.assistant_state == "disabled"
    assert view.automation_enabled is False
    assert view.assistant_enabled is False


@pytest.mark.asyncio
@respx.mock
async def test_capability_state_ready_when_enabled_and_has_credential(
    service: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )
    await service.accept_data_policy(user)
    await service.accept_automation_consent(user)
    await service.accept_assistant_consent(user)

    result = await service.enable_automation(user)
    assert result.view.automation_enabled is True
    assert result.view.automation_state == "ready"


@pytest.mark.asyncio
@respx.mock
async def test_capability_state_unavailable_when_credential_revoked(
    service: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )
    await service.accept_data_policy(user)
    await service.accept_automation_consent(user)
    await service.accept_assistant_consent(user)
    # Enable automation
    await service.enable_automation(user)

    # Revoke the key
    await service.revoke_org_api_key(user)

    view = await service.get_view()
    assert view.automation_enabled is True  # toggle stays on
    assert view.automation_state == "unavailable"  # but no credential


# ---------------------------------------------------------------------------
# #384 — a key present but undecryptable must not look like "no key"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_view_flags_decrypt_failure_distinct_from_no_key(
    service: OrganizationAIConfigService,
) -> None:
    """decrypt() raising must not collapse to the same payload as no key at all.

    Before #384, ``get_view`` either crashed outright (the credential-usability
    check only caught the "missing key" validation error, not a real decrypt
    failure) or -- once that crash was avoided -- reported ``api_key_masked =
    None`` indistinguishable from "never configured". An admin who sees that
    concludes there's no key and enters a new one, silently overwriting
    whatever was there. The two situations need opposite actions (restore the
    encryption key vs. add a new one), so the view must carry a signal that
    tells them apart.
    """
    service.repository.config = OrganizationAIConfiguration(
        id=uuid4(),
        organization_singleton_key="default",
        provider="openai",
        base_url="https://api.example.test/v1",
        model="gpt-4o",
        api_key_enc="not-valid-ciphertext-at-all",
        credential_source=CredentialSource.ORG_API_KEY.value,
    )

    broken_view = await service.get_view()

    assert broken_view.api_key_masked is None
    assert broken_view.api_key_decrypt_failed is True

    assert service.repository.config is not None
    service.repository.config.api_key_enc = ""
    unconfigured_view = await service.get_view()

    assert unconfigured_view.api_key_masked is None
    assert unconfigured_view.api_key_decrypt_failed is False


# ---------------------------------------------------------------------------
# Enable rejection tests (safe enable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_rejected_when_no_config(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    with pytest.raises(OrganizationAIConfigValidationError, match="no provider configuration"):
        await service.enable_automation(user)
    with pytest.raises(OrganizationAIConfigValidationError, match="no provider configuration"):
        await service.enable_assistant(user)


@pytest.mark.asyncio
@respx.mock
async def test_enable_rejected_when_no_consent(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )

    with pytest.raises(
        OrganizationAIConfigValidationError, match="data policy has not been accepted"
    ):
        await service.enable_automation(user)
    with pytest.raises(
        OrganizationAIConfigValidationError, match="data policy has not been accepted"
    ):
        await service.enable_assistant(user)


@pytest.mark.asyncio
@respx.mock
async def test_enable_rejected_when_health_check_fails(
    service: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )
    await service.accept_data_policy(user)
    await service.accept_automation_consent(user)
    await service.accept_assistant_consent(user)

    # Now make health check fail
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "down"})
    )

    with pytest.raises(OrganizationAIConfigTestError, match="health check failed"):
        await service.enable_automation(user)


# ---------------------------------------------------------------------------
# Independent toggle tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_can_enable_automation_independently(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )
    await service.accept_data_policy(user)
    await service.accept_automation_consent(user)
    await service.accept_assistant_consent(user)

    result = await service.enable_automation(user)
    assert result.view.automation_enabled is True
    assert result.view.assistant_enabled is False
    assert result.audit_details["capability"] == "automation"
    assert result.audit_details["action"] == "enable"


@pytest.mark.asyncio
@respx.mock
async def test_can_enable_assistant_independently(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )
    await service.accept_data_policy(user)
    await service.accept_automation_consent(user)
    await service.accept_assistant_consent(user)

    result = await service.enable_assistant(user)
    assert result.view.assistant_enabled is True
    assert result.view.automation_enabled is False
    assert result.audit_details["capability"] == "assistant"
    assert result.audit_details["action"] == "enable"


@pytest.mark.asyncio
@respx.mock
async def test_can_enable_both_independently(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )
    await service.accept_data_policy(user)
    await service.accept_automation_consent(user)
    await service.accept_assistant_consent(user)

    await service.enable_automation(user)
    result = await service.enable_assistant(user)

    assert result.view.automation_enabled is True
    assert result.view.assistant_enabled is True


@pytest.mark.asyncio
@respx.mock
async def test_disable_does_not_require_consent_or_health(
    service: OrganizationAIConfigService,
) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate("openai", "https://api.example.test/v1", "gpt-4o", "sk-key"),
        user,
    )
    # Enable without consent (simulate pre-existing state)
    service.repository.config.ai_automation_enabled = True
    service.repository.config.ai_assistant_enabled = True

    result = await service.disable_automation(user)
    assert result.view.automation_enabled is False
    assert result.audit_details["action"] == "disable"
    assert result.audit_details["capability"] == "automation"

    result = await service.disable_assistant(user)
    assert result.view.assistant_enabled is False
    assert result.audit_details["action"] == "disable"
    assert result.audit_details["capability"] == "assistant"


# ---------------------------------------------------------------------------
# Audit safety tests: no secrets in audit details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_consent_audit_has_no_secret(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate(
            "openai", "https://api.example.test/v1", "gpt-4o", "sk-secret-key"
        ),
        user,
    )

    result = await service.accept_data_policy(user)
    assert "sk-secret-key" not in str(result.audit_details)
    assert "api_key" not in result.audit_details


@pytest.mark.asyncio
@respx.mock
async def test_toggle_audit_has_no_secret(service: OrganizationAIConfigService) -> None:
    user = User(id=uuid4(), email="hr@example.com", name="HR")
    respx.post("https://api.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await service.update(
        AIConfigurationCandidate(
            "openai", "https://api.example.test/v1", "gpt-4o", "sk-toggle-secret"
        ),
        user,
    )
    await service.accept_data_policy(user)
    await service.accept_automation_consent(user)
    await service.accept_assistant_consent(user)

    result = await service.enable_automation(user)
    assert "sk-toggle-secret" not in str(result.audit_details)
    assert "provider" in result.audit_details
    assert "model" in result.audit_details
    assert "capability" in result.audit_details
