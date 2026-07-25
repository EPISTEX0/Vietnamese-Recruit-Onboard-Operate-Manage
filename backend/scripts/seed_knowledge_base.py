"""Seed test data for Knowledge Base (Tài liệu nội bộ) UI testing.

Creates documents in both HR and Employee KB tabs with various categories
and statuses for testing filters, upload, edit, re-upload, and delete.

Usage:
    cd backend && uv run python scripts/seed_knowledge_base.py

Pre-requisites:
    - PostgreSQL running (docker compose up -d)
    - No hard FK dependencies

Actions testable with this seed data:
    /knowledge-base page:
      - Tabs: HR Documents / Employee Documents
      - Filter: category (7 values), status (pending/processing/ready/error)
      - View list with display name, file info, chunk count, category, dates
      - Upload: file + name + category → processes to ready
      - Edit metadata: display name, category, description
      - Re-upload: replace file
      - Delete: confirm modal
      - Detail modal: view full metadata + error details
      - Error tooltip: hover error badge to see message
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# ── Constants ──────────────────────────────────────────────────────────
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr"


def _now() -> datetime:
    return datetime.now(UTC)


# ── Document data ──────────────────────────────────────────────────────
# (kb_type, display_name, category, status, file_name, size, mime, chunks, description, error)

HR_DOCS = [
    (
        "hr", "Quy chế làm việc 2026", "policy", "ready",
        "quy-che-lam-viec-2026.pdf", 450_000, "application/pdf", 24,
        "Quy chế làm việc áp dụng từ 01/01/2026. Bao gồm giờ làm việc, nghỉ phép, đánh giá hiệu suất.",
        None,
    ),
    (
        "hr", "Quy trình tuyển dụng nhân sự", "procedure", "ready",
        "quy-trinh-tuyen-dung-v2.pdf", 320_000, "application/pdf", 18,
        "Hướng dẫn chi tiết quy trình tuyển dụng từ đăng tin đến onboard. Cập nhật tháng 3/2026.",
        None,
    ),
    (
        "hr", "Mẫu đơn xin nghỉ phép", "form", "ready",
        "mau-don-xin-nghi-phep.docx", 85_000, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 4,
        "Form mẫu cho nhân viên xin nghỉ phép năm, nghỉ ốm, nghỉ không lương.",
        None,
    ),
    (
        "hr", "Hướng dẫn sử dụng hệ thống Vroom HR", "training", "ready",
        "huong-dan-vroom-hr.pdf", 1_200_000, "application/pdf", 56,
        "Tài liệu hướng dẫn toàn diện cho nhân viên mới về cách sử dụng Vroom HR.",
        None,
    ),
    (
        "hr", "Hợp đồng lao động mẫu", "legal", "ready",
        "hop-dong-lao-dong-mau-2026.docx", 95_000, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 6,
        "Mẫu hợp đồng lao động chuẩn theo Bộ luật Lao động 2019, cập nhật 2026.",
        None,
    ),
    (
        "hr", "Báo cáo nhân sự quý 2-2026", "general", "error",
        "bao-cao-nhan-su-q2-2026.pdf", 2_500_000, "application/pdf", 0,
        None,
        "Không thể trích xuất văn bản: file PDF bị mã hóa hoặc chứa toàn ảnh scan chất lượng thấp. Vui lòng tải lên bản PDF có thể tìm kiếm.",
    ),
]

EMP_DOCS = [
    (
        "employee", "Sổ tay nhân viên mới", "training", "ready",
        "so-tay-nhan-vien-2026.pdf", 680_000, "application/pdf", 32,
        "Tài liệu onboarding: văn hóa công ty, phúc lợi, quy tắc ứng xử, an toàn lao động.",
        None,
    ),
    (
        "employee", "Chính sách bảo hiểm & phúc lợi", "policy", "ready",
        "chinh-sach-bao-hiem-2026.pdf", 280_000, "application/pdf", 14,
        "Chi tiết các gói bảo hiểm sức khỏe, bảo hiểm xã hội, và phúc lợi khác cho nhân viên.",
        None,
    ),
]


# ── Main ───────────────────────────────────────────────────────────────


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with AsyncSession(engine) as session:
        # ── 0. Check if already seeded ────────────────────────────
        from src.modules.knowledge_base.domain.entities import KnowledgeBaseDocument

        existing = await session.execute(
            select(KnowledgeBaseDocument).where(
                KnowledgeBaseDocument.display_name == "Quy chế làm việc 2026"
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            print("⚠️  Seed knowledge base documents already exist. Skipping creation.")
            await session.close()
            return

        # ── 1. Create HR Documents ─────────────────────────────────
        from src.modules.knowledge_base.domain.entities import EmployeeKnowledgeBaseDocument
        from src.modules.gmail.domain.entities import EmailMessage  # FK resolution

        now = _now()
        hr_count = 0
        emp_count = 0

        for kb_type, name, cat, status, fname, size, mime, chunks, desc, err in HR_DOCS:
            doc = KnowledgeBaseDocument(
                id=uuid4(),
                display_name=name,
                category=cat,
                file_name=fname,
                storage_path=f"knowledge-base/hr/{fname}",
                file_size=size,
                mime_type=mime,
                status=status,
                error_message=err,
                chunk_count=chunks,
                description=desc,
                kb_type=kb_type,
                created_at=now,
                updated_at=now,
            )
            session.add(doc)
            hr_count += 1

        for kb_type, name, cat, status, fname, size, mime, chunks, desc, err in EMP_DOCS:
            doc = EmployeeKnowledgeBaseDocument(
                id=uuid4(),
                display_name=name,
                category=cat,
                file_name=fname,
                storage_path=f"knowledge-base/employee/{fname}",
                file_size=size,
                mime_type=mime,
                status=status,
                error_message=err,
                chunk_count=chunks,
                description=desc,
                kb_type=kb_type,
                created_at=now,
                updated_at=now,
            )
            session.add(doc)
            emp_count += 1

        await session.flush()

        print(f"✅ HR Documents:     {hr_count}")
        print(f"✅ Employee Documents: {emp_count}")

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        total = hr_count + emp_count
        print("\n🎉 Seed complete! Knowledge Base test data ready.")
        print(f"\nSummary:")
        print(f"  Documents:    {total} ({hr_count} HR + {emp_count} Employee)")
        print(f"  Categories:   policy, procedure, form, training, legal, general")
        print(f"  Statuses:     {total - 1} ready + 1 error (Báo cáo nhân sự)")
        print(f"\nActions to test on UI (/knowledge-base):")
        print(f"  Tabs: HR Documents (6) / Employee Documents (2)")
        print(f"  Filter: category (7 values) — test all")
        print(f"  Filter: status — 'ready' vs 'error'")
        print(f"  Upload: file → name + category → processing → ready")
        print(f"  Edit: ✏️ icon → change display name, category, description")
        print(f"  Re-upload: ☁️↑ icon → replace file")
        print(f"  Delete: 🗑️ icon → confirm modal")
        print(f"  Detail: click name → modal with full metadata")
        print(f"  Error: 'Báo cáo nhân sự' has error badge → hover for tooltip")


if __name__ == "__main__":
    import sys
    asyncio.run(seed())
