"""OutboundEmailService for the outbound email lifecycle.

Manages creation, sending, retry, and status tracking of outbound emails
using the Organization Google Connection (not individual HR tokens).
"""

from __future__ import annotations

import hashlib
import json
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx

from src.modules.gmail.domain.entities import OutboundEmail
from src.modules.gmail.domain.enums import OutboundEmailStatus
from src.modules.gmail.domain.exceptions import (
    GmailFetchError,
    GmailSendFailedException,
    OrganizationNotConnectedError,
    OutboundEmailAlreadySentError,
    OutboundEmailMaxRetriesExceededError,
    OutboundEmailNotFoundError,
    RateLimitedException,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.modules.gmail.infrastructure.gmail_adapter import GmailAdapter
    from src.modules.gmail.infrastructure.outbound_email_repository import (
        OutboundEmailRepository,
    )
    from src.modules.identity.application.audit_service import AuditService
    from src.modules.identity.infrastructure.connection_state_repository import (
        OrganizationGoogleConnectionRepository,
    )
    from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
    from src.modules.recruitment.infrastructure.repositories import CandidateRepository

logger = logging.getLogger(__name__)


def _make_idempotency_key(
    candidate_id: UUID | None,
    recipient_email: str,
    subject: str,
    body_hash: str,
) -> str:
    """Generate a deterministic idempotency key.

    Uses SHA-256 of canonicalized inputs so the same send command
    always produces the same key.

    Args:
        candidate_id: The candidate UUID or None.
        recipient_email: The recipient email address.
        subject: The email subject.
        body_hash: SHA-256 hex digest of the body content.

    Returns:
        A 64-character hex string.
    """
    raw = f"{candidate_id or ''}:{recipient_email}:{subject}:{body_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OutboundEmailService:
    """Manages the outbound email lifecycle.

    Creates outbound email commands (pending), processes them through
    the Organization Google Connection, and tracks status/results.

    Args:
        session: Async database session.
        outbound_repo: Repository for OutboundEmail persistence.
        connection_repo: Repository for Organization Google Connection.
        candidate_repo: Repository for Candidate lookup.
        gmail_adapter: Gmail API adapter for sending.
        crypto: Encryption utilities for token/secret decryption.
        audit_service: Admin audit service for logging actions.
        oauth_config_client_id: Google OAuth2 client ID (from config).
        http_client: Shared httpx AsyncClient for token refresh.
        audit_action_type: The AuditActionType class (injected to avoid circular import).
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        outbound_repo: OutboundEmailRepository,
        connection_repo: OrganizationGoogleConnectionRepository,
        candidate_repo: CandidateRepository,
        gmail_adapter: GmailAdapter,
        crypto: CryptoUtils,
        audit_service: AuditService,
        oauth_config_client_id: str,
        http_client: httpx.AsyncClient,
        audit_action_type: type[Any],
    ) -> None:
        self._session = session
        self._outbound_repo = outbound_repo
        self._connection_repo = connection_repo
        self._candidate_repo = candidate_repo
        self._gmail_adapter = gmail_adapter
        self._crypto = crypto
        self._audit_service = audit_service
        self._client_id = oauth_config_client_id
        self._http_client = http_client
        self._AuditActionType = audit_action_type

    async def create_outbound(
        self,
        *,
        candidate_id: UUID | None,
        recipient_email: str,
        subject: str,
        body_html: str,
        created_by_user_id: UUID,
        hr_user: Any,
        cc_recipients: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> OutboundEmail:
        """Create an outbound email command with pending status.

        Generates an idempotency key and checks for duplicates.
        Does NOT send the email — only creates the record.

        Args:
            candidate_id: Optional candidate UUID for tracking.
            recipient_email: The target recipient email.
            subject: The email subject line.
            body_html: The HTML email body.
            created_by_user_id: The UUID of the HR user confirming the send.
            hr_user: The User entity for audit logging.
            cc_recipients: Optional JSON array of CC recipient emails.
            reply_to_message_id: Optional Gmail message ID for threading replies.

        Returns:
            The persisted OutboundEmail entity.

        Raises:
            OutboundEmailIdempotencyConflictError: If the exact same
                email was already created (by idempotency key).
        """
        from src.modules.gmail.domain.exceptions import (
            OutboundEmailIdempotencyConflictError,
        )

        body_hash = hashlib.sha256(body_html.encode("utf-8")).hexdigest()
        idempotency_key = _make_idempotency_key(
            candidate_id=candidate_id,
            recipient_email=recipient_email,
            subject=subject,
            body_hash=body_hash,
        )

        # Check for existing by idempotency key
        existing = await self._outbound_repo.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            raise OutboundEmailIdempotencyConflictError(
                f"An outbound email with the same content already exists "
                f"(id={existing.id}, status={existing.status})"
            )

        entity = OutboundEmail(
            idempotency_key=idempotency_key,
            candidate_id=candidate_id,
            subject=subject,
            body_html=body_html,
            recipient_email=recipient_email,
            cc_recipients=cc_recipients,
            reply_to_message_id=reply_to_message_id,
            status=OutboundEmailStatus.pending,
            created_by_user_id=created_by_user_id,
        )
        result = await self._outbound_repo.create(entity)
        await self._session.commit()

        await self._audit_service.log_action(
            admin=hr_user,
            action_type=self._AuditActionType.OUTBOUND_EMAIL_CREATED,
            details={
                "outbound_id": str(result.id),
                "recipient_email": recipient_email,
                "candidate_id": str(candidate_id) if candidate_id else None,
                "subject_preview": subject[:100],
            },
        )

        logger.info(
            "Outbound email created: id=%s, recipient=%s, status=pending",
            result.id,
            recipient_email,
        )
        return result

    async def send_outbound(
        self,
        outbound_id: UUID,
        *,
        hr_user: Any | None = None,
    ) -> OutboundEmail:
        """Send a pending outbound email via Organization Google Connection.

        Uses the Organization's stored Google token, not the HR user's
        personal token. On auth failure, updates the connection status.

        Args:
            outbound_id: The UUID of the outbound email to send.
            hr_user: Optional User entity for audit logging.

        Returns:
            The updated OutboundEmail entity.

        Raises:
            OutboundEmailNotFoundError: If the outbound ID does not exist.
            OutboundEmailAlreadySentError: If already sent.
            OrganizationNotConnectedError: If the org connection is not active.
            GmailSendFailedException: If the Gmail API call fails permanently.
        """
        outbound = await self._outbound_repo.get_by_id(outbound_id)
        if outbound is None:
            raise OutboundEmailNotFoundError(f"Outbound email not found: {outbound_id}")

        if outbound.status == OutboundEmailStatus.sent:
            raise OutboundEmailAlreadySentError(
                f"Outbound email {outbound_id} has already been sent"
            )

        # Get the Organization Google Connection
        connection = await self._connection_repo.get_singleton()
        if connection is None or connection.status != "connected":
            raise OrganizationNotConnectedError("Organization Google Connection is not active")

        # Decrypt the access token
        if not connection.access_token_enc:
            raise OrganizationNotConnectedError("No stored access token")
        access_token = self._crypto.decrypt(connection.access_token_enc)

        # Update status to sending and increment retry count
        new_retry_count = outbound.retry_count + 1
        _updated = await self._outbound_repo.update_status(
            outbound_id,
            status=OutboundEmailStatus.sending,
            retry_count=new_retry_count,
            sender_email=connection.email,
        )
        if _updated is None:
            raise OutboundEmailNotFoundError(f"Outbound email not found: {outbound_id}")
        outbound = _updated
        await self._session.commit()
        # Build the MIME message
        mime_bytes = self._build_mime_message(
            to=[outbound.recipient_email],
            subject=outbound.subject,
            body_html=outbound.body_html,
            cc=json.loads(outbound.cc_recipients) if outbound.cc_recipients else None,
            reply_to_message_id=outbound.reply_to_message_id,
        )

        # Send via GmailAdapter using the org token
        try:
            from src.modules.gmail.infrastructure.gmail_adapter import SentMessageInfo

            sent_info: SentMessageInfo = await self._gmail_adapter.send_message(
                access_token, mime_bytes
            )

            # Update status to sent
            _updated = await self._outbound_repo.update_status(
                outbound_id,
                status=OutboundEmailStatus.sent,
                gmail_message_id=sent_info.message_id,
                gmail_thread_id=sent_info.thread_id,
                sender_email=connection.email,
            )
            if _updated is None:
                raise OutboundEmailNotFoundError(
                    f"Outbound email not found after send: {outbound_id}"
                )
            outbound = _updated
            await self._session.commit()

            # Audit
            if hr_user is not None:
                await self._audit_service.log_action(
                    admin=hr_user,
                    action_type=self._AuditActionType.OUTBOUND_EMAIL_SENT,
                    details={
                        "outbound_id": str(outbound_id),
                        "recipient_email": outbound.recipient_email,
                        "result": "sent",
                        "gmail_message_id": sent_info.message_id,
                    },
                )

            logger.info(
                "Outbound email sent: id=%s, gmail_message_id=%s",
                outbound_id,
                sent_info.message_id,
            )

        except httpx.HTTPStatusError as exc:
            # Only 401 ever reaches this branch in practice (GmailAdapter wraps
            # every other HTTP error into GmailSendFailedException), and an auth
            # failure is not something a manual retry alone fixes.
            outbound = await self._handle_send_error(
                outbound_id=outbound_id,
                outbound=outbound,
                connection=connection,
                status_code=exc.response.status_code,
                error_detail=exc.response.text[:500],
                is_retryable=False,
                hr_user=hr_user,
            )

        except (GmailFetchError, RateLimitedException, TimeoutError) as exc:
            logger.warning(
                "Transient error sending outbound %s, eligible for manual retry: %s",
                outbound_id,
                exc,
            )
            outbound = await self._handle_send_error(
                outbound_id=outbound_id,
                outbound=outbound,
                connection=connection,
                status_code=0,
                error_detail=str(exc),
                is_retryable=True,
                hr_user=hr_user,
            )

        except GmailSendFailedException as exc:
            logger.warning("Permanent send failure for outbound %s: %s", outbound_id, exc)
            outbound = await self._handle_send_error(
                outbound_id=outbound_id,
                outbound=outbound,
                connection=connection,
                status_code=0,
                error_detail=str(exc),
                is_retryable=False,
                hr_user=hr_user,
            )

        except Exception as exc:
            logger.exception("Unexpected error sending outbound %s", outbound_id)
            outbound = await self._handle_send_error(
                outbound_id=outbound_id,
                outbound=outbound,
                connection=connection,
                status_code=0,
                error_detail=str(exc),
                is_retryable=False,
                hr_user=hr_user,
            )

        return outbound

    async def _handle_send_error(
        self,
        *,
        outbound_id: UUID,
        outbound: OutboundEmail,
        connection: Any,
        status_code: int,
        error_detail: str,
        is_retryable: bool,
        hr_user: Any | None = None,
    ) -> OutboundEmail:
        """Handle a send error by updating status and optionally connection.

        On auth failures (401/403), updates the connection status to
        reauthorization_required. For retryable errors, keeps the status
        as pending. For permanent errors, sets status to failed.

        Args:
            outbound_id: The outbound email ID.
            outbound: The current OutboundEmail entity.
            connection: The OrganizationGoogleConnection entity.
            status_code: HTTP status code (0 for non-HTTP errors).
            error_detail: Error detail string.
            is_retryable: Whether the caller classified the underlying
                exception as transient (see send_outbound's except clauses).
            hr_user: Optional User entity for audit logging.

        Returns:
            The updated OutboundEmail entity.

        Raises:
            GmailSendFailedException: If the error is permanent.
        """
        # Auth failure -> update connection to reauthorization_required
        if status_code in (401, 403):
            await self._connection_repo.update_status("reauthorization_required")
            await self._session.commit()
            logger.warning(
                "Organization Google Connection auth failed for outbound %s, "
                "set status to reauthorization_required",
                outbound_id,
            )

        if is_retryable and outbound.retry_count < outbound.max_retries:
            new_status = OutboundEmailStatus.pending
            error_msg = (
                f"Temporary failure (HTTP {status_code}): {error_detail} — awaiting manual retry"
            )
        else:
            new_status = OutboundEmailStatus.failed
            error_msg = f"Send failed (HTTP {status_code}): {error_detail}"

        _updated = await self._outbound_repo.update_status(
            outbound_id,
            status=new_status,
            error_message=error_msg,
        )
        if _updated is None:
            raise OutboundEmailNotFoundError(f"Outbound email not found after error: {outbound_id}")
        outbound = _updated
        await self._session.commit()

        if hr_user is not None:
            await self._audit_service.log_action(
                admin=hr_user,
                action_type=self._AuditActionType.OUTBOUND_EMAIL_FAILED,
                details={
                    "outbound_id": str(outbound_id),
                    "recipient_email": outbound.recipient_email,
                    "result": "failed",
                    "error_code": str(status_code),
                },
            )

        if new_status == OutboundEmailStatus.failed:
            raise GmailSendFailedException(error_msg)

        return outbound

    async def retry_outbound(
        self,
        outbound_id: UUID,
        *,
        hr_user: Any | None = None,
    ) -> OutboundEmail:
        """Retry a failed outbound email.

        Validates that the email can be retried (not already sent,
        not exceeded max retries), then re-sends.

        Args:
            outbound_id: The UUID of the failed outbound email.
            hr_user: Optional User entity for audit logging.

        Returns:
            The updated OutboundEmail entity.

        Raises:
            OutboundEmailNotFoundError: If the outbound does not exist.
            OutboundEmailAlreadySentError: If already sent.
            OutboundEmailMaxRetriesExceededError: If max retries exceeded.
        """
        outbound = await self._outbound_repo.get_by_id(outbound_id)
        if outbound is None:
            raise OutboundEmailNotFoundError(f"Outbound email not found: {outbound_id}")

        if outbound.status == OutboundEmailStatus.sent:
            raise OutboundEmailAlreadySentError(
                f"Outbound email {outbound_id} has already been sent"
            )

        if outbound.retry_count >= outbound.max_retries:
            raise OutboundEmailMaxRetriesExceededError(
                f"Outbound email {outbound_id} has exceeded max retries ({outbound.max_retries})"
            )

        return await self.send_outbound(outbound_id, hr_user=hr_user)

    async def get_outbound(self, outbound_id: UUID) -> OutboundEmail:
        """Get an outbound email by ID.

        Args:
            outbound_id: The UUID of the outbound email.

        Returns:
            The OutboundEmail entity.

        Raises:
            OutboundEmailNotFoundError: If not found.
        """
        outbound = await self._outbound_repo.get_by_id(outbound_id)
        if outbound is None:
            raise OutboundEmailNotFoundError(f"Outbound email not found: {outbound_id}")
        return outbound

    async def list_for_candidate(
        self,
        candidate_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[OutboundEmail], int]:
        """List outbound emails for a candidate.

        Args:
            candidate_id: The candidate UUID.
            page: Page number (1-indexed).
            page_size: Items per page.

        Returns:
            Tuple of (list of OutboundEmail, total count).
        """
        return await self._outbound_repo.list_by_candidate(
            candidate_id=candidate_id,
            page=page,
            page_size=page_size,
        )

    @staticmethod
    def _build_mime_message(
        to: list[str],
        subject: str,
        body_html: str,
        cc: list[str] | None = None,
        reply_to_message_id: str | None = None,
    ) -> bytes:
        """Build an RFC 2822 MIME message for sending.

        Args:
            to: List of recipient email addresses.
            subject: The email subject.
            body_html: The HTML body content.
            cc: Optional list of CC recipient email addresses.
            reply_to_message_id: Optional Gmail message ID for threading (In-Reply-To / References).

        Returns:
            The MIME message as bytes.
        """
        msg = MIMEMultipart("alternative")
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = ", ".join(cc)
        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            msg["References"] = reply_to_message_id
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        return msg.as_bytes()
