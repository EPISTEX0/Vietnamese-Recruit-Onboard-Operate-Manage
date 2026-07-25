"""Seed test data for Onboarding Processes UI testing.

Creates Employees, OnboardingProcesses with tasks across both statuses
(in_progress, complete). Requires seed candidates from seed_candidates.py.

Usage:
    cd backend && uv run python scripts/seed_onboarding.py

Pre-requisites:
    - seed_candidates.py must have been run first
    - PostgreSQL running (docker compose up -d)

Actions testable with this seed data:
    /onboarding page:
      - Counts cards (total, in_progress, complete)
      - Filter by all / in_progress / complete
      - Expand process → view task checklist
      - Toggle task done/pending (in_progress only)
      - View missing fields warning
      - Activation hint (all tasks done + complete status)
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, date
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
        result = await session.execute(
            text("SELECT 1 FROM onboarding_processes WHERE employee_id IN ("
                 "SELECT id FROM employees WHERE email LIKE '%@example.com') LIMIT 1")
        )
        if result.scalar_one_or_none():
            print("⚠️  Seed onboarding already exists. Skipping creation.")
            await session.close()
            return

        # ── 1. Look up seed data ───────────────────────────────────
        from src.modules.recruitment.domain.entities import Candidate
        from src.modules.employee.domain.entities import Department, Position
        from src.modules.identity.domain.entities import User

        cands = await session.execute(
            select(Candidate).where(Candidate.email.like("%@example.com"))
        )
        candidates_by_email: dict[str, Candidate] = {
            c.email: c for c in cands.scalars().all()
        }

        if len(candidates_by_email) < 6:
            print("❌ Seed candidates not found. Run seed_candidates.py first.")
            sys.exit(1)

        cand_accepted = candidates_by_email["dung.pham@example.com"]
        cand_new = candidates_by_email["an.nguyen@example.com"]

        admin = (await session.execute(
            select(User).where(User.email == "admin@vroomhr.com").limit(1)
        )).scalar_one()
        admin_id: UUID = admin.id

        # Get or create department and position
        dept = (await session.execute(
            select(Department).where(Department.name == "Công nghệ").limit(1)
        )).scalar_one()

        pos = (await session.execute(
            select(Position).where(Position.name == "Senior Python Developer").limit(1)
        )).scalar_one()

        # ── 2. Import models ───────────────────────────────────────
        from src.modules.employee.domain.entities import Employee
        from src.modules.onboarding.domain.entities import (
            OnboardingProcess, OnboardingTask,
        )
        from src.modules.gmail.domain.entities import EmailMessage  # FK resolution

        now = _now()
        today = date.today()

        # ── 3. Create Employees ────────────────────────────────────
        emp1 = Employee(
            id=uuid4(),
            employee_code="NV-001",
            full_name="Phạm Thị Dung",
            email="dung.pham-hr@example.com",
            phone="0934567890",
            department_id=dept.id,
            position_id=pos.id,
            start_date=today,
            candidate_id=cand_accepted.id,
            is_active=False,  # inactive until onboarding complete
            created_at=now,
            updated_at=now,
        )
        emp2 = Employee(
            id=uuid4(),
            employee_code="NV-002",
            full_name="Nguyễn Văn An",
            email="an.nguyen-hr@example.com",
            phone="0901234567",
            department_id=dept.id,
            position_id=pos.id,
            start_date=today,
            candidate_id=cand_new.id,
            is_active=True,  # already active (onboarding complete)
            created_at=now,
            updated_at=now,
        )
        session.add_all([emp1, emp2])
        await session.flush()
        emp1_id: UUID = emp1.id
        emp2_id: UUID = emp2.id
        print(f"✅ Employee 1 (inactive): {emp1_id}")
        print(f"✅ Employee 2 (active):   {emp2_id}")

        # ── 4. Create Onboarding Processes ─────────────────────────
        proc1 = OnboardingProcess(
            id=uuid4(),
            candidate_id=cand_accepted.id,
            employee_id=emp1_id,
            status="in_progress",
            created_at=now,
            updated_at=now,
        )
        proc2 = OnboardingProcess(
            id=uuid4(),
            candidate_id=cand_new.id,
            employee_id=emp2_id,
            status="complete",
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([proc1, proc2])
        await session.flush()
        proc1_id: UUID = proc1.id
        proc2_id: UUID = proc2.id
        print(f"✅ Onboarding Process [in_progress]: {proc1_id}")
        print(f"✅ Onboarding Process [complete]:    {proc2_id}")

        # ── 5. Create Onboarding Tasks ─────────────────────────────
        tasks_data = [
            # Process 1 (in_progress) — 2/4 done
            ("setup_profile", "Tạo hồ sơ nhân sự", 1, "done", admin_id, now),
            ("setup_accounts", "Tạo tài khoản hệ thống", 2, "done", admin_id, now),
            ("assign_equipment", "Bàn giao thiết bị", 3, "pending", None, None),
            ("orientation", "Đào tạo định hướng", 4, "pending", None, None),
        ]

        tasks: list[OnboardingTask] = []
        for task_key, name, order_idx, status, completed_by, completed_at in tasks_data:
            tasks.append(
                OnboardingTask(
                    process_id=proc1_id,
                    task_key=task_key,
                    name=name,
                    status=status,
                    order_index=order_idx,
                    completed_at=completed_at,
                    completed_by_user_id=completed_by,
                    created_at=now,
                )
            )

        # Process 2 (complete) — 4/4 done
        for task_key, name, order_idx in [
            ("setup_profile", "Tạo hồ sơ nhân sự", 1),
            ("setup_accounts", "Tạo tài khoản hệ thống", 2),
            ("assign_equipment", "Bàn giao thiết bị", 3),
            ("orientation", "Đào tạo định hướng", 4),
        ]:
            tasks.append(
                OnboardingTask(
                    process_id=proc2_id,
                    task_key=task_key,
                    name=name,
                    status="done",
                    order_index=order_idx,
                    completed_at=now,
                    completed_by_user_id=admin_id,
                    created_at=now,
                )
            )

        session.add_all(tasks)
        await session.flush()
        print(f"✅ Onboarding Tasks: {len(tasks)} created (8 total)")

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! Onboarding test data ready.")
        print(f"\nSummary:")
        print(f"  Employees:            2 (1 inactive, 1 active)")
        print(f"  OnboardingProcesses:  2 (1 in_progress, 1 complete)")
        print(f"  OnboardingTasks:      8 (4 per process)")
        print(f"\nActions to test on UI (/onboarding):")
        print(f"  Counts: 2 total, 1 in_progress, 1 complete")
        print(f"  Filter by all / in_progress / complete")
        print(f"  Process #1 ({proc1_id}) — Phạm Thị Dung [in_progress]:")
        print(f"    2/4 tasks done. Toggle 'Bàn giao thiết bị' + 'Đào tạo định hướng'")
        print(f"    Has missing_setup_fields warning")
        print(f"  Process #2 ({proc2_id}) — Nguyễn Văn An [complete]:")
        print(f"    4/4 tasks done. Shows activation complete.")
        print(f"    Tasks locked (cannot toggle).")


if __name__ == "__main__":
    asyncio.run(seed())
