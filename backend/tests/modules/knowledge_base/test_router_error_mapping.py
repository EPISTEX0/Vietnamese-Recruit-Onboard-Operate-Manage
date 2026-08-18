"""The knowledge_base router maps HTTP status by exception type, not message text (#398).

Before this fix, ``replace_document_file`` picked 404 vs. 400 by checking
``"Không tìm thấy" in str(exc)`` on a caught ``ValueError``, and ``delete_document``
mapped *every* ``ValueError`` to 404 regardless of what actually went wrong. Both
call the route handlers directly with a stub :class:`DocumentService` so the
message text can be set independently of the exception type — the two tests
below deliberately mismatch text and type in each direction to prove the router
no longer branches on the string.

The service-ordering bug this text mismatch corresponds to in production (kb_type
validated before the not-found check, so a bad kb_type on DELETE fell into the
blanket-404 branch) is reproduced end-to-end, against the real service, by
``test_delete_document_invalid_kb_type_returns_400`` in ``test_kb05_api.py``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.modules.knowledge_base.api.router import delete_document, replace_document_file
from src.modules.knowledge_base.domain.exceptions import (
    DocumentNotFoundError,
    InvalidKnowledgeBaseTypeError,
)


class _RaisingDocumentService:
    """Stub DocumentService whose delete_document/replace_file always raise ``exc``."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def delete_document(self, document_id: uuid.UUID, kb_type: str = "hr") -> str | None:
        raise self._exc

    async def replace_file(
        self,
        *,
        document_id: uuid.UUID,
        file: object,
        file_name: str,
        mime_type: str,
        kb_type: str = "hr",
    ) -> object:
        raise self._exc


def _fake_upload(filename: str = "a.pdf", content_type: str = "application/pdf") -> SimpleNamespace:
    """A minimal stand-in for FastAPI's ``UploadFile`` — only .filename/.content_type/.file
    are read by ``replace_document_file`` before the service call raises."""
    return SimpleNamespace(filename=filename, content_type=content_type, file=object())


@pytest.mark.unit
class TestDeleteDocumentStatusMapping:
    """DELETE /documents/{id} maps by exception type."""

    async def test_not_found_error_worded_like_a_kb_type_error_still_returns_404(self):
        """DocumentNotFoundError text mimics an invalid-kb_type message; status stays 404.

        The old code's ``"Không tìm thấy" in str(exc)`` substring check is absent
        from this message, so it would have fallen to 400 under the old logic (in
        the one call site that had such a fallback) — proving type, not text,
        now decides.
        """
        service = _RaisingDocumentService(
            DocumentNotFoundError("kb_type không hợp lệ cho tài liệu này")
        )

        with pytest.raises(HTTPException) as exc_info:
            await delete_document(
                document_id=uuid.uuid4(), kb_type="hr", _user=None, service=service
            )

        assert exc_info.value.status_code == 404

    async def test_invalid_kb_type_error_worded_like_not_found_still_returns_400(self):
        """InvalidKnowledgeBaseTypeError text contains "Không tìm thấy"; status stays 400.

        This is the sharpest case: the message carries the exact substring the old
        code matched on for 404, but the exception's type says 400. Type-based
        mapping is unmoved by it.
        """
        service = _RaisingDocumentService(
            InvalidKnowledgeBaseTypeError("Không tìm thấy loại knowledge base hợp lệ")
        )

        with pytest.raises(HTTPException) as exc_info:
            await delete_document(
                document_id=uuid.uuid4(), kb_type="bogus", _user=None, service=service
            )

        assert exc_info.value.status_code == 400


@pytest.mark.unit
class TestReplaceDocumentFileStatusMapping:
    """PUT /documents/{id} maps by exception type — the original substring-match site."""

    async def test_not_found_error_worded_like_a_kb_type_error_still_returns_404(self):
        service = _RaisingDocumentService(
            DocumentNotFoundError("kb_type không hợp lệ cho tài liệu này")
        )

        with pytest.raises(HTTPException) as exc_info:
            await replace_document_file(
                document_id=uuid.uuid4(),
                file=_fake_upload(),
                kb_type="hr",
                _user=None,
                service=service,
            )

        assert exc_info.value.status_code == 404

    async def test_invalid_kb_type_error_worded_like_not_found_still_returns_400(self):
        service = _RaisingDocumentService(
            InvalidKnowledgeBaseTypeError("Không tìm thấy loại knowledge base hợp lệ")
        )

        with pytest.raises(HTTPException) as exc_info:
            await replace_document_file(
                document_id=uuid.uuid4(),
                file=_fake_upload(),
                kb_type="bogus",
                _user=None,
                service=service,
            )

        assert exc_info.value.status_code == 400
