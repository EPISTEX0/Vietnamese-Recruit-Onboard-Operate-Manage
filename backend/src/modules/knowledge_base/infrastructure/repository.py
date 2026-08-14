"""Knowledge Base document repository.

Provides async CRUD operations for both HR and Employee Knowledge Base
documents and chunks. Methods dispatch to the correct physical table based
on kb_type (physical security isolation per Issue #260).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from src.modules.knowledge_base.domain.entities import (
    EmployeeKnowledgeBaseChunk,
    EmployeeKnowledgeBaseDocument,
    KnowledgeBaseChunk,
    KnowledgeBaseDocument,
)

# ---------------------------------------------------------------------------
# Entity dispatch helpers
# ---------------------------------------------------------------------------

_DOC_ENTITY_MAP: dict[str, type] = {
    "hr": KnowledgeBaseDocument,
    "employee": EmployeeKnowledgeBaseDocument,
}

_CHUNK_ENTITY_MAP: dict[str, type] = {
    "hr": KnowledgeBaseChunk,
    "employee": EmployeeKnowledgeBaseChunk,
}

_VALID_KB_TYPES = frozenset({"hr", "employee"})


def _get_doc_entity(kb_type: str) -> type:
    """Return the document entity class for a kb_type."""
    if kb_type not in _VALID_KB_TYPES:
        raise ValueError(f"Invalid kb_type: {kb_type}. Must be one of {_VALID_KB_TYPES}.")
    return _DOC_ENTITY_MAP[kb_type]


def _get_chunk_entity(kb_type: str) -> type:
    """Return the chunk entity class for a kb_type."""
    if kb_type not in _VALID_KB_TYPES:
        raise ValueError(f"Invalid kb_type: {kb_type}. Must be one of {_VALID_KB_TYPES}.")
    return _CHUNK_ENTITY_MAP[kb_type]


class KnowledgeBaseRepository:
    """Async repository for Knowledge Base documents and chunks.

    All methods receive and share a single AsyncSession; transaction
    boundaries are owned by the caller (service layer).

    Handles both HR and Employee KB tables with physical separation.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    async def insert_document(
        self,
        doc: KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument,
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument:
        """Insert a new document record and return it with the generated id.

        The document entity itself carries the kb_type, so the correct
        table is used automatically via SQLModel inheritance.
        """
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def get_document(
        self,
        document_id: uuid.UUID,
        kb_type: str = "hr",
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument | None:
        """Fetch a single document by id from the appropriate table."""
        doc_entity = _get_doc_entity(kb_type)
        result = await self._session.execute(select(doc_entity).where(doc_entity.id == document_id))
        return result.scalar_one_or_none()

    async def list_documents(
        self,
        kb_type: str = "hr",
        page: int = 1,
        page_size: int = 20,
        category: str | None = None,
        status: str | None = None,
    ) -> tuple[Sequence, int]:
        """List documents with pagination and optional filters, ordered by created_at DESC.

        Queries only the table for the given kb_type.
        Supports optional filtering by category and/or status (Issue #261, KB-05).

        Returns:
            Tuple of (documents, total_count).
        """
        doc_entity = _get_doc_entity(kb_type)

        # Build where clauses
        conditions = [doc_entity.kb_type == kb_type]
        if category:
            conditions.append(doc_entity.category == category)
        if status:
            conditions.append(doc_entity.status == status)

        where_clause = and_(*conditions)

        # Total count
        count_result = await self._session.execute(
            select(func.count()).select_from(doc_entity).where(where_clause)
        )
        total = count_result.scalar_one()

        # Paginated query
        offset = (page - 1) * page_size
        result = await self._session.execute(
            select(doc_entity)
            .where(where_clause)
            .order_by(col(doc_entity.created_at).desc())
            .offset(offset)
            .limit(page_size)
        )
        docs = result.scalars().all()
        return docs, total

    async def update_document_metadata(
        self,
        document_id: uuid.UUID,
        *,
        kb_type: str = "hr",
        display_name: str | None = None,
        category: str | None = None,
        description: str | None = None,
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument | None:
        """Update document metadata (display_name, category, description).

        Does NOT modify file, chunks, or indexing. Returns the updated
        document or None if not found (Issue #261, KB-05).
        """
        doc = await self.get_document(document_id, kb_type=kb_type)
        if doc is None:
            return None
        if display_name is not None:
            doc.display_name = display_name
        if category is not None:
            doc.category = category
        if description is not None:
            doc.description = description
        doc.updated_at = datetime.now(UTC)
        await self._session.flush()
        return doc

    async def update_document_status(
        self,
        document_id: uuid.UUID,
        status: str,
        *,
        kb_type: str = "hr",
        chunk_count: int | None = None,
        error_message: str | None = None,
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument | None:
        """Update document status and related fields.

        Returns the updated document or None if not found.
        """
        doc = await self.get_document(document_id, kb_type=kb_type)
        if doc is None:
            return None
        doc.status = status
        doc.updated_at = datetime.now(UTC)
        if chunk_count is not None:
            doc.chunk_count = chunk_count
        if error_message is not None:
            doc.error_message = error_message
        await self._session.flush()
        return doc

    async def delete_document(
        self,
        document_id: uuid.UUID,
        kb_type: str = "hr",
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument | None:
        """Hard-delete a document and its chunks.

        Deletes chunks first (via explicit DELETE to avoid ORM cascade issues
        with pgvector), then the document row itself. Returns the deleted
        document's metadata (including storage_path for MinIO cleanup)
        or None if not found (Issue #261, KB-05).
        """
        doc = await self.get_document(document_id, kb_type=kb_type)
        if doc is None:
            return None

        # Delete chunks first
        await self.delete_chunks_by_document(document_id, kb_type=kb_type)

        # Store storage_path for caller (MinIO cleanup)
        storage_path = doc.storage_path

        # Delete document row
        await self._session.delete(doc)
        await self._session.flush()

        # Set storage_path on the detached object for caller convenience
        doc.storage_path = storage_path
        return doc

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------

    async def insert_chunks(
        self,
        chunks: list[KnowledgeBaseChunk | EmployeeKnowledgeBaseChunk],
    ) -> None:
        """Insert multiple chunks in bulk. Each chunk knows its own table."""
        self._session.add_all(chunks)
        await self._session.flush()

    async def delete_chunks_by_document(
        self,
        document_id: uuid.UUID,
        kb_type: str = "hr",
    ) -> None:
        """Delete all chunks for a document (used before re-ingestion)."""
        from sqlalchemy import delete

        chunk_entity = _get_chunk_entity(kb_type)
        await self._session.execute(
            delete(chunk_entity).where(
                chunk_entity.document_id == document_id,
            )
        )
        await self._session.flush()

    # ------------------------------------------------------------------
    # Similarity search
    # ------------------------------------------------------------------

    async def search_similar_chunks(
        self,
        query_embedding: list[float],
        kb_types: list[str] | None = None,
        top_k: int = 3,
        similarity_threshold: float = 0.7,
    ) -> list[tuple,]:
        """Find chunks semantically similar to query_embedding via pgvector cosine distance.

        Joins with the appropriate document table to filter by kb_type and retrieve
        the document's display_name for citation formatting.

        **Two statements, not one.** The first is the approximate one, shaped so
        pgvector can answer it from the HNSW index on ``embedding`` (revision
        088) -- median 1.34 ms against 87.2 ms for the exact form on 50 000
        rows. If any branch comes back short of its own ``LIMIT``, the
        exact form runs instead and its rows are the ones returned.
        ``_ranked_chunks_statement`` builds both and explains why the second is
        deliberately unindexable, and why the fallback is not optional.

        The fallback is why the speed-up is not paid for in silent wrong
        answers, and it never costs more than the old behaviour did: it fires
        only when a branch is short, which is either a knowledge base too small
        to be slow, or the case where the old code's full scan was the only
        correct answer anyway.

        Cosine similarity = 1 - cosine_distance. The threshold and the final
        ``top_k`` are applied in Python, over at most ``top_k`` rows per branch.
        Keeping them out of SQL is not tidiness: as a ``WHERE`` predicate the
        threshold cuts the planner's row estimate (50 000 down to 16 667) far
        enough that the sequential-scan path wins on estimated cost even with
        the ORDER BY correct, which measured 90.6 ms -- no better than doing
        neither. Filtering after ``LIMIT`` cannot change the answer: the
        threshold is a predicate on distance and the rows are ordered by
        distance, so what it keeps is a prefix of that order.

        ``tests/modules/knowledge_base/test_embedding_index.py`` holds all of
        this -- the shape of each statement, and the fallback itself.

        Args:
            query_embedding: The embedding vector of the query text.
            kb_types: Optional list of kb_type values to filter (e.g. ['hr']).
            top_k: Maximum number of chunks to return.
            similarity_threshold: Minimum cosine similarity (0.0-1.0).

        Returns:
            List of (chunk, document_display_name, similarity_score) tuples.
        """
        if kb_types is None:
            kb_types = ["hr"]

        # Validate kb_types
        for kbt in kb_types:
            if kbt not in _VALID_KB_TYPES:
                raise ValueError(f"Invalid kb_type: {kbt}. Must be one of {_VALID_KB_TYPES}.")

        max_distance = 1.0 - similarity_threshold

        statement = _ranked_chunks_statement(query_embedding, kb_types, top_k, indexable=True)
        rows = (await self._session.execute(statement)).all()

        if len(rows) < top_k * len(kb_types):
            # At least one branch came back short of its own LIMIT. Either the
            # knowledge base genuinely holds fewer than `top_k` eligible chunks
            # -- the ordinary state while it is small, where the exact scan
            # costs microseconds -- or the index scan was starved. Nothing here
            # can tell those apart, and only one of them is safe to believe.
            statement = _ranked_chunks_statement(query_embedding, kb_types, top_k, indexable=False)
            rows = (await self._session.execute(statement)).all()

        # Reconstruct chunk-like objects from rows.
        # We return tuples of (chunk_dict, display_name, similarity) since we can't
        # reconstruct full ORM objects from UNION results.
        # The RetrievalService only uses .content and .strip() on the chunk.
        class _ChunkProxy:
            """Minimal proxy for chunk results from UNION queries."""

            __slots__ = ("id", "document_id", "chunk_index", "content", "token_count", "created_at")

            def __init__(self, row: tuple) -> None:
                self.id = row.chunk_id
                self.document_id = row.document_id
                self.chunk_index = row.chunk_index
                self.content = row.content
                self.token_count = row.token_count
                self.created_at = row.chunk_created_at

        # The threshold and the final `top_k` are applied here rather than in
        # SQL. In SQL the threshold has to be a predicate, and a predicate on
        # the distance is exactly what pushes the planner off the index (see the
        # docstring); in Python it is a filter over at most `top_k` rows per
        # branch. `distance` rather than `similarity` comes back from the query
        # for the same reason -- `1 - distance` in the SELECT list would have to
        # be repeated in the ORDER BY. The callers' contract is unchanged:
        # cosine similarity, high is close.
        kept = [row for row in rows if float(row.distance) < max_distance]
        kept.sort(key=lambda row: float(row.distance))
        return [
            (_ChunkProxy(row), row.display_name, 1.0 - float(row.distance)) for row in kept[:top_k]
        ]


def _ranked_chunks_statement(
    query_embedding: list[float],
    kb_types: list[str],
    top_k: int,
    *,
    indexable: bool,
):
    """Build the nearest-``top_k``-chunks statement, in one of two orderings.

    One branch per knowledge base, each ranked and limited on its own so each
    can be answered from its own table's index, then UNION ALL'd. Ranking per
    branch cannot lose a row the combined query would have kept: the overall
    ``top_k`` is always a subset of the union of the per-branch ``top_k``.

    ``indexable`` picks the ORDER BY, and that single choice decides whether the
    query is an approximate index scan or an exact one:

    * ``True``  -> ``ORDER BY embedding <=> :q`` -- the bare ascending distance,
      the one form pgvector can answer from the HNSW index (revision 088).
      Median on 50 000 rows: 1.34 ms against 87.2 ms for the exact form.
    * ``False`` -> ``ORDER BY 1 - (embedding <=> :q) DESC`` -- the identical row
      order, which no pgvector index can serve because the operator is wrapped.
      This is *not* a leftover; it is how the exact scan is guaranteed. Forcing
      it any other way (``enable_seqscan``, a distance predicate in ``WHERE``)
      would leave the choice to the planner, and the whole point of this branch
      is to not depend on the planner.

    The caller runs the approximate form first and falls back to the exact one
    when a branch returns fewer rows than its own ``LIMIT``. That is necessary
    because an HNSW scan yields a bounded candidate set (~400 tuples at the
    default ``ef_search``) and ``documents.status = 'ready'`` is applied to
    those candidates *after* the index has chosen them. If every candidate
    belongs to a document that is not ready, the branch returns nothing while an
    exact scan would return the right rows -- reproduced on pgvector 0.8.6 with
    1 000 chunks under an ``error`` document sitting nearer the query than any
    ready chunk: 0 rows from the approximate form, 3 from the exact one.
    ``hnsw.iterative_scan`` is pgvector's remedy for filtered ANN and does not
    close this: measured at ``strict_order`` and ``relaxed_order``, with
    ``scan_mem_multiplier`` up to 64, the scan stopped at 532 candidates and
    still returned 0 rows. That state is reachable -- ``ingest()`` inserts
    chunks and only then sets ``ready``, and its ``except`` handler sets
    ``error`` and returns normally, so the worker commits the chunks alongside
    the ``error`` status.
    """
    branches = []
    for kbt in kb_types:
        doc_entity = _get_doc_entity(kbt)
        chunk_entity = _get_chunk_entity(kbt)
        # One expression object, used in both the SELECT list and the ORDER BY,
        # so the rendered ORDER BY is the bare `embedding <=> $1` pgvector matches.
        distance = chunk_entity.embedding.cosine_distance(query_embedding)
        ordering = distance if indexable else (1.0 - distance).desc()

        branches.append(
            select(
                chunk_entity.id.label("chunk_id"),
                chunk_entity.document_id.label("document_id"),
                chunk_entity.chunk_index.label("chunk_index"),
                chunk_entity.content.label("content"),
                chunk_entity.token_count.label("token_count"),
                chunk_entity.created_at.label("chunk_created_at"),
                doc_entity.display_name.label("display_name"),
                distance.label("distance"),
            )
            .join(doc_entity, chunk_entity.document_id == doc_entity.id)
            .where(
                chunk_entity.embedding.isnot(None),
                doc_entity.status == "ready",
            )
            .order_by(ordering)
            .limit(top_k)
        )

    if len(branches) == 1:
        return branches[0]
    # Each branch is wrapped before the UNION ALL: a branch carries its own
    # ORDER BY and LIMIT, which UNION ALL cannot hold directly.
    return union_all(*(branch.subquery().select() for branch in branches))
