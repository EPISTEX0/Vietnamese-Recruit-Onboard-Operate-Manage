"""FastAPI router for system-administration endpoints.

Defines the /api/system-admin/* endpoints for managing whitelist entries,
OAuth configuration, LLM provider credentials, user roles, and audit logs.
Every endpoint here requires the SYSTEM_ADMIN role (ADR-0009).

This module also owns the two shared role guards, ``require_system_admin``
and ``require_hr``. There is deliberately no generic ``require_admin``: an
alias that accepts either role is how 38 HR endpoints silently ended up
gated by SYSTEM_ADMIN. Call sites must name the role they mean.

Every writing endpoint here ends with an explicit ``await session.commit()``
(#312). ``get_db_session`` also commits, but only when FastAPI unwinds its
dependency stack -- which happens *after* the response has been sent, so the
teardown alone answers 200 before the write is durable and turns a failed
commit into a success the client already holds. The teardown stays as the
safety net for anything that forgets; these commits put the transaction
boundary back where the decision is made.

The commits sit in the handlers rather than one layer down, unlike most of the
70 explicit commits elsewhere in the repo, because these endpoints hold their
use case in the handler itself: there is no application service between the
route and the repositories, and the audit call the commit must follow is right
here too. Anything that grows a service layer should move its commit with it.

Each commit sits *after* the audit call, so the audit row is durable before
the response goes out and not just the action it describes. Note that this is
an ordering guarantee, not an atomicity one: the whitelist endpoints, the
three domain endpoints and ``create_staff_account`` reach code that already
commits its own work, so for those six the audit row lands in a later
transaction of its own. Precisely: ``WhitelistRepository.add``/``.remove``
commit; the three domain endpoints all land in
``OrganizationSettingsRepository.set_allowed_domains``, ``add_domains`` and
``remove_domain`` by delegating to it and ``replace_domains`` directly; and
``AuthService.create_staff_account`` commits when it holds a session, which
on this path it always does because ``get_auth_service`` passes one.
A bare ``commit()`` is correct here because
identity audits through ``AuditService.log_action``, which propagates
failures; the swallowing ``log_audit`` that forces recruitment's
``_commit_audit()`` dance has no call site in this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

if TYPE_CHECKING:
    from src.modules.recruitment.infrastructure.org_settings_repository import (
        OrganizationSettingsRepository,
    )

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.assistant.infrastructure.tool_config_repository import (
    ToolConfigRepository,
)
from src.modules.gmail.application.classification_rollout import ReleaseMetrics
from src.modules.identity.api.admin_schemas import (
    ActivateOrgApiKeyRequest,
    AdminUserResponse,
    AIConnectionTestResponse,
    AssistantToolConfigListResponse,
    AssistantToolConfigResponse,
    AssistantToolConfigUpdateRequest,
    AuditLogResponse,
    ClassificationReleaseMetricsRequest,
    ClassificationRolloutRequest,
    ClassificationRolloutTelemetryResponse,
    DataPolicyResponse,
    DomainAddRequest,
    DomainListResponse,
    DomainRemoveResponse,
    DomainReplaceRequest,
    OrganizationAIConfigurationRequest,
    OrganizationAIConfigurationResponse,
    PaginatedAuditLogsResponse,
    RoleUpdateRequest,
    SetCredentialSourceRequest,
    StaffAccountCreateRequest,
    StaffAccountCreateResponse,
    UpdateProviderConfigRequest,
)
from src.modules.identity.api.schemas import (
    OAuthConfigResponse,
    OAuthConfigUpdateRequest,
    WhitelistAddRequest,
    WhitelistEntryCreatedResponse,
    WhitelistEntrySchema,
    WhitelistListResponse,
)
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.application.auth_service import (
    AccountAlreadyExistsError,
    AuthService,
)
from src.modules.identity.application.oauth_config_manager import (
    OAuthConfigManager,
    OAuthConfigValidationError,
)
from src.modules.identity.application.organization_ai_config_service import (
    AIConfigurationCandidate,
    AIPolicyPreset,
    ClassificationRolloutCandidate,
    OrganizationAIConfigService,
    OrganizationAIConfigTestError,
    OrganizationAIConfigValidationError,
)
from src.modules.identity.application.role_service import (
    LastAdminError,
    RoleService,
    SelfDemotionError,
    SuperAdminProtectedError,
    UserNotFoundError,
)
from src.modules.identity.application.whitelist_manager import WhitelistManager
from src.modules.identity.container import (
    get_auth_service,
    get_crypto_utils,
    get_current_user,
    get_db_session,
    get_oauth_config_manager,
    get_settings,
    get_whitelist_manager,
)
from src.modules.identity.domain.entities import AuditActionType, User, UserRole
from src.modules.identity.infrastructure.audit_log_repository import AuditLogRepository

admin_router = APIRouter(prefix="/api/system-admin", tags=["system-admin"])


async def require_system_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Verify the current user has the SYSTEM_ADMIN role."""
    if current_user.role != UserRole.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "SYSTEM_ADMIN_ACCESS_DENIED",
                "message": "System Admin access required",
            },
        )
    return current_user


