"""Dependency injection container for the Knowledge Base module.

Wires together DocumentService, IngestionService, MinIOClient, and
KnowledgeBaseSettings. Provides FastAPI Depends providers for the API router
and ARQ task functions for the worker.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache

import arq
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.employee.infrastructure.minio_client import MinIOClient
from src.modules.identity.container import get_db_session
from src.modules.knowledge_base.application.document_service import DocumentService
from src.modules.knowledge_base.application.ingestion_service import IngestionService
from src.modules.knowledge_base.infrastructure.config import KnowledgeBaseSettings
from src.modules.knowledge_base.infrastructure.repository import (
    KnowledgeBaseRepository,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings singleton
# ---------------------------------------------------------------------------


@lru_cache
def get_kb_settings() -> KnowledgeBaseSettings:
    """Load and cache KnowledgeBaseSettings from KB_* env vars."""
    return KnowledgeBaseSettings()


# ---------------------------------------------------------------------------
# MinIO client (for knowledge-base bucket)
# ---------------------------------------------------------------------------


@lru_cache
def get_kb_minio_client() -> MinIOClient:
    """Create and cache a MinIOClient configured for the knowledge-base bucket.

    Uses the same aioboto3 infrastructure as the employee module but with
    the KB-specific bucket name from KnowledgeBaseSettings.

    KnowledgeBaseSettings exposes the same minio_* attributes that
    MinIOClient reads (endpoint, access_key, secret_key, bucket), so this
    is safe at runtime despite the nominal type mismatch.
    """
    settings = get_kb_settings()
    return MinIOClient(settings)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ARQ Redis pool (lazy singleton)
# ---------------------------------------------------------------------------

_arq_pool: ArqRedis | None = None
_arq_last_failure_at: float | None = None

# How long a failed connection is remembered before the next attempt.
#
# Every knowledge-base endpoint awaits get_arq_redis() through
# get_document_service, including the read-only ones. Without this cooldown a
# Redis outage cost each request the full arq connect budget -- several
# seconds against a closed port -- so a degraded queue turned into a stalled
# API. Short enough that a recovered Redis is picked up promptly.
ARQ_RETRY_COOLDOWN_SECONDS = 30.0


async def get_arq_redis() -> ArqRedis | None:
    """Get or create the ARQ Redis connection pool for enqueuing jobs.

    Returns None if the Redis connection fails (non-fatal for document
    uploads). A failure is remembered for ``ARQ_RETRY_COOLDOWN_SECONDS`` so
    callers are not made to wait on a dial that just timed out; after that the
    next call tries again and picks up a recovered Redis.
    """
    global _arq_pool, _arq_last_failure_at
    if _arq_pool is not None:
        return _arq_pool

    if (
        _arq_last_failure_at is not None
        and time.monotonic() - _arq_last_failure_at < ARQ_RETRY_COOLDOWN_SECONDS
    ):
        return None

    try:
        settings = get_kb_settings()
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        _arq_pool = await arq.create_pool(redis_settings, default_queue_name="kb-worker")
    except Exception:
        _arq_last_failure_at = time.monotonic()
        logger.exception(
            "Failed to create ARQ Redis pool — uploads will not be enqueued. "
            "Retrying in at most %.0fs.",
            ARQ_RETRY_COOLDOWN_SECONDS,
        )
        return None

    _arq_last_failure_at = None
    logger.info("ARQ Redis pool created for knowledge-base worker.")
    return _arq_pool


# ---------------------------------------------------------------------------
# FastAPI Depends providers
# ---------------------------------------------------------------------------


async def get_document_service(
    session: AsyncSession = Depends(get_db_session),
) -> DocumentService:
    """Provide a DocumentService wired to the request session.

    Used by the knowledge-base API router for upload/list/detail endpoints.
    ARQ Redis is lazily connected for enqueuing ingestion jobs.
    """
    settings = get_kb_settings()
    minio = get_kb_minio_client()
    repo = KnowledgeBaseRepository(session)
    arq_redis = await get_arq_redis()
    return DocumentService(
        repo=repo,
        minio_client=minio,
        settings=settings,
        arq_redis=arq_redis,
    )


# ---------------------------------------------------------------------------
# ARQ task function
# ---------------------------------------------------------------------------


async def ingest_document(
    ctx: dict,
    document_id: str,
    kb_type: str = "hr",
) -> None:
    """ARQ task: ingest a knowledge base document.

    Called by the ARQ worker when a document upload is complete.
    Runs the full ingestion pipeline: download → parse → chunk → embed → index.

    Args:
        ctx: ARQ worker context (must contain session_maker, kb_settings,
             kb_minio_client).
        document_id: UUID string of the document to ingest.
        kb_type: Knowledge base type — 'hr' or 'employee' (Issue #260).
    """
    from uuid import UUID

    session_maker = ctx["session_maker"]
    settings: KnowledgeBaseSettings = ctx["kb_settings"]
    minio: MinIOClient = ctx["kb_minio_client"]

    doc_id = UUID(document_id)

    async with session_maker() as session:
        repo = KnowledgeBaseRepository(session)
        ingestion = IngestionService(
            repo=repo,
            minio_client=minio,
            settings=settings,
        )
        try:
            await ingestion.ingest(doc_id, kb_type=kb_type)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_arq_tasks() -> list:
    """Return the ARQ task functions registered by the knowledge base module.

    Consumed by the KB worker settings to register ``ingest_document``.
    """
    return [ingest_document]
