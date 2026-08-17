"""Unit tests for Knowledge Base retrieval service and ContextBuilder integration.

Tests:
- RetrievalService.retrieve() formatting and error handling
- ContextBuilder.build_hr_context() with KB retrieval injection
- ContextBuilder.build_employee_context() with Employee KB isolation
- Similarity threshold: irrelevant queries → no injection
- Empty query / no retrieval service → graceful degradation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.modules.assistant.application.context_builder import ContextBuilder
from src.modules.knowledge_base.application.retrieval_service import RetrievalService
from src.modules.knowledge_base.domain.entities import KnowledgeBaseChunk
from src.modules.knowledge_base.infrastructure.config import KnowledgeBaseSettings
from src.modules.knowledge_base.infrastructure.repository import (
    KnowledgeBaseRepository,
)


def make_chunk(
    content: str,
    document_id: UUID | None = None,
    chunk_index: int = 0,
    embedding: list[float] | None = None,
) -> KnowledgeBaseChunk:
    """Factory for a KnowledgeBaseChunk with minimal required fields."""
    return KnowledgeBaseChunk(
        id=uuid4(),
        document_id=document_id or uuid4(),
        chunk_index=chunk_index,
        content=content,
        embedding=embedding or [0.0] * 768,
        token_count=42,
    )


# ---------------------------------------------------------------------------
# RetrievalService
# ---------------------------------------------------------------------------


class TestRetrievalService:
    """Unit tests for RetrievalService.retrieve()."""

    @pytest.fixture
    def repo(self) -> MagicMock:
        return MagicMock(spec=KnowledgeBaseRepository)

    @pytest.fixture
    def settings(self) -> KnowledgeBaseSettings:
        return KnowledgeBaseSettings(
            embedding_service_url="http://test-embed:8080",
        )

    @pytest.fixture
    def service(self, repo: MagicMock, settings: KnowledgeBaseSettings) -> RetrievalService:
        return RetrievalService(repo=repo, settings=settings)

    async def test_retrieve_returns_formatted_block(
        self, service: RetrievalService, repo: MagicMock
    ) -> None:
        """Retrieval returns properly formatted [TÀI LIỆU NỘI BỘ LIÊN QUAN] block."""
        chunk1 = make_chunk("Nội quy lao động quy định 12 ngày phép năm.", chunk_index=0)
        chunk2 = make_chunk("Quy chế phúc lợi thêm 3 ngày nghỉ hưởng lương.", chunk_index=1)

        repo.search_similar_chunks.return_value = [
            (chunk1, "Nội quy lao động 2025", 0.92),
            (chunk2, "Quy chế phúc lợi", 0.85),
        ]

        with patch(
            "src.modules.knowledge_base.application.retrieval_service.call_embedding_service",
            new_callable=AsyncMock,
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 768]

            result = await service.retrieve(query="Tôi có bao nhiêu ngày phép?", kb_types=["hr"])

        assert "[TÀI LIỆU NỘI BỘ LIÊN QUAN]" in result
        assert '(Nội quy lao động 2025): "Nội quy lao động quy định 12 ngày phép năm."' in result
        assert '(Quy chế phúc lợi): "Quy chế phúc lợi thêm 3 ngày nghỉ hưởng lương."' in result
        assert result.startswith("---")
        assert result.endswith("---")

    async def test_retrieve_empty_when_no_results(
        self, service: RetrievalService, repo: MagicMock
    ) -> None:
        """Returns empty string when no chunks meet similarity threshold."""
        repo.search_similar_chunks.return_value = []

        with patch(
            "src.modules.knowledge_base.application.retrieval_service.call_embedding_service",
            new_callable=AsyncMock,
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 768]

            result = await service.retrieve(query="Câu hỏi không liên quan gì", kb_types=["hr"])

        assert result == ""

    async def test_retrieve_graceful_embedding_failure(
        self, service: RetrievalService, repo: MagicMock
    ) -> None:
        """Returns empty string when embedding service fails (never blocks chat)."""
        with patch(
            "src.modules.knowledge_base.application.retrieval_service.call_embedding_service",
            new_callable=AsyncMock,
        ) as mock_embed:
            mock_embed.side_effect = Exception("Embedding service down")

            result = await service.retrieve(query="Test query", kb_types=["hr"])

        assert result == ""

    async def test_retrieve_graceful_db_failure(
        self, service: RetrievalService, repo: MagicMock
    ) -> None:
        """Returns empty string when pgvector search fails (never blocks chat)."""
        repo.search_similar_chunks.side_effect = Exception("DB error")

        with patch(
            "src.modules.knowledge_base.application.retrieval_service.call_embedding_service",
            new_callable=AsyncMock,
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 768]

            result = await service.retrieve(query="Test query", kb_types=["hr"])

        assert result == ""

    async def test_retrieve_trims_long_chunks(
        self, service: RetrievalService, repo: MagicMock
    ) -> None:
        """Chunks over 1500 chars are trimmed with '...'."""
        long_content = "X" * 2000
        chunk = make_chunk(long_content)
        repo.search_similar_chunks.return_value = [
            (chunk, "Tài liệu dài", 0.88),
        ]

        with patch(
            "src.modules.knowledge_base.application.retrieval_service.call_embedding_service",
            new_callable=AsyncMock,
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 768]

            result = await service.retrieve(query="Test", kb_types=["hr"])

        assert "..." in result
        assert len(result) < 2000  # Should be trimmed

    async def test_retrieve_passes_kb_types_to_repo(
        self, service: RetrievalService, repo: MagicMock
    ) -> None:
        """Verifies kb_types are forwarded to repository search."""
        chunk = make_chunk("Nội dung")
        repo.search_similar_chunks.return_value = [(chunk, "Test Doc", 0.80)]

        with patch(
            "src.modules.knowledge_base.application.retrieval_service.call_embedding_service",
            new_callable=AsyncMock,
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 768]

            await service.retrieve(query="Test", kb_types=["employee"])

        # Verify kb_types was passed correctly
        call_args = repo.search_similar_chunks.call_args
        assert call_args.kwargs["kb_types"] == ["employee"]


# ---------------------------------------------------------------------------
# ContextBuilder integration
# ---------------------------------------------------------------------------


class TestContextBuilderKBIntegration:
    """Tests for ContextBuilder with KB retrieval injection."""

    @pytest.fixture
    def mock_org_settings_repo(self) -> AsyncMock:
        repo = AsyncMock()
        repo.get_name = AsyncMock(return_value="Test Corp")
        return repo

    @pytest.fixture
    def mock_retrieval(self) -> AsyncMock:
        service = AsyncMock(spec=RetrievalService)
        service.retrieve.return_value = ""
        return service

    async def test_hr_context_includes_kb_block(self, mock_retrieval: AsyncMock) -> None:
        """HR context includes KB retrieval block when query provided."""
        mock_retrieval.retrieve.return_value = (
            '---\n[TÀI LIỆU NỘI BỘ LIÊN QUAN]\n(Nội quy): "Nội dung mẫu"\n---\n'
        )

        builder = ContextBuilder(
            retrieval_service=mock_retrieval,
        )

        result = await builder.build_hr_context(user_query="Quy định phép năm?")

        assert "[TÀI LIỆU NỘI BỘ LIÊN QUAN]" in result

    async def test_hr_context_no_kb_when_no_query(self, mock_retrieval: AsyncMock) -> None:
        """HR context does NOT call retrieval when no user_query provided."""
        mock_retrieval.retrieve.return_value = (
            '---\n[TÀI LIỆU NỘI BỘ LIÊN QUAN]\n(Nội quy): "Nội dung mẫu"\n---\n'
        )
        builder = ContextBuilder(
            retrieval_service=mock_retrieval,
        )

        result = await builder.build_hr_context(user_query=None)

        mock_retrieval.retrieve.assert_not_called()
        assert "[TÀI LIỆU NỘI BỘ LIÊN QUAN]" not in result

    async def test_hr_context_no_kb_when_empty_query(self, mock_retrieval: AsyncMock) -> None:
        """HR context does NOT call retrieval when query is empty/whitespace."""
        mock_retrieval.retrieve.return_value = (
            '---\n[TÀI LIỆU NỘI BỘ LIÊN QUAN]\n(Nội quy): "Nội dung mẫu"\n---\n'
        )
        builder = ContextBuilder(
            retrieval_service=mock_retrieval,
        )

        result = await builder.build_hr_context(user_query="   ")

        mock_retrieval.retrieve.assert_not_called()
        assert "[TÀI LIỆU NỘI BỘ LIÊN QUAN]" not in result

    async def test_hr_context_no_kb_when_service_none(self) -> None:
        """HR context degrades gracefully when no retrieval service."""
        builder = ContextBuilder(
            retrieval_service=None,
        )

        result = await builder.build_hr_context(user_query="Test query")

        # Should not raise, and should not include KB section
        assert "[TÀI LIỆU NỘI BỘ LIÊN QUAN]" not in result

    async def test_hr_context_no_kb_when_retrieval_fails(
        self, mock_org_settings_repo: AsyncMock, mock_retrieval: AsyncMock
    ) -> None:
        """HR context degrades gracefully when retrieval raises."""
        mock_retrieval.retrieve.side_effect = RuntimeError("Boom")

        builder = ContextBuilder(
            retrieval_service=mock_retrieval,
            org_settings_repo=mock_org_settings_repo,
        )

        result = await builder.build_hr_context(user_query="Test")

        # Should not include KB section, should still have standard context
        assert "[TÀI LIỆU NỘI BỘ LIÊN QUAN]" not in result
        # Standard context should still be present
        assert "Tổ chức: Test Corp" in result

    async def test_employee_context_uses_employee_kb(self, mock_retrieval: AsyncMock) -> None:
        """Employee context queries only Employee KB (security boundary)."""
        builder = ContextBuilder(
            employee_service=AsyncMock(),
            retrieval_service=mock_retrieval,
        )

        await builder.build_employee_context(
            employee_id=UUID("00000000-0000-0000-0000-000000000001"),
            user_query="Quyền lợi của tôi?",
        )

        # Verify retrieval was called with employee kb_type only
        call_kwargs = mock_retrieval.retrieve.call_args.kwargs
        assert call_kwargs["kb_types"] == ["employee"]

    async def test_similarity_threshold_no_injection(self, mock_retrieval: AsyncMock) -> None:
        """When retrieval returns empty (below threshold), no KB section injected."""
        mock_retrieval.retrieve.return_value = ""

        builder = ContextBuilder(
            retrieval_service=mock_retrieval,
        )

        result = await builder.build_hr_context(user_query="Câu hỏi không liên quan")

        assert "[TÀI LIỆU NỘI BỘ LIÊN QUAN]" not in result

    async def test_hr_context_kb_after_standard_context(
        self, mock_org_settings_repo: AsyncMock, mock_retrieval: AsyncMock
    ) -> None:
        """KB section appears after standard context blocks."""
        mock_retrieval.retrieve.return_value = (
            '---\n[TÀI LIỆU NỘI BỘ LIÊN QUAN]\n(Doc): "Nội dung"\n---\n'
        )

        builder = ContextBuilder(
            retrieval_service=mock_retrieval,
            onboarding_service=AsyncMock(),
            org_settings_repo=mock_org_settings_repo,
        )

        result = await builder.build_hr_context(user_query="Test query")

        # KB block should come after standard context (Tổ chức)
        org_idx = result.find("Tổ chức:")
        kb_idx = result.find("[TÀI LIỆU NỘI BỘ LIÊN QUAN]")
        assert org_idx < kb_idx, "KB section should appear after standard context blocks"


class TestEmployeeKBSecurityIsolation:
    """Security tests: Employee KB must NOT leak HR KB documents (Issue #260)."""

    @pytest.fixture
    def repo(self) -> MagicMock:
        return MagicMock(spec=KnowledgeBaseRepository)

    @pytest.fixture
    def settings(self) -> KnowledgeBaseSettings:
        return KnowledgeBaseSettings(
            embedding_service_url="http://test-embed:8080",
        )

    @pytest.fixture
    def service(self, repo: MagicMock, settings: KnowledgeBaseSettings) -> RetrievalService:
        return RetrievalService(repo=repo, settings=settings)

    async def test_employee_kb_search_excludes_hr_documents(
        self, service: RetrievalService, repo: MagicMock
    ) -> None:
        """When kb_types=["employee"], the repository must NOT receive kb_types=["hr"].

        The RetrievalService should pass kb_types=["employee"] to the repo,
        which means only employee_knowledge_base_* tables are queried.
        HR documents are physically isolated at the table level.
        """
        chunk = make_chunk("Nội dung dành cho nhân viên")
        repo.search_similar_chunks.return_value = [(chunk, "Tài liệu Nhân viên", 0.85)]

        with patch(
            "src.modules.knowledge_base.application.retrieval_service.call_embedding_service",
            new_callable=AsyncMock,
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 768]

            await service.retrieve(query="Câu hỏi từ nhân viên", kb_types=["employee"])

        # Verify kb_types was passed as ["employee"] to repo — never ["hr"]
        call_args = repo.search_similar_chunks.call_args
        assert call_args.kwargs["kb_types"] == ["employee"], (
            "Employee retrieval must only query employee tables, "
            f"got {call_args.kwargs['kb_types']}"
        )

    async def test_employee_context_never_queries_hr_kb(
        self,
    ) -> None:
        """Employee Assistant context builder ONLY queries Employee KB.

        Verifies that build_employee_context passes kb_types=["employee"]
        to the retrieval service, never ["hr"].
        """
        mock_retrieval = AsyncMock(spec=RetrievalService)
        mock_retrieval.retrieve.return_value = ""

        # Mock employee service to return basic profile
        mock_employee_svc = AsyncMock()
        mock_employee = MagicMock()
        mock_employee.full_name = "Nguyễn Văn A"
        mock_employee.department_id = None
        mock_employee.position_id = None
        mock_employee.employee_code = "NV001"
        mock_employee_svc.get_employee = AsyncMock(return_value=mock_employee)

        builder = ContextBuilder(
            employee_service=mock_employee_svc,
            retrieval_service=mock_retrieval,
        )

        await builder.build_employee_context(
            employee_id=UUID("00000000-0000-0000-0000-000000000001"),
            user_query="Chế độ phép năm của tôi?",
        )

        # Verify retrieval was called with ["employee"] only — the security boundary
        call_kwargs = mock_retrieval.retrieve.call_args.kwargs
        assert call_kwargs["kb_types"] == ["employee"], (
            f"Employee context must only query Employee KB, got {call_kwargs['kb_types']}"
        )

    async def test_repo_search_with_employee_kb_type_returns_only_employee_results(
        self, repo: MagicMock, settings: KnowledgeBaseSettings
    ) -> None:
        """Verify the contract: when repo receives kb_types=["employee"], it only
        searches employee tables.

        This is a contract test — the actual table-level isolation is enforced by
        the repository dispatching to the correct entity classes.
        """
        service = RetrievalService(repo=repo, settings=settings)

        chunk = make_chunk("Nội dung Employee KB")
        repo.search_similar_chunks.return_value = [(chunk, "Tài liệu Employee", 0.90)]

        with patch(
            "src.modules.knowledge_base.application.retrieval_service.call_embedding_service",
            new_callable=AsyncMock,
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 768]

            result = await service.retrieve(query="Câu hỏi nhân viên", kb_types=["employee"])

        # The service should return results (employee table queries work)
        assert result != ""
        assert "Tài liệu Employee" in result

        # And repo was called with correct kb_types
        call_args = repo.search_similar_chunks.call_args
        assert call_args.kwargs["kb_types"] == ["employee"]
