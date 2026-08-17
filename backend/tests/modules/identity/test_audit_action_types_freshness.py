"""File sinh `audit-action-types.generated.ts` phải khớp enum `AuditActionType`.

Không có ràng buộc nào khác nối enum backend với artifact frontend — file đó
là tay sinh (`scripts/gen_audit_action_types.py`), không phải hằng tính lại
mỗi lần build. Test này là thứ duy nhất báo khi ai đó thêm/xoá một giá trị
trong enum mà quên chạy lại script.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.modules.identity.domain.entities import AuditActionType

GENERATED_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "frontend"
    / "lib"
    / "audit-action-types.generated.ts"
)


def _extract_generated_values(text: str) -> list[str]:
    """Mọi chuỗi trong mảng `AUDIT_ACTION_TYPES = [...]`."""
    match = re.search(r"AUDIT_ACTION_TYPES\s*=\s*\[(.*?)\]\s*as const", text, re.DOTALL)
    assert match is not None, "Không tìm thấy mảng AUDIT_ACTION_TYPES trong file sinh."
    return re.findall(r"'([^']+)'", match.group(1))


class TestExtractorSeesSomething:
    """Luật known-positive: một extractor trích sai (regex khớp type annotation,
    khối rỗng, ...) không được đỏ — nó xanh rỗng. Hai assert dưới đây chết nếu
    `_extract_generated_values` ngừng nhìn thấy giá trị thật.
    """

    def test_known_positive_value_present(self) -> None:
        text = GENERATED_PATH.read_text(encoding="utf-8")
        assert "whitelist_add" in _extract_generated_values(text)

    def test_floor_on_value_count(self) -> None:
        text = GENERATED_PATH.read_text(encoding="utf-8")
        assert len(_extract_generated_values(text)) >= 30


class TestGeneratedFileMatchesEnum:
    def test_generated_values_equal_enum_values(self) -> None:
        text = GENERATED_PATH.read_text(encoding="utf-8")
        generated = set(_extract_generated_values(text))
        enum_values = {member.value for member in AuditActionType}
        assert generated == enum_values, (
            "frontend/lib/audit-action-types.generated.ts lệch với AuditActionType — "
            "chạy `cd backend && uv run python scripts/gen_audit_action_types.py` "
            "rồi commit lại file sinh."
        )
