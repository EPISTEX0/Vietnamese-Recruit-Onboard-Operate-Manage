"""Tests for batching in ``call_embedding_service`` and large-document ingest.

The embedding service caps ``EmbedRequest.texts`` at 100 items, so a document
that chunks into more than that used to fail the whole ingest with a 422.
These tests pin the batching behaviour that fixes it: request size stays
under the service's cap, vector order still matches chunk order, a
short-counting provider is loud rather than silent, and a document whose
embedding calls drag on eventually gives the worker back.

No network: every HTTP call is served by respx.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from src.modules.employee.infrastructure.minio_client import MinIOClient
from src.modules.knowledge_base.application import ingestion_service
from src.modules.knowledge_base.application.ingestion_service import (
    EMBEDDING_REQUEST_BATCH_SIZE,
    EMBEDDING_SERVICE_MAX_TEXTS,
    IngestionService,
    call_embedding_service,
    chunk_text,
)
from src.modules.knowledge_base.domain.enums import KnowledgeBaseDocumentStatus
from src.modules.knowledge_base.infrastructure.config import KnowledgeBaseSettings
from src.modules.knowledge_base.infrastructure.repository import (
    KnowledgeBaseRepository,
)

EMBED_URL = "http://test-embed:8080"
VECTOR_WIDTH = 4


def _text_at(index: int) -> str:
    """Build a text whose embedding is recoverable, so order is checkable."""
    return f"chunk-{index}"


def _index_of(text: str) -> int:
    """Recover the index encoded by :func:`_text_at`."""
    return int(text.rsplit("-", 1)[1])


def _recording_provider(batches: list[list[str]]):
    """respx side effect that records each batch and echoes order-tagged vectors.

    Each returned vector is ``[i, i, i, i]`` for input ``chunk-i``, so a
    reordering or misalignment anywhere in the batching shows up as a
    vector that no longer matches its chunk.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        batches.append(texts)
        return httpx.Response(
            200,
            json={"embeddings": [[float(_index_of(text))] * VECTOR_WIDTH for text in texts]},
        )

    return handler


