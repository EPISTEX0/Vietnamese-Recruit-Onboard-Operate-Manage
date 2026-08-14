"""Dependency injection container for the Recruitment CV Pipeline module.

Provides FastAPI dependency functions that wire together all services,
repositories, and infrastructure components using the shared async
database session from the identity module.

Also registers ARQ task functions for background processing:
- process_cv_from_email: CV processing pipeline triggered by intent classification
- retention_cleanup: Scheduled cleanup of expired rejected candidates

Requirements: 1.4, 2.7, 15.4, 16.2
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from uuid import UUID

import httpx
from arq.connections import RedisSettings
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.container import (
    get_crypto_utils,
    get_current_user,
    get_db_session,
    get_settings,
)
from src.modules.identity.domain.entities import User
from src.modules.identity.infrastructure.connection_state_repository import (
    OrganizationGoogleConnectionRepository,
)
from src.modules.recruitment.application.calendar_sync_service import CalendarSyncService
from src.modules.recruitment.application.candidate_lifecycle_service import (
    CandidateLifecycleService,
)
from src.modules.recruitment.application.candidate_notification_service import (
    CandidateNotificationService,
)
from src.modules.recruitment.application.cv_processor import CVProcessorService
from src.modules.recruitment.application.intent_classifier import IntentClassifierService
from src.modules.recruitment.application.interview_scheduler_service import (
    InterviewSchedulerService,
)
from src.modules.recruitment.application.review_service import ReviewService
from src.modules.recruitment.infrastructure.calendar_adapter import CalendarAdapter
from src.modules.recruitment.infrastructure.config import RecruitmentSettings
from src.modules.recruitment.infrastructure.event_publisher import ArqDomainEventPublisher
from src.modules.recruitment.infrastructure.llm_adapter import LLMAdapter
from src.modules.recruitment.infrastructure.minio_client import RecruitmentMinIOClient
from src.modules.recruitment.infrastructure.ocr_adapter import OCRAdapter
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)
from src.modules.recruitment.infrastructure.pii_redactor import PIIRedactor
from src.modules.recruitment.infrastructure.repositories import (
    CandidateRepository,
    CVDocumentRepository,
    InterviewRepository,
    JobOpeningRepository,
)
from src.modules.recruitment.infrastructure.sync_cursor_repository import (
    CalendarSyncCursorRepository,
)

# ---------------------------------------------------------------------------
# Singleton infrastructure components
# ---------------------------------------------------------------------------


@lru_cache
def get_recruitment_settings() -> RecruitmentSettings:
    """Load and cache RecruitmentSettings from environment variables.

    Returns:
        The RecruitmentSettings singleton loaded from RECRUITMENT_* env vars.
    """
    return RecruitmentSettings()


@lru_cache
def get_minio_client() -> RecruitmentMinIOClient:
    """Create and cache the RecruitmentMinIOClient singleton.

    Returns:
        A RecruitmentMinIOClient configured with recruitment settings.
    """
    settings = get_recruitment_settings()
    return RecruitmentMinIOClient(settings)


@lru_cache
def get_llm_adapter() -> LLMAdapter:
    """Create and cache the LLMAdapter singleton.

    Returns:
        An LLMAdapter configured with recruitment LLM settings.
    """
    settings = get_recruitment_settings()
    return LLMAdapter(settings)


@lru_cache
def get_ocr_adapter() -> OCRAdapter:
    """Create and cache the OCRAdapter singleton.

    Returns:
        An OCRAdapter configured with recruitment olmOCR settings.
    """
    settings = get_recruitment_settings()
    return OCRAdapter(settings)


@lru_cache
def get_pii_redactor() -> PIIRedactor:
    """Create and cache the PIIRedactor singleton.

    Returns:
        A PIIRedactor instance for sanitizing text before LLM calls.
    """
    return PIIRedactor()


@lru_cache
def get_calendar_http_client() -> httpx.AsyncClient:
    """Create and cache the shared httpx.AsyncClient for the Calendar adapter.

    A single long-lived async client is reused across requests so connection
    pools are not torn down per request. This is safe for a long-lived app and
    mirrors how the other Google adapters obtain their HTTP client.

    Returns:
        A cached httpx.AsyncClient for Google Calendar API calls.
    """
    return httpx.AsyncClient()


@lru_cache
def get_calendar_adapter() -> CalendarAdapter:
    """Create and cache the CalendarAdapter singleton.

    Returns:
        A CalendarAdapter built with the shared httpx client and recruitment
        settings.
    """
    settings = get_recruitment_settings()
    return CalendarAdapter(settings=settings, http_client=get_calendar_http_client())


@lru_cache
def get_event_publisher() -> ArqDomainEventPublisher:
    """Create and cache the ARQ-backed domain event publisher singleton.

    The publisher enqueues ``process_candidate_accepted`` ARQ jobs for the
    ``candidate_accepted`` domain event emitted by
    ``CandidateService.accept_candidate``, bridging recruitment to the
    onboarding consumer. The underlying ARQ Redis pool is created lazily on the
    first publish, so constructing this singleton opens no connection.

    Returns:
        An ArqDomainEventPublisher configured with the shared Redis DSN.
    """
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    return ArqDomainEventPublisher(redis_settings=redis_settings)


# ---------------------------------------------------------------------------
# FastAPI dependency functions for services
# ---------------------------------------------------------------------------


async def get_candidate_lifecycle_service(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> CandidateLifecycleService:
    """Provide a CandidateLifecycleService instance with all dependencies.

    Wires lifecycle dependencies: repositories, MinIO, event publisher, and the
    interview-cancellation seam. The lifecycle service takes no Calendar, Gmail,
    or crypto dependency of its own -- rejecting or archiving a Candidate must
    call off their booked interview (R8), and that Calendar work is delegated
    whole to :class:`InterviewSchedulerService` through the one-method
    ``InterviewCanceller`` protocol.

    Args:
        session: The async database session from DI.
        current_user: The authenticated user.

    Returns:
        A fully configured CandidateLifecycleService.
    """
    candidate_repo = CandidateRepository(session)
    cv_document_repo = CVDocumentRepository(session)
    job_opening_repo = JobOpeningRepository(session)
    minio_client = get_minio_client()

    return CandidateLifecycleService(
        candidate_repo=candidate_repo,
        cv_document_repo=cv_document_repo,
        job_opening_repo=job_opening_repo,
        minio_client=minio_client,
        event_publisher=get_event_publisher(),
        session=session,
        user_id=current_user.id,
        interview_canceller=await get_interview_scheduler_service(session, current_user),
    )


async def get_interview_scheduler_service(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> InterviewSchedulerService:
    """Provide an InterviewSchedulerService instance with all dependencies.

    Wires Calendar scheduling dependencies (per ADR-0008):
    InterviewRepository, CalendarPort, OrgSettingsRepo, ConnectionRepo, Crypto.

    Args:
        session: The async database session from DI.
        current_user: The authenticated user.

    Returns:
        A fully configured InterviewSchedulerService.
    """
    candidate_repo = CandidateRepository(session)
    interview_repo = InterviewRepository(session)

    # Calendar scheduling dependencies (ADR-0008).
    org_settings_repo = OrganizationSettingsRepository(session, get_recruitment_settings())
    connection_repo = OrganizationGoogleConnectionRepository(session)
    crypto = get_crypto_utils()

    return InterviewSchedulerService(
        candidate_repo=candidate_repo,
        interview_repo=interview_repo,
        calendar_port=get_calendar_adapter(),
        org_settings_repo=org_settings_repo,
        connection_repo=connection_repo,
        crypto=crypto,
        session=session,
        user_id=current_user.id,
    )


async def get_candidate_notification_service(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> CandidateNotificationService:
    """Provide a CandidateNotificationService instance with all dependencies.

    Wires notification-only dependencies: CandidateRepository.
    Does NOT include Calendar or crypto dependencies.

    Args:
        session: The async database session from DI.
        current_user: The authenticated user.

    Returns:
        A fully configured CandidateNotificationService.
    """
    candidate_repo = CandidateRepository(session)

    return CandidateNotificationService(
        candidate_repo=candidate_repo,
        session=session,
        user_id=current_user.id,
    )


async def get_review_service(
    session: AsyncSession = Depends(get_db_session),
) -> ReviewService:
    """Provide a ReviewService instance with all dependencies.

    Args:
        session: The async database session from DI.

    Returns:
        A fully configured ReviewService.
    """
    cv_document_repo = CVDocumentRepository(session)
    candidate_repo = CandidateRepository(session)
    minio_client = get_minio_client()

    # CandidateLifecycleService acts as the CandidateCreatorProtocol
    job_opening_repo = JobOpeningRepository(session)
    candidate_service = CandidateLifecycleService(
        candidate_repo=candidate_repo,
        cv_document_repo=cv_document_repo,
        job_opening_repo=job_opening_repo,
        minio_client=minio_client,
        session=session,
    )

    # CVProcessorService acts as the CVRetryParserProtocol
    settings = get_recruitment_settings()
    cv_processor = CVProcessorService(
        minio_client=minio_client,
        ocr_adapter=get_ocr_adapter(),
        llm_adapter=get_llm_adapter(),
        pii_redactor=get_pii_redactor(),
        candidate_repo=candidate_repo,
        cv_document_repo=cv_document_repo,
        settings=settings,
        session=session,
        candidate_creator=candidate_service,
    )

    return ReviewService(
        cv_document_repo=cv_document_repo,
        candidate_creator=candidate_service,
        cv_retry_parser=cv_processor,
        session=session,
    )


async def get_cv_processor_service(
    session: AsyncSession = Depends(get_db_session),
) -> CVProcessorService:
    """Provide a CVProcessorService instance with all dependencies.

    Args:
        session: The async database session from DI.

    Returns:
        A fully configured CVProcessorService.
    """
    settings = get_recruitment_settings()
    candidate_repo = CandidateRepository(session)
    cv_document_repo = CVDocumentRepository(session)
    minio_client = get_minio_client()

    # CandidateLifecycleService acts as the CandidateCreator protocol
    job_opening_repo = JobOpeningRepository(session)
    candidate_service = CandidateLifecycleService(
        candidate_repo=candidate_repo,
        cv_document_repo=cv_document_repo,
        job_opening_repo=job_opening_repo,
        minio_client=minio_client,
        session=session,
    )

    return CVProcessorService(
        minio_client=minio_client,
        ocr_adapter=get_ocr_adapter(),
        llm_adapter=get_llm_adapter(),
        pii_redactor=get_pii_redactor(),
        candidate_repo=candidate_repo,
        cv_document_repo=cv_document_repo,
        settings=settings,
        session=session,
        candidate_creator=candidate_service,
    )


async def get_intent_classifier_service(
    session: AsyncSession = Depends(get_db_session),
) -> IntentClassifierService:
    """Provide an IntentClassifierService instance with all dependencies.

    Args:
        session: The async database session from DI.

    Returns:
        A fully configured IntentClassifierService.
    """
    return IntentClassifierService(
        llm_adapter=get_llm_adapter(),
        pii_redactor=get_pii_redactor(),
        session=session,
    )


# ---------------------------------------------------------------------------
# ARQ task functions
# ---------------------------------------------------------------------------


async def arq_process_cv_from_email(ctx: dict[str, Any], email_message_id: UUID) -> None:
    """ARQ task: process CV attachments from a classified email.

    This task is enqueued by the IntentClassifierService when an email
    is classified as CV intent. It downloads attachments, runs OCR,
    parses with LLM, and creates candidate records.

    Args:
        ctx: ARQ job context dict with shared resources.
        email_message_id: UUID of the email message to process.
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info("ARQ task: processing CV from email %s", email_message_id)

    session_maker = ctx["session_maker"]

    async with session_maker() as session:
        try:
            settings = get_recruitment_settings()
            candidate_repo = CandidateRepository(session)
            cv_document_repo = CVDocumentRepository(session)
            minio_client = get_minio_client()

            job_opening_repo = JobOpeningRepository(session)
            candidate_service = CandidateLifecycleService(
                candidate_repo=candidate_repo,
                cv_document_repo=cv_document_repo,
                job_opening_repo=job_opening_repo,
                minio_client=minio_client,
                session=session,
            )

            cv_processor = CVProcessorService(
                minio_client=minio_client,
                ocr_adapter=get_ocr_adapter(),
                llm_adapter=get_llm_adapter(),
                pii_redactor=get_pii_redactor(),
                candidate_repo=candidate_repo,
                cv_document_repo=cv_document_repo,
                settings=settings,
                session=session,
                candidate_creator=candidate_service,
            )

            # Fetch email message to get gmail_message_id and user_id
            import httpx
            import redis.asyncio as redis
            from sqlmodel import select

            from src.modules.gmail.application.attachment_service import (
                AttachmentMetadata,
                AttachmentService,
            )
            from src.modules.gmail.domain.entities import EmailAttachment, EmailMessage
            from src.modules.gmail.infrastructure.audit_logger import AuditLogger
            from src.modules.gmail.infrastructure.config import GmailSettings
            from src.modules.gmail.infrastructure.gmail_adapter import GmailAdapter
            from src.modules.gmail.infrastructure.quota_tracker import QuotaTracker
            from src.modules.identity.infrastructure.config import AuthSettings
            from src.modules.identity.infrastructure.connection_state_repository import (
                OrganizationGoogleConnectionRepository,
            )
            from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
            from src.modules.recruitment.application.cv_processor import AttachmentInput

            # Get email message record
            stmt = select(EmailMessage).where(EmailMessage.id == email_message_id)
            result = await session.execute(stmt)
            email_msg = result.scalars().first()

            if email_msg is None:
                logger.error("Email message not found for ARQ task: %s", email_message_id)
                return

            gmail_message_id = email_msg.gmail_message_id
            user_id = email_msg.user_id

            # Get attachment metadata from database
            att_stmt = select(EmailAttachment).where(
                EmailAttachment.email_message_id == email_message_id
            )
            att_result = await session.execute(att_stmt)
            db_attachments = list(att_result.scalars().all())

            if not db_attachments:
                logger.info(
                    "No attachments found for email %s, skipping CV processing",
                    email_message_id,
                )
                return

            # Resolve the shared Organization Google Connection. The HR user
            # on the email is an audit actor, never a Google credential owner.
            auth_settings = AuthSettings()  # type: ignore[call-arg]
            gmail_settings = GmailSettings()
            crypto = CryptoUtils(auth_settings.oauth_token_encryption_key)
            connection = await OrganizationGoogleConnectionRepository(session).get_singleton()

            if (
                connection is None
                or connection.status != "connected"
                or not connection.access_token_enc
            ):
                logger.error(
                    "No active Organization Google Connection, cannot process CV for %s",
                    email_message_id,
                )
                return

            access_token = crypto.decrypt(connection.access_token_enc)

            # Build Gmail adapter and AttachmentService to fetch binary data
            redis_client = ctx.get("redis_client")
            if redis_client is None:
                redis_client = redis.from_url(auth_settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]

            quota_tracker = QuotaTracker(redis_client, gmail_settings)

            async with httpx.AsyncClient() as http_client:
                gmail_adapter = GmailAdapter(
                    settings=gmail_settings,
                    quota_tracker=quota_tracker,
                    http_client=http_client,
                    user_id=user_id,
                )
                audit_logger = AuditLogger(session, gmail_settings)

                attachment_service = AttachmentService(
                    gmail_adapter=gmail_adapter,
                    settings=gmail_settings,
                    audit_logger=audit_logger,
                )

                # Build AttachmentMetadata list from DB records
                attachment_metadata_list = [
                    AttachmentMetadata(
                        attachment_id=att.gmail_attachment_id,
                        filename=att.filename,
                        mime_type=att.mime_type,
                        size_bytes=att.size_bytes,
                    )
                    for att in db_attachments
                ]

                # Fetch attachment binary data via AttachmentService
                fetch_result = await attachment_service.fetch_attachments(
                    user_id=user_id,
                    message_id=gmail_message_id,
                    access_token=access_token,
                    attachments=attachment_metadata_list,
                )

                # Convert fetched attachments to CVProcessor input format
                attachments_data: list[AttachmentInput] = [
                    AttachmentInput(
                        filename=att.filename,
                        mime_type=att.mime_type,
                        size_bytes=att.size_bytes,
                        data=att.data,
                    )
                    for att in fetch_result.fetched
                ]

                if not attachments_data:
                    logger.info(
                        "No valid attachments fetched for email %s, skipping",
                        email_message_id,
                    )
                    return

                # Process all attachments through the CV pipeline
                await cv_processor.process_cv_from_email(
                    email_message_id=email_message_id,
                    attachments=attachments_data,
                    gmail_message_id=gmail_message_id,
                )

            await session.commit()
            logger.info("ARQ task completed: CV processing for email %s", email_message_id)

        except Exception:
            await session.rollback()
            logger.error(
                "ARQ task failed: CV processing for email %s",
                email_message_id,
                exc_info=True,
            )
            raise