async def require_hr(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Verify the current user has the HR role."""
    if current_user.role != UserRole.HR:
        raise HTTPException(
            status_code=403,
            detail={"code": "HR_ACCESS_DENIED", "message": "HR access required"},
        )
    return current_user


SystemAdminUserDep = Annotated[User, Depends(require_system_admin)]
HRUserDep = Annotated[User, Depends(require_hr)]

# --- Dependency providers for admin services ---


async def get_role_service(
    session: AsyncSession = Depends(get_db_session),
) -> RoleService:
    """Provide a RoleService instance with the current session.

    Args:
        session: The async database session from DI.

    Returns:
        A RoleService bound to the current session and super admin config.
    """
    settings = get_settings()
    return RoleService(session=session, super_admin_email=settings.super_admin_email)


async def get_audit_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuditService:
    """Provide an AuditService instance with the current session.

    Args:
        session: The async database session from DI.

    Returns:
        An AuditService bound to the current session's audit log repository.
    """
    repository = AuditLogRepository(session)
    return AuditService(repository=repository)


async def get_organization_ai_config_service(
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigService:
    from src.modules.identity.infrastructure.organization_ai_config_repository import (
        OrganizationAIConfigRepository,
    )

    return OrganizationAIConfigService(OrganizationAIConfigRepository(session), get_crypto_utils())


def _ai_view_response(view: object) -> OrganizationAIConfigurationResponse:
    return OrganizationAIConfigurationResponse(
        provider=view.provider,  # type: ignore[attr-defined]
        base_url=view.base_url,  # type: ignore[attr-defined]
        model=view.model,  # type: ignore[attr-defined]
        api_key_masked=view.api_key_masked,  # type: ignore[attr-defined]
        configured=view.configured,  # type: ignore[attr-defined]
        updated_at=view.updated_at,  # type: ignore[attr-defined]
        credential_source=view.credential_source,  # type: ignore[attr-defined]
        deployment_key_available=view.deployment_key_available,  # type: ignore[attr-defined]
        data_policy_accepted=view.data_policy_accepted,  # type: ignore[attr-defined]
        data_policy_accepted_at=view.data_policy_accepted_at,  # type: ignore[attr-defined]
        data_policy_version=view.data_policy_version,  # type: ignore[attr-defined]
        automation_enabled=view.automation_enabled,  # type: ignore[attr-defined]
        automation_state=view.automation_state,  # type: ignore[attr-defined]
        assistant_enabled=view.assistant_enabled,  # type: ignore[attr-defined]
        assistant_state=view.assistant_state,  # type: ignore[attr-defined]
        classification_policy=view.classification_policy,  # type: ignore[attr-defined]
        classification_policy_version=view.classification_policy_version,  # type: ignore[attr-defined]
        stable_classifier_version=view.stable_classifier_version,  # type: ignore[attr-defined]
        candidate_classifier_version=view.candidate_classifier_version,  # type: ignore[attr-defined]
        candidate_classification_policy=view.candidate_classification_policy,  # type: ignore[attr-defined]
        candidate_classification_policy_version=view.candidate_classification_policy_version,  # type: ignore[attr-defined]
        rollout_mode=view.rollout_mode,  # type: ignore[attr-defined]
        canary_percentage=view.canary_percentage,  # type: ignore[attr-defined]
        ai_automation_consent=view.ai_automation_consent,  # type: ignore[attr-defined]
        ai_assistant_consent=view.ai_assistant_consent,  # type: ignore[attr-defined]
        ai_policy_preset=view.ai_policy_preset,  # type: ignore[attr-defined]
        ai_policy_preset_version=view.ai_policy_preset_version,  # type: ignore[attr-defined]
    )


@admin_router.get("/organization/ai-config", response_model=OrganizationAIConfigurationResponse)
@admin_router.get(
    "/organization/ai-configuration",
    response_model=OrganizationAIConfigurationResponse,
    include_in_schema=False,
)
async def get_organization_ai_config(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
) -> OrganizationAIConfigurationResponse:
    return _ai_view_response(await service.get_view())


@admin_router.get(
    "/organization/ai-config/classification-rollout/telemetry",
    response_model=ClassificationRolloutTelemetryResponse,
)
async def get_classification_rollout_telemetry(
    system_admin_user: SystemAdminUserDep,
    session: AsyncSession = Depends(get_db_session),
    hours: int = Query(default=24, ge=1, le=720),
) -> ClassificationRolloutTelemetryResponse:
    """Return measured operational metrics from durable rollout/workflow state."""
    from src.modules.gmail.infrastructure.classification_rollout_repository import (
        ClassificationRolloutRepository,
    )

    telemetry = await ClassificationRolloutRepository(session).summarize(hours=hours)
    return ClassificationRolloutTelemetryResponse(**telemetry.__dict__)


@admin_router.put(
    "/organization/ai-config/classification-rollout",
    response_model=OrganizationAIConfigurationResponse,
)
async def configure_classification_rollout(
    body: ClassificationRolloutRequest,
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Select an audited business policy and rollout stage."""
    metrics = (
        ReleaseMetrics(**body.release_metrics.model_dump())
        if body.release_metrics is not None
        else None
    )
    try:
        result = await service.configure_classification_rollout(
            ClassificationRolloutCandidate(
                mode=body.mode,
                business_policy=body.business_policy,
                policy_version=body.policy_version,
                classifier_version=body.classifier_version,
                canary_percentage=body.canary_percentage,
                release_metrics=metrics,
            ),
            system_admin_user,
        )
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "CLASSIFICATION_ROLLOUT_BLOCKED", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CLASSIFICATION_ROLLOUT,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


@admin_router.post(
    "/organization/ai-config/classification-rollout/guardrails",
    response_model=OrganizationAIConfigurationResponse,
)
async def enforce_classification_guardrails(
    body: ClassificationReleaseMetricsRequest,
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Apply measured guardrails and automatically roll back unsafe rollout state."""
    result = await service.enforce_classification_guardrails(
        ReleaseMetrics(**body.model_dump()), system_admin_user
    )
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CLASSIFICATION_ROLLOUT,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


@admin_router.post(
    "/organization/ai-config/classification-rollout/rollback",
    response_model=OrganizationAIConfigurationResponse,
)
async def rollback_classification_rollout(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Restore retained stable versions without deleting Recruitment Inbox work."""
    try:
        result = await service.rollback_classification_rollout(system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "CLASSIFICATION_ROLLOUT_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CLASSIFICATION_ROLLOUT,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


@admin_router.post("/organization/ai-config/test", response_model=AIConnectionTestResponse)
async def test_organization_ai_config(
    body: OrganizationAIConfigurationRequest,
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
) -> AIConnectionTestResponse:
    try:
        await service.test_connection(
            AIConfigurationCandidate(body.provider, body.base_url, body.model, body.api_key)
        )
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    except OrganizationAIConfigTestError as exc:
        return AIConnectionTestResponse(success=False, message=str(exc))
    return AIConnectionTestResponse(success=True, message="Connection test succeeded")


@admin_router.put("/organization/ai-config", response_model=OrganizationAIConfigurationResponse)
async def update_organization_ai_config(
    body: OrganizationAIConfigurationRequest,
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    try:
        result = await service.update(
            AIConfigurationCandidate(body.provider, body.base_url, body.model, body.api_key),
            system_admin_user,
        )
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    except OrganizationAIConfigTestError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "AI_CONNECTION_FAILED", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONFIG_UPDATE,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


# --- Credential source management ---


@admin_router.put(
    "/organization/ai-config/source",
    response_model=OrganizationAIConfigurationResponse,
)
async def set_credential_source(
    body: SetCredentialSourceRequest,
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Change the AI credential source (org_api_key or deployment_key)."""
    try:
        result = await service.set_credential_source(body.credential_source, system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    except OrganizationAIConfigTestError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "AI_CONNECTION_FAILED", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONFIG_SOURCE,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


# --- Organization API key rotation ---


@admin_router.post(
    "/organization/ai-config/activate-key",
    response_model=OrganizationAIConfigurationResponse,
)
async def activate_org_api_key(
    body: ActivateOrgApiKeyRequest,
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Activate a new Organization API key (assumes test already passed)."""
    try:
        result = await service.activate_org_api_key(body.api_key, system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONFIG_ROTATE,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


@admin_router.post("/organization/ai-config/revoke-key", status_code=200)
async def revoke_org_api_key(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Revoke the Organization API key, preserving provider/model configuration."""
    try:
        result = await service.revoke_org_api_key(system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONFIG_REVOKE,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


# --- Deployment key health check ---


@admin_router.post(
    "/organization/ai-config/test-deployment-key",
    response_model=AIConnectionTestResponse,
)
async def test_deployment_key_connection(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
) -> AIConnectionTestResponse:
    """Test connectivity using the deployment-wide AI key (if configured)."""
    view = await service.get_view()
    if not view.configured:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": "No provider configuration exists"},
        )
    try:
        await service.test_deployment_key_connection(
            provider=view.provider or "",
            base_url=view.base_url or "",
            model=view.model or "",
        )
    except OrganizationAIConfigTestError as exc:
        return AIConnectionTestResponse(success=False, message=str(exc))
    return AIConnectionTestResponse(
        success=True, message="Deployment key connection test succeeded"
    )


# --- Provider config update without key change ---


@admin_router.put(
    "/organization/ai-config/provider",
    response_model=OrganizationAIConfigurationResponse,
)
async def update_provider_config(
    body: UpdateProviderConfigRequest,
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Update provider/model/base_url without changing the API key."""
    try:
        result = await service.update_provider_config(
            body.provider, body.base_url, body.model, system_admin_user
        )
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONFIG_UPDATE,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


# --- Data Policy & Consent ---


@admin_router.get(
    "/organization/ai-config/data-policy",
    response_model=DataPolicyResponse,
)
async def get_data_policy(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
) -> DataPolicyResponse:
    """Return the Organization AI data policy describing data sent to the provider."""
    policy = service.get_data_policy()
    return DataPolicyResponse(
        version=str(policy["version"]),
        items=policy["items"],  # type: ignore[arg-type]
    )


@admin_router.post(
    "/organization/ai-config/accept-data-policy",
    response_model=OrganizationAIConfigurationResponse,
)
async def accept_data_policy(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Accept the data policy before enabling AI capabilities for the first time."""
    try:
        result = await service.accept_data_policy(system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONSENT,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


# --- Independent capability consent ---


@admin_router.post(
    "/organization/ai-config/automation/consent",
    response_model=OrganizationAIConfigurationResponse,
)
async def accept_automation_consent(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    result = await service.accept_automation_consent(system_admin_user)
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONSENT,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


@admin_router.post(
    "/organization/ai-config/assistant/consent",
    response_model=OrganizationAIConfigurationResponse,
)
async def accept_assistant_consent(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    result = await service.accept_assistant_consent(system_admin_user)
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONSENT,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)

    # --- Capability toggles: AI Automation ---


@admin_router.post(
    "/organization/ai-config/automation/enable",
    response_model=OrganizationAIConfigurationResponse,
)
async def enable_ai_automation(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Enable AI Automation after validating preconditions."""
    try:
        result = await service.enable_automation(system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    except OrganizationAIConfigTestError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "AI_CONNECTION_FAILED", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_TOGGLE_AUTOMATION,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


@admin_router.post(
    "/organization/ai-config/automation/disable",
    response_model=OrganizationAIConfigurationResponse,
)
async def disable_ai_automation(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Disable AI Automation."""
    try:
        result = await service.disable_automation(system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_TOGGLE_AUTOMATION,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


# --- Capability toggles: AI Assistant ---


@admin_router.post(
    "/organization/ai-config/assistant/enable",
    response_model=OrganizationAIConfigurationResponse,
)
async def enable_ai_assistant(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Enable AI Assistant after validating preconditions."""
    try:
        result = await service.enable_assistant(system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    except OrganizationAIConfigTestError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "AI_CONNECTION_FAILED", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_TOGGLE_ASSISTANT,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


@admin_router.post(
    "/organization/ai-config/assistant/disable",
    response_model=OrganizationAIConfigurationResponse,
)
async def disable_ai_assistant(
    system_admin_user: SystemAdminUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    """Disable AI Assistant."""
    try:
        result = await service.disable_assistant(system_admin_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_TOGGLE_ASSISTANT,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)


# --- Versioned AI policy preset ---


@admin_router.put(
    "/organization/ai-config/policy-preset", response_model=OrganizationAIConfigurationResponse
)
async def set_ai_policy_preset(
    system_admin_user: SystemAdminUserDep,
    preset: AIPolicyPreset = Body(...),
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationAIConfigurationResponse:
    result = await service.set_policy_preset(preset, system_admin_user)
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_AI_CONFIG_UPDATE,
        details=result.audit_details,
    )
    await session.commit()
    return _ai_view_response(result.view)

    # --- Whitelist Endpoints ---


@admin_router.get("/whitelist", response_model=WhitelistListResponse)
async def list_whitelist(
    system_admin_user: SystemAdminUserDep,
    whitelist_manager: WhitelistManager = Depends(get_whitelist_manager),
) -> WhitelistListResponse:
    """List all whitelist entries (merged file + database).

    Returns all whitelist entries from both the file-based whitelist and
    the database. File-based entries are marked as read-only.

    Args:
        system_admin_user: The authenticated system admin (enforced by require_system_admin).
        whitelist_manager: The WhitelistManager for querying entries.

    Returns:
        A list of all whitelist entries with metadata.
    """
    entries = await whitelist_manager.list_entries()
    items = [
        WhitelistEntrySchema(
            id=e.id,
            value=e.value,
            entry_type=e.entry_type,
            added_by_email=e.added_by_email,
            created_at=e.created_at,
            source=e.source,
            is_readonly=e.is_readonly,
        )
        for e in entries
    ]
    return WhitelistListResponse(items=items, total=len(items))


@admin_router.post("/whitelist", response_model=WhitelistEntryCreatedResponse, status_code=201)
async def add_whitelist_entry(
    body: WhitelistAddRequest,
    system_admin_user: SystemAdminUserDep,
    whitelist_manager: WhitelistManager = Depends(get_whitelist_manager),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> WhitelistEntryCreatedResponse:
    """Add a new whitelist entry.

    Validates the input format (email or domain pattern), checks for
    duplicates, and persists the entry. Logs an audit trail entry.

    Args:
        body: The request body containing the value to whitelist.
        system_admin_user: The authenticated admin user performing the action.
        whitelist_manager: The WhitelistManager for entry management.
        audit_service: The AuditService for audit logging.

    Returns:
        The newly created whitelist entry.

    Raises:
        HTTPException: 422 if format is invalid, 409 if duplicate.
    """
    entry = await whitelist_manager.add_entry(value=body.value, admin=system_admin_user)

    # Log the whitelist addition in the audit trail.
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.WHITELIST_ADD,
        details={
            "entry_id": str(entry.id),
            "value": entry.value,
            "entry_type": entry.entry_type.value,
        },
    )
    await session.commit()

    return WhitelistEntryCreatedResponse.model_validate(entry)


@admin_router.delete("/whitelist/{entry_id}", status_code=204)
async def remove_whitelist_entry(
    entry_id: UUID,
    system_admin_user: SystemAdminUserDep,
    whitelist_manager: WhitelistManager = Depends(get_whitelist_manager),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a whitelist entry by ID.

    Only database-sourced entries can be removed. File-based entries
    are read-only and cannot be deleted via the API.

    Args:
        entry_id: The UUID of the entry to remove.
        system_admin_user: The authenticated admin user performing the action.
        whitelist_manager: The WhitelistManager for entry management.
        audit_service: The AuditService for audit logging.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    await whitelist_manager.remove_entry(entry_id=entry_id, admin=system_admin_user)

    # Log the whitelist removal in the audit trail.
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.WHITELIST_REMOVE,
        details={
            "entry_id": str(entry_id),
        },
    )
    await session.commit()


# --- User Management Endpoints ---


@admin_router.get("/users", response_model=list[AdminUserResponse])
async def list_users(
    system_admin_user: SystemAdminUserDep,
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminUserResponse]:
    """List all users with their roles.

    Returns all users in the system with their profile information
    and current role assignment.

    Args:
        system_admin_user: The authenticated system admin (enforced by require_system_admin).
        session: The async database session.

    Returns:
        A list of all users with their roles and profile data.
    """
    statement = select(User).order_by(User.email)
    result = await session.execute(statement)
    users = result.scalars().all()
    return [AdminUserResponse.model_validate(user) for user in users]


@admin_router.post("/users", response_model=StaffAccountCreateResponse, status_code=201)
async def create_staff_account(
    body: StaffAccountCreateRequest,
    system_admin_user: SystemAdminUserDep,
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> StaffAccountCreateResponse:
    """Provision an HR or SYSTEM_ADMIN account with a temporary password.

    ADR-0009 section 3 puts the first HR account in the system admin's hands.
    First-run setup mints exactly one SYSTEM_ADMIN and every other
    account-creation route is gated by ``require_hr``, so this endpoint is the
    only thing standing between a fresh deployment and a bootstrap deadlock.

    Args:
        body: The email, display name, and staff role to provision.
        system_admin_user: The authenticated system admin performing the change.
        auth_service: The AuthService that creates the local account.
        audit_service: The AuditService for audit logging.

    Returns:
        The created account plus its one-time temporary password.

    Raises:
        HTTPException: 409 if an account already exists for that email.
    """
    try:
        user, temporary_password = await auth_service.create_staff_account(
            email=body.email,
            name=body.name,
            role=body.role,
        )
    except AccountAlreadyExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc

    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ROLE_CHANGE,
        details={
            "target_user_id": str(user.id),
            "target_user_email": user.email,
            "old_role": None,
            "new_role": user.role.value,
            "reason": "staff_account_created",
        },
    )
    await session.commit()

    return StaffAccountCreateResponse(
        user=AdminUserResponse.model_validate(user),
        temporary_password=temporary_password,
    )


@admin_router.patch("/users/{user_id}/role", response_model=AdminUserResponse)
async def change_user_role(
    user_id: UUID,
    body: RoleUpdateRequest,
    system_admin_user: SystemAdminUserDep,
    role_service: RoleService = Depends(get_role_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserResponse:
    """Assign any of the three roles (system_admin, hr, user) to a user.

    Role assignment is a system-administration act (ADR-0009 section 3): the
    system admin provisions HR accounts and other system admins. The service
    refuses changes that would lock the deployment out -- self-changes, the
    configured super admin, and removing the last system admin.

    Args:
        user_id: The UUID of the target user.
        body: The request body containing the new role.
        system_admin_user: The authenticated system admin performing the change.
        role_service: The RoleService for role management.
        audit_service: The AuditService for audit logging.

    Returns:
        The updated user with their new role.

    Raises:
        HTTPException: 404 if the user does not exist; 400 if the change is
            refused (self-change, super admin, or last system admin).
    """
    try:
        updated_user, previous_role = await role_service.change_role(
            user_id, body.role, system_admin_user
        )
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc
    except LastAdminError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc
    except SuperAdminProtectedError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc
    except SelfDemotionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc

    # Log the role change in the audit trail.
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ROLE_CHANGE,
        details={
            "target_user_id": str(updated_user.id),
            "target_user_email": updated_user.email,
            "old_role": previous_role.value,
            "new_role": updated_user.role.value,
        },
    )
    await session.commit()

    return AdminUserResponse.model_validate(updated_user)


# --- Audit Log Endpoints ---


@admin_router.get("/audit-logs", response_model=PaginatedAuditLogsResponse)
async def get_audit_logs(
    system_admin_user: SystemAdminUserDep,
    audit_service: AuditService = Depends(get_audit_service),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    action_type: str | None = Query(default=None, description="Filter by action type"),
    start_date: datetime | None = Query(
        default=None, description="Filter entries on or after this date"
    ),
    end_date: datetime | None = Query(
        default=None, description="Filter entries on or before this date"
    ),
) -> PaginatedAuditLogsResponse:
    """Retrieve paginated audit logs with optional filters.

    Returns audit log entries ordered by most recent first, with
    support for filtering by action type and date range.

    Args:
        system_admin_user: The authenticated system admin (enforced by require_system_admin).
        audit_service: The AuditService for querying logs.
        page: The page number to retrieve (1-indexed).
        page_size: The number of entries per page (1-100).
        action_type: Optional filter by action type string value.
        start_date: Optional filter for entries on or after this date.
        end_date: Optional filter for entries on or before this date.

    Returns:
        Paginated audit log entries with metadata.
    """
    paginated = await audit_service.get_logs(
        page=page,
        page_size=page_size,
        action_type=action_type,
        start_date=start_date,
        end_date=end_date,
    )

    return PaginatedAuditLogsResponse(
        items=[AuditLogResponse.model_validate(log) for log in paginated.items],
        total=paginated.total,
        page=paginated.page,
        page_size=page_size,
    )


# --- OAuth Config Endpoints ---


@admin_router.get("/oauth/config", response_model=OAuthConfigResponse)
async def get_oauth_config(
    system_admin_user: SystemAdminUserDep,
    oauth_manager: OAuthConfigManager = Depends(get_oauth_config_manager),
) -> OAuthConfigResponse:
    """Get the current OAuth configuration with masked secret.

    Returns the active OAuth configuration from the database if one exists,
    otherwise returns the environment variable configuration. The client_secret
    is always masked, showing only the last 4 characters.

    Args:
        system_admin_user: The authenticated system admin (enforced by require_system_admin).
        oauth_manager: The OAuthConfigManager for retrieving configuration.

    Returns:
        The current OAuth configuration with masked secret.
    """
    config = await oauth_manager.get_active_config()
    return OAuthConfigResponse(
        client_id=config.client_id,
        client_secret_masked=config.client_secret_masked,
        redirect_uri=config.redirect_uri,
        updated_at=config.updated_at,
        source=config.source,
    )


@admin_router.post("/oauth/config", response_model=OAuthConfigResponse)
async def update_oauth_config(
    body: OAuthConfigUpdateRequest,
    system_admin_user: SystemAdminUserDep,
    oauth_manager: OAuthConfigManager = Depends(get_oauth_config_manager),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> OAuthConfigResponse:
    """Update OAuth credentials with validation.

    Validates the submitted credentials (non-empty client_id, valid redirect_uri,
    and Google discovery endpoint check) before encrypting and persisting them.
    Previous credentials are retained until new ones are validated. An audit log
    entry is created recording the update.

    Args:
        body: The request body containing client_id, client_secret, and redirect_uri.
        system_admin_user: The authenticated admin user performing the update.
        oauth_manager: The OAuthConfigManager for credential management.
        audit_service: The AuditService for audit logging.

    Returns:
        The updated OAuth configuration with masked secret.

    Raises:
        HTTPException: 400 if credential validation fails (invalid format or
            Google discovery check failure).
    """
    try:
        config = await oauth_manager.update_config(
            client_id=body.client_id,
            client_secret=body.client_secret,
            redirect_uri=body.redirect_uri,
            admin=system_admin_user,
        )
    except OAuthConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "OAUTH_VALIDATION_FAILED", "message": exc.message},
        ) from exc

    # Log the OAuth config update in the audit trail.
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.OAUTH_UPDATE,
        details={
            "client_id": body.client_id,
            "redirect_uri": body.redirect_uri,
            # Never log the client_secret value
        },
    )
    await session.commit()

    return OAuthConfigResponse(
        client_id=config.client_id,
        client_secret_masked=config.client_secret_masked,
        redirect_uri=config.redirect_uri,
        updated_at=config.updated_at,
        source=config.source,
    )


# --- Organization Domain Endpoints ---


async def get_org_settings_repo(
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationSettingsRepository:
    """Provide an OrganizationSettingsRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        An OrganizationSettingsRepository bound to the current session.
    """
    from src.modules.recruitment.infrastructure.org_settings_repository import (
        OrganizationSettingsRepository,
    )

    return OrganizationSettingsRepository(session)


@admin_router.get("/organization/domains", response_model=DomainListResponse)
async def list_domains(
    system_admin_user: SystemAdminUserDep,
    org_repo: OrganizationSettingsRepository = Depends(get_org_settings_repo),
) -> DomainListResponse:
    """List the Organization's allowed login domains.

    Returns the current list of email domains that are permitted for
    employee login.  An empty list means no domain restriction.

    Args:
        system_admin_user: The authenticated admin user.
        org_repo: Repository for Organization settings.

    Returns:
        The list of allowed domain strings.
    """
    domains = await org_repo.get_allowed_domains()
    return DomainListResponse(allowed_domains=domains)


@admin_router.post("/organization/domains", response_model=DomainListResponse, status_code=200)
async def add_domains(
    body: DomainAddRequest,
    system_admin_user: SystemAdminUserDep,
    org_repo: OrganizationSettingsRepository = Depends(get_org_settings_repo),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> DomainListResponse:
    """Add one or more domains to the allowed list.

    Domains are normalized to lowercase.  Duplicates are rejected.

    Args:
        body: Request containing the domains to add.
        system_admin_user: The authenticated admin user.
        org_repo: Repository for Organization settings.
        audit_service: Service for audit logging.

    Returns:
        The updated full list of allowed domains.

    Raises:
        HTTPException: 400 if domains are invalid or duplicates.
    """
    try:
        updated = await org_repo.add_domains(body.domains)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "DOMAIN_ERROR", "message": str(exc)})

    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_DOMAIN_UPDATE,
        details={"action": "add", "domains": body.domains},
    )
    await session.commit()

    return DomainListResponse(allowed_domains=updated)


@admin_router.put("/organization/domains", response_model=DomainListResponse)
async def replace_domains(
    body: DomainReplaceRequest,
    system_admin_user: SystemAdminUserDep,
    org_repo: OrganizationSettingsRepository = Depends(get_org_settings_repo),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> DomainListResponse:
    """Replace the entire allowed domains list.

    Args:
        body: Request containing the new complete list of domains.
        system_admin_user: The authenticated admin user.
        org_repo: Repository for Organization settings.
        audit_service: Service for audit logging.

    Returns:
        The new list of allowed domains.

    Raises:
        HTTPException: 400 if any domain is invalid.
    """
    try:
        updated = await org_repo.set_allowed_domains(body.domains)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "DOMAIN_ERROR", "message": str(exc)})

    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_DOMAIN_UPDATE,
        details={"action": "replace", "domains": body.domains},
    )
    await session.commit()

    return DomainListResponse(allowed_domains=updated)


@admin_router.delete("/organization/domains/{domain}", response_model=DomainRemoveResponse)
async def remove_domain(
    domain: str,
    system_admin_user: SystemAdminUserDep,
    org_repo: OrganizationSettingsRepository = Depends(get_org_settings_repo),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> DomainRemoveResponse:
    """Remove a single domain from the allowed list.

    Args:
        domain: The domain string to remove.
        system_admin_user: The authenticated admin user.
        org_repo: Repository for Organization settings.
        audit_service: Service for audit logging.

    Returns:
        The removed domain and the updated list.

    Raises:
        HTTPException: 400 if the domain is not in the list.
    """
    try:
        updated = await org_repo.remove_domain(domain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "DOMAIN_ERROR", "message": str(exc)})

    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ORG_DOMAIN_UPDATE,
        details={"action": "remove", "domain": domain},
    )
    await session.commit()

    return DomainRemoveResponse(removed=domain, allowed_domains=updated)


# ---------------------------------------------------------------------------
# Assistant Tool Config Endpoints
# ---------------------------------------------------------------------------


async def get_tool_config_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ToolConfigRepository:
    """Provide a ToolConfigRepository for the current session."""
    return ToolConfigRepository(session)


@admin_router.get(
    "/assistant-tools",
    response_model=AssistantToolConfigListResponse,
)
async def list_assistant_tools(
    system_admin_user: SystemAdminUserDep,
    tool_config_repo: ToolConfigRepository = Depends(get_tool_config_repository),
) -> AssistantToolConfigListResponse:
    """List all assistant tools with their enabled status.

    Returns tools from TOOL_DEFINITIONS merged with DB config.
    Tools not yet in DB default to enabled=True.
    """
    from src.modules.assistant.domain.tools import TOOL_DEFINITIONS

    db_configs = {c.tool_name: c for c in await tool_config_repo.get_all()}

    tools = []
    for t in TOOL_DEFINITIONS:
        db_config = db_configs.get(t.name)
        tools.append(
            AssistantToolConfigResponse(
                tool_name=t.name,
                display_name=t.display_name,
                description=t.description,
                kind=t.kind.value,
                enabled=db_config.enabled if db_config else True,
                updated_at=db_config.updated_at if db_config else None,
            )
        )

    return AssistantToolConfigListResponse(tools=tools)


@admin_router.put(
    "/assistant-tools",
    response_model=AssistantToolConfigListResponse,
)
async def update_assistant_tools(
    body: AssistantToolConfigUpdateRequest,
    system_admin_user: SystemAdminUserDep,
    tool_config_repo: ToolConfigRepository = Depends(get_tool_config_repository),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> AssistantToolConfigListResponse:
    """Batch update assistant tool configs (Apply button).

    Upserts all tool configs from the request, then returns the updated list.
    Creates an audit log entry for the change.
    """
    from src.modules.assistant.domain.tools import TOOL_DEFINITIONS

    # Validate: all tool_names in request must exist in TOOL_DEFINITIONS
    valid_names = {t.name for t in TOOL_DEFINITIONS}
    invalid = set(body.tools.keys()) - valid_names
    if invalid:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_TOOL_NAMES",
                "message": f"Unknown tools: {sorted(invalid)}",
            },
        )

    # Upsert configs
    await tool_config_repo.upsert_many(body.tools)

    # Audit log
    await audit_service.log_action(
        admin=system_admin_user,
        action_type=AuditActionType.ASSISTANT_TOOL_CONFIG,
        details={"tools": body.tools},
    )
    await session.commit()

    # Return updated list
    return await list_assistant_tools(system_admin_user, tool_config_repo)
