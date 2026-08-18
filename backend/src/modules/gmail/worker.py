"""ARQ worker configuration for Gmail email polling.

Defines the cron job that periodically fetches new emails for the connected
Organization Google Connection. Runs every GMAIL_POLL_INTERVAL_SECONDS
(default 300 = 5 minutes). The worker exits cleanly when the singleton
connection is absent or not connected.

Usage:
    arq src.modules.gmail.worker.WorkerSettings
"""

from __future__ import annotations

import logging
import traceback

from dotenv import load_dotenv

# Load .env before any settings are instantiated (same pattern as main.py).
load_dotenv()

from typing import Any

import httpx
import redis.asyncio as redis
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.modules.gmail.application.email_sync_service import EmailSyncService
from src.modules.gmail.application.import_service import HistoricalImportService
from src.modules.gmail.infrastructure.audit_logger import AuditLogger
from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.gmail.infrastructure.email_repository import EmailRepository
from src.modules.gmail.infrastructure.gmail_adapter import GmailAdapter
from src.modules.gmail.infrastructure.quota_tracker import QuotaTracker
from src.modules.gmail.infrastructure.sync_cursor_repository import SyncCursorRepository
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.connection_state_repository import (
    OrganizationGoogleConnectionRepository,
)
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    """ARQ worker startup hook.

    Initializes shared resources (database engine, Redis client, HTTP client,
    settings) and stores them in the worker context dict for use by cron jobs.

    Args:
        ctx: The ARQ worker context dictionary.
    """
    auth_settings = AuthSettings()  # type: ignore[call-arg]
    gmail_settings = GmailSettings()

    engine = create_async_engine(auth_settings.database_url, echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    redis_client = redis.from_url(  # type: ignore[no-untyped-call]
        auth_settings.redis_url, decode_responses=True
    )
    http_client = httpx.AsyncClient()
    crypto = CryptoUtils(auth_settings.oauth_token_encryption_key)
    quota_tracker = QuotaTracker(redis_client, gmail_settings)

    ctx["session_maker"] = session_maker
    ctx["redis_client"] = redis_client
    ctx["http_client"] = http_client
    ctx["crypto"] = crypto
    ctx["quota_tracker"] = quota_tracker
    ctx["auth_settings"] = auth_settings
    ctx["gmail_settings"] = gmail_settings

    logger.info("Gmail ARQ worker started successfully")

    # Write heartbeat for runtime health monitoring
    await redis_client.set("runtime:heartbeat:gmail-worker", __import__("time").time(), ex=600)


async def shutdown(ctx: dict[str, Any]) -> None:
    """ARQ worker shutdown hook.

    Cleans up shared resources (HTTP client, Redis connection).
    Clears heartbeat BEFORE closing Redis so the delete succeeds.

    Args:
        ctx: The ARQ worker context dictionary.
    """
    # Clear heartbeat first while Redis is still connected
    redis_client: redis.Redis | None = ctx.get("redis_client")
    if redis_client:
        try:
            await redis_client.delete("runtime:heartbeat:gmail-worker")
        except Exception:
            logger.warning("Failed to clear heartbeat on shutdown")
        await redis_client.aclose()

    http_client: httpx.AsyncClient | None = ctx.get("http_client")
    if http_client:
        await http_client.aclose()

    logger.info("Gmail ARQ worker shut down")


async def refresh_heartbeat(ctx: dict[str, Any]) -> None:
    """Refresh the runtime heartbeat key in Redis."""
    redis_client: redis.Redis | None = ctx.get("redis_client")
    if redis_client:
        try:
            await redis_client.set(
                "runtime:heartbeat:gmail-worker", __import__("time").time(), ex=600
            )
        except Exception:
            # Intentionally silent: runtime_health() reads this key. A failed
            # set() just means the key isn't refreshed, so it expires after its
            # existing ex=600 TTL and /runtime/health flips to unhealthy on its own
            # — the failure is already fail-visible without a log line here.
            pass


async def poll_gmail_emails(ctx: dict[str, Any]) -> None:
    # Refresh heartbeat so runtime health stays accurate
    await refresh_heartbeat(ctx)

    """ARQ cron job: fetch new emails for the Organization singleton.

    The worker checks the singleton connection status and polls only when it
    is connected. Exceptions are logged and re-raised for the next interval.
    Gmail polling uses the Organization Google Connection; HR identity is
    retained only as the actor for existing email and audit records.

    Args:
        ctx: The ARQ worker context dictionary containing shared resources.
    """
    session_maker: async_sessionmaker[AsyncSession] = ctx["session_maker"]
    redis_client: redis.Redis = ctx["redis_client"]
    http_client: httpx.AsyncClient = ctx["http_client"]
    crypto: CryptoUtils = ctx["crypto"]
    quota_tracker: QuotaTracker = ctx["quota_tracker"]
    auth_settings: AuthSettings = ctx["auth_settings"]
    gmail_settings: GmailSettings = ctx["gmail_settings"]

    async with session_maker() as session:
        try:
            connection_repo = OrganizationGoogleConnectionRepository(session)
            connection = await connection_repo.get_singleton()

            if connection is None or connection.status != "connected":
                logger.debug("No active Organization Google Connection found")
                return

            logger.info(
                "Starting Gmail poll cycle for Organization Connection: %s", connection.email
            )

            if connection.connected_by_user_id is None:
                logger.error(
                    "Organization Google Connection has no connected_by_user_id; "
                    "skipping poll cycle."
                )
                return
            user_id = connection.connected_by_user_id

            gmail_adapter = GmailAdapter(
                settings=gmail_settings,
                quota_tracker=quota_tracker,
                http_client=http_client,
                user_id=user_id,
            )
            email_repo = EmailRepository(session)
            sync_cursor_repo = SyncCursorRepository(session)
            audit_logger = AuditLogger(session, gmail_settings)

            email_sync_service = EmailSyncService(
                session=session,
                gmail_adapter=gmail_adapter,
                email_repo=email_repo,
                sync_cursor_repo=sync_cursor_repo,
                connection_repo=connection_repo,
                crypto=crypto,
                audit_logger=audit_logger,
                settings=gmail_settings,
                redis_client=redis_client,
                client_id=auth_settings.google_client_id,
                client_secret=auth_settings.google_client_secret,
            )

            count = await email_sync_service.poll_emails(user_id)
            logger.info("Polled %d new email(s) for organization", count)

            await session.commit()

        except Exception:
            logger.error(
                "Unhandled exception in poll_gmail_emails cron job:\n%s",
                traceback.format_exc(),
            )
            await session.rollback()
            raise  # Let ARQ handle the retry at next interval


async def import_historical_emails(ctx: dict[str, Any]) -> dict[str, Any]:
    """ARQ task: execute the historical email import job.

    Reads job metadata from Redis (set by the API's start_import endpoint),
    builds a HistoricalImportService, and processes the import.
    The job checks cancellation between batches.

    Args:
        ctx: The ARQ worker context dictionary containing shared resources.

    Returns:
        A summary dict with status and counts.
    """
    session_maker: async_sessionmaker[AsyncSession] = ctx["session_maker"]
    redis_client: redis.Redis = ctx["redis_client"]
    http_client: httpx.AsyncClient = ctx["http_client"]
    crypto: CryptoUtils = ctx["crypto"]
    quota_tracker: QuotaTracker = ctx["quota_tracker"]
    auth_settings: AuthSettings = ctx["auth_settings"]
    gmail_settings: GmailSettings = ctx["gmail_settings"]

    async with session_maker() as session:
        try:
            connection_repo = OrganizationGoogleConnectionRepository(session)
            connection = await connection_repo.get_singleton()

            if connection is None or connection.status != "connected":
                logger.warning("Historical import skipped: no active connection")
                return {"status": "skipped", "reason": "no_connection"}

            if connection.connected_by_user_id is None:
                logger.error("Connection has no connected_by_user_id")
                return {"status": "failed", "reason": "no_user_id"}

            user_id = connection.connected_by_user_id

            gmail_adapter = GmailAdapter(
                settings=gmail_settings,
                quota_tracker=quota_tracker,
                http_client=http_client,
                user_id=user_id,
            )
            email_repo = EmailRepository(session)
            sync_cursor_repo = SyncCursorRepository(session)
            audit_logger = AuditLogger(session, gmail_settings)

            import_service = HistoricalImportService(
                gmail_adapter=gmail_adapter,
                session=session,
                email_repo=email_repo,
                sync_cursor_repo=sync_cursor_repo,
                connection_repo=connection_repo,
                crypto=crypto,
                audit_logger=audit_logger,
                settings=gmail_settings,
                redis_client=redis_client,
                http_client=http_client,
                client_id=auth_settings.google_client_id,
                client_secret=auth_settings.google_client_secret,
            )

            result = await import_service.process_import_job()

            await session.commit()
            return result

        except Exception:
            logger.error(
                "Unhandled exception in import_historical_emails:\n%s",
                traceback.format_exc(),
            )
            await session.rollback()
            return {"status": "failed", "error": "Worker exception"}


def _build_cron_schedule(poll_interval_seconds: int) -> set[int]:
    """Build the set of minute marks for the ARQ cron schedule.

    Converts the poll interval (in seconds) to a set of minutes within
    the hour at which the job should run.

    Args:
        poll_interval_seconds: The polling interval in seconds (60-3600).

    Returns:
        A set of minute values (0-59) for the cron schedule.
    """
    interval_minutes = max(1, poll_interval_seconds // 60)
    return set(range(0, 60, interval_minutes))


# Load settings for cron schedule configuration.
_gmail_settings = GmailSettings()
_auth_settings = AuthSettings()  # type: ignore[call-arg]


class WorkerSettings:
    """ARQ worker settings for Gmail polling.

    Configures the cron job schedule, Redis connection, and worker
    lifecycle hooks (startup/shutdown).

    The cron schedule is derived from GMAIL_POLL_INTERVAL_SECONDS:
    - Default 300s (5 min) → runs at minutes 0, 5, 10, 15, ..., 55
    - 60s (1 min) → runs every minute
    - 600s (10 min) → runs at minutes 0, 10, 20, 30, 40, 50
    """

    on_startup = startup
    on_shutdown = shutdown
    functions = [import_historical_emails]

    queue_name = "gmail-worker"

    cron_jobs = [
        cron(
            poll_gmail_emails,
            minute=_build_cron_schedule(_gmail_settings.poll_interval_seconds),
            second={0},
        ),
        cron(refresh_heartbeat, minute=set(range(0, 60, 3)), second={0}),
    ]

    redis_settings = RedisSettings.from_dsn(_auth_settings.redis_url)
