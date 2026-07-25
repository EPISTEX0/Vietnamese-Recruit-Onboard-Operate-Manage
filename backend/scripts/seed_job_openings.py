"""Seed test data for Job Openings (Vị trí Tuyển dụng) UI testing.

Creates job openings covering all 4 JobOpeningStatus values, plus supporting
departments and positions. Complements the 2 open job openings already created
by seed_candidates.py.

Usage:
    cd backend && uv run python scripts/seed_job_openings.py

Pre-requisites:
    - PostgreSQL running (docker compose up -d)
    - Migrations applied (uv run alembic upgrade head)
    - At least one user exists (admin@vroomhr.com)

Actions testable with this seed data:
    - List job openings with metrics cards
    - Filter by each JobOpeningStatus
    - Search by title
    - Create new job opening (via UI form)
    - Edit title, description, target_headcount
    - Status transitions: draft→open, open→closed, closed→open (reopen)
    - Cancel: draft→cancelled, open→cancelled
    - View headcount metrics (filled/remaining) for openings with candidates
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# ── Constants ──────────────────────────────────────────────────────────
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr"


def _now() -> datetime:
    return datetime.now(UTC)


# ── Main ───────────────────────────────────────────────────────────────


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with AsyncSession(engine) as session:
        # ── 0. Check if already seeded ────────────────────────────
        seed_titles = [
            "UI/UX Designer Senior",
            "QA Engineer",
            "HR Business Partner",
            "Junior Designer (đã huỷ)",
        ]
        result = await session.execute(
            text("SELECT 1 FROM job_openings WHERE title = ANY(:titles) LIMIT 1"),
            {"titles": seed_titles},
        )
        if result.scalar_one_or_none():
            print("⚠️  Seed job openings already exist. Skipping creation.")
            await session.close()
            return

        # ── 1. Look up admin user ──────────────────────────────────
        from src.modules.identity.domain.entities import User

        admin_result = await session.execute(
            select(User).where(User.email == "admin@vroomhr.com").limit(1)
        )
        admin = admin_result.scalar_one_or_none()
        if admin is None:
            print("❌ Admin user not found.")
            sys.exit(1)
        admin_id: UUID = admin.id
        print(f"✅ Admin user: {admin_id}")

        # ── 2. Create supporting Departments + Positions ───────────
        from src.modules.employee.domain.entities import Department, Position

        # Check if we can reuse existing departments
        existing_dept = await session.execute(
            select(Department).where(Department.name == "Công nghệ").limit(1)
        )
        dept_tech = existing_dept.scalar_one_or_none()
        if dept_tech is None:
            dept_tech = Department(
                id=uuid4(), name="Công nghệ", created_at=_now()
            )
            session.add(dept_tech)
            await session.flush()
            print(f"✅ Dept Tech: {dept_tech.id}")
        else:
            print(f"♻️  Reusing Dept Tech: {dept_tech.id}")

        # Additional departments
        dept_design = Department(
            id=uuid4(), name="Thiết kế", created_at=_now()
        )
        dept_hr = Department(
            id=uuid4(), name="Nhân sự", created_at=_now()
        )
        session.add_all([dept_design, dept_hr])
        await session.flush()
        print(f"✅ Dept Design: {dept_design.id}")
        print(f"✅ Dept HR: {dept_hr.id}")

        # Check existing positions + create new ones
        existing_pos = await session.execute(
            select(Position).where(Position.name == "Senior Python Developer").limit(1)
        )
        pos_python = existing_pos.scalar_one_or_none()

        if pos_python is None:
            pos_python = Position(
                id=uuid4(),
                name="Senior Python Developer",
                department_id=dept_tech.id,
                created_at=_now(),
            )
            session.add(pos_python)
            await session.flush()
            print(f"✅ Position Python: {pos_python.id}")
        else:
            print(f"♻️  Reusing Position Python: {pos_python.id}")

        pos_designer = Position(
            id=uuid4(),
            name="UI/UX Designer",
            department_id=dept_design.id,
            created_at=_now(),
        )
        pos_hrbp = Position(
            id=uuid4(),
            name="HR Business Partner",
            department_id=dept_hr.id,
            created_at=_now(),
        )
        pos_qa = Position(
            id=uuid4(),
            name="QA Engineer",
            department_id=dept_tech.id,
            created_at=_now(),
        )
        session.add_all([pos_designer, pos_hrbp, pos_qa])
        await session.flush()
        print(f"✅ Position Designer: {pos_designer.id}")
        print(f"✅ Position HRBP: {pos_hrbp.id}")
        print(f"✅ Position QA: {pos_qa.id}")

        # ── 3. Create Job Openings (4 statuses) ────────────────────
        from src.modules.recruitment.domain.entities import JobOpening
        from src.modules.recruitment.domain.enums import JobOpeningStatus
        from src.modules.gmail.domain.entities import EmailMessage  # for FK resolution

        now = _now()

        # #1: draft — test Edit, Open, Cancel
        jo_draft = JobOpening(
            id=uuid4(),
            title="UI/UX Designer Senior",
            description=(
                "Tuyển 1 UI/UX Designer Senior cho team Product Design. "
                "Yêu cầu: 3+ năm kinh nghiệm, thành thạo Figma, có portfolio. "
                "Ưu tiên ứng viên có kinh nghiệm Design System."
            ),
            position_id=pos_designer.id,
            target_headcount=1,
            status=JobOpeningStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )

        # #2: open — test Close, Cancel, Edit, view headcount
        # (Complements the 2 open openings from seed_candidates.py)
        jo_open = JobOpening(
            id=uuid4(),
            title="QA Engineer",
            description=(
                "Tuyển 2 QA Engineer cho team chất lượng. "
                "Yêu cầu: biết viết test case, experience với Selenium/Cypress. "
                "Ưu tiên có ISTQB foundation."
            ),
            position_id=pos_qa.id,
            target_headcount=2,
            status=JobOpeningStatus.OPEN,
            opened_at=now,
            created_at=now,
            updated_at=now,
        )

        # #3: closed — test Reopen
        jo_closed = JobOpening(
            id=uuid4(),
            title="HR Business Partner",
            description=(
                "Tuyển 1 HRBP cho mảng Công nghệ. "
                "Yêu cầu: 5+ năm kinh nghiệm HR, hiểu biết về mảng tech. "
                "Đã đóng do tìm được ứng viên nội bộ."
            ),
            position_id=pos_hrbp.id,
            target_headcount=1,
            status=JobOpeningStatus.CLOSED,
            opened_at=datetime(2025, 12, 1, tzinfo=UTC),
            closed_at=now,
            created_at=datetime(2025, 11, 15, tzinfo=UTC),
            updated_at=now,
        )

        # #4: cancelled — terminal, view only
        jo_cancelled = JobOpening(
            id=uuid4(),
            title="Junior Designer (đã huỷ)",
            description=(
                "Tuyển 2 Junior Designer. Đã huỷ do thay đổi chiến lược nhân sự — "
                "quyết định chuyển sang thuê freelance thay vì full-time."
            ),
            position_id=pos_designer.id,
            target_headcount=2,
            status=JobOpeningStatus.CANCELLED,
            cancelled_at=now,
            created_at=datetime(2025, 10, 1, tzinfo=UTC),
            updated_at=now,
        )

        session.add_all([jo_draft, jo_open, jo_closed, jo_cancelled])
        await session.flush()

        for jo in [jo_draft, jo_open, jo_closed, jo_cancelled]:
            print(f"✅ Job Opening [{jo.status}]: {jo.id} — {jo.title}")

        # Save IDs before commit (session expires objects)
        jo_draft_id: UUID = jo_draft.id
        jo_open_id: UUID = jo_open.id
        jo_closed_id: UUID = jo_closed.id
        jo_cancelled_id: UUID = jo_cancelled.id

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! Job Openings test data ready.")
        print(f"\nSummary:")
        print(f"  Departments:     3 (Công nghệ, Thiết kế, Nhân sự)")
        print(f"  Positions:       4 (Python, Designer, HRBP, QA)")
        print(f"  Job Openings:    4 new + 2 existing from seed_candidates = 6 total")
        print(f"\nActions to test on UI (/recruitment/job-openings):")
        print(f"  Metrics cards:   6 total (1 draft, 3 open, 1 closed, 1 cancelled)")
        print(f"  Draft     ({jo_draft_id}):  Edit → Open → Close, hoặc Cancel")
        print(f"  Open      ({jo_open_id}):  Edit, Close, Cancel")
        print(f"  Closed    ({jo_closed_id}):  Reopen")
        print(f"  Cancelled ({jo_cancelled_id}): View only (terminal)")
        print(f"\n  Existing open (from seed_candidates.py): Python + React — test headcount metrics")


if __name__ == "__main__":
    asyncio.run(seed())
