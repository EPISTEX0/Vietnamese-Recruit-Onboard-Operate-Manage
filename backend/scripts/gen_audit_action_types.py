"""Sinh danh sách audit action type phía frontend từ enum backend.

`AuditActionType` (src/modules/identity/domain/entities.py) là nguồn sự thật
duy nhất cho những gì thực sự được ghi vào `audit_logs.action_type`. Script
này import thẳng enum đó (không regex) và ghi ra một artifact TypeScript để
frontend đối chiếu, thay vì duy trì một union viết tay có thể lệch.

Usage:
    cd backend && uv run python scripts/gen_audit_action_types.py

Sinh lại là thao tác tay — không có watch mode, không nối vào build/dev,
không pre-commit hook. `frontend/audit-action-types.test.ts` báo khi file
sinh ra không còn khớp enum.
"""

from __future__ import annotations

from pathlib import Path

from src.modules.identity.domain.entities import AuditActionType

OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "frontend"
    / "lib"
    / "audit-action-types.generated.ts"
)


def render() -> str:
    # Thứ tự khai báo enum — tất định, nên sinh lại không tạo diff giả.
    items = ",\n".join(f"  '{member.value}'" for member in AuditActionType)
    return (
        "// DO NOT EDIT — sinh từ AuditActionType"
        " (backend/src/modules/identity/domain/entities.py).\n"
        "// Sinh lại: cd backend && uv run python scripts/gen_audit_action_types.py\n"
        "\n"
        "export const AUDIT_ACTION_TYPES = [\n"
        f"{items},\n"
        "] as const;\n"
        "\n"
        "export type AuditActionType = (typeof AUDIT_ACTION_TYPES)[number];\n"
    )


def main() -> None:
    OUTPUT_PATH.write_text(render())
    print(f"Đã sinh {OUTPUT_PATH} với {len(list(AuditActionType))} giá trị.")


if __name__ == "__main__":
    main()
