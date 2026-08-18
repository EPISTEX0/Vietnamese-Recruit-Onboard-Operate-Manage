"""ARQ worker configuration for the Knowledge Base ingestion pipeline.

Defines the ARQ worker entrypoint that consumes ``ingest_document`` jobs
enqueued by the knowledge-base API after a document upload.

Usage:
    arq src.modules.knowledge_base.worker.KnowledgeBaseWorkerSettings
"""

from __future__ import annotations

import logging
from typing import Any

from dotenv import load_dotenv

# Load .env before any settings are instantiated (same pattern as main.py and
# the other workers).
load_dotenv()

import redis.asyncio as redis
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.modules.employee.infrastructure.minio_client import MinIOClient
from src.modules.knowledge_base.container import get_arq_tasks, get_kb_settings

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    """ARQ worker startup hook.

    Builds the shared async database engine and session factory, loads
    KnowledgeBaseSettings, and creates the MinIO client.
    """
    settings = get_kb_settings()

    engine = create_async_engine(settings.database_url, echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    minio = MinIOClient(settings)  # type: ignore[arg-type]

    ctx["engine"] = engine
    ctx["session_maker"] = session_maker
    ctx["kb_settings"] = settings
    ctx["kb_minio_client"] = minio

    logger.info("Knowledge Base ARQ worker started successfully")

    # Write heartbeat for runtime health monitoring
    import time as _time

    redis_client = redis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=True
    )
    await redis_client.set("runtime:heartbeat:kb-worker", _time.time(), ex=600)
    ctx["redis_client"] = redis_client


async def shutdown(ctx: dict[str, Any]) -> None:
    """ARQ worker shutdown hook."""
    engine: AsyncEngine | None = ctx.get("engine")
    if engine:
        await engine.dispose()

    logger.info("Knowledge Base ARQ worker shut down")

    # Clear heartbeat on shutdown
    redis_client = ctx.get("redis_client")
    if redis_client:
        try:
            await redis_client.delete("runtime:heartbeat:kb-worker")
        except Exception:
            logger.warning("Failed to clear heartbeat on shutdown")
        await redis_client.aclose()


async def refresh_heartbeat(ctx: dict[str, Any]) -> None:
    """Refresh the KB worker heartbeat in Redis."""
    redis_client = ctx.get("redis_client")
    if redis_client:
        try:
            await redis_client.set(
                "runtime:heartbeat:kb-worker",
                __import__("time").time(),
                ex=600,
            )
        except Exception:
            # Unlike gmail/onboarding, no reader for runtime:heartbeat:kb-worker
            # exists yet (tracked separately in #397), so an expired key doesn't
            # surface anywhere. Log here so the failure isn't silently invisible
            # until that reader lands.
            logger.warning("Failed to refresh heartbeat")


# Load settings for the Redis connection configuration.
_kb_settings = get_kb_settings()


class KnowledgeBaseWorkerSettings:
    """ARQ worker settings for the Knowledge Base ingestion pipeline.

    Registers the ingest_document task function, configures the Redis
    connection, wires lifecycle hooks, and bounds per-job retries at 3.
    """

    functions = get_arq_tasks()

    queue_name = "kb-worker"

    on_startup = startup
    on_shutdown = shutdown

    redis_settings = RedisSettings.from_dsn(_kb_settings.redis_url)

    max_tries = 3

    cron_jobs = [
        __import__("arq").cron(
            refresh_heartbeat,
            minute={1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34, 37, 40, 43, 46, 49, 52, 55, 58},
        )
    ]
