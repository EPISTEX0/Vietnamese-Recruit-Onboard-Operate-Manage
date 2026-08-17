"""Dependency injection container for the AI Assistant module.

Wires together the AssistantService with its dependencies:
- AssistantLLMClient (own client, separate from recruitment's LLMAdapter)
- ToolRegistry (reads from recruitment + onboarding services)
- AssistantSettings (ASSISTANT_LLM_* env vars)

Dependency direction is one-way:
assistant/ depends on recruitment/ and onboarding/ services,
but no module depends on assistant/.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.assistant.application.assistant_service import AssistantService
from src.modules.assistant.application.context_builder import ContextBuilder
from src.modules.assistant.application.tool_registry import ToolRegistry
from src.modules.assistant.infrastructure.config import AssistantSettings
from src.modules.assistant.infrastructure.llm_client import AssistantLLMClient
from src.modules.employee.application.department_service import DepartmentService
from src.modules.employee.container import get_department_service
from src.modules.identity.application.organization_ai_config_service import (
    OrganizationAIConfigService,
    OrganizationAIConfigValidationError,
)
from src.modules.identity.container import get_crypto_utils, get_db_session
from src.modules.identity.infrastructure.organization_ai_config_repository import (
    OrganizationAIConfigRepository,
)
from src.modules.knowledge_base.application.retrieval_service import RetrievalService
from src.modules.knowledge_base.container import get_kb_settings
from src.modules.knowledge_base.infrastructure.repository import (
    KnowledgeBaseRepository,
)
from src.modules.onboarding.application.onboarding_service import OnboardingService
from src.modules.onboarding.container import get_onboarding_service
from src.modules.recruitment.application.candidate_lifecycle_service import (
    CandidateLifecycleService,
)
from src.modules.recruitment.container import get_candidate_lifecycle_service
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)
from src.modules.recruitment.infrastructure.repositories import JobOpeningRepository

# ---------------------------------------------------------------------------
# Settings & LLM Client (singletons)
# ---------------------------------------------------------------------------


@lru_cache
def get_assistant_settings() -> AssistantSettings:
    """Load and cache AssistantSettings from ASSISTANT_LLM_* env vars."""
    return AssistantSettings()


@lru_cache
def get_assistant_llm_client() -> AssistantLLMClient:
    """Create and cache the assistant's own LLM client."""
    settings = get_assistant_settings()
    return AssistantLLMClient(settings)


# ---------------------------------------------------------------------------
# FastAPI Depends providers
# ---------------------------------------------------------------------------


async def get_tool_registry(
    session: AsyncSession = Depends(get_db_session),
    candidate_service: CandidateLifecycleService = Depends(get_candidate_lifecycle_service),
    onboarding_service: OnboardingService = Depends(get_onboarding_service),
    department_service: DepartmentService = Depends(get_department_service),
) -> ToolRegistry:
    """Provide a ToolRegistry wired to recruitment + onboarding + employee services.

    Read-only dependency on other modules' services.
    """
    return ToolRegistry(
        candidate_service=candidate_service,
        onboarding_service=onboarding_service,
        session=session,
        department_service=department_service,
    )


async def get_configured_assistant_settings(
    session: AsyncSession = Depends(get_db_session),
) -> AssistantSettings:
    """Resolve Organization AI settings, falling back only before setup."""
    service = OrganizationAIConfigService(
        repository=OrganizationAIConfigRepository(session),
        crypto=get_crypto_utils(),
    )
    try:
        runtime = await service.get_runtime_config(capability="assistant")
    except OrganizationAIConfigValidationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if runtime is None:
        return get_assistant_settings()

    return AssistantSettings(
        base_url=runtime.base_url,
        api_key=runtime.api_key,
        model=runtime.model,
    )


async def get_configured_assistant_llm_client(
    settings: AssistantSettings = Depends(get_configured_assistant_settings),
) -> AssistantLLMClient:
    """Create an LLM client from the active Organization provider config."""
    return AssistantLLMClient(settings)


async def get_context_builder(
    session: AsyncSession = Depends(get_db_session),
    candidate_service: CandidateLifecycleService = Depends(get_candidate_lifecycle_service),
    onboarding_service: OnboardingService = Depends(get_onboarding_service),
) -> ContextBuilder:
    """Provide a ContextBuilder wired to recruitment + onboarding + KB retrieval."""
    kb_settings = get_kb_settings()
    kb_repo = KnowledgeBaseRepository(session)
    retrieval_service = RetrievalService(repo=kb_repo, settings=kb_settings)
    return ContextBuilder(
        candidate_service=candidate_service,
        onboarding_service=onboarding_service,
        job_opening_repo=JobOpeningRepository(session),
        org_settings_repo=OrganizationSettingsRepository(session),
        retrieval_service=retrieval_service,
    )


async def get_assistant_service(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    llm_client: AssistantLLMClient = Depends(get_configured_assistant_llm_client),
    settings: AssistantSettings = Depends(get_configured_assistant_settings),
    context_builder: ContextBuilder = Depends(get_context_builder),
) -> AssistantService:
    """Provide an AssistantService using the active Organization provider."""
    return AssistantService(
        llm_client=llm_client,
        tool_registry=tool_registry,
        settings=settings,
        context_builder=context_builder,
    )
