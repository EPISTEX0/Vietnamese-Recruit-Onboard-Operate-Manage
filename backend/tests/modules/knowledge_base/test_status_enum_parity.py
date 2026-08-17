"""`DocumentStatus` union ở frontend phải khớp `KnowledgeBaseDocumentStatus`.

Không có ràng buộc runtime nào nối union TypeScript với enum backend — hai bên
trôi độc lập cho tới khi ai đó tự phát hiện. Test này là thứ duy nhất báo khi
một bên thêm/xoá giá trị mà quên sửa bên kia (Issue #362).
"""

from __future__ import annotations

import re
from pathlib import Path

from src.modules.knowledge_base.domain.enums import KnowledgeBaseDocumentStatus

FRONTEND_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "frontend"
    / "lib"
    / "api"
    / "knowledge-base.ts"
)


def _extract_document_status_values(text: str) -> list[str]:
    """Mọi chuỗi trong khai báo `export type DocumentStatus = "..." | "...";`."""
    match = re.search(r"export type DocumentStatus\s*=\s*([^;]+);", text)
    assert match is not None, "Không tìm thấy khai báo `export type DocumentStatus` trong file."
    return re.findall(r'"([^"]+)"', match.group(1))


class TestExtractorSeesSomething:
    """Luật known-positive: extractor trích sai (regex khớp interface khác,
    khối rỗng, ...) không được đỏ — nó xanh rỗng. Hai assert dưới đây chết
    nếu `_extract_document_status_values` ngừng nhìn thấy giá trị thật.
    """

    def test_known_positive_value_present(self) -> None:
        text = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "pending" in _extract_document_status_values(text)

    def test_floor_on_value_count(self) -> None:
        text = FRONTEND_PATH.read_text(encoding="utf-8")
        assert len(_extract_document_status_values(text)) >= 4


class TestFrontendUnionMatchesBackendEnum:
    def test_document_status_values_equal_enum_values(self) -> None:
        text = FRONTEND_PATH.read_text(encoding="utf-8")
        frontend_values = set(_extract_document_status_values(text))
        enum_values = {member.value for member in KnowledgeBaseDocumentStatus}
        assert frontend_values == enum_values, (
            "frontend/lib/api/knowledge-base.ts::DocumentStatus lệch với "
            "KnowledgeBaseDocumentStatus (backend/src/modules/knowledge_base/domain/enums.py) — "
            "sửa cả hai để cùng một tập giá trị."
        )