class TestCallEmbeddingServiceBatching:
    """``call_embedding_service`` splits big inputs into service-sized requests."""

    def test_batch_size_fits_the_service_contract(self) -> None:
        """The batch we send must be accepted by EmbedRequest's max_length."""
        assert 0 < EMBEDDING_REQUEST_BATCH_SIZE <= EMBEDDING_SERVICE_MAX_TEXTS

    @respx.mock
    async def test_one_over_the_batch_size_splits_into_two_requests(self) -> None:
        """The boundary case: batch_size + 1 texts must not go out as one request."""
        texts = [_text_at(i) for i in range(EMBEDDING_REQUEST_BATCH_SIZE + 1)]
        batches: list[list[str]] = []
        respx.post(f"{EMBED_URL}/embed").mock(side_effect=_recording_provider(batches))

        vectors = await call_embedding_service(texts, EMBED_URL)

        assert [len(batch) for batch in batches] == [EMBEDDING_REQUEST_BATCH_SIZE, 1]
        assert len(vectors) == len(texts)

    @respx.mock
    async def test_exactly_the_batch_size_is_a_single_request(self) -> None:
        """No gratuitous extra round trip when the input lands on the boundary."""
        texts = [_text_at(i) for i in range(EMBEDDING_REQUEST_BATCH_SIZE)]
        batches: list[list[str]] = []
        respx.post(f"{EMBED_URL}/embed").mock(side_effect=_recording_provider(batches))

        vectors = await call_embedding_service(texts, EMBED_URL)

        assert len(batches) == 1
        assert len(vectors) == EMBEDDING_REQUEST_BATCH_SIZE

    @respx.mock
    async def test_vector_order_matches_text_order_across_batches(self) -> None:
        """Concatenating batch responses must preserve the caller's ordering."""
        count = EMBEDDING_REQUEST_BATCH_SIZE * 3 + 7
        texts = [_text_at(i) for i in range(count)]
        batches: list[list[str]] = []
        respx.post(f"{EMBED_URL}/embed").mock(side_effect=_recording_provider(batches))

        vectors = await call_embedding_service(texts, EMBED_URL)

        assert len(vectors) == count
        assert [vector[0] for vector in vectors] == [float(i) for i in range(count)]
        # And no request exceeded what the embedding service will accept.
        assert max(len(batch) for batch in batches) <= EMBEDDING_SERVICE_MAX_TEXTS

    @respx.mock
    async def test_no_request_is_made_for_empty_input(self) -> None:
        """An empty chunk list is a caller bug upstream, not an HTTP round trip."""
        route = respx.post(f"{EMBED_URL}/embed").mock(side_effect=_recording_provider([]))

        assert await call_embedding_service([], EMBED_URL) == []
        assert not route.called

    @respx.mock
    async def test_short_batch_response_raises_instead_of_misaligning(self) -> None:
        """A provider that drops a vector must fail loudly, not shift the rest."""
        texts = [_text_at(i) for i in range(EMBEDDING_REQUEST_BATCH_SIZE + 1)]

        def short_provider(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)["texts"]
            return httpx.Response(
                200,
                json={"embeddings": [[0.0] * VECTOR_WIDTH] * (len(sent) - 1)},
            )

        respx.post(f"{EMBED_URL}/embed").mock(side_effect=short_provider)

        with pytest.raises(ValueError, match="không khớp"):
            await call_embedding_service(texts, EMBED_URL)

    @respx.mock
    async def test_http_error_on_a_later_batch_propagates(self) -> None:
        """A 422/500 on batch two must fail the call, not return a partial list."""
        texts = [_text_at(i) for i in range(EMBEDDING_REQUEST_BATCH_SIZE + 1)]
        calls = {"n": 0}

        def flaky_provider(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                sent = json.loads(request.content)["texts"]
                return httpx.Response(200, json={"embeddings": [[0.0] * VECTOR_WIDTH] * len(sent)})
            return httpx.Response(500, json={"detail": "boom"})

        respx.post(f"{EMBED_URL}/embed").mock(side_effect=flaky_provider)

        with pytest.raises(httpx.HTTPStatusError):
            await call_embedding_service(texts, EMBED_URL)

    @respx.mock
    async def test_total_timeout_stops_a_document_from_pinning_the_worker(self) -> None:
        """The overall budget aborts the loop rather than embedding forever."""
        texts = [_text_at(i) for i in range(EMBEDDING_REQUEST_BATCH_SIZE * 4)]

        def slow_provider(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)["texts"]
            return httpx.Response(200, json={"embeddings": [[0.0] * VECTOR_WIDTH] * len(sent)})

        respx.post(f"{EMBED_URL}/embed").mock(side_effect=slow_provider)

        with pytest.raises(TimeoutError, match="ngân sách"):
            await call_embedding_service(
                texts,
                EMBED_URL,
                # Already spent by the time the first batch returns.
                total_timeout=0.0,
            )

    @respx.mock
    async def test_budget_is_for_the_whole_document_not_per_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Time spent on earlier batches must count against the later ones.

        Uses a fake clock advanced by each request rather than real sleeps, so
        a per-batch budget — which would let a many-batch document run for
        arbitrarily long — fails here instead of passing quietly.
        """
        texts = [_text_at(i) for i in range(EMBEDDING_REQUEST_BATCH_SIZE * 4)]
        clock = {"now": 0.0}
        monkeypatch.setattr(
            ingestion_service, "time", SimpleNamespace(monotonic=lambda: clock["now"])
        )

        seen_timeouts: list[float] = []

        def slow_provider(request: httpx.Request) -> httpx.Response:
            seen_timeouts.append(request.extensions["timeout"]["read"])
            clock["now"] += 50.0  # Each request burns 50s of the budget.
            sent = json.loads(request.content)["texts"]
            return httpx.Response(200, json={"embeddings": [[0.0] * VECTOR_WIDTH] * len(sent)})

        respx.post(f"{EMBED_URL}/embed").mock(side_effect=slow_provider)

        with pytest.raises(TimeoutError, match="ngân sách"):
            await call_embedding_service(texts, EMBED_URL, timeout=120.0, total_timeout=120.0)

        # Three batches fit in the 120s budget (0s, 50s, 100s); the fourth is
        # refused rather than started.
        assert len(seen_timeouts) == 3
        # And the last request was clamped to what was left, not the full 120s,
        # so no single request can outlive the document's budget.
        assert seen_timeouts == [120.0, 70.0, 20.0]

    @respx.mock
    async def test_rejects_a_batch_size_the_service_would_refuse(self) -> None:
        """Callers cannot opt into a batch size that guarantees a 422."""
        route = respx.post(f"{EMBED_URL}/embed").mock(side_effect=_recording_provider([]))

        with pytest.raises(ValueError, match="batch_size"):
            await call_embedding_service(
                [_text_at(0)], EMBED_URL, batch_size=EMBEDDING_SERVICE_MAX_TEXTS + 1
            )
        with pytest.raises(ValueError, match="batch_size"):
            await call_embedding_service([_text_at(0)], EMBED_URL, batch_size=0)

        assert not route.called

    @respx.mock
    async def test_single_query_path_is_untouched(self) -> None:
        """RetrievalService's one-text call still costs exactly one request."""
        batches: list[list[str]] = []
        respx.post(f"{EMBED_URL}/embed").mock(side_effect=_recording_provider(batches))

        vectors = await call_embedding_service([_text_at(0)], EMBED_URL, timeout=30.0)

        assert batches == [["chunk-0"]]
        assert len(vectors) == 1


class TestLargeDocumentIngest:
    """End-to-end ingest of a document that chunks past the service limit."""

    @respx.mock
    async def test_ingest_document_with_more_than_100_chunks(self) -> None:
        """A handbook-sized document ingests fully instead of failing with 422."""
        # ~240k chars of Vietnamese prose — a ~100-page staff handbook.
        sentence = "Nhân viên được nghỉ phép năm theo quy định của công ty. "
        text = sentence * 4300
        settings = KnowledgeBaseSettings(embedding_service_url=EMBED_URL)
        expected_chunks = chunk_text(
            text,
            chunk_size_tokens=settings.chunk_size_tokens,
            chunk_overlap_tokens=settings.chunk_overlap_tokens,
        )
        assert len(expected_chunks) > EMBEDDING_SERVICE_MAX_TEXTS, (
            "fixture must exceed the embedding service's per-request cap"
        )

        batches: list[list[str]] = []

        def provider(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)["texts"]
            batches.append(sent)
            return httpx.Response(
                200,
                json={"embeddings": [[float(len(t))] * VECTOR_WIDTH for t in sent]},
            )

        respx.post(f"{EMBED_URL}/embed").mock(side_effect=provider)

        document_id = uuid.uuid4()
        doc = MagicMock()
        doc.storage_path = "hr/handbook.txt"
        doc.mime_type = "text/plain"

        repo = MagicMock(spec=KnowledgeBaseRepository)
        repo.get_document = AsyncMock(return_value=doc)
        repo.update_document_status = AsyncMock()
        repo.insert_chunks = AsyncMock()

        minio = MagicMock(spec=MinIOClient)
        minio.download_file = AsyncMock(return_value=text.encode("utf-8"))

        service = IngestionService(repo=repo, minio_client=minio, settings=settings)
        await service.ingest(document_id, kb_type="hr")

        # Every request respected the service cap, and nothing was dropped.
        assert max(len(batch) for batch in batches) <= EMBEDDING_SERVICE_MAX_TEXTS
        assert sum(len(batch) for batch in batches) == len(expected_chunks)

        inserted = repo.insert_chunks.await_args.args[0]
        assert len(inserted) == len(expected_chunks)
        assert [entity.chunk_index for entity in inserted] == list(range(len(expected_chunks)))
        # The stub encodes each chunk's length into its vector, so this proves
        # vector i really belongs to chunk i after reassembly.
        for entity in inserted:
            assert entity.embedding[0] == float(len(entity.content))

        repo.update_document_status.assert_any_await(
            document_id,
            KnowledgeBaseDocumentStatus.READY,
            kb_type="hr",
            chunk_count=len(expected_chunks),
        )

    @respx.mock
    async def test_ingest_marks_error_when_a_batch_fails(self) -> None:
        """A mid-document embedding failure still lands the doc in 'error'."""
        sentence = "Quy chế lương thưởng áp dụng từ ngày một tháng một. "
        text = sentence * 4300
        settings = KnowledgeBaseSettings(embedding_service_url=EMBED_URL)

        respx.post(f"{EMBED_URL}/embed").mock(
            return_value=httpx.Response(422, json={"detail": "too many texts"})
        )

        doc = MagicMock()
        doc.storage_path = "hr/policy.txt"
        doc.mime_type = "text/plain"

        repo = MagicMock(spec=KnowledgeBaseRepository)
        repo.get_document = AsyncMock(return_value=doc)
        repo.update_document_status = AsyncMock()
        repo.insert_chunks = AsyncMock()

        minio = MagicMock(spec=MinIOClient)
        minio.download_file = AsyncMock(return_value=text.encode("utf-8"))

        service = IngestionService(repo=repo, minio_client=minio, settings=settings)
        document_id = uuid.uuid4()
        await service.ingest(document_id, kb_type="hr")

        repo.insert_chunks.assert_not_awaited()
        status_call = repo.update_document_status.await_args
        assert status_call.args[1] == KnowledgeBaseDocumentStatus.ERROR
