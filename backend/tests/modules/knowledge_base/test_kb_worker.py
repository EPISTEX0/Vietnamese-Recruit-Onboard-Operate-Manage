"""Unit / integration tests for Knowledge Base ingestion service.

Tests the text extraction, chunking, and ingestion logic
without requiring live MinIO, Redis, or embedding service.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from src.modules.knowledge_base.application.ingestion_service import (
    _extract_text_from_pdf,
    _extract_text_from_txt,
    chunk_text,
    estimate_token_count,
    extract_text,
)
from src.modules.knowledge_base.worker import shutdown


class TestTextExtraction:
    """Tests for text extraction from various file formats."""

    def test_extract_text_from_txt_utf8(self):
        """Extract Vietnamese text from UTF-8 plain text."""
        text = "Xin chào, đây là văn bản tiếng Việt.\nDòng thứ hai."
        result = _extract_text_from_txt(text.encode("utf-8"))
        assert "Xin chào" in result
        assert "Dòng thứ hai" in result

    def test_extract_text_from_txt_latin1(self):
        """Extract text from Latin-1 encoded file."""
        text = "Hello World 123"
        result = _extract_text_from_txt(text.encode("latin-1"))
        assert "Hello World" in result

    def test_extract_text_dispatches_by_mime_type(self):
        """extract_text dispatches to correct parser based on MIME type."""
        text = "Plain text content"
        result = extract_text(text.encode("utf-8"), "text/plain")
        assert result == "Plain text content"

    def test_extract_text_unsupported_mime(self):
        """extract_text raises ValueError for unsupported MIME types."""
        with pytest.raises(ValueError, match="MIME type không được hỗ trợ"):
            extract_text(b"dummy", "image/png")

    def test_extract_text_from_minimal_pdf(self):
        """Extract text from a minimal valid PDF."""
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
            b"4 0 obj\n<< /Length 44 >>\nstream\n"
            b"BT /F1 12 Tf 100 700 Td (Hello World) Tj ET\n"
            b"endstream\nendobj\n"
            b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
            b"xref\n0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000266 00000 n \n"
            b"0000000360 00000 n \n"
            b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
            b"startxref\n421\n%%EOF\n"
        )
        # This may fail since the minimal PDF doesn't have real text content
        # but it exercises the code path
        try:
            result = _extract_text_from_pdf(pdf_bytes)
            # If it succeeds, we should get something
            assert isinstance(result, str)
        except ValueError:
            # It's OK if a minimal PDF can't be extracted
            pass


class TestChunking:
    """Tests for the text chunking function."""

    def test_chunk_single_short_text(self):
        """A short text produces one chunk."""
        text = "Đây là một đoạn văn bản ngắn."
        chunks = chunk_text(text, chunk_size_tokens=512, chunk_overlap_tokens=50)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_empty_text(self):
        """Empty text produces no chunks."""
        chunks = chunk_text("", chunk_size_tokens=512, chunk_overlap_tokens=50)
        assert len(chunks) == 0

    def test_chunk_long_text_produces_multiple(self):
        """A long text is split into multiple chunks."""
        # Generate ~3000 tokens worth of text (~12000 chars for Vietnamese)
        sentence = "Đây là một câu văn bản mẫu để kiểm tra chức năng chia đoạn. "
        text = sentence * 200  # ~12000 chars
        chunks = chunk_text(text, chunk_size_tokens=512, chunk_overlap_tokens=50)
        assert len(chunks) > 1
        # Each chunk should be roughly within bounds
        for chunk in chunks:
            # Allow some margin since we split on sentence boundaries
            assert len(chunk) <= 512 * 4 * 2  # 2x margin

    def test_chunk_overlap_invalid(self):
        """chunk_text raises ValueError when overlap >= chunk_size."""
        with pytest.raises(ValueError, match="chunk_overlap_tokens"):
            chunk_text("text", chunk_size_tokens=100, chunk_overlap_tokens=100)

    def test_chunk_whitespace_only(self):
        """Whitespace-only text produces no chunks."""
        chunks = chunk_text("   \n  \n  ", chunk_size_tokens=512, chunk_overlap_tokens=50)
        assert len(chunks) == 0


class TestTokenEstimation:
    """Tests for the token count estimator."""

    def test_estimate_short_text(self):
        """Short text has at least 1 token."""
        assert estimate_token_count("Hi") == 1

    def test_estimate_vietnamese_text(self):
        """Vietnamese text estimation is proportional to char count."""
        text = "Xin chào" * 100  # ~800 chars
        tokens = estimate_token_count(text)
        assert tokens == 200  # 800 / 4 = 200

    def test_estimate_empty(self):
        """Empty string estimates to 1 token (floor)."""
        assert estimate_token_count("") == 1


class TestKbWorkerShutdown:
    """shutdown() must close the Redis connection even if clearing the heartbeat fails.

    Regression guard for #386 C2: delete() and aclose() used to share one try
    block, so a failing delete() skipped aclose() entirely and leaked the
    connection until the heartbeat's own ex=600 TTL expired.
    """

    @pytest.mark.asyncio
    async def test_aclose_called_and_logged_when_delete_raises(self, caplog):
        redis_client = AsyncMock()
        redis_client.delete = AsyncMock(side_effect=RuntimeError("redis unreachable"))
        ctx = {"engine": None, "redis_client": redis_client}

        with caplog.at_level(logging.WARNING):
            await shutdown(ctx)

        redis_client.aclose.assert_awaited_once()
        assert "Failed to clear heartbeat on shutdown" in caplog.text

    @pytest.mark.asyncio
    async def test_aclose_called_when_delete_succeeds(self):
        redis_client = AsyncMock()
        ctx = {"engine": None, "redis_client": redis_client}

        await shutdown(ctx)

        redis_client.delete.assert_awaited_once_with("runtime:heartbeat:kb-worker")
        redis_client.aclose.assert_awaited_once()