async def arq_retention_cleanup(ctx: dict[str, Any]) -> int:
    """ARQ task: retention cleanup of expired rejected candidates.

    Delegates to the retention_cleanup function which handles the full
    cleanup logic including MinIO file deletion and audit logging.

    Args:
        ctx: ARQ job context dict with shared resources.

    Returns:
        Number of candidates successfully deleted.
    """
    import logging

    from src.modules.recruitment.application.retention_job import retention_cleanup

    logger = logging.getLogger(__name__)
    logger.info("ARQ task: starting retention cleanup")

    session_maker = ctx["session_maker"]

    async with session_maker() as session:
        # Build the context expected by retention_cleanup
        retention_ctx: dict[str, Any] = {
            "session": session,
            "minio_client": get_minio_client(),
            "settings": get_recruitment_settings(),
        }

        deleted_count = await retention_cleanup(retention_ctx)
        logger.info("ARQ task completed: retention cleanup deleted %d candidates", deleted_count)
        return deleted_count


# ---------------------------------------------------------------------------
# Calendar Sync Service
# ---------------------------------------------------------------------------


async def get_calendar_sync_service(
    session: AsyncSession = Depends(get_db_session),
) -> CalendarSyncService:
    """Provide a CalendarSyncService instance with all dependencies.

    Reads the selected ``calendar_id`` from the Organization Google
    Connection so the sync service operates on the correct calendar.

    Args:
        session: The async database session from DI.

    Returns:
        A configured CalendarSyncService.

    Raises:
        CalendarGrantMissingError: If no org connection or no selected calendar.
    """
    from src.modules.recruitment.application.calendar_sync_service import (
        CalendarSyncService,
    )
    from src.modules.recruitment.domain.exceptions import CalendarGrantMissingError

    calendar_adapter = get_calendar_adapter()
    sync_cursor_repo = CalendarSyncCursorRepository(session)

    conn_repo = OrganizationGoogleConnectionRepository(session)
    connection = await conn_repo.get_singleton()
    if connection is None or connection.status != "connected":
        raise CalendarGrantMissingError(message="Organization Google Connection is not active")
    calendar_id = connection.selected_calendar_id
    if not calendar_id:
        raise CalendarGrantMissingError(
            message="No recruitment calendar selected; "
            "select a calendar in Organization settings first"
        )

    return CalendarSyncService(
        adapter=calendar_adapter,
        sync_cursor_repo=sync_cursor_repo,
        calendar_id=calendar_id,
    )


def get_arq_tasks() -> list[Any]:
    """Return the list of ARQ task functions for the recruitment module.

    These tasks should be registered in the ARQ worker settings alongside
    the Gmail module's cron jobs.

    Returns:
        List of ARQ-compatible async task functions.
    """
    return [arq_process_cv_from_email, arq_retention_cleanup]
