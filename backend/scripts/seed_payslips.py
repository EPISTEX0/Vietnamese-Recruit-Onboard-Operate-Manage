"""Seed test data for Payslip Management (Quản lý Phiếu lương) UI testing.

Creates payslips across multiple employees and months with both draft and
published statuses for testing the full payslip lifecycle.

Usage:
    cd backend && uv run python scripts/seed_payslips.py

Pre-requisites:
    - seed_employees.py must have been run first
    - PostgreSQL running (docker compose up -d)

Actions testable with this seed data:
    /payroll/payslips page:
      - List with filters: employee, status, period (year+month)
      - Bulk select drafts → Bulk Publish
      - Create draft (modal with salary breakdown + net mismatch check)
      - View detail (modal)
      - Edit draft (net mismatch check)
      - Publish individual
      - Unpublish published
      - Delete draft
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# ── Constants ──────────────────────────────────────────────────────────
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr"


def _now() -> datetime:
    return datetime.now(UTC)


# ── Payslip data ───────────────────────────────────────────────────────
# (employee_code, period_month, gross, deductions, ins, taxable, pit, net, status, pdf)

def _d(year: int, month: int) -> date:
    return date(year, month, 1)

PAYSLIPS = [
    # Employee Hải (NV-003): 2 months, both published
    ("NV-003", _d(2026, 6), 25_000_000, 0, 2_625_000, 22_375_000, 1_118_750, 21_256_250, "published", None),
    ("NV-003", _d(2026, 7), 25_000_000, 0, 2_625_000, 22_375_000, 1_118_750, 21_256_250, "published", None),

    # Employee Kim (NV-004): 2 months, 1 published + 1 draft
    ("NV-004", _d(2026, 6), 18_000_000, 500_000, 1_890_000, 15_610_000, 312_200, 15_297_800, "published", None),
    ("NV-004", _d(2026, 7), 20_000_000, 0, 2_100_000, 17_900_000, 716_000, 17_184_000, "draft", "https://minio:9000/payslips/payslip_nv004_202607.pdf"),

    # Employee Tuấn (NV-005): 2 months, 1 draft + 1 draft (to test bulk publish)
    ("NV-005", _d(2026, 6), 22_000_000, 0, 2_310_000, 19_690_000, 787_600, 18_902_400, "draft", None),
    ("NV-005", _d(2026, 7), 22_000_000, 1_000_000, 2_310_000, 18_690_000, 560_700, 18_129_300, "draft", None),

    # Employee Mai (NV-006): 2 months, 1 published + 1 draft
    ("NV-006", _d(2026, 6), 15_000_000, 0, 1_575_000, 13_425_000, 134_250, 13_290_750, "published", None),
    ("NV-006", _d(2026, 7), 15_000_000, 0, 1_575_000, 13_425_000, 134_250, 13_290_750, "draft", None),
]


# ── Main ───────────────────────────────────────────────────────────────


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with AsyncSession(engine) as session:
        # ── 0. Check if already seeded ────────────────────────────
        from src.modules.payslip.domain.entities import Payslip

        existing = await session.execute(
            select(Payslip).where(Payslip.period_month == date(2026, 6, 1)).limit(1)
        )
        if existing.scalar_one_or_none():
            print("⚠️  Seed payslips may already exist. Skipping creation.")
            await session.close()
            return

        # ── 1. Look up employees ───────────────────────────────────
        from src.modules.employee.domain.entities import Employee

        emp_result = await session.execute(
            select(Employee).order_by(Employee.employee_code)
        )
        employees: dict[str, Employee] = {
            e.employee_code: e for e in emp_result.scalars().all()
        }

        missing = [e[0] for e in PAYSLIPS if e[0] not in employees]
        if missing:
            print(f"❌ Missing employees: {missing}. Run seed_employees.py first.")
            sys.exit(1)

        print(f"✅ Found {len(employees)} employees")

        # ── 2. Create Payslips ─────────────────────────────────────
        from src.modules.payslip.domain.entities import PayslipStatus
        from src.modules.gmail.domain.entities import EmailMessage  # FK

        now = _now()
        payslips: list[Payslip] = []

        for code, period, gross, deductions, ins, taxable, pit, net, status, pdf in PAYSLIPS:
            emp = employees[code]
            is_published = status == "published"
            payslips.append(
                Payslip(
                    id=uuid4(),
                    employee_id=emp.id,
                    period_month=period,
                    gross_salary=Decimal(gross),
                    deductions=Decimal(deductions),
                    insurance_employee=Decimal(ins),
                    taxable_income=Decimal(taxable),
                    pit_amount=Decimal(pit),
                    net_salary=Decimal(net),
                    currency="VND",
                    status=PayslipStatus.PUBLISHED if is_published else PayslipStatus.DRAFT,
                    published_at=now if is_published else None,
                    pdf_url=pdf,
                    created_at=now,
                    updated_at=now,
                )
            )

        session.add_all(payslips)
        await session.flush()

        draft_count = sum(1 for p in payslips if p.status == PayslipStatus.DRAFT)
        pub_count = sum(1 for p in payslips if p.status == PayslipStatus.PUBLISHED)

        for p in payslips:
            print(f"✅ Payslip [{p.status.value}]: {p.period_month} — "
                  f"employee={p.employee_id} gross={p.gross_salary} net={p.net_salary}")

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! Payslip test data ready.")
        print(f"\nSummary:")
        print(f"  Payslips:  {len(payslips)} ({draft_count} draft, {pub_count} published)")
        print(f"  Employees: 4 (Hải, Kim, Tuấn, Mai)")
        print(f"  Months:    Tháng 6 + Tháng 7/2026")
        print(f"\nActions to test on UI (/payroll/payslips):")
        print(f"  Filter: employee, status (draft/published), year+month")
        print(f"  Bulk: select {draft_count} drafts → Bulk Publish")
        print(f"  Create: New draft → salary fields + net mismatch check")
        print(f"  View: Click eye → detail modal with breakdown")
        print(f"  Edit: Pencil icon → edit salary fields → save")
        print(f"  Publish: Send icon → draft → published")
        print(f"  Unpublish: Published → unpublish → back to draft")
        print(f"  Delete: Trash icon → delete draft")


if __name__ == "__main__":
    import sys
    asyncio.run(seed())
