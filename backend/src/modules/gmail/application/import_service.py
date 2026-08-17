"""HistoricalImportService for on-demand import of historical recruitment emails.

Orchestrates the preview, execution, progress tracking, and cancellation of
historical email import from the Organization Shared Google Account. The import
operates on a bounded time window (7 or 30 days), reads only INBOX messages,
uses the same AI Automation pipeline as live sync (only ``job_application`` continues
to Backbone Flow), and never modifies the live Gmail history cursor.

Import state is stored in Redis so the ARQ worker and API endpoints can
coordinate on the same job: progress, cancellation, and result metadata.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.gmail.domain.exceptions import GmailImportException

if TYPE_CHECKING:
    import httpx

    # Redis client is not imported at runtime to avoid import-order issues.
    import redis.asyncio as redis

    from src.modules.gmail.infrastructure.audit_logger import AuditLogger
    from src.modules.gmail.infrastructure.config import GmailSettings
    from src.modules.gmail.infrastructure.email_repository import EmailRepository
    from src.modules.gmail.infrastructure.gmail_adapter import (
        GmailAdapter,
        GmailMessageMetadata,
    )
    from src.modules.gmail.infrastructure.sync_cursor_repository import (
        SyncCursorRepository,
    )
    from src.modules.identity.infrastructure.connection_state_repository import (
        OrganizationGoogleConnectionRepository,
    )
    from src.modules.identity.infrastructure.crypto_utils import CryptoUtils

logger = logging.getLogger(__name__)

# Redis key prefixes for historical import state.
_REDIS_PREFIX = "gmail:historical_import"
_REDIS_JOB_KEY = f"{_REDIS_PREFIX}:job"
_REDIS_PROGRESS_KEY = f"{_REDIS_PREFIX}:progress"
_REDIS_CANCEL_FLAG = f"{_REDIS_PREFIX}:cancel"
_REDIS_RESULT_KEY = f"{_REDIS_PREFIX}:result"

IMPORT_ALLOWED_DAYS = frozenset({7, 30})
IMPORT_BATCH_SIZE = 50
IMPORT_REDIS_TTL_SECONDS = 86400  # 24-hour TTL for import state keys.


@dataclass
class ImportPreview:
    """Preview of a historical import operation.

    Attributes:
        days: The requested time window in days.
        estimated_count: Estimated number of importable messages.
        already_imported_count: Messages in the window already imported.
        query_window_start: ISO timestamp of the window start.
        query_window_end: ISO timestamp of the window end.
    """

    days: int
    estimated_count: int
    already_imported_count: int
    query_window_start: str
    query_window_end: str


@dataclass
class ImportStatus:
    """Current status of a running or completed import.

    Attributes:
        job_id: UUID string for the job, or None.
        status: One of 'running', 'completed', 'cancelled', 'failed', 'none'.
        days: The time window of the job, if any.
        total_count: Total messages found for the window.
        processed_count: Messages processed so far / final.
        job_application_count: Messages classified as Job Applications that entered Backbone Flow.
        errors: Count of errors encountered.
        started_at: ISO timestamp when the job started.
        completed_at: ISO timestamp when the job finished or None.
        error_message: Final error message if status == 'failed'.
    """

    job_id: str | None = None
    status: str = "none"
    days: int | None = None
    total_count: int = 0
    processed_count: int = 0
    job_application_count: int = 0
    errors: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None


class HistoricalImportService:
    """Manages on-demand historical email import operations.

    Uses Redis for state coordination between the API layer and the ARQ worker.
    The live sync cursor is never touched; a separate deduplication check
    (via ``get_by_gmail_ids``) prevents re-importing already-persisted messages.

    Args:
        session: Async database session for persistence.
        gmail_adapter: Gmail API adapter for fetching messages.
        email_repo: Repository for persisting email messages.
        sync_cursor_repo: Repository for sync cursor management.
        connection_repo: Repository for the singleton Organization Google Connection.
        crypto: AES-256-GCM encryption utilities for token decryption.
        audit_logger: Structured audit logger for operation tracking.
        settings: Gmail module configuration.
        redis_client: Async Redis client for job state coordination.
        http_client: HTTP client for Gmail API calls.
        client_id: Google OAuth2 client ID for token refresh.
        client_secret: Google OAuth2 client secret for token refresh.
    """

    def __init__(
        self,
        session: AsyncSession,
        gmail_adapter: GmailAdapter,
        email_repo: EmailRepository,
        sync_cursor_repo: SyncCursorRepository,
        connection_repo: OrganizationGoogleConnectionRepository,
        crypto: CryptoUtils,
        audit_logger: AuditLogger,
        settings: GmailSettings,
        redis_client: redis.Redis,
        http_client: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._session = session
        self._gmail_adapter = gmail_adapter
        self._email_repo = email_repo
        self._sync_cursor_repo = sync_cursor_repo
        self._connection_repo = connection_repo
        self._crypto = crypto
        self._audit_logger = audit_logger
        self._settings = settings
        self._redis = redis_client
        self._http_client = http_client
        self._client_id = client_id
        self._client_secret = client_secret

        # Connection identity generation captured at job start for integrity
        # verification throughout the job lifecycle.
        self._connection_generation: tuple[datetime | None, str | None, str | None] | None = None

    # ------------------------------------------------------------------
    # Redis typed helpers (decode_responses=True -> str values)
    # ------------------------------------------------------------------

    async def _hgetall_str(self, key: str) -> dict[str, str]:
        """Wrap hgetall, casting bytes keys/values to str.

        With ``decode_responses=True`` the actual runtime type is
        ``dict[str, str]`` but mypy stubs still assume ``dict[bytes, bytes]``.
        """
        raw = await self._redis.hgetall(key)  # type: ignore[misc]
        return {
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in raw.items()
        }

    async def _hset_str(self, key: str, mapping: dict[str, str]) -> None:
        """Wrap hset with str mapping (decode_responses=True compat)."""
        await self._redis.hset(key, mapping=mapping)  # type: ignore[misc]

    async def _hset_field_str(self, key: str, field: str, value: str) -> None:
        """Wrap hset for a single str field (decode_responses=True compat)."""
        await self._redis.hset(key, field, value)  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def preview_import(self, days: int, user_id: UUID) -> ImportPreview:
        """Preview the number of importable emails in the given time window.

        Queries Gmail to estimate how many INBOX messages fall in the window and
        have not already been imported. Does not persist anything.

        Args:
            days: Time window in days (7 or 30).
            user_id: The UUID of the HR user requesting the preview.

        Returns:
            ImportPreview with estimated counts.

        Raises:
            GmailImportException: If days is not in {7, 30} or connection invalid.
        """
        access_token = await self._resolve_access_token(user_id)
        if access_token is None:
            raise GmailImportException(
                "No valid access token available; connection may be disconnected."
            )
        window_start, window_end = self._build_time_window(days)
        query = self._build_query(window_start, window_end)

        # Fetch message IDs in the window (first page, max 500 for preview).
        all_ids = await self._fetch_all_message_ids(access_token, query)
        all_ids = all_ids[:500]

        if not all_ids:
            return ImportPreview(
                days=days,
                estimated_count=0,
                already_imported_count=0,
                query_window_start=window_start.isoformat(),
                query_window_end=window_end.isoformat(),
            )

        # Determine which messages are already imported.
        existing = await self._email_repo.get_by_gmail_ids(all_ids)
        existing_ids = {e.gmail_message_id for e in existing}
        estimated_new = len(set(all_ids) - existing_ids)

        return ImportPreview(
            days=days,
            estimated_count=estimated_new,
            already_imported_count=len(existing_ids),
            query_window_start=window_start.isoformat(),
            query_window_end=window_end.isoformat(),
        )

    async def start_import(self, days: int, user_id: UUID) -> str:
        """Start a historical import job by recording state in Redis.

        Validates that no import is already running, then records the job
        metadata in Redis so the ARQ worker can pick it up.

        Args:
            days: Time window in days (7 or 30).
            user_id: The UUID of the HR user starting the import.

        Returns:
            The job_id UUID string.

        Raises:
            GmailImportException: If an import is already running or days invalid.
        """
        if days not in IMPORT_ALLOWED_DAYS:
            raise GmailImportException(
                f"Invalid time window: {days} days. Allowed: {sorted(IMPORT_ALLOWED_DAYS)}"
            )

        # Check if an import is already running.
        current = await self._hgetall_str(_REDIS_JOB_KEY)
        if current:
            existing_status = current.get("status", "none")
            if existing_status == "running":
                job_id = current.get("job_id", "")
                raise GmailImportException(
                    f"An import job ({job_id}) is already running. "
                    "Cancel it or wait for it to complete before starting a new one."
                )

        # Validate connection.
        access_token = await self._resolve_access_token(user_id)
        if not access_token:
            raise GmailImportException("No valid Google connection available")

        job_id = str(uuid4())
        now_ts = str(time.time())

        job_metadata: dict[str, str] = {
            "job_id": job_id,
            "status": "running",
            "days": str(days),
            "user_id": str(user_id),
            "started_at": now_ts,
            "total_count": "0",
            "processed_count": "0",
            "job_application_count": "0",
            "errors": "0",
        }

        await self._hset_str(_REDIS_JOB_KEY, job_metadata)
        await self._redis.expire(_REDIS_JOB_KEY, IMPORT_REDIS_TTL_SECONDS)
        await self._redis.delete(_REDIS_PROGRESS_KEY, _REDIS_CANCEL_FLAG, _REDIS_RESULT_KEY)

        logger.info(
            "Historical import job %s started: %d days by user %s",
            job_id,
            days,
            user_id,
        )

        await self._audit_logger.log_operation(
            operation_type="historical_import_start",
            user_id=user_id,
            message_count=0,
            success=True,
            metadata={"job_id": job_id, "days": days},
        )

        return job_id

    async def get_import_status(self) -> ImportStatus:
        """Return the current/last import status from Redis.

        Returns:
            ImportStatus reflecting the current or most recent job.
        """
        job = await self._hgetall_str(_REDIS_JOB_KEY)
        if not job:
            return ImportStatus(status="none")

        job_id = job.get("job_id", "")
        status = job.get("status", "none")
        days_str = job.get("days", "")
        started_at = job.get("started_at", "")
        completed_at = job.get("completed_at", "")

        progress = await self._hgetall_str(_REDIS_PROGRESS_KEY)
        total_count = int(progress.get("total_count", job.get("total_count", "0")))
        processed_count = int(progress.get("processed_count", job.get("processed_count", "0")))
        job_application_count = int(
            progress.get("job_application_count", job.get("job_application_count", "0"))
        )
        errors = int(progress.get("errors", job.get("errors", "0")))
        error_message = progress.get("error_message", "") or None

        return ImportStatus(
            job_id=job_id or None,
            status=status,
            days=int(days_str) if days_str else None,
            total_count=total_count,
            processed_count=processed_count,
            job_application_count=job_application_count,
            errors=errors,
            started_at=started_at or None,
            completed_at=completed_at or None,
            error_message=error_message,
        )

    async def cancel_import(self, user_id: UUID) -> bool:
        """Request cancellation of the running import job.

        Sets a cancellation flag in Redis. The ARQ worker checks this flag
        between batches and stops gracefully.

        Args:
            user_id: The UUID of the HR user requesting cancellation.

        Returns:
            True if a running job was found and cancellation was requested.
        """
        job = await self._hgetall_str(_REDIS_JOB_KEY)
        if not job:
            return False

        current_status = job.get("status", "")
        if current_status != "running":
            return False

        await self._redis.set(_REDIS_CANCEL_FLAG, "1", ex=IMPORT_REDIS_TTL_SECONDS)

        job_id = job.get("job_id", "")

        logger.info(
            "Cancellation requested for historical import job %s by user %s",
            job_id,
            user_id,
        )

        await self._audit_logger.log_operation(
            operation_type="historical_import_cancel",
            user_id=user_id,
            message_count=0,
            success=True,
            metadata={"job_id": job_id},
        )

        return True

    # ------------------------------------------------------------------
    # Worker-side methods (called from ARQ job)
    # ------------------------------------------------------------------

    async def process_import_job(self) -> dict[str, Any]:
        """Execute the historical import job (called by the ARQ worker).

        Reads job metadata from Redis, fetches messages from Gmail in batches
        within the time window, persists them, classifies them, and tracks
        progress in Redis.

        Returns:
            A summary dict with total, processed, job_application_count, errors.
        """
        job = await self._hgetall_str(_REDIS_JOB_KEY)
        if not job:
            logger.warning("Historical import job metadata not found in Redis")
            return {"status": "no_job"}

        job_status = job.get("status", "")
        if job_status != "running":
            logger.info(
                "Historical import job not in 'running' state (%s), skipping",
                job_status,
            )
            return {"status": job_status}

        job_id = job.get("job_id", "")
        days = int(job.get("days", "7"))
        user_id_str = job.get("user_id", "")

        try:
            user_id = UUID(user_id_str) if user_id_str else None
        except ValueError:
            user_id = None

        if user_id is None:
            await self._mark_failed(job_id, "Invalid or missing user_id")
            return {"status": "failed", "error": "Invalid user_id"}

        access_token = await self._resolve_access_token(user_id)
        if not access_token:
            await self._mark_failed(job_id, "Could not resolve access token")
            return {"status": "failed", "error": "No access token"}

        # Capture the connection generation for integrity checks.
        self._connection_generation = await self._capture_connection_generation()

        window_start, window_end = self._build_time_window(days)
        query = self._build_query(window_start, window_end)

        total_processed = 0
        total_job_applications = 0
        total_errors = 0

        try:
            all_ids = await self._fetch_all_message_ids(access_token, query)
            total_count = len(all_ids)

            # If generation capture failed, the connection is gone.
            if self._connection_generation is None:
                await self._mark_failed(job_id, "Connection unavailable at job start")
                return {"status": "failed", "error": "No connection"}

            await self._hset_field_str(_REDIS_JOB_KEY, "total_count", str(total_count))
            await self._hset_field_str(_REDIS_PROGRESS_KEY, "total_count", str(total_count))

            if total_count == 0:
                logger.info(
                    "Historical import job %s: no messages in window",
                    job_id,
                )
                await self._mark_final(
                    job_id, "completed", total_processed, total_job_applications, total_errors
                )
                return {
                    "status": "completed",
                    "total": 0,
                    "processed": 0,
                    "job_applications": 0,
                }

            for i in range(0, total_count, IMPORT_BATCH_SIZE):
                if await self._is_cancelled():
                    logger.info(
                        "Historical import job %s cancelled at batch %d",
                        job_id,
                        i // IMPORT_BATCH_SIZE,
                    )
                    await self._mark_final(
                        job_id,
                        "cancelled",
                        total_processed,
                        total_job_applications,
                        total_errors,
                    )
                    return {
                        "status": "cancelled",
                        "total": total_count,
                        "processed": total_processed,
                        "job_applications": total_job_applications,
                    }

                # Verify connection identity hasn't changed (disconnect, reconnect).
                if not await self._verify_connection_integrity():
                    logger.info(
                        "Historical import job %s aborted: connection changed",
                        job_id,
                    )
                    await self._mark_failed(
                        job_id,
                        "Connection changed during import",
                    )
                    return {
                        "status": "failed",
                        "error": "Connection changed during import",
                        "total": total_count,
                        "processed": total_processed,
                    }

                batch_ids = all_ids[i : i + IMPORT_BATCH_SIZE]

                # Deduplicate: skip already-imported messages.
                existing = await self._email_repo.get_by_gmail_ids(batch_ids)
                existing_ids = {e.gmail_message_id for e in existing}
                new_ids = [mid for mid in batch_ids if mid not in existing_ids]

                if not new_ids:
                    continue

                for msg_id in new_ids:
                    try:
                        metadata = await self._gmail_adapter.get_single_message_metadata(
                            access_token, msg_id
                        )
                        if metadata is None:
                            total_errors += 1
                            continue

                        # Only process INBOX messages.
                        if "INBOX" not in (metadata.label_ids or []):
                            continue

                        entity = self._metadata_to_entity(user_id, metadata)
                        await self._email_repo.batch_upsert([entity])
                        total_processed += 1

                    except Exception as exc:
                        total_errors += 1
                        logger.error(
                            "Failed to process message %s in import: %s",
                            msg_id,
                            exc,
                        )

                await self._hset_str(
                    _REDIS_PROGRESS_KEY,
                    {
                        "processed_count": str(total_processed),
                        "errors": str(total_errors),
                    },
                )

            # Classify newly imported emails.
            if total_processed > 0 and self._settings.classification_enabled:
                try:
                    total_job_applications = await self._classify_recent_emails(
                        user_id, total_processed
                    )
                except Exception as exc:
                    logger.error(
                        "Classification after historical import failed: %s",
                        exc,
                    )

            await self._hset_field_str(
                _REDIS_PROGRESS_KEY, "job_application_count", str(total_job_applications)
            )

            await self._mark_final(
                job_id, "completed", total_processed, total_job_applications, total_errors
            )

            logger.info(
                "Historical import job %s completed: %d processed, %d Job Applications, %d errors",
                job_id,
                total_processed,
                total_job_applications,
                total_errors,
            )

            return {
                "status": "completed",
                "total": total_count,
                "processed": total_processed,
                "job_applications": total_job_applications,
                "errors": total_errors,
            }

        except Exception as exc:
            logger.error(
                "Historical import job %s failed: %s",
                job_id,
                exc,
                exc_info=True,
            )
            await self._mark_failed(job_id, str(exc))
            return {"status": "failed", "error": str(exc)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_access_token(self, user_id: UUID) -> str | None:
        """Resolve a valid access token from the Organization Google Connection.

        Handles token refresh if expired.

        Args:
            user_id: Fallback user ID for audit/logging.

        Returns:
            Decrypted access token string, or None if unavailable.
        """
        connection = await self._connection_repo.get_singleton()
        if connection is None or connection.status != "connected":
            return None

        if not connection.access_token_enc:
            return None

        # Refresh if expired.
        if connection.token_expires_at and connection.token_expires_at <= datetime.now(UTC):
            return await self._refresh_connection_token(connection)

        raw = connection.access_token_enc
        return self._crypto.decrypt(raw) if raw else None

    async def _refresh_connection_token(
        self,
        connection: Any,
    ) -> str | None:
        """Refresh the Organization Google Connection access token.

        Args:
            connection: The OrganizationGoogleConnection entity.

        Returns:
            New access token string, or None on failure.
        """
        try:
            if not connection.refresh_token_enc:
                logger.error("No refresh token in connection for historical import")
                return None

            refresh_token = self._crypto.decrypt(connection.refresh_token_enc)
            if not refresh_token:
                return None

            client_secret = self._client_secret
            if connection.client_secret_enc:
                decrypted_secret = self._crypto.decrypt(connection.client_secret_enc)
                if decrypted_secret:
                    client_secret = decrypted_secret

            new_access_token, expires_at = await self._gmail_adapter.refresh_access_token(
                refresh_token=refresh_token,
                client_id=self._client_id,
                client_secret=client_secret,
            )

            connection.access_token_enc = self._crypto.encrypt(new_access_token)
            connection.token_expires_at = expires_at
            connection.updated_at = datetime.now(UTC)
            await self._connection_repo.upsert_singleton(connection)

            return new_access_token

        except Exception as exc:
            logger.error(
                "Token refresh for historical import failed: %s",
                exc,
            )
            return None

    async def _fetch_all_message_ids(self, access_token: str, query: str) -> list[str]:
        """Fetch all message IDs matching a query via pagination.

        Uses the GmailAdapter's public ``list_message_ids`` method to
        iterate through pages of results.

        Args:
            access_token: Valid Gmail access token.
            query: Gmail search query string.

        Returns:
            List of Gmail message ID strings.
        """
        all_ids: list[str] = []
        page_token: str | None = None

        for _ in range(100):  # Safety limit.
            if await self._is_cancelled():
                break

            stubs, page_token = await self._gmail_adapter.list_message_ids(
                access_token,
                query=query,
                max_results=100,
                page_token=page_token,
            )
            for stub in stubs:
                all_ids.append(stub["id"])

            if not page_token:
                break

        return all_ids

    async def _capture_connection_generation(
        self,
    ) -> tuple[datetime | None, str | None, str | None] | None:
        """Capture the current connection identity for integrity checks.

        Reads the Organization Google Connection from the database and captures
        (updated_at, status, email). If the connection changes during an import
        job (disconnect, reconnect, account switch), the generation will differ
        and the job will abort.

        Returns:
            A tuple of (updated_at, status, email) or None if no connection.
        """
        connection = await self._connection_repo.get_singleton()
        if connection is None:
            return None
        return (
            connection.updated_at,
            connection.status,
            connection.email,
        )

    async def _verify_connection_integrity(self) -> bool:
        """Verify the connection identity has not changed since job start.

        Re-reads the connection from the database and compares its identity
        (updated_at, status, email) against the captured generation. If the
        connection was disconnected or switched to a different account during
        the job, this returns False and the job should abort.

        Returns:
            True if the connection is still valid with the same identity.
        """
        if self._connection_generation is None:
            return False
        current = await self._capture_connection_generation()
        return current == self._connection_generation

    async def _is_cancelled(self) -> bool:
        """Check whether cancellation has been requested.

        Returns:
            True if the cancel flag is set in Redis.
        """
        val = await self._redis.get(_REDIS_CANCEL_FLAG)
        return val is not None

    async def _cleanup_job_state(self) -> None:
        """Clean up all Redis state for the current import job.

        Deletes job metadata, progress, cancel flag, and result keys.
        Used when the ARQ enqueue fails after job metadata was written,
        to prevent zombie 'running' state with no worker.
        """
        await self._redis.delete(
            _REDIS_JOB_KEY,
            _REDIS_PROGRESS_KEY,
            _REDIS_CANCEL_FLAG,
            _REDIS_RESULT_KEY,
        )
        self._connection_generation = None

    async def _mark_final(
        self,
        job_id: str,
        status: str,
        processed: int,
        job_applications: int,
        errors: int,
    ) -> None:
        """Mark the job as completed/cancelled in Redis.

        Args:
            job_id: The job UUID.
            status: 'completed' or 'cancelled'.
            processed: Total messages processed.
            job_applications: Messages classified as Job Applications.
            errors: Error count.
        """
        now_ts = str(time.time())
        await self._hset_str(
            _REDIS_JOB_KEY,
            {
                "status": status,
                "completed_at": now_ts,
            },
        )
        await self._hset_str(
            _REDIS_PROGRESS_KEY,
            {
                "processed_count": str(processed),
                "job_application_count": str(job_applications),
                "errors": str(errors),
            },
        )
        await self._hset_str(
            _REDIS_RESULT_KEY,
            {
                "job_id": job_id,
                "status": status,
                "processed_count": str(processed),
                "job_application_count": str(job_applications),
                "errors": str(errors),
                "completed_at": now_ts,
            },
        )
        await self._redis.expire(_REDIS_RESULT_KEY, IMPORT_REDIS_TTL_SECONDS)
        await self._redis.delete(_REDIS_CANCEL_FLAG)

    async def _mark_failed(self, job_id: str, error_message: str) -> None:
        """Mark the job as failed in Redis.

        Args:
            job_id: The job UUID.
            error_message: Description of the failure.
        """
        now_ts = str(time.time())
        await self._hset_str(
            _REDIS_JOB_KEY,
            {
                "status": "failed",
                "completed_at": now_ts,
            },
        )
        await self._hset_str(
            _REDIS_PROGRESS_KEY,
            {
                "error_message": error_message[:500],
            },
        )

    async def _classify_recent_emails(self, user_id: UUID, limit: int) -> int:
        """Run classification on the most recently imported emails.

        Uses the same ClassificationService as the live sync pipeline.
        Only emails classified as Job Applications are counted.

        Args:
            user_id: The user ID for ownership.
            limit: Max number of recent emails to classify.

        Returns:
            Count of emails classified as Job Applications.
        """
        from sqlmodel import desc, select

        from src.modules.gmail.container import build_classification_service
        from src.modules.gmail.domain.entities import EmailMessage

        stmt = (
            select(EmailMessage)
            .where(EmailMessage.user_id == user_id)
            .order_by(desc(EmailMessage.created_at))
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        imported_emails = list(result.scalars().all())

        if not imported_emails:
            return 0

        classification_service = await build_classification_service(self._session)

        await classification_service.classify_batch(user_id=user_id, emails=imported_emails)

        job_application_count = sum(
            1 for e in imported_emails if getattr(e, "category", None) == "recruitment"
        )

        logger.info(
            "Classified %d imported emails (%d as Job Applications)",
            len(imported_emails),
            job_application_count,
        )

        return job_application_count

    def _metadata_to_entity(self, user_id: UUID, metadata: GmailMessageMetadata) -> Any:
        """Convert GmailMessageMetadata to an EmailMessage domain entity.

        Args:
            user_id: The UUID of the user who owns this email.
            metadata: The Gmail message metadata from the adapter.

        Returns:
            An EmailMessage entity ready for persistence.
        """
        from src.modules.gmail.domain.entities import EmailMessage

        return EmailMessage(
            user_id=user_id,
            gmail_message_id=metadata.id,
            gmail_thread_id=metadata.thread_id,
            subject=(metadata.subject[:998] if metadata.subject else ""),
            sender_email=metadata.sender_email or "",
            sender_name=metadata.sender_name or "",
            recipient_emails=metadata.recipient_emails[:50],
            cc_emails=metadata.cc_emails[:50],
            received_at=(metadata.received_at or datetime.now(UTC)),
            snippet=(metadata.snippet[:200] if metadata.snippet else ""),
            label_ids=metadata.label_ids or [],
            has_attachments=metadata.has_attachments,
        )

    @staticmethod
    def _build_time_window(days: int) -> tuple[datetime, datetime]:
        """Build a time window for the Gmail query.

        Args:
            days: Number of days to look back.

        Returns:
            Tuple of (window_start, window_end) as UTC datetimes.
        """
        now = datetime.now(UTC)
        window_start = now - timedelta(days=days)
        return window_start, now

    @staticmethod
    def _build_query(window_start: datetime, window_end: datetime) -> str:
        """Build a Gmail search query for the time window.

        Args:
            window_start: Start of the window.
            window_end: End of the window.

        Returns:
            Gmail search query string with epoch timestamps.
        """
        start_epoch = int(window_start.timestamp())
        end_epoch = int(window_end.timestamp())
        return f"in:inbox after:{start_epoch} before:{end_epoch}"
