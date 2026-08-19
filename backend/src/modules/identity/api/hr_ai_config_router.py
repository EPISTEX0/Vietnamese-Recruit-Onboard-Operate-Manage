"""FastAPI router for HR ownership of Organization AI configuration.

Defines /api/hr/organization/ai-config/* -- the data-policy consent, capability
consent, capability toggles, and policy-preset endpoints. These moved here
wholesale from ``admin_router`` (#420): the decision to send recruitment data
to an external AI provider is HR's to make, not System Admin's, and ADR-0009
bars System Admin from HR data entirely. Every endpoint here requires the HR
role.

The root ``GET`` and ``/provider-status`` are new, not moved: the nine moved
endpoints are all writes (or the data-policy text), and #420 named the pair
of them separately from the "nine routes" count. Without the root read the
frontend would have nothing to render state from before the first mutation --
the same role System Admin's own ``GET /organization/ai-config`` already
plays for that side.

System Admin keeps everything about *how* the provider is wired: provider
name, base_url, model, API key, credential source, and classification
rollout. Nothing in this module accepts or returns any of that -- see
``hr_ai_config_schemas.HRAIConfigurationResponse`` and
``HRAIProviderStatusResponse``, both deliberately narrower than System
Admin's ``OrganizationAIConfigurationResponse``.

Follows the same commit convention as ``admin_router`` (see its module
docstring): every writing endpoint here ends with an explicit
``await session.commit()`` after the audit call, rather than relying on
``get_db_session``'s post-response teardown commit.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.api.admin_router import (
    HRUserDep,
    get_organization_ai_config_service,
)
from src.modules.identity.api.admin_schemas import DataPolicyResponse
from src.modules.identity.api.hr_ai_config_schemas import (
    HRAIConfigurationResponse,
    HRAIProviderStatusResponse,
)
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.application.organization_ai_config_service import (
    AIConfigurationView,
    AIPolicyPreset,
    OrganizationAIConfigService,
    OrganizationAIConfigTestError,
    OrganizationAIConfigValidationError,
)
from src.modules.identity.container import get_audit_service, get_db_session
from src.modules.identity.domain.entities import AuditActionType

hr_ai_config_router = APIRouter(prefix="/api/hr/organization/ai-config", tags=["hr", "ai-config"])


def _hr_ai_view_response(view: AIConfigurationView) -> HRAIConfigurationResponse:
    return HRAIConfigurationResponse(
        provider_configured=view.configured,
        updated_at=view.updated_at,
        data_policy_accepted=view.data_policy_accepted,
        data_policy_accepted_at=view.data_policy_accepted_at,
        data_policy_version=view.data_policy_version,
        automation_enabled=view.automation_enabled,
        automation_state=view.automation_state,
        assistant_enabled=view.assistant_enabled,
        assistant_state=view.assistant_state,
        ai_automation_consent=view.ai_automation_consent,
        ai_assistant_consent=view.ai_assistant_consent,
        ai_policy_preset=view.ai_policy_preset,
        ai_policy_preset_version=view.ai_policy_preset_version,
    )


# --- Current configuration (read-only, credential-free) ---


@hr_ai_config_router.get("", response_model=HRAIConfigurationResponse)
async def get_hr_ai_config(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
) -> HRAIConfigurationResponse:
    """Current data-policy, consent, toggle, and preset state.

    Mirrors System Admin's own ``GET /organization/ai-config`` (same
    ``service.get_view()`` source), narrowed to the credential-free response
    shape -- the read HR needs to render this page on load, without which
    every write endpoint below would have nothing to show state from.
    """
    return _hr_ai_view_response(await service.get_view())


# --- Provider status (read-only, credential-free) ---


@hr_ai_config_router.get("/provider-status", response_model=HRAIProviderStatusResponse)
async def get_provider_status(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
) -> HRAIProviderStatusResponse:
    """Whether System Admin has connected a usable AI provider.

    The only signal HR gets about the provider itself: connected or not.
    Never the provider name, base_url, model, or any part of the API key --
    contact System Admin for anything beyond this boolean.
    """
    return HRAIProviderStatusResponse(connected=await service.is_provider_connected())


# --- Data policy ---


@hr_ai_config_router.get("/data-policy", response_model=DataPolicyResponse)
async def get_data_policy(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
) -> DataPolicyResponse:
    """Return the Organization AI data policy describing data sent to the provider."""
    policy = service.get_data_policy()
    return DataPolicyResponse(
        version=str(policy["version"]),
        items=policy["items"],  # type: ignore[arg-type]
    )


@hr_ai_config_router.post("/accept-data-policy", response_model=HRAIConfigurationResponse)
async def accept_data_policy(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> HRAIConfigurationResponse:
    """Accept the data policy before enabling AI capabilities for the first time."""
    try:
        result = await service.accept_data_policy(hr_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=hr_user,
        action_type=AuditActionType.ORG_AI_CONSENT,
        details=result.audit_details,
    )
    await session.commit()
    return _hr_ai_view_response(result.view)


# --- Independent capability consent ---


@hr_ai_config_router.post("/automation/consent", response_model=HRAIConfigurationResponse)
async def accept_automation_consent(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> HRAIConfigurationResponse:
    result = await service.accept_automation_consent(hr_user)
    await audit_service.log_action(
        admin=hr_user,
        action_type=AuditActionType.ORG_AI_CONSENT,
        details=result.audit_details,
    )
    await session.commit()
    return _hr_ai_view_response(result.view)


@hr_ai_config_router.post("/assistant/consent", response_model=HRAIConfigurationResponse)
async def accept_assistant_consent(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> HRAIConfigurationResponse:
    result = await service.accept_assistant_consent(hr_user)
    await audit_service.log_action(
        admin=hr_user,
        action_type=AuditActionType.ORG_AI_CONSENT,
        details=result.audit_details,
    )
    await session.commit()
    return _hr_ai_view_response(result.view)


# --- Capability toggles: AI Automation ---


@hr_ai_config_router.post("/automation/enable", response_model=HRAIConfigurationResponse)
async def enable_ai_automation(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> HRAIConfigurationResponse:
    """Enable AI Automation after validating preconditions."""
    try:
        result = await service.enable_automation(hr_user)
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
        admin=hr_user,
        action_type=AuditActionType.ORG_AI_TOGGLE_AUTOMATION,
        details=result.audit_details,
    )
    await session.commit()
    return _hr_ai_view_response(result.view)


@hr_ai_config_router.post("/automation/disable", response_model=HRAIConfigurationResponse)
async def disable_ai_automation(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> HRAIConfigurationResponse:
    """Disable AI Automation."""
    try:
        result = await service.disable_automation(hr_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=hr_user,
        action_type=AuditActionType.ORG_AI_TOGGLE_AUTOMATION,
        details=result.audit_details,
    )
    await session.commit()
    return _hr_ai_view_response(result.view)


# --- Capability toggles: AI Assistant ---


@hr_ai_config_router.post("/assistant/enable", response_model=HRAIConfigurationResponse)
async def enable_ai_assistant(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> HRAIConfigurationResponse:
    """Enable AI Assistant after validating preconditions."""
    try:
        result = await service.enable_assistant(hr_user)
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
        admin=hr_user,
        action_type=AuditActionType.ORG_AI_TOGGLE_ASSISTANT,
        details=result.audit_details,
    )
    await session.commit()
    return _hr_ai_view_response(result.view)


@hr_ai_config_router.post("/assistant/disable", response_model=HRAIConfigurationResponse)
async def disable_ai_assistant(
    hr_user: HRUserDep,
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> HRAIConfigurationResponse:
    """Disable AI Assistant."""
    try:
        result = await service.disable_assistant(hr_user)
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONFIG_INVALID", "message": str(exc)},
        ) from exc
    await audit_service.log_action(
        admin=hr_user,
        action_type=AuditActionType.ORG_AI_TOGGLE_ASSISTANT,
        details=result.audit_details,
    )
    await session.commit()
    return _hr_ai_view_response(result.view)


# --- Versioned AI policy preset ---


@hr_ai_config_router.put("/policy-preset", response_model=HRAIConfigurationResponse)
async def set_ai_policy_preset(
    hr_user: HRUserDep,
    preset: AIPolicyPreset = Body(...),
    service: OrganizationAIConfigService = Depends(get_organization_ai_config_service),
    audit_service: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_db_session),
) -> HRAIConfigurationResponse:
    result = await service.set_policy_preset(preset, hr_user)
    await audit_service.log_action(
        admin=hr_user,
        action_type=AuditActionType.ORG_AI_CONFIG_UPDATE,
        details=result.audit_details,
    )
    await session.commit()
    return _hr_ai_view_response(result.view)
