"""ARQ worker configuration for the Onboarding event consumer.

Defines the ARQ worker entrypoint that consumes ``candidate_accepted`` events
enqueued by the recruitment-side publisher and drives the onboarding flow via
:func:`~src.modules.onboarding.container.process_candidate_accepted`.

Unlike the Gmail worker (a cron poller), this worker is a queue *consumer*: it
registers the onboarding task functions returned by
:func:`~src.modules.onboarding.container.get_arq_tasks` and lets ARQ deliver
jobs to them. ``max_tries`` bounds the per-job retry count at 3 so transient
failures are retried up to three times before the consumer records the final
failure in the audit log (R1.7).

Usage:
    arq src.modules.onboarding.worker.OnboardingWorkerSettings
"""

from __future__ import annotations

import logging
from typing import Any

from dotenv import load_dotenv

# Load .env before any settings are instantiated (same pattern as main.py and
# the Gmail worker).
load_dotenv()

import redis.asyncio as redis
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.modules.onboarding.container import get_arq_tasks
from src.modules.onboarding.infrastructure.config import OnboardingSettings

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    """ARQ worker startup hook.

    Builds the shared async database engine and session factory and stores them
    in the worker context dict. The consumer task reads ``ctx["session_maker"]``
    to open a fresh session per job (see ``process_candidate_accepted``).

    Args:
        ctx: The ARQ worker context dictionary.
    """
    onboarding_settings = OnboardingSettings()

    engine = create_async_engine(onboarding_settings.database_url, echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    ctx["engine"] = engine
    ctx["session_maker"] = session_maker
    ctx["onboarding_settings"] = onboarding_settings

    logger.info("Onboarding ARQ worker started successfully")

    # Write heartbeat for runtime health monitoring
    import time as _time

    redis_client = redis.from_url(  # type: ignore[no-untyped-call]
        onboarding_settings.redis_url, decode_responses=True
    )
    await redis_client.set("runtime:heartbeat:onboarding-worker", _time.time(), ex=600)
    ctx["redis_client"] = redis_client


async def shutdown(ctx: dict[str, Any]) -> None:
    """ARQ worker shutdown hook.

    Disposes the async database engine created at startup so its connection
    pool is released cleanly.

    Args:
        ctx: The ARQ worker context dictionary.
    """
    engine: AsyncEngine | None = ctx.get("engine")
    if engine:
        await engine.dispose()

    logger.info("Onboarding ARQ worker shut down")

    # Clear heartbeat on shutdown
    redis_client = ctx.get("redis_client")
    if redis_client:
        try:
            await redis_client.delete("runtime:heartbeat:onboarding-worker")
        except Exception:
            logger.warning("Failed to clear heartbeat on shutdown")
        await redis_client.aclose()


async def refresh_heartbeat(ctx: dict[str, Any]) -> None:
    """Refresh the onboarding worker heartbeat in Redis."""
    redis_client = ctx.get("redis_client")
    if redis_client:
        try:
            await redis_client.set(
                "runtime:heartbeat:onboarding-worker",
                __import__("time").time(),
                ex=600,
            )
        except Exception:
            # Intentionally silent: runtime_router.py:130 reads this key. A failed
            # set() just means the key isn't refreshed, so it expires after its
            # existing ex=600 TTL and /runtime/health flips to unhealthy on its own
            # — the failure is already fail-visible without a log line here.
            pass


# Load settings for the Redis connection configuration.
_onboarding_settings = OnboardingSettings()


class OnboardingWorkerSettings:
    """ARQ worker settings for the Onboarding event consumer.

    Registers the onboarding task functions, configures the Redis connection
    from the shared ``redis_url``, wires the worker lifecycle hooks
    (startup/shutdown), and bounds per-job retries at 3 (R1.7).
    """

    functions = get_arq_tasks()

    queue_name = "onboarding-worker"

    on_startup = startup
    on_shutdown = shutdown

    redis_settings = RedisSettings.from_dsn(_onboarding_settings.redis_url)

    max_tries = 3

    cron_jobs = [
        cron(
            refresh_heartbeat,
            minute={1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34, 37, 40, 43, 46, 49, 52, 55, 58},
        )
    ]
