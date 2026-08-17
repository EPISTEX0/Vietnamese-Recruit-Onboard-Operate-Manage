"""EmailSyncService for periodic and manual email synchronization.

Orchestrates email fetching from Gmail via polling (ARQ cron) and manual
sync triggers. Handles first-poll logic (7-day lookback), incremental sync
(history_id-based), token refresh on 401, partial failure handling, and
manual sync rate limiting via Redis.

Note: Token handling uses the singleton OrganizationGoogleConnection
for the org-level token; per-user OAuth grants are no longer used.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import redis.asyncio as redis

from src.modules.gmail.domain.entities import EmailMessage, SyncCursor
from src.modules.gmail.domain.exceptions import (
    GmailFetchError,
    GmailNotConnectedException,
    RateLimitedException,
)
from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils

if TYPE_CHECKING:
    from src.modules.gmail.infrastructure.audit_logger import AuditLogger
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

logger = logging.getLogger(__name__)


class EmailSyncService:
    """Orchestrates email fetching from Gmail.

    Handles both scheduled polling (via ARQ cron) and manual sync triggers.
    Implements baseline-only first-poll logic, incremental sync (history_id-based),
    token refresh on 401, partial failure handling, and manual sync rate limiting.

    Args:
        gmail_adapter: Gmail API adapter for fetching messages.
        email_repo: Repository for persisting email messages.
        sync_cursor_repo: Repository for sync cursor management.
        crypto: AES-256-GCM encryption utilities for token decryption.
        audit_logger: Structured audit logger for operation tracking.
        settings: Gmail module configuration.
        redis_client: Async Redis client for manual sync rate limiting.
        client_id: Google OAuth2 client ID for token refresh.
        client_secret: Google OAuth2 client secret for token refresh.
        connection_repo: Repository for the singleton Organization Google
            Connection. If not provided it is constructed from the
            email_repo session at runtime.
    """

    def __init__(
        self,
        gmail_adapter: GmailAdapter,
        email_repo: EmailRepository,
        sync_cursor_repo: SyncCursorRepository,
        crypto: CryptoUtils,
        audit_logger: AuditLogger,
        settings: GmailSettings,
        redis_client: redis.Redis,
        client_id: str,
        client_secret: str,
        connection_repo: OrganizationGoogleConnectionRepository | None = None,
    ) -> None:
        """Initialize EmailSyncService with dependencies.

        Args:
            gmail_adapter: Gmail API adapter for fetching messages.
            email_repo: Repository for persisting email messages.
            sync_cursor_repo: Repository for sync cursor management.
            crypto: AES-256-GCM encryption utilities for token decryption.
            audit_logger: Structured audit logger for operation tracking.
            settings: Gmail module configuration.
            redis_client: Async Redis client for manual sync rate limiting.
            client_id: Google OAuth2 client ID for token refresh.
            client_secret: Google OAuth2 client secret for token refresh.
            connection_repo: Repository for the singleton Organization Google
                Connection. If not provided it is constructed from the
                email_repo session at runtime.
        """
        self._gmail_adapter = gmail_adapter
        self._email_repo = email_repo
        self._sync_cursor_repo = sync_cursor_repo
        self._crypto = crypto
        self._audit_logger = audit_logger
        self._settings = settings
        self._redis = redis_client
        self._client_id = client_id
        self._client_secret = client_secret
        self._connection_repo = connection_repo

    async def _handle_connection_token_refresh(
        self,
        connection: Any,
        connection_repo: Any,
    ) -> str | None:
        """Attempt to refresh the organization connection's access token."""
        try:
            if not connection.refresh_token_enc:
                raise Exception("No refresh token in connection")
            refresh_token = self._crypto.decrypt(connection.refresh_token_enc)

            client_secret = self._client_secret
            if connection.client_secret_enc:
                client_secret = self._crypto.decrypt(connection.client_secret_enc)

            new_access_token, expires_at = await self._gmail_adapter.refresh_access_token(
                refresh_token=refresh_token,
                client_id=self._client_id,
                client_secret=client_secret,
            )

            connection.access_token_enc = self._crypto.encrypt(new_access_token)
            connection.token_expires_at = expires_at
            connection.updated_at = datetime.now(UTC)
            await connection_repo.upsert_singleton(connection)

            logger.info("Successfully refreshed access token for organization connection")
            return new_access_token

        except Exception as exc:
            exc_str = str(exc).lower()
            is_invalid_grant = "invalid_grant" in exc_str or "revoked" in exc_str

            await self._update_ingestion_health("degraded")
            logger.error("Token refresh failed for organization connection: %s", exc)

            if is_invalid_grant or (
                isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 400
            ):
                connection.status = "reauthorization_required"
                connection.updated_at = datetime.now(UTC)
                await connection_repo.upsert_singleton(connection)
                logger.warning("Organization connection transitioned to reauthorization_required")

            audit_user_id = connection.connected_by_user_id
            if audit_user_id is not None:
                await self._audit_logger.log_operation(
                    operation_type="fetch",
                    user_id=audit_user_id,
                    message_count=0,
                    success=False,
                    metadata={"error": "token_refresh_failed", "reason": str(exc)},
                )
            return None

    async def _update_ingestion_health(self, status: str) -> None:
        try:
            await self._redis.set("gmail:health:gmail_ingestion", status, ex=3600)
        except Exception:
            pass

    async def poll_emails(self, user_id: UUID) -> int:
        """Execute a poll cycle to fetch new emails from Gmail.

        Called by the ARQ cron job on schedule. Checks connection status,
        retrieves the access token, fetches emails (initial or incremental),
        and persists them. Handles 401 errors by attempting token refresh.
        """
        connection_repo = self._connection_repo or OrganizationGoogleConnectionRepository(
            self._email_repo.session
        )
        connection = await connection_repo.get_singleton()

        if connection is not None and connection.status != "disconnected":
            if connection.status == "reauthorization_required":
                raise GmailNotConnectedException()

            if not connection.access_token_enc:
                raise GmailNotConnectedException()
            access_token = self._crypto.decrypt(connection.access_token_enc)

            connected_user_id = connection.connected_by_user_id or user_id
            cursor = await self._sync_cursor_repo.get_cursor()

            if connection.token_expires_at and connection.token_expires_at <= datetime.now(UTC):
                new_access_token = await self._handle_connection_token_refresh(
                    connection, connection_repo
                )
                if new_access_token is None:
                    return 0
                access_token = new_access_token

            try:
                count = await self._fetch_and_persist(connected_user_id, access_token, cursor)
                await self._update_ingestion_health("healthy")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401:
                    new_access_token = await self._handle_connection_token_refresh(
                        connection, connection_repo
                    )
                    if new_access_token is None:
                        return 0
                    try:
                        count = await self._fetch_and_persist(
                            connected_user_id, new_access_token, cursor
                        )
                        await self._update_ingestion_health("healthy")
                    except Exception as retry_exc:
                        if isinstance(
                            retry_exc, (RateLimitedException, httpx.HTTPError, TimeoutError)
                        ):
                            await self._update_ingestion_health("degraded")
                        raise
                else:
                    if exc.response.status_code == 400:
                        connection.status = "reauthorization_required"
                        connection.updated_at = datetime.now(UTC)
                        await connection_repo.upsert_singleton(connection)
                    await self._update_ingestion_health("degraded")
                    raise
            except (RateLimitedException, httpx.HTTPError, TimeoutError, GmailFetchError):
                await self._update_ingestion_health("degraded")
                raise

            await self._audit_logger.log_operation(
                operation_type="fetch",
                user_id=connected_user_id,
                message_count=count,
                success=True,
                metadata={"sync_type": "poll"},
            )
            return count

        raise GmailNotConnectedException("Organization Google Connection is not connected")

    async def manual_sync(self, user_id: UUID) -> int:
        """Trigger an immediate email sync outside the regular schedule."""
        await self._check_manual_sync_rate_limit(user_id)

        connection_repo = self._connection_repo or OrganizationGoogleConnectionRepository(
            self._email_repo.session
        )
        connection = await connection_repo.get_singleton()
        if connection is not None and connection.status != "disconnected":
            if connection.status == "reauthorization_required":
                raise GmailNotConnectedException()

            if not connection.access_token_enc:
                raise GmailNotConnectedException()
            access_token = self._crypto.decrypt(connection.access_token_enc)

            connected_user_id = connection.connected_by_user_id or user_id
            cursor = await self._sync_cursor_repo.get_cursor()

            if connection.token_expires_at and connection.token_expires_at <= datetime.now(UTC):
                new_access_token = await self._handle_connection_token_refresh(
                    connection, connection_repo
                )
                if new_access_token is None:
                    return 0
                access_token = new_access_token

            try:
                count = await self._fetch_and_persist(connected_user_id, access_token, cursor)
                await self._update_ingestion_health("healthy")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401:
                    new_access_token = await self._handle_connection_token_refresh(
                        connection, connection_repo
                    )
                    if new_access_token is None:
                        return 0
                    try:
                        count = await self._fetch_and_persist(
                            connected_user_id, new_access_token, cursor
                        )
                        await self._update_ingestion_health("healthy")
                    except Exception as retry_exc:
                        if isinstance(
                            retry_exc, (RateLimitedException, httpx.HTTPError, TimeoutError)
                        ):
                            await self._update_ingestion_health("degraded")
                        raise
                else:
                    if exc.response.status_code == 400:
                        connection.status = "reauthorization_required"
                        connection.updated_at = datetime.now(UTC)
                        await connection_repo.upsert_singleton(connection)
                    await self._update_ingestion_health("degraded")
                    raise
            except (RateLimitedException, httpx.HTTPError, TimeoutError, GmailFetchError):
                await self._update_ingestion_health("degraded")
                raise

            await self._record_manual_sync_timestamp(user_id)
            await self._audit_logger.log_operation(
                operation_type="fetch",
                user_id=connected_user_id,
                message_count=count,
                success=True,
                metadata={"sync_type": "manual"},
            )
            return count

        raise GmailNotConnectedException("Organization Google Connection is not connected")

    async def _fetch_and_persist(
        self, user_id: UUID, access_token: str, cursor: SyncCursor | None
    ) -> int:
        """Fetch emails from Gmail and persist them to the database."""
        latest_history_id: str | None = None

        if cursor is None:
            # First poll: establish baseline history ID, do not import historical emails.
            latest_history_id = await self._gmail_adapter.get_latest_history_id(access_token)
            if latest_history_id:
                await self._sync_cursor_repo.upsert_cursor(latest_history_id)
            return 0
        else:
            # Incremental sync: fetch since last history_id
            try:
                messages, new_history_id = await self._gmail_adapter.fetch_history(
                    access_token=access_token,
                    start_history_id=cursor.history_id,
                    max_results=self._settings.batch_size,
                )
                latest_history_id = new_history_id
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.warning(
                        "History ID %s expired/404. Clearing cursor before bounded recovery.",
                        cursor.history_id,
                    )
                    await self._sync_cursor_repo.clear_cursor()
                    await self._email_repo.clear_incomplete_ingestion_state()
                    days_ago = datetime.now(UTC) - timedelta(days=self._settings.initial_sync_days)
                    epoch_seconds = int(days_ago.timestamp())
                    query = f"after:{epoch_seconds}"

                    messages = await self._gmail_adapter.fetch_messages(
                        access_token=access_token,
                        query=query,
                        max_results=self._settings.batch_size,
                    )
                    if messages:
                        latest_history_id = max(
                            (m.history_id for m in messages if m.history_id),
                            default=None,
                        )
                    else:
                        latest_history_id = await self._gmail_adapter.get_latest_history_id(
                            access_token
                        )
                else:
                    raise

        # Filter: Incremental sync only processes new emails in INBOX
        messages = [m for m in messages if "INBOX" in (m.label_ids or [])]

        if not messages:
            # No new emails — update cursor timestamp if we have a new history_id.
            if latest_history_id and cursor is not None:
                await self._sync_cursor_repo.upsert_cursor(latest_history_id)
            return 0

        # Convert metadata to EmailMessage entities and persist
        email_entities: list[EmailMessage] = []
        failed_message_ids: list[str] = []

        for msg_metadata in messages:
            try:
                entity = self._metadata_to_entity(user_id, msg_metadata)
                email_entities.append(entity)
            except Exception:
                logger.error(
                    "Failed to convert message metadata to entity: gmail_message_id=%s",
                    msg_metadata.id,
                    exc_info=True,
                )
                failed_message_ids.append(msg_metadata.id)

        # Batch upsert successful entities
        persisted_count = 0
        if email_entities:
            persisted_count = await self._email_repo.batch_upsert(email_entities)

        # Handle failed messages: increment retry count and check for permanent failure
        await self._handle_failed_messages(failed_message_ids)

        # Do not advance the cursor when any message failed; retry it next cycle.
        if failed_message_ids or persisted_count < len(email_entities):
            return persisted_count

        # Atomic cursor update: update cursor to latest history_id.
        if latest_history_id:
            await self._sync_cursor_repo.upsert_cursor(latest_history_id)

        # Classify newly synced emails (async, non-blocking).
        # Re-query from DB to get session-attached instances, since
        # batch_upsert() uses Core INSERT and returns transient objects.
        if email_entities and self._settings.classification_enabled:
            await self._classify_new_emails(user_id, [e.gmail_message_id for e in email_entities])

        return persisted_count

    async def _check_manual_sync_rate_limit(self, user_id: UUID) -> None:
        """Check if the user is within the manual sync cooldown period.

        Uses Redis to track the last manual sync timestamp per user.
        Raises RateLimitedException if within the cooldown window.

        Args:
            user_id: The UUID of the user to check.

        Raises:
            RateLimitedException: If the cooldown period has not elapsed.
        """
        key = f"gmail:manual_sync:{user_id}"
        last_sync_str = await self._redis.get(key)

        if last_sync_str is not None:
            last_sync_time = float(last_sync_str)
            elapsed = time.time() - last_sync_time
            cooldown = self._settings.manual_sync_cooldown_seconds

            if elapsed < cooldown:
                remaining = int(cooldown - elapsed)
                raise RateLimitedException(retry_after=max(remaining, 1))

    async def _record_manual_sync_timestamp(self, user_id: UUID) -> None:
        """Record the current timestamp as the last manual sync time in Redis.

        Sets the key with a TTL equal to the cooldown period to auto-expire.

        Args:
            user_id: The UUID of the user who performed the manual sync.
        """
        key = f"gmail:manual_sync:{user_id}"
        await self._redis.set(
            key,
            str(time.time()),
            ex=self._settings.manual_sync_cooldown_seconds,
        )

    async def _handle_failed_messages(self, failed_message_ids: list[str]) -> None:
        """Handle messages that failed during fetch/conversion.

        Increments the retry count for each failed message. If a message
        reaches the permanent failure threshold (5 consecutive failures),
        marks it as permanently failed.

        Args:
            failed_message_ids: List of Gmail message IDs that failed processing.
        """
        if not failed_message_ids:
            return

        try:
            messages = await self._email_repo.get_by_gmail_ids(failed_message_ids)
            for message in messages:
                message.retry_count += 1
                if message.retry_count >= self._settings.permanent_failure_threshold:
                    message.is_permanently_failed = True
                    logger.warning(
                        "Message %s marked as permanently failed after %d consecutive failures",
                        message.gmail_message_id,
                        message.retry_count,
                    )
            await self._email_repo.save_all(messages)
        except Exception:
            logger.error(
                "Failed to bulk update retry counts for %d messages",
                len(failed_message_ids),
                exc_info=True,
            )

    def _metadata_to_entity(self, user_id: UUID, metadata: GmailMessageMetadata) -> EmailMessage:
        """Convert GmailMessageMetadata to an EmailMessage domain entity.

        Maps adapter response fields to the EmailMessage entity fields,
        applying defaults for missing values.

        Args:
            user_id: The UUID of the user who owns this email.
            metadata: The Gmail message metadata from the adapter.

        Returns:
            An EmailMessage entity ready for persistence.
        """
        return EmailMessage(
            user_id=user_id,
            gmail_message_id=metadata.id,
            gmail_thread_id=metadata.thread_id,
            subject=metadata.subject[:998] if metadata.subject else "",
            sender_email=metadata.sender_email or "",
            sender_name=metadata.sender_name or "",
            recipient_emails=metadata.recipient_emails[:50],
            cc_emails=metadata.cc_emails[:50],
            received_at=metadata.received_at or datetime.now(UTC),
            snippet=metadata.snippet[:200] if metadata.snippet else "",
            label_ids=metadata.label_ids or [],
            has_attachments=metadata.has_attachments,
        )

    async def _classify_new_emails(self, user_id: UUID, gmail_message_ids: list[str]) -> None:
        """Classify newly synced emails using the two-tier classification pipeline.

        Re-queries emails from the database to get session-attached instances,
        avoiding integrity errors from transient objects returned by batch_upsert.

        Args:
            user_id: The UUID of the user who owns the emails.
            gmail_message_ids: List of Gmail message IDs to classify.
        """
        try:
            from sqlmodel import select

            from src.modules.gmail.container import build_classification_service
            from src.modules.gmail.domain.entities import EmailMessage as EmailMessageEntity

            # Re-query persisted emails from DB to get session-attached instances
            statement = (
                select(EmailMessageEntity)
                .where(EmailMessageEntity.user_id == user_id)
                .where(EmailMessageEntity.gmail_message_id.in_(gmail_message_ids))  # type: ignore[attr-defined]
                .where(EmailMessageEntity.processing_status == "unprocessed")
            )
            result = await self._email_repo.session.execute(statement)
            emails = list(result.scalars().all())

            if not emails:
                return

            classification_service = await build_classification_service(self._email_repo.session)

            classified_count = await classification_service.classify_batch(
                user_id=user_id, emails=emails
            )
            logger.info(
                "Classified %d/%d new emails for user %s",
                classified_count,
                len(emails),
                user_id,
            )
        except Exception as exc:
            # Classification failure should never break the sync pipeline.
            # Unlike the historical import job (#352), an incremental sync
            # cycle has no per-run status a user watches after the fact --
            # it is cron-driven and re-runs every poll interval, so a
            # persistent misconfiguration keeps landing in this log rather
            # than presenting once as a false "all clear" the way a single
            # completed import job would. exc_info below is that surface.
            logger.error(
                "Email classification failed for user %s: %s",
                user_id,
                exc,
                exc_info=True,
            )
