"""Dependency injection container for the Gmail Integration module.

Provides FastAPI dependency functions that wire together all services,
repositories, and infrastructure components using the shared async
database session and Redis client from the identity module.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import httpx
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from arq.connections import ArqRedis

from src.modules.gmail.application.attachment_service import AttachmentService
from src.modules.gmail.application.classification_service import ClassificationService
from src.modules.gmail.application.connection_service import ConnectionService
from src.modules.gmail.application.email_sync_service import EmailSyncService
from src.modules.gmail.application.import_service import HistoricalImportService
from src.modules.gmail.application.outbound_email_service import OutboundEmailService
from src.modules.gmail.application.send_service import SendService
from src.modules.gmail.infrastructure.ai_classifier import AIClassifier
from src.modules.gmail.infrastructure.audit_logger import AuditLogger
from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.gmail.infrastructure.email_repository import EmailRepository
from src.modules.gmail.infrastructure.gmail_adapter import GmailAdapter
from src.modules.gmail.infrastructure.outbound_email_repository import (
    OutboundEmailRepository,
)
from src.modules.gmail.infrastructure.quota_tracker import QuotaTracker
from src.modules.gmail.infrastructure.sync_cursor_repository import SyncCursorRepository
from src.modules.identity.container import (
    get_crypto_utils,
    get_db_session,
    get_redis_client,
)
from src.modules.identity.container import (
    get_settings as get_auth_settings,
)
from src.modules.identity.infrastructure.connection_state_repository import (
    OrganizationGoogleConnectionRepository,
)
from src.modules.identity.infrastructure.oauth_grant_repository import OAuthGrantRepository
from src.modules.recruitment.infrastructure.repositories import (
    CandidateRepository,
    RecruitmentInboxItemRepository,
)

# ---------------------------------------------------------------------------
# Singleton infrastructure components
# ---------------------------------------------------------------------------


@lru_cache
def get_gmail_settings() -> GmailSettings:
    """Load and cache GmailSettings from environment variables.

    Returns:
        The GmailSettings singleton loaded from GMAIL_* env vars.
    """
    return GmailSettings()


@lru_cache
def get_quota_tracker() -> QuotaTracker:
    """Create and cache the QuotaTracker singleton.

    Returns:
        A QuotaTracker configured with the Redis client and settings.
    """
    redis_client = get_redis_client()
    return QuotaTracker(redis_client, get_gmail_settings())


@lru_cache
def get_http_client() -> httpx.AsyncClient:
    """Create and cache the shared httpx AsyncClient.

    Returns:
        An httpx.AsyncClient for Gmail API calls.
    """
    return httpx.AsyncClient()


async def get_arq_pool() -> ArqRedis:
    """Create and cache the shared ARQ Redis pool.

    Uses the same Redis URL as the auth settings. The pool is created
    lazily and cached, so multiple call sites share one connection.

    Returns:
        An ArqRedis pool for enqueuing ARQ jobs.
    """
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(
        RedisSettings.from_dsn(get_auth_settings().redis_url), default_queue_name="gmail-worker"
    )
    return pool


# ---------------------------------------------------------------------------
# Repository dependency functions
# ---------------------------------------------------------------------------


async def get_oauth_grant_repository(
    session: AsyncSession = Depends(get_db_session),
) -> OAuthGrantRepository:
    """Provide an OAuthGrantRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        An OAuthGrantRepository bound to the current session.
    """
    return OAuthGrantRepository(session)


async def get_email_repository(
    session: AsyncSession = Depends(get_db_session),
) -> EmailRepository:
    """Provide an EmailRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        An EmailRepository bound to the current session.
    """
    return EmailRepository(session)


async def get_sync_cursor_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SyncCursorRepository:
    """Provide a SyncCursorRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        A SyncCursorRepository bound to the current session.
    """
    return SyncCursorRepository(session)


async def get_outbound_email_repository(
    session: AsyncSession = Depends(get_db_session),
) -> OutboundEmailRepository:
    """Provide an OutboundEmailRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        An OutboundEmailRepository bound to the current session.
    """
    return OutboundEmailRepository(session)


async def get_audit_logger(
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogger:
    """Provide an AuditLogger instance.

    Args:
        session: The async database session from DI.

    Returns:
        An AuditLogger bound to the current session.
    """
    return AuditLogger(session, get_gmail_settings())


# ---------------------------------------------------------------------------
# Service dependency functions
# ---------------------------------------------------------------------------


async def get_gmail_adapter() -> GmailAdapter:
    """Provide a GmailAdapter instance.

    Note: The GmailAdapter requires a user_id for quota tracking.
    This provides a base adapter; endpoints should pass user_id context.

    Returns:
        A GmailAdapter configured with settings, quota tracker, and HTTP client.
    """
    from uuid import UUID

    # The adapter needs a user_id for quota tracking; we use a placeholder
    # that will be overridden per-request in the router endpoints.
    # For DI purposes, we create with a nil UUID; actual usage passes user context.
    return GmailAdapter(
        settings=get_gmail_settings(),
        quota_tracker=get_quota_tracker(),
        http_client=get_http_client(),
        user_id=UUID("00000000-0000-0000-0000-000000000000"),
    )


async def get_connection_service(
    oauth_grant_repo: OAuthGrantRepository = Depends(get_oauth_grant_repository),
) -> ConnectionService:
    """Provide the legacy service for internal attachment compatibility."""
    auth_settings = get_auth_settings()
    return ConnectionService(
        settings=get_gmail_settings(),
        auth_settings_client_id=auth_settings.google_client_id,
        auth_settings_client_secret=auth_settings.google_client_secret,
        gmail_redirect_uri=auth_settings.google_redirect_uri,
        oauth_grant_repo=oauth_grant_repo,
        gmail_adapter=await get_gmail_adapter(),
        crypto=get_crypto_utils(),
    )


async def get_organization_google_connection_repository(
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationGoogleConnectionRepository:
    """Provide an OrganizationGoogleConnectionRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        An OrganizationGoogleConnectionRepository bound to the current session.
    """
    return OrganizationGoogleConnectionRepository(session)


async def get_email_sync_service(
    email_repo: EmailRepository = Depends(get_email_repository),
    sync_cursor_repo: SyncCursorRepository = Depends(get_sync_cursor_repository),
    connection_repo: OrganizationGoogleConnectionRepository = Depends(
        get_organization_google_connection_repository
    ),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> EmailSyncService:
    """Provide an EmailSyncService instance.

    Args:
        email_repo: The email repository from DI.
        sync_cursor_repo: The sync cursor repository from DI.
        connection_repo: The organization Google connection repository from DI.
        audit_logger: The audit logger from DI.

    Returns:
        An EmailSyncService configured with all dependencies.
    """
    auth_settings = get_auth_settings()
    gmail_adapter = await get_gmail_adapter()

    from src.modules.identity.infrastructure.organization_ai_config_repository import (
        OrganizationAIConfigRepository,
    )

    org_config = await OrganizationAIConfigRepository(email_repo.session).get()
    ai_classifier = _build_ai_classifier(get_gmail_settings(), org_config)

    return EmailSyncService(
        gmail_adapter=gmail_adapter,
        email_repo=email_repo,
        sync_cursor_repo=sync_cursor_repo,
        connection_repo=connection_repo,
        crypto=get_crypto_utils(),
        audit_logger=audit_logger,
        settings=get_gmail_settings(),
        redis_client=get_redis_client(),
        client_id=auth_settings.google_client_id,
        client_secret=auth_settings.google_client_secret,
        ai_classifier=ai_classifier,
    )


async def build_send_service(session: AsyncSession) -> SendService:
    """Build a SendService bound to ``session``, with no DI resolver required.

    Callers outside a request -- other containers, workers, scripts -- must use
    this rather than ``get_send_service``. A FastAPI provider's ``Depends(...)``
    defaults are resolved by FastAPI and by nothing else, so calling one bare
    binds the sentinels themselves as the repositories and yields a SendService
    that raises ``AttributeError`` on first use (#327).

    Args:
        session: An async database session; every repository is bound to it, so
            the send path shares the caller's transaction.

    Returns:
        A fully configured SendService.
    """
    auth_settings = get_auth_settings()
    gmail_adapter = await get_gmail_adapter()

    return SendService(
        gmail_adapter=gmail_adapter,
        email_repo=EmailRepository(session),
        connection_repo=OrganizationGoogleConnectionRepository(session),
        crypto=get_crypto_utils(),
        audit_logger=AuditLogger(session, get_gmail_settings()),
        settings=get_gmail_settings(),
        client_id=auth_settings.google_client_id,
        client_secret=auth_settings.google_client_secret,
    )


async def get_send_service(
    session: AsyncSession = Depends(get_db_session),
) -> SendService:
    """FastAPI dependency: provide a SendService.

    Args:
        session: The async database session from DI.

    Returns:
        A fully configured SendService.
    """
    return await build_send_service(session)


async def get_attachment_service(
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> AttachmentService:
    """Provide an AttachmentService instance.

    Args:
        audit_logger: The audit logger from DI.

    Returns:
        An AttachmentService configured with all dependencies.
    """
    gmail_adapter = await get_gmail_adapter()

    return AttachmentService(
        gmail_adapter=gmail_adapter,
        settings=get_gmail_settings(),
        audit_logger=audit_logger,
    )


def _build_ai_classifier(
    settings: GmailSettings,
    org_config: object | None,
) -> AIClassifier:
    """Build an AIClassifier from Organization AI Configuration (DB only).

    There is no env-var fallback — AI classification requires the
    Organization AI Configuration to be set up in Admin → AI Settings.
    """
    if org_config is None or not org_config.api_key_enc:
        raise RuntimeError(
            "AI classification is not configured. "
            "Set up your AI provider in Admin → AI Settings first."
        )
    crypto = get_crypto_utils()
    api_key = crypto.decrypt(org_config.api_key_enc)
    return AIClassifier(
        settings,
        base_url=org_config.base_url,
        api_key=api_key,
        model=org_config.model,
    )


async def build_classification_service(session: AsyncSession) -> ClassificationService:
    """Build classification with idempotent Job Application ingestion and Recruitment Inbox.

    Takes the session explicitly and derives every collaborator from it, so it is
    callable anywhere -- a request handler, a worker, a script. Compare
    ``build_send_service`` / ``build_outbound_email_service``.

    No FastAPI provider wraps this, deliberately. A provider's ``Depends``
    defaults are resolved by FastAPI and by nothing else, so calling one directly
    binds the sentinels themselves; that is how the reclassify handler ended up
    passing a ``Depends`` object as its session (#332), and how password reset
    broke before it (#327). Nothing resolves this service through ``Depends``
    today, so a thin provider would add no reach and re-offer the shape both
    incidents came from. A future route needs one line to add one back.

    Args:
        session: An async database session; every repository is bound to it, so
            classification shares the caller's transaction.

    Returns:
        A fully configured ClassificationService.
    """
    from src.modules.gmail.application.classification_rollout import (
        BusinessPolicy,
        ClassificationRollout,
        RolloutConfig,
        RolloutMode,
    )
    from src.modules.gmail.application.rules_classifier import RulesClassifier
    from src.modules.gmail.infrastructure.classification_rollout_repository import (
        ClassificationRolloutRepository,
    )
    from src.modules.identity.infrastructure.organization_ai_config_repository import (
        OrganizationAIConfigRepository,
    )
    from src.modules.recruitment.application.inbox_service import InboxService
    from src.modules.recruitment.application.job_application_service import (
        build_job_application_ingestion,
    )

    settings = get_gmail_settings()
    job_app_service = build_job_application_ingestion(session)
    inbox_repo = RecruitmentInboxItemRepository(session)
    inbox_service = InboxService(session=session, inbox_repo=inbox_repo)
    rollout = None
    candidate_classifier = None
    organization_ai_config = await OrganizationAIConfigRepository(session).get()

    # Resolve the AI classifier, preferring DB-stored credentials from the
    # admin panel (Organization AI Config) over GMAIL_* env vars.
    ai_classifier = _build_ai_classifier(settings, organization_ai_config)

    if (
        organization_ai_config is not None
        and organization_ai_config.candidate_classifier_version
        and organization_ai_config.rollout_mode != RolloutMode.STABLE.value
    ):
        candidate_policy = (
            organization_ai_config.candidate_classification_policy
            or organization_ai_config.classification_policy
        )
        policy_version = (
            organization_ai_config.candidate_classification_policy_version
            or organization_ai_config.classification_policy_version
        )
        telemetry_repo = ClassificationRolloutRepository(session)
        rollout = ClassificationRollout(
            RolloutConfig(
                mode=RolloutMode(organization_ai_config.rollout_mode),
                business_policy=BusinessPolicy(candidate_policy),
                policy_version=policy_version,
                stable_classifier_version=organization_ai_config.stable_classifier_version,
                candidate_classifier_version=(organization_ai_config.candidate_classifier_version),
                canary_percentage=organization_ai_config.canary_percentage,
            ),
            record=telemetry_repo.record,
        )
        candidate_classifier = _build_ai_classifier(settings, organization_ai_config)
    return ClassificationService(
        rules_classifier=RulesClassifier(),
        ai_classifier=ai_classifier,
        email_repo=EmailRepository(session),
        audit_logger=AuditLogger(session, settings),
        settings=settings,
        session=session,
        on_application_created=job_app_service.create_from_classification,
        on_uncertain_classification=inbox_service.create_from_classification,
        rollout=rollout,
        candidate_ai_classifier=candidate_classifier,
    )


async def get_historical_import_service(
    email_repo: EmailRepository = Depends(get_email_repository),
    sync_cursor_repo: SyncCursorRepository = Depends(get_sync_cursor_repository),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    session: AsyncSession = Depends(get_db_session),
) -> HistoricalImportService:
    """Provide a HistoricalImportService instance.

    Args:
        email_repo: The email repository from DI.
        sync_cursor_repo: The sync cursor repository from DI.
        audit_logger: The audit logger from DI.
        session: The async database session from DI.

    Returns:
        A HistoricalImportService configured with all dependencies.
    """
    auth_settings = get_auth_settings()
    gmail_adapter = await get_gmail_adapter()
    connection_repo = OrganizationGoogleConnectionRepository(session)

    return HistoricalImportService(
        session=session,
        gmail_adapter=gmail_adapter,
        email_repo=email_repo,
        sync_cursor_repo=sync_cursor_repo,
        connection_repo=connection_repo,
        crypto=get_crypto_utils(),
        audit_logger=audit_logger,
        settings=get_gmail_settings(),
        redis_client=get_redis_client(),
        http_client=get_http_client(),
        client_id=auth_settings.google_client_id,
        client_secret=auth_settings.google_client_secret,
    )


async def build_outbound_email_service(session: AsyncSession) -> OutboundEmailService:
    """Build an OutboundEmailService instance with all dependencies.

    Args:
        session: An async database session.

    Returns:
        A fully configured OutboundEmailService.
    """
    from src.modules.gmail.infrastructure.outbound_email_repository import (
        OutboundEmailRepository,
    )
    from src.modules.identity.application.audit_service import AuditService
    from src.modules.identity.domain.entities import AuditActionType
    from src.modules.identity.infrastructure.audit_log_repository import (
        AuditLogRepository,
    )

    auth_settings = get_auth_settings()
    gmail_adapter = await get_gmail_adapter()
    connection_repo = OrganizationGoogleConnectionRepository(session)
    outbound_repo = OutboundEmailRepository(session)
    candidate_repo = CandidateRepository(session)
    audit_log_repo = AuditLogRepository(session)
    audit_service = AuditService(repository=audit_log_repo)

    return OutboundEmailService(
        session=session,
        outbound_repo=outbound_repo,
        connection_repo=connection_repo,
        candidate_repo=candidate_repo,
        gmail_adapter=gmail_adapter,
        crypto=get_crypto_utils(),
        audit_service=audit_service,
        oauth_config_client_id=auth_settings.google_client_id,
        http_client=get_http_client(),
        audit_action_type=AuditActionType,
    )


async def get_outbound_email_service(
    session: AsyncSession = Depends(get_db_session),
) -> OutboundEmailService:
    """FastAPI dependency: provide an OutboundEmailService.

    Args:
        session: The async database session from DI.

    Returns:
        A fully configured OutboundEmailService.
    """
    return await build_outbound_email_service(session)
