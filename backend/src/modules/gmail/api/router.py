"""FastAPI router for the Gmail Integration module.

Defines the /api/gmail/* endpoints for Gmail OAuth2 connection management,
email synchronization, message body fetching, label management, email
sending, and attachment retrieval. All endpoints require authentication.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.modules.gmail.domain.entities import EmailMessage as EmailMessageEntity
    from src.modules.gmail.infrastructure.audit_logger import AuditLogger
    from src.modules.gmail.infrastructure.config import GmailSettings

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from src.modules.gmail.api.schemas import (
    ErrorResponse,
    ImportCancelResponse,
    ImportPreviewResponse,
    ImportStartRequest,
    ImportStartResponse,
    ImportStatusResponse,
    MessageBodyResponse,
    MessageListItem,
    MessageListResponse,
    OutboundEmailCreateRequest,
    OutboundEmailListResponse,
    OutboundEmailResponse,
    SendEmailRequest,
    SendEmailResponse,
    SyncResponse,
)
from src.modules.gmail.application.attachment_service import (
    AttachmentMetadata,
    AttachmentService,
)
from src.modules.gmail.application.email_sync_service import EmailSyncService
from src.modules.gmail.application.import_service import HistoricalImportService
from src.modules.gmail.application.outbound_email_service import OutboundEmailService
from src.modules.gmail.application.send_service import (
    AttachmentData,
    SendEmailParams,
    SendService,
)
from src.modules.gmail.container import (
    get_arq_pool,
    get_attachment_service,
    get_email_repository,
    get_email_sync_service,
    get_gmail_adapter,
    get_historical_import_service,
    get_outbound_email_service,
    get_send_service,
)
from src.modules.gmail.infrastructure.email_repository import EmailRepository
from src.modules.gmail.infrastructure.gmail_adapter import GmailAdapter
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import User, UserRole

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases for injected dependencies
# ---------------------------------------------------------------------------

CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def require_hr(current_user: CurrentUserDep) -> User:
    if current_user.role != UserRole.HR:
        raise HTTPException(status_code=403, detail="HR access required")
    return current_user


HRUserDep = Annotated[User, Depends(require_hr)]

EmailSyncServiceDep = Annotated[EmailSyncService, Depends(get_email_sync_service)]
EmailRepositoryDep = Annotated[EmailRepository, Depends(get_email_repository)]
SendServiceDep = Annotated[SendService, Depends(get_send_service)]


OutboundEmailServiceDep = Annotated[
    OutboundEmailService,
    Depends(get_outbound_email_service),
]
AttachmentServiceDep = Annotated[AttachmentService, Depends(get_attachment_service)]
GmailAdapterDep = Annotated[GmailAdapter, Depends(get_gmail_adapter)]
HistoricalImportServiceDep = Annotated[
    HistoricalImportService,
    Depends(get_historical_import_service),
]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/gmail", tags=["gmail"])


# ---------------------------------------------------------------------------
# Sync endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/sync",
    response_model=SyncResponse,
    responses={
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def manual_sync(
    current_user: CurrentUserDep,
    sync_service: EmailSyncServiceDep,
) -> SyncResponse:
    """Trigger a manual email synchronization.

    Performs an immediate email fetch (same logic as the scheduled poll)
    outside the regular schedule. Rate limited to 1 request per 30 seconds.

    Args:
        current_user: The authenticated user.
        sync_service: The email sync service.

    Returns:
        SyncResponse with the count of new emails fetched.
    """
    synced_count = await sync_service.manual_sync(current_user.id)
    return SyncResponse(
        synced_count=synced_count,
        status="ok",
    )


# ---------------------------------------------------------------------------
# Historical import endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/import/preview",
    response_model=ImportPreviewResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def preview_import(
    current_user: CurrentUserDep,
    import_service: HistoricalImportServiceDep,
    body: ImportStartRequest,
) -> ImportPreviewResponse:
    """Preview the number of importable emails in a time window.

    Scans the Organization Shared Google Account INBOX for emails
    within the specified window (7 or 30 days) and reports how many
    have not yet been imported.

    Args:
        current_user: The authenticated HR user.
        import_service: The historical import service.
        body: Request with days parameter.

    Returns:
        ImportPreviewResponse with estimated counts.
    """
    result = await import_service.preview_import(body.days, current_user.id)
    return ImportPreviewResponse(
        days=result.days,
        estimated_count=result.estimated_count,
        already_imported_count=result.already_imported_count,
        query_window_start=result.query_window_start,
        query_window_end=result.query_window_end,
    )


@router.post(
    "/import/start",
    response_model=ImportStartResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def start_import(
    current_user: CurrentUserDep,
    import_service: HistoricalImportServiceDep,
    body: ImportStartRequest,
) -> ImportStartResponse:
    """Start a historical email import job.

    Enqueues an import for the specified window (7 or 30 days).
    The import runs in the background; progress can be tracked
    via the status endpoint.

    Args:
        current_user: The authenticated HR user.
        import_service: The historical import service.
        body: Request with days parameter.

    Returns:
        ImportStartResponse with job_id.
    """
    job_id = await import_service.start_import(body.days, current_user.id)

    # Enqueue the ARQ background job using the shared pool.
    try:
        arq_pool = await get_arq_pool()
        await arq_pool.enqueue_job("import_historical_emails")
    except Exception as exc:
        # Surface failure: clean up the zombie job state so the client
        # sees a clear error instead of a phantom "running" job.
        await import_service._cleanup_job_state()
        logger.error(
            "Failed to enqueue ARQ job for historical import %s: %s",
            job_id,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=(f"Không thể xếp hàng đợi import: {exc}. Vui lòng thử lại sau."),
        )
    return ImportStartResponse(
        job_id=job_id,
        status="running",
        days=body.days,
        message=f"Import {body.days}-ngày đã được khởi tạo. "
        f"Kiểm tra trạng thái để theo dõi tiến độ.",
    )


@router.get(
    "/import/status",
    response_model=ImportStatusResponse,
    responses={
        401: {"model": ErrorResponse},
    },
)
async def get_import_status(
    current_user: CurrentUserDep,
    import_service: HistoricalImportServiceDep,
) -> ImportStatusResponse:
    """Get the status of current or last historical import job.

    Returns progress counters and current state.

    Args:
        import_service: The historical import service.

    Returns:
        ImportStatusResponse with job progress.
    """
    result = await import_service.get_import_status()
    return ImportStatusResponse(
        job_id=result.job_id,
        status=result.status,
        days=result.days,
        total_count=result.total_count,
        processed_count=result.processed_count,
        job_application_count=result.job_application_count,
        errors=result.errors,
        started_at=result.started_at,
        completed_at=result.completed_at,
        error_message=result.error_message,
    )


@router.post(
    "/import/cancel",
    response_model=ImportCancelResponse,
    responses={
        401: {"model": ErrorResponse},
    },
)
async def cancel_import(
    current_user: CurrentUserDep,
    import_service: HistoricalImportServiceDep,
) -> ImportCancelResponse:
    """Cancel a running historical import job.

    Requests cancellation of the running import. The worker will
    stop at the next batch boundary.

    Args:
        current_user: The authenticated HR user.
        import_service: The historical import service.

    Returns:
        ImportCancelResponse confirming cancellation.
    """
    cancelled = await import_service.cancel_import(current_user.id)
    if cancelled:
        return ImportCancelResponse(
            status="cancelled",
            message="Đã yêu cầu dừng import. Job sẽ dừng sau batch hiện tại.",
        )
    return ImportCancelResponse(
        status="no_active_job",
        message="Không có import nào đang chạy.",
    )


# ---------------------------------------------------------------------------
# Message endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/messages",
    response_model=MessageListResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def list_messages(
    current_user: HRUserDep,
    email_repo: EmailRepositoryDep,
    limit: int = Query(default=50, ge=1, le=100, description="Max messages to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    category: str | None = Query(default=None, description="Filter by category"),
) -> MessageListResponse:
    """List email messages for the authenticated user.

    Returns messages ordered by received_at descending (most recent first).
    Supports pagination via limit/offset parameters.
    Optionally filter by category.

    Args:
        current_user: The authenticated user.
        email_repo: The email repository.
        limit: Maximum number of messages to return (1-100, default 50).
        offset: Number of messages to skip for pagination.
        category: Optional category filter (e.g. "job_application", "cv").

    Returns:
        MessageListResponse with list of messages and total count.
    """
    messages = await email_repo.list_by_user(
        user_id=current_user.id, limit=limit, offset=offset, category=category
    )
    total_count = await email_repo.count_by_user(user_id=current_user.id, category=category)

    # M2: Reset stuck cv_processing emails (>30 min)
    from datetime import UTC, datetime, timedelta

    from sqlmodel import update

    from src.modules.gmail.domain.entities import EmailMessage as EmailMessageEntity

    stale_cv_stmt = (
        update(EmailMessageEntity)
        .where(EmailMessageEntity.user_id == current_user.id)
        .where(EmailMessageEntity.processing_status == "cv_processing")
        .where(EmailMessageEntity.updated_at < datetime.now(UTC) - timedelta(minutes=30))
        .values(processing_status="classified")
        .execution_options(synchronize_session="fetch")
    )
    result = await email_repo.session.execute(stale_cv_stmt)
    if result.rowcount:
        await email_repo.session.commit()
        logger.info(
            "Reset %d stuck cv_processing emails to classified for user %s",
            result.rowcount,
            current_user.id,
        )

    items = [
        MessageListItem(
            id=str(msg.id),
            gmail_message_id=msg.gmail_message_id,
            gmail_thread_id=msg.gmail_thread_id,
            subject=msg.subject,
            sender_email=msg.sender_email,
            sender_name=msg.sender_name,
            recipient_emails=msg.recipient_emails,
            cc_emails=msg.cc_emails,
            received_at=msg.received_at.isoformat(),
            snippet=msg.snippet,
            label_ids=msg.label_ids,
            has_attachments=msg.has_attachments,
            category=msg.category,
            processing_status=msg.processing_status,
        )
        for msg in messages
    ]

    return MessageListResponse(messages=items, total=total_count)


@router.get(
    "/messages/{message_id}/body",
    response_model=MessageBodyResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def get_message_body(
    message_id: str,
    current_user: HRUserDep,
    email_repo: EmailRepositoryDep,
    gmail_adapter: GmailAdapterDep,
) -> MessageBodyResponse:
    """Fetch the full email body content for a message.

    Tries to retrieve the body from locally stored encrypted payload first.
    Falls back to Gmail API if no cached payload is available.

    Args:
        message_id: The Gmail message ID.
        current_user: The authenticated user.
        email_repo: The email repository for session access.
        gmail_adapter: The Gmail API adapter.

    Returns:
        MessageBodyResponse with plain_text and/or html content.
    """
    # Try local cache first: look up email by Gmail message ID
    email = await email_repo.get_by_gmail_id(message_id)
    if email is not None and email.raw_payload_enc:
        try:
            from src.modules.identity.container import get_crypto_utils

            crypto = get_crypto_utils()
            raw_json = crypto.decrypt(email.raw_payload_enc)
            data = json.loads(raw_json)
            payload = data.get("payload", {})
            body = gmail_adapter._extract_body(payload)
            return MessageBodyResponse(
                plain_text=body.plain_text,
                html=body.html,
            )
        except Exception:
            logger.warning(
                "Failed to decrypt/extract cached body for message %s, falling back to Gmail API",
                message_id,
                exc_info=True,
            )

    # Fallback: fetch from Gmail API
    access_token = await _get_user_access_token(email_repo.session)
    body = await gmail_adapter.get_message_body(access_token, message_id)
    return MessageBodyResponse(
        plain_text=body.plain_text,
        html=body.html,
    )


# ---------------------------------------------------------------------------
# Send endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/send",
    response_model=SendEmailResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def send_email(
    body: SendEmailRequest,
    current_user: CurrentUserDep,
    send_service: SendServiceDep,
) -> SendEmailResponse:
    """Send an email via the user's connected Gmail account.

    Composes and sends an email with support for HTML/plain text body,
    CC recipients, reply threading, and file attachments.

    Args:
        body: The email send request with recipients, subject, and body.
        current_user: The authenticated user.
        send_service: The send service.

    Returns:
        SendEmailResponse with the sent message_id and thread_id.
    """
    # Convert request schema to service params
    attachments: list[AttachmentData] = []
    if body.attachments:
        for att in body.attachments:
            attachments.append(
                AttachmentData(
                    filename=att.filename,
                    content=base64.b64decode(att.content),
                    mime_type=att.mime_type,
                )
            )

    params = SendEmailParams(
        to=body.to,
        subject=body.subject,
        body_html=body.body_html,
        body_text=body.body_text,
        cc=body.cc or [],
        reply_to_message_id=body.reply_to_message_id,
        attachments=attachments,
    )

    result = await send_service.send_email(current_user.id, params)
    return SendEmailResponse(
        message_id=result.message_id,
        thread_id=result.thread_id,
    )


# ---------------------------------------------------------------------------
# Attachment endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/messages/{message_id}/attachments",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def fetch_attachments(
    message_id: str,
    current_user: HRUserDep,
    attachment_service: AttachmentServiceDep,
    email_repo: EmailRepositoryDep,
    gmail_adapter: GmailAdapterDep,
) -> dict[str, Any]:
    """Fetch and validate attachments for an email message.

    Downloads attachments from Gmail API, validates MIME types and file
    sizes against configured limits, and returns metadata about fetched
    and skipped attachments.

    Args:
        message_id: The Gmail message ID containing attachments.
        current_user: The authenticated user.
        attachment_service: The attachment service.
        email_repo: The email repository for session access.
        gmail_adapter: The Gmail API adapter.

    Returns:
        Dictionary with fetched_count, skipped_count, and attachment metadata.
    """
    # Get access token from organization connection (raises if not connected)
    access_token = await _get_user_access_token(email_repo.session)

    # Fetch the full message to get attachment parts
    msg_data = await gmail_adapter.get_full_message(access_token, message_id)

    # Extract attachment metadata from message parts
    attachments_meta = _extract_attachment_metadata(msg_data.get("payload", {}))

    # Fetch attachments via the service
    result = await attachment_service.fetch_attachments(
        user_id=current_user.id,
        message_id=message_id,
        access_token=access_token,
        attachments=attachments_meta,
    )

    return {
        "fetched_count": result.fetched_count,
        "skipped_count": result.skipped_count,
        "total_count": result.total_count,
        "attachments": [
            {
                "attachment_id": att.attachment_id,
                "filename": att.filename,
                "mime_type": att.mime_type,
                "size_bytes": att.size_bytes,
            }
            for att in result.fetched
        ],
    }


@router.post(
    "/messages/{message_id}/process-attachments",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def process_attachments(
    message_id: str,
    current_user: HRUserDep,
    attachment_service: AttachmentServiceDep,
    email_repo: EmailRepositoryDep,
    gmail_adapter: GmailAdapterDep,
) -> dict[str, Any]:
    """Fetch email attachments and trigger CV processing pipeline.

    Downloads attachments from Gmail API, then runs the recruitment
    CV processor pipeline (OCR -> LLM parse -> confidence check).
    Creates CVDocument records and either creates a Candidate
    (high confidence) or flags for review (low confidence).

    Args:
        message_id: The Gmail message ID containing attachments.
        current_user: The authenticated user.
        attachment_service: The attachment service.
        email_repo: The email repository.

    Returns:
        Dictionary with processing results.
    """
    from sqlmodel import select

    from src.modules.gmail.domain.entities import EmailMessage as EmailMessageEntity

    # Probe the organization connection up front (raises if not connected) so a
    # disconnected account fails before the status transition below. The helper
    # fetches its own token when it actually calls Gmail.
    await _get_user_access_token(email_repo.session)

    # Find email record
    stmt = select(EmailMessageEntity).where(EmailMessageEntity.gmail_message_id == message_id)
    result = await email_repo.session.execute(stmt)
    email = result.scalar_one_or_none()

    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    if email.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Guard: only classified recruitment emails with attachments
    if email.category != "recruitment":
        raise HTTPException(
            status_code=400,
            detail=f"Email category is '{email.category}', expected 'recruitment'",
        )
    if email.processing_status != "classified":
        raise HTTPException(
            status_code=400,
            detail=f"Email status is '{email.processing_status}', expected 'classified'",
        )

    # Atomically transition to cv_processing to prevent race conditions.
    # A second concurrent request will see processing_status != "classified"
    # and get 400 before starting the expensive pipeline.
    email.processing_status = "cv_processing"
    email_repo.session.add(email)
    await email_repo.session.commit()

    # Run CV processing via shared helper
    from src.modules.gmail.infrastructure.audit_logger import AuditLogger
    from src.modules.gmail.infrastructure.config import GmailSettings

    settings = GmailSettings()
    classify_logger = logging.getLogger("gmail.classify")

    try:
        cv_documents = await _fetch_and_process_cv_for_email(
            current_user=current_user,
            email_repo=email_repo,
            gmail_adapter=gmail_adapter,
            settings=settings,
            audit_logger=AuditLogger(email_repo.session, settings),
            email=email,
            classify_logger=classify_logger,
        )
    except Exception as exc:
        logger.error("CV processing failed for email %s: %s", message_id, exc)
        # Revert to needs_review so HR can retry via reclassify
        email.processing_status = "needs_review"
        email_repo.session.add(email)
        await email_repo.session.commit()
        raise HTTPException(
            status_code=500,
            detail=f"CV processing failed: {exc}",
        ) from exc

    if not cv_documents:
        # Revert: no valid attachments to process
        email.processing_status = "classified"
        email_repo.session.add(email)
        await email_repo.session.commit()
        return {"processed_count": 0, "message": "No attachments found"}

    # Set final status based on CV processing results.
    # If any CVDocument ended up as needs_review or failed, propagate that.
    # Otherwise mark as classified.
    if any(doc.processing_status in {"needs_review", "failed"} for doc in cv_documents):
        email.processing_status = "needs_review"
    else:
        email.processing_status = "classified"
    email_repo.session.add(email)
    await email_repo.session.commit()

    return {
        "processed_count": len(cv_documents),
        "cv_documents": [
            {
                "id": str(doc.id),
                "original_filename": doc.original_filename,
                "processing_status": doc.processing_status,
                "confidence_score": doc.confidence_score,
            }
            for doc in cv_documents
        ],
    }


# ---------------------------------------------------------------------------
# Classification endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/classify",
    response_model=None,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def classify_emails(
    current_user: HRUserDep,
    email_repo: EmailRepositoryDep,
    gmail_adapter: GmailAdapterDep,
    limit: int = Query(default=5, ge=1, le=20, description="Max emails to classify per request"),
) -> dict[str, Any] | JSONResponse:
    """Trigger AI classification for all unclassified emails.

    Finds emails with processing_status='unprocessed' or category=NULL
    and runs the two-tier classification pipeline (rules + AI).
    This is useful for classifying emails that were synced before
    the classification feature was enabled.

    Args:
        current_user: The authenticated user.
        email_repo: The email repository.

    Returns:
        Dictionary with classified_count and total_unclassified,
        or a 504 JSONResponse if the request times out.
    """
    from src.modules.gmail.infrastructure.config import GmailSettings

    settings = GmailSettings()

    async def _do_classify(
        gmail_adapter: GmailAdapter,
    ) -> dict[str, Any]:

        # Fetch unclassified emails and the total remaining count
        unclassified_emails, total_remaining = await _get_unclassified_emails_and_count(
            current_user, email_repo, limit
        )

        if not unclassified_emails:
            return {
                "classified_count": 0,
                "total": 0,
                "remaining": 0,
                "message": "Tất cả email đã được phân loại",
                "results": [],
            }

        import logging

        classify_logger = logging.getLogger("gmail.classify")
        classify_logger.info(
            "Starting classification for %d emails (user=%s)",
            len(unclassified_emails),
            current_user.id,
        )

        classified_count = await _evaluate_rules(
            current_user_id=current_user.id,
            unclassified_emails=unclassified_emails,
            email_repo=email_repo,
        )

        cv_processed_count = await _update_database_and_process_cvs(
            current_user=current_user,
            gmail_adapter=gmail_adapter,
            settings=settings,
            email_repo=email_repo,
            unclassified_emails=unclassified_emails,
            classify_logger=classify_logger,
        )

        # Build results summary
        results_summary = []
        for email in unclassified_emails[:20]:  # Limit to first 20 for response size
            results_summary.append(
                {
                    "subject": email.subject[:60],
                    "category": email.category,
                }
            )

        classify_logger.info(
            "Classification complete: %d/%d emails classified, %d CVs processed",
            classified_count,
            len(unclassified_emails),
            cv_processed_count,
        )

        return {
            "classified_count": classified_count,
            "total": len(unclassified_emails),
            "remaining": max(0, total_remaining - classified_count),
            "cv_processed_count": cv_processed_count,
            "message": (
                f"AI đã phân loại {classified_count}/{len(unclassified_emails)} email"
                + (f", xử lý {cv_processed_count} CV" if cv_processed_count > 0 else "")
            ),
            "results": results_summary,
        }

    try:
        return await asyncio.wait_for(
            _do_classify(gmail_adapter),
            timeout=settings.classification_request_timeout_seconds,
        )
    except TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"detail": "Phân loại email bị timeout. Vui lòng thử lại với số lượng ít hơn."},
        )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


async def _fetch_and_process_cv_for_email(
    *,
    current_user: User,
    email_repo: EmailRepository,
    gmail_adapter: GmailAdapter,
    settings: GmailSettings,
    audit_logger: AuditLogger,
    email: EmailMessageEntity,
    classify_logger: logging.Logger,
) -> list[Any]:
    """Fetch attachments from Gmail and run CV processing for one email.

    Shared helper used by process_attachments (endpoint) and
    _update_database_and_process_cvs (classification flow).

    Returns:
        List of CVDocument entities created (empty if no attachments).
    """
    from src.modules.gmail.application.attachment_service import AttachmentService
    from src.modules.recruitment.application.cv_processor import AttachmentInput
    from src.modules.recruitment.container import get_cv_processor_service

    access_token = await _get_user_access_token(email_repo.session)
    msg_data = await gmail_adapter.get_full_message(access_token, email.gmail_message_id)
    attachments_meta = _extract_attachment_metadata(msg_data.get("payload", {}))

    if not attachments_meta:
        classify_logger.info(
            "No attachments found for email %s, skipping CV processing",
            email.gmail_message_id,
        )
        return []

    attachment_service = AttachmentService(
        gmail_adapter=gmail_adapter,
        settings=settings,
        audit_logger=audit_logger,
    )
    fetch_result = await attachment_service.fetch_attachments(
        user_id=current_user.id,
        message_id=email.gmail_message_id,
        access_token=access_token,
        attachments=attachments_meta,
    )

    if not fetch_result.fetched:
        return []

    attachment_inputs = [
        AttachmentInput(
            filename=att.filename,
            mime_type=att.mime_type,
            size_bytes=att.size_bytes,
            data=att.data,
        )
        for att in fetch_result.fetched
    ]

    cv_processor = await get_cv_processor_service(session=email_repo.session)
    cv_documents = await cv_processor.process_cv_from_email(
        email_message_id=email.id,
        attachments=attachment_inputs,
        gmail_message_id=email.gmail_message_id,
    )

    classify_logger.info(
        "Processed %d CV documents for email %s",
        len(cv_documents),
        email.gmail_message_id,
    )
    return cv_documents


async def _get_unclassified_emails_and_count(
    current_user: User, email_repo: EmailRepository, limit: int
) -> tuple[list[EmailMessageEntity], int]:
    """Fetch unclassified emails and the total remaining count."""
    from datetime import UTC, datetime

    from sqlalchemy import func, or_
    from sqlmodel import select

    from src.modules.gmail.domain.entities import EmailMessage as EmailMessageEntity

    pending_filter = or_(
        EmailMessageEntity.processing_status == "unprocessed",
        (EmailMessageEntity.processing_status == "ai_unavailable")
        & (EmailMessageEntity.next_retry_at <= datetime.now(UTC)),
    )
    statement = (
        select(EmailMessageEntity)
        .where(EmailMessageEntity.user_id == current_user.id)
        .where(pending_filter)
        .limit(limit)
    )
    result = await email_repo.session.execute(statement)
    unclassified_emails = list(result.scalars().all())

    count_stmt = (
        select(func.count())
        .select_from(EmailMessageEntity)
        .where(EmailMessageEntity.user_id == current_user.id)
        .where(pending_filter)
    )
    total_remaining_result = await email_repo.session.execute(count_stmt)
    total_remaining = total_remaining_result.scalar() or 0

    return unclassified_emails, total_remaining


async def _get_user_access_token(session: AsyncSession) -> str:
    """Retrieve the decrypted Gmail access token from the organization connection.

    Uses the OrganizationGoogleConnectionRepository singleton lookup
    to fetch the organization's Google connection and decrypt its
    stored access token.

    Args:
        session: The async database session.

    Returns:
        The decrypted access token string.

    Raises:
        GmailNotConnectedException: If no valid connection or token is available.
    """
    from src.modules.gmail.domain.exceptions import GmailNotConnectedException
    from src.modules.identity.container import get_crypto_utils
    from src.modules.identity.infrastructure.connection_state_repository import (
        OrganizationGoogleConnectionRepository,
    )

    repo = OrganizationGoogleConnectionRepository(session)
    connection = await repo.get_singleton()

    if connection is None or connection.status != "connected":
        raise GmailNotConnectedException("Organization Google Connection is not active")

    if not connection.access_token_enc:
        raise GmailNotConnectedException("No stored access token")

    crypto = get_crypto_utils()
    return crypto.decrypt(connection.access_token_enc)


def _extract_attachment_metadata(payload: dict[str, Any]) -> list[AttachmentMetadata]:
    """Extract attachment metadata from a Gmail message payload.

    Recursively searches the message parts for attachments (parts with
    a filename and body.attachmentId).

    Args:
        payload: The message payload from Gmail API.

    Returns:
        List of AttachmentMetadata objects for each attachment found.
    """
    attachments: list[AttachmentMetadata] = []
    _walk_parts_for_attachments(payload, attachments)
    return attachments


def _walk_parts_for_attachments(
    part: dict[str, Any], attachments: list[AttachmentMetadata]
) -> None:
    """Recursively walk message parts to find attachments.

    Args:
        part: A message part from the Gmail API payload.
        attachments: Accumulator list for found attachments.
    """
    filename = part.get("filename", "")
    body = part.get("body", {})
    attachment_id = body.get("attachmentId")

    if filename and attachment_id:
        attachments.append(
            AttachmentMetadata(
                attachment_id=attachment_id,
                filename=filename,
                mime_type=part.get("mimeType", "application/octet-stream"),
                size_bytes=body.get("size", 0),
            )
        )

    # Recurse into nested parts
    for sub_part in part.get("parts", []):
        _walk_parts_for_attachments(sub_part, attachments)


async def _evaluate_rules(
    current_user_id: UUID,
    unclassified_emails: list[Any],
    email_repo: EmailRepository,
) -> int:
    """Evaluate classification rules for a batch of emails.

    Args:
        current_user_id: The UUID of the user.
        unclassified_emails: List of unclassified EmailMessage entities.
        email_repo: The email repository.

    Returns:
        The number of emails successfully classified.
    """
    from src.modules.gmail.container import build_classification_service

    classification_service = await build_classification_service(email_repo.session)

    return await classification_service.classify_batch(
        user_id=current_user_id,
        emails=unclassified_emails,
    )


async def _update_database_and_process_cvs(
    current_user: User,
    gmail_adapter: GmailAdapter,
    settings: Any,
    email_repo: EmailRepository,
    unclassified_emails: list[Any],
    classify_logger: logging.Logger,
) -> int:
    """Commit classifications and auto-process CVs.

    Args:
        current_user: The authenticated user.
        gmail_adapter: The Gmail API adapter.
        settings: The Gmail settings.
        email_repo: The email repository.
        unclassified_emails: The original list of unclassified emails.
        classify_logger: The classification logger.

    Returns:
        The number of CVs processed.
    """
    from src.modules.gmail.infrastructure.audit_logger import AuditLogger

    audit_logger = AuditLogger(email_repo.session, settings)

    await email_repo.session.commit()

    # Auto-process CV attachments for classified recruitment emails only.
    # Skip emails that ended up as needs_review (low confidence) —
    # those require human review before CV processing.
    cv_processed_count = 0
    recruitment_with_attachments = [
        e
        for e in unclassified_emails
        if e.category == "recruitment" and e.has_attachments and e.processing_status == "classified"
    ]

    if recruitment_with_attachments:
        classify_logger.info(
            "Auto-processing CV attachments for %d recruitment emails",
            len(recruitment_with_attachments),
        )
        for email in recruitment_with_attachments:
            try:
                # Import here to avoid circular imports

                # Run CV processing via shared helper
                cv_documents = await _fetch_and_process_cv_for_email(
                    current_user=current_user,
                    email_repo=email_repo,
                    gmail_adapter=gmail_adapter,
                    settings=settings,
                    audit_logger=audit_logger,
                    email=email,
                    classify_logger=classify_logger,
                )
                cv_processed_count += len(cv_documents)
                classify_logger.info(
                    "Processed %d CV documents for email %s",
                    len(cv_documents),
                    email.gmail_message_id,
                )
            except Exception as exc:
                classify_logger.error(
                    "Failed to auto-process CV for email %s: %s",
                    email.gmail_message_id,
                    exc,
                )

        await email_repo.session.commit()

    return cv_processed_count


# ---------------------------------------------------------------------------
# Dead-letter queue endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/review/emails",
    response_model=MessageListResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def list_emails_needing_review(
    current_user: HRUserDep,
    email_repo: EmailRepositoryDep,
    limit: int = Query(default=50, ge=1, le=100, description="Max emails to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> MessageListResponse:
    """List emails that need human review (needs_review status).

    Returns emails with processing_status='needs_review' that require
    manual classification by HR. These are emails where classification
    confidence was too low or classification failed.

    Args:
        current_user: The authenticated user.
        email_repo: The email repository.
        limit: Maximum number of emails to return.
        offset: Number of emails to skip for pagination.

    Returns:
        MessageListResponse with list of emails needing review.
    """
    from sqlalchemy import desc
    from sqlmodel import select

    from src.modules.gmail.domain.entities import EmailMessage as EmailMessageEntity

    statement = (
        select(EmailMessageEntity)
        .where(EmailMessageEntity.user_id == current_user.id)
        .where(EmailMessageEntity.processing_status == "needs_review")
        .order_by(desc(EmailMessageEntity.received_at))  # type: ignore[arg-type]
        .limit(limit)
        .offset(offset)
    )
    result = await email_repo.session.execute(statement)
    messages = list(result.scalars().all())

    items = [
        MessageListItem(
            id=str(msg.id),
            gmail_message_id=msg.gmail_message_id,
            gmail_thread_id=msg.gmail_thread_id,
            subject=msg.subject,
            sender_email=msg.sender_email,
            sender_name=msg.sender_name,
            recipient_emails=msg.recipient_emails,
            cc_emails=msg.cc_emails,
            received_at=msg.received_at.isoformat(),
            snippet=msg.snippet,
            label_ids=msg.label_ids,
            has_attachments=msg.has_attachments,
            category=msg.category,
            processing_status=msg.processing_status,
        )
        for msg in messages
    ]

    # Get total count for pagination
    from sqlalchemy import func

    count_stmt = (
        select(func.count())
        .select_from(EmailMessageEntity)
        .where(EmailMessageEntity.user_id == current_user.id)
        .where(EmailMessageEntity.processing_status == "needs_review")
    )
    total_result = await email_repo.session.execute(count_stmt)
    total = total_result.scalar() or 0

    return MessageListResponse(messages=items, total=total)


@router.post(
    "/review/emails/{message_id}/reclassify",
    response_model=MessageListItem,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def reclassify_email(
    message_id: str,
    current_user: HRUserDep,
    email_repo: EmailRepositoryDep,
) -> MessageListItem:
    """Reclassify a needs_review email and mark as reviewed.

    Runs the classification pipeline on the given email and updates
    its status. Only emails with processing_status='needs_review' can
    be reclassified.

    Args:
        message_id: The UUID of the email to reclassify.
        current_user: The authenticated user.
        email_repo: The email repository.

    Returns:
        MessageListItem with updated classification info.
    """
    from sqlalchemy import select

    from src.modules.gmail.domain.entities import (
        EmailMessage as EmailMessageEntity,
    )

    # Fetch the email
    statement = select(EmailMessageEntity).where(  # type: ignore[arg-type]
        EmailMessageEntity.id == message_id
    )
    # type: ignore[arg-type]
    result = await email_repo.session.execute(statement)
    email = result.scalar_one_or_none()

    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")

    if email.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # HR may retry both low-confidence and provider-unavailable work items.
    if email.processing_status not in {"needs_review", "ai_unavailable", "permanently_failed"}:
        raise HTTPException(
            status_code=400,
            detail=(f"Email status is '{email.processing_status}', expected a reviewable item"),
        )

    from src.modules.gmail.container import build_classification_service

    classification_service = await build_classification_service(email_repo.session)
    await classification_service.classify_single_email(
        user_id=current_user.id,
        email=email,
    )
    await email_repo.session.commit()

    return MessageListItem(
        id=str(email.id),
        gmail_message_id=email.gmail_message_id,
        gmail_thread_id=email.gmail_thread_id,
        subject=email.subject,
        sender_email=email.sender_email,
        sender_name=email.sender_name,
        recipient_emails=email.recipient_emails,
        cc_emails=email.cc_emails,
        received_at=email.received_at.isoformat(),
        snippet=email.snippet,
        label_ids=email.label_ids,
        has_attachments=email.has_attachments,
        category=email.category,
        processing_status=email.processing_status,
    )


# ---------------------------------------------------------------------------
@router.post("/review/emails/{message_id}/classify-manually")
async def classify_email_manually(
    message_id: UUID,
    category: str,
    current_user: CurrentUserDep,
    email_repo: EmailRepositoryDep,
) -> dict[str, str]:
    """Let HR classify a provider-pending email without AI."""
    from sqlalchemy import select

    from src.modules.gmail.domain.entities import EmailMessage as EmailMessageEntity
    from src.modules.gmail.domain.enums import EmailCategory

    try:
        EmailCategory(category)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid email category") from exc
    result = await email_repo.session.execute(
        select(EmailMessageEntity).where(  # type: ignore[arg-type]
            EmailMessageEntity.id == message_id
        )
    )
    email = result.scalar_one_or_none()
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    if email.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if email.processing_status not in {"ai_unavailable", "permanently_failed", "needs_review"}:
        raise HTTPException(status_code=400, detail="Email is not awaiting manual recovery")
    email.category = category
    email.processing_status = "classified"
    email.processing_error = None
    email.next_retry_at = None
    email.retry_count = 0
    email.is_permanently_failed = False
    email_repo.session.add(email)
    await email_repo.session.commit()
    return {"id": str(email.id), "category": category, "processing_status": email.processing_status}


# Outbound Email endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/outbound",
    response_model=OutboundEmailResponse,
    responses={
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def create_outbound_email(
    body: OutboundEmailCreateRequest,
    current_user: CurrentUserDep,
    outbound_service: OutboundEmailServiceDep,
) -> OutboundEmailResponse:
    """Create a new outbound email command (pending).

    Does NOT send the email. Generates an idempotency key so
    duplicate creation requests return the existing record.

    Args:
        body: The outbound email creation request.
        current_user: The authenticated HR user.
        outbound_service: The outbound email service.

    Returns:
        OutboundEmailResponse with the created record.
    """
    # Resolve recipient_email: prefer to[0] from frontend, fall back to legacy field
    import json

    recipient_email = body.recipient_email
    if body.to and len(body.to) > 0:
        recipient_email = body.to[0]
    if not recipient_email:
        raise HTTPException(status_code=422, detail="recipient_email is required")

    # Resolve body_html: use body_html if provided, else wrap body_text in basic HTML
    body_html = body.body_html
    if not body_html and body.body_text:
        escaped = body.body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body_html = f"<div>{escaped}</div>"
    if not body_html:
        raise HTTPException(status_code=422, detail="body_html is required")

    # Serialize cc to JSON for storage
    cc_recipients = json.dumps(body.cc) if body.cc else None

    result = await outbound_service.create_outbound(
        candidate_id=body.candidate_id,
        recipient_email=recipient_email,
        subject=body.subject,
        body_html=body_html,
        created_by_user_id=current_user.id,
        hr_user=current_user,
        cc_recipients=cc_recipients,
        reply_to_message_id=body.reply_to_message_id,
    )
    return OutboundEmailResponse(
        id=result.id,
        candidate_id=result.candidate_id,
        subject=result.subject,
        recipient_email=result.recipient_email,
        sender_email=result.sender_email,
        status=result.status,
        gmail_message_id=result.gmail_message_id,
        gmail_thread_id=result.gmail_thread_id,
        error_message=result.error_message,
        retry_count=result.retry_count,
        max_retries=result.max_retries,
        last_retry_at=result.last_retry_at,
        created_by_user_id=result.created_by_user_id,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


@router.post(
    "/outbound/{outbound_id}/send",
    response_model=OutboundEmailResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def send_outbound_email(
    outbound_id: UUID,
    current_user: CurrentUserDep,
    outbound_service: OutboundEmailServiceDep,
) -> OutboundEmailResponse:
    """Send a pending outbound email immediately.

    Uses the Organization Google Connection token to send.
    On auth failure, marks the connection as reauthorization_required.

    Args:
        outbound_id: The UUID of the outbound email to send.
        current_user: The authenticated HR user.
        outbound_service: The outbound email service.

    Returns:
        OutboundEmailResponse with updated status.
    """
    result = await outbound_service.send_outbound(
        outbound_id,
        hr_user=current_user,
    )
    return OutboundEmailResponse(
        id=result.id,
        candidate_id=result.candidate_id,
        subject=result.subject,
        recipient_email=result.recipient_email,
        sender_email=result.sender_email,
        status=result.status,
        gmail_message_id=result.gmail_message_id,
        gmail_thread_id=result.gmail_thread_id,
        error_message=result.error_message,
        retry_count=result.retry_count,
        max_retries=result.max_retries,
        last_retry_at=result.last_retry_at,
        created_by_user_id=result.created_by_user_id,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


@router.post(
    "/outbound/{outbound_id}/retry",
    response_model=OutboundEmailResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def retry_outbound_email(
    outbound_id: UUID,
    current_user: CurrentUserDep,
    outbound_service: OutboundEmailServiceDep,
) -> OutboundEmailResponse:
    """Retry a failed outbound email.

    Validates the email can be retried (not sent, not exceeded
    max retries), then re-sends via the Organization Google Connection.

    Args:
        outbound_id: The UUID of the outbound email to retry.
        current_user: The authenticated HR user.
        outbound_service: The outbound email service.

    Returns:
        OutboundEmailResponse with updated status.
    """
    result = await outbound_service.retry_outbound(
        outbound_id,
        hr_user=current_user,
    )
    return OutboundEmailResponse(
        id=result.id,
        candidate_id=result.candidate_id,
        subject=result.subject,
        recipient_email=result.recipient_email,
        sender_email=result.sender_email,
        status=result.status,
        gmail_message_id=result.gmail_message_id,
        gmail_thread_id=result.gmail_thread_id,
        error_message=result.error_message,
        retry_count=result.retry_count,
        max_retries=result.max_retries,
        last_retry_at=result.last_retry_at,
        created_by_user_id=result.created_by_user_id,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


@router.get(
    "/outbound/{outbound_id}",
    response_model=OutboundEmailResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_outbound_email(
    outbound_id: UUID,
    current_user: CurrentUserDep,
    outbound_service: OutboundEmailServiceDep,
) -> OutboundEmailResponse:
    """Get an outbound email by ID.

    Args:
        outbound_id: The UUID of the outbound email.
        current_user: The authenticated HR user.
        outbound_service: The outbound email service.

    Returns:
        OutboundEmailResponse with the record details.

    Raises:
        404 if the outbound email is not found.
    """
    result = await outbound_service.get_outbound(outbound_id)
    return OutboundEmailResponse(
        id=result.id,
        candidate_id=result.candidate_id,
        subject=result.subject,
        recipient_email=result.recipient_email,
        sender_email=result.sender_email,
        status=result.status,
        gmail_message_id=result.gmail_message_id,
        gmail_thread_id=result.gmail_thread_id,
        error_message=result.error_message,
        retry_count=result.retry_count,
        max_retries=result.max_retries,
        last_retry_at=result.last_retry_at,
        created_by_user_id=result.created_by_user_id,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


@router.get(
    "/candidates/{candidate_id}/outbound",
    response_model=OutboundEmailListResponse,
    responses={
        401: {"model": ErrorResponse},
    },
)
async def list_candidate_outbound_emails(
    candidate_id: UUID,
    current_user: CurrentUserDep,
    outbound_service: OutboundEmailServiceDep,
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
) -> OutboundEmailListResponse:
    """List outbound emails for a candidate.

    Args:
        candidate_id: The UUID of the candidate.
        current_user: The authenticated HR user.
        outbound_service: The outbound email service.
        page: Page number (1-indexed).
        page_size: Items per page.

    Returns:
        OutboundEmailListResponse with paginated results.
    """
    items, total = await outbound_service.list_for_candidate(
        candidate_id,
        page=page,
        page_size=page_size,
    )
    return OutboundEmailListResponse(
        items=[
            OutboundEmailResponse(
                id=item.id,
                candidate_id=item.candidate_id,
                subject=item.subject,
                recipient_email=item.recipient_email,
                sender_email=item.sender_email,
                status=item.status,
                gmail_message_id=item.gmail_message_id,
                gmail_thread_id=item.gmail_thread_id,
                error_message=item.error_message,
                retry_count=item.retry_count,
                max_retries=item.max_retries,
                last_retry_at=item.last_retry_at,
                created_by_user_id=item.created_by_user_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
