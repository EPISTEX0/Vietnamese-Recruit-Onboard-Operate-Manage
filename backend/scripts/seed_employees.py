"""Seed test data for Employee List (Danh sách Nhân viên) UI testing.

Creates employees across all departments/positions with varied fields
for testing the employee list page with filtering, search, and pagination.

Usage:
    cd backend && uv run python scripts/seed_employees.py

Pre-requisites:
    - seed_candidates.py + seed_job_openings.py must have been run first
    - PostgreSQL running (docker compose up -d)

Actions testable with this seed data:
    /employees page:
      - Table with 12 employees (10 new + 2 from onboarding seed)
      - Filter by department, position, status (active/inactive/all)
      - Search by name, email, employee_code
      - Pagination (page_size=20, so all on 1 page)
    /employees/[id] detail page:
      - View full employee profile
      - Edit employee fields
      - Document vault (upload/download/delete)
      - Employee account management (create/delete account)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, date
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# ── Constants ──────────────────────────────────────────────────────────
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr"


def _now() -> datetime:
    return datetime.now(UTC)


# ── Employee data ──────────────────────────────────────────────────────

EMPLOYEES = [
    # (code, name, email, phone, dob, gender, dept, pos, start, active, id_num, tax, contract)
    (
        "NV-003", "Trần Văn Hải", "hai.tran@example.com",
        "0978123456", date(1992, 3, 15), "male",
        "Công nghệ", "Senior Python Developer",
        date(2024, 1, 15), True,
        "001092003015", "TX001", "full_time",
    ),
    (
        "NV-004", "Nguyễn Thị Kim", "kim.nguyen@example.com",
        "0967234567", date(1995, 7, 22), "female",
        "Công nghệ", "Frontend React Developer",
        date(2024, 3, 1), True,
        "001095007022", "TX002", "full_time",
    ),
    (
        "NV-005", "Lê Minh Tuấn", "tuan.le@example.com",
        "0956345678", date(1990, 11, 8), "male",
        "Công nghệ", "QA Engineer",
        date(2024, 6, 1), True,
        "001090011008", "TX003", "full_time",
    ),
    (
        "NV-006", "Phan Thị Mai", "mai.phan@example.com",
        "0945456789", date(1997, 5, 30), "female",
        "Thiết kế", "UI/UX Designer",
        date(2024, 2, 10), True,
        "001097005030", "TX004", "full_time",
    ),
    (
        "NV-007", "Võ Thanh Sơn", "son.vo@example.com",
        "0934567890", date(1988, 9, 14), "male",
        "Nhân sự", "HR Business Partner",
        date(2023, 8, 1), True,
        "001088009014", "TX005", "full_time",
    ),
    (
        "NV-008", "Đặng Thu Hương", "huong.dang@example.com",
        "0923678901", date(1994, 1, 25), "female",
        "Sản phẩm", "Frontend React Developer",
        date(2024, 4, 15), True,
        "001094001025", "TX006", "full_time",
    ),
    (
        "NV-009", "Bùi Quốc Đạt", "dat.bui@example.com",
        "0912789012", date(1993, 6, 18), "male",
        "Công nghệ", "Senior Python Developer",
        date(2024, 5, 1), True,
        "001093006018", "TX007", "probation",
    ),
    (
        "NV-010", "Hồ Thị Lan", "lan.ho@example.com",
        "0989890123", date(1996, 12, 3), "female",
        "Thiết kế", "UI/UX Designer",
        date(2024, 7, 1), True,
        "001096012003", "TX008", "part_time",
    ),
    (
        "NV-011", "Đỗ Hoàng Long", "long.do@example.com",
        "0978901234", date(1985, 4, 10), "male",
        "Công nghệ", "QA Engineer",
        date(2023, 3, 15), False,  # inactive (resigned)
        "001085004010", "TX009", "full_time",
    ),
    (
        "NV-012", "Mai Thị Yến", "yen.mai@example.com",
        "0967012345", date(1998, 8, 20), "female",
        "Sản phẩm", "UI/UX Designer",
        date(2024, 8, 1), False,  # inactive (on leave)
        "001098008020", "TX010", "contract",
    ),
]


# ── Main ───────────────────────────────────────────────────────────────


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with AsyncSession(engine) as session:
        # ── 0. Check if already seeded ────────────────────────────
        result = await session.execute(
            text("SELECT 1 FROM employees WHERE employee_code IN ('NV-003','NV-004','NV-005','NV-006','NV-007','NV-008','NV-009','NV-010','NV-011','NV-012') LIMIT 1")
        )
        if result.scalar_one_or_none():
            print("⚠️  Seed employees already exist. Skipping creation.")
            await session.close()
            return

        # ── 1. Look up departments and positions ───────────────────
        from src.modules.employee.domain.entities import Department, Position

        dept_result = await session.execute(select(Department))
        depts: dict[str, Department] = {d.name: d for d in dept_result.scalars().all()}

        pos_result = await session.execute(select(Position))
        positions: dict[str, Position] = {p.name: p for p in pos_result.scalars().all()}

        print(f"✅ Departments: {list(depts.keys())}")
        print(f"✅ Positions: {list(positions.keys())}")

        # ── 2. Create Employees ────────────────────────────────────
        from src.modules.employee.domain.entities import Employee
        from src.modules.gmail.domain.entities import EmailMessage  # FK resolution

        now = _now()
        employees: list[Employee] = []

        for code, name, email, phone, dob, gender, dept_name, pos_name, start, active, id_num, tax, contract in EMPLOYEES:
            dept = depts.get(dept_name)
            pos = positions.get(pos_name)
            if dept is None or pos is None:
                print(f"⚠️  Skipping {name}: dept={dept_name} pos={pos_name} not found")
                continue

            employees.append(
                Employee(
                    id=uuid4(),
                    employee_code=code,
                    full_name=name,
                    email=email,
                    phone=phone,
                    date_of_birth=dob,
                    gender=gender,
                    department_id=dept.id,
                    position_id=pos.id,
                    start_date=start,
                    is_active=active,
                    id_number=id_num,
                    tax_code=tax,
                    contract_type=contract,
                    created_at=now,
                    updated_at=now,
                )
            )

        session.add_all(employees)
        await session.flush()

        for emp in employees:
            print(f"✅ Employee [{emp.employee_code}]: {emp.full_name} — "
                  f"{'active' if emp.is_active else 'inactive'} — {emp.department_id}")

        active = sum(1 for e in employees if e.is_active)
        inactive = sum(1 for e in employees if not e.is_active)

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! Employee test data ready.")
        print(f"\nSummary:")
        print(f"  New Employees:  {len(employees)} ({active} active, {inactive} inactive)")
        print(f"  Total (with onboarding seed): ~12 employees")
        print(f"\nActions to test on UI (/employees):")
        print(f"  Table with all employees")
        print(f"  Filter: department (Công nghệ=5, Thiết kế=2, Nhân sự=1, Sản phẩm=2)")
        print(f"  Filter: status (active=10, inactive=2)")
        print(f"  Search: name, email, employee_code")
        print(f"  Click row → /employees/[id] detail page:")
        print(f"    - Edit fields")
        print(f"    - Document vault")
        print(f"    - Employee account management")


if __name__ == "__main__":
    asyncio.run(seed())
