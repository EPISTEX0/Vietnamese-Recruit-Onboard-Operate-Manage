"""Seed test data for Attendance & Allowlist UI testing.

Creates attendance records (checked_in / completed / corrected) across
multiple employees and recent dates.

Usage:
    cd backend && uv run python scripts/seed_attendance.py

Pre-requisites:
    - seed_employees.py must have been run first
    - PostgreSQL running (docker compose up -d)

Actions testable with this seed data:
    /attendance page (Records tab):
      - Filter: date range, employee, status (checked_in / completed)
      - Table: employee, date, check-in, check-out, IP, status, correction
      - Correction: edit check-in/out times with reason
      - Export CSV
    /attendance page (Network tab):
      - Network allowlist is runtime config (Redis-based), not seedable
      - Test: Add/remove CIDR, bulk add, replace all
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, date, timedelta
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
        from src.modules.attendance.domain.entities import AttendanceRecord

        existing = await session.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.correction_reason == "seed_attendance_marker"
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            print("⚠️  Seed attendance records already exist. Skipping creation.")
            await session.close()
            return

        # ── 1. Look up employees and admin ─────────────────────────
        from src.modules.employee.domain.entities import Employee
        from src.modules.identity.domain.entities import User

        emp_result = await session.execute(
            select(Employee).where(Employee.is_active == True).order_by(Employee.employee_code)
        )
        employees: list[Employee] = list(emp_result.scalars().all())

        if len(employees) < 5:
            print("❌ Not enough active employees. Run seed_employees.py first.")
            sys.exit(1)

        # Pick 5 active employees
        emp_hai = next(e for e in employees if e.employee_code == "NV-003")
        emp_kim = next(e for e in employees if e.employee_code == "NV-004")
        emp_tuan = next(e for e in employees if e.employee_code == "NV-005")
        emp_mai = next(e for e in employees if e.employee_code == "NV-006")
        emp_son = next(e for e in employees if e.employee_code == "NV-007")

        admin = (await session.execute(
            select(User).where(User.email == "admin@vroomhr.com").limit(1)
        )).scalar_one()
        admin_id: UUID = admin.id

        print(f"✅ Found {len(employees)} active employees, using 5")

        # ── 2. Create Attendance Records ───────────────────────────
        from src.modules.attendance.domain.entities import AttendanceSource
        from src.modules.gmail.domain.entities import EmailMessage  # FK resolution

        now = _now()
        today = date.today()

        # Work days (Mon-Fri of current week + last week)
        def last_weekday(d: date, weekday: int) -> date:
            """Return the most recent `weekday` (0=Mon) on or before `d`."""
            offset = (d.weekday() - weekday) % 7
            return d - timedelta(days=offset)

        this_week = [last_weekday(today, d) for d in range(5)]  # Mon-Fri this week
        last_week_dates = [d - timedelta(days=7) for d in this_week]  # Mon-Fri last week

        records: list[AttendanceRecord] = []
        marker = "seed_attendance_marker"

        # Helper: create check-in time on given date at given hour
        def dt(d: date, hour: int, minute: int = 0) -> datetime:
            return datetime(d.year, d.month, d.day, hour, minute, 0, tzinfo=UTC)

        # Employee 1 (Hải): full week, all completed
        for i, d in enumerate(this_week):
            records.append(AttendanceRecord(
                employee_id=emp_hai.id,
                work_date=d,
                check_in_at=dt(d, 1, 0),    # 8:00 VN = 1:00 UTC
                check_out_at=dt(d, 10, 0),  # 17:00 VN = 10:00 UTC
                check_in_ip="192.168.1.100",
                check_out_ip="192.168.1.100",
                source=AttendanceSource.WEB,
                correction_reason=marker,
                created_at=now,
                updated_at=now,
            ))

        # Employee 2 (Kim): 3 days, 2 completed + 1 checked_in (today)
        for d in this_week[:2]:
            records.append(AttendanceRecord(
                employee_id=emp_kim.id,
                work_date=d,
                check_in_at=dt(d, 1, 15),
                check_out_at=dt(d, 10, 30),
                check_in_ip="10.0.0.50",
                check_out_ip="10.0.0.50",
                source=AttendanceSource.WEB,
                correction_reason=marker,
                created_at=now,
                updated_at=now,
            ))
        # Today: still checked in (no check-out)
        records.append(AttendanceRecord(
            employee_id=emp_kim.id,
            work_date=this_week[-1] if this_week else today,
            check_in_at=dt(today, 1, 30),
            check_out_at=None,
            check_in_ip="10.0.0.50",
            source=AttendanceSource.MOBILE,
            correction_reason=marker,
            created_at=now,
            updated_at=now,
        ))

        # Employee 3 (Tuấn): 4 days, 3 completed + 1 checked_in
        for d in this_week[:3]:
            records.append(AttendanceRecord(
                employee_id=emp_tuan.id,
                work_date=d,
                check_in_at=dt(d, 1, 45),
                check_out_at=dt(d, 10, 45),
                check_in_ip="172.16.0.25",
                check_out_ip="172.16.0.25",
                source=AttendanceSource.WEB,
                correction_reason=marker,
                created_at=now,
                updated_at=now,
            ))
        records.append(AttendanceRecord(
            employee_id=emp_tuan.id,
            work_date=this_week[3] if len(this_week) > 3 else today,
            check_in_at=dt(this_week[3] if len(this_week) > 3 else today, 2, 0),
            check_out_at=None,
            check_in_ip="172.16.0.25",
            source=AttendanceSource.WEB,
            correction_reason=marker,
            created_at=now,
            updated_at=now,
        ))

        # Employee 4 (Mai): last week, 2 completed + 1 corrected
        for d in last_week_dates[:2]:
            records.append(AttendanceRecord(
                employee_id=emp_mai.id,
                work_date=d,
                check_in_at=dt(d, 1, 0),
                check_out_at=dt(d, 10, 0),
                check_in_ip="192.168.2.30",
                check_out_ip="192.168.2.30",
                source=AttendanceSource.WEB,
                correction_reason=marker,
                created_at=now,
                updated_at=now,
            ))
        # Corrected record: original check-in was late, HR corrected
        d = last_week_dates[2] if len(last_week_dates) > 2 else today - timedelta(days=7)
        original_check_in = dt(d, 3, 0)   # 10:00 VN (was late)
        records.append(AttendanceRecord(
            employee_id=emp_mai.id,
            work_date=d,
            check_in_at=dt(d, 1, 30),      # corrected to 8:30 VN
            check_out_at=dt(d, 10, 0),
            check_in_ip="192.168.2.30",
            check_out_ip="192.168.2.30",
            source=AttendanceSource.WEB,
            corrected_by_user_id=admin_id,
            corrected_at=now,
            correction_reason="Nhầm giờ check-in do quên không bấm. Đã xác nhận có mặt từ 8h30.",
            previous_check_in_at=original_check_in,
            created_at=now,
            updated_at=now,
        ))

        # Employee 5 (Sơn): 2 checked_in only (forgot to check out), last week
        for d in last_week_dates[3:5]:
            records.append(AttendanceRecord(
                employee_id=emp_son.id,
                work_date=d,
                check_in_at=dt(d, 1, 15),
                check_out_at=None,
                check_in_ip="192.168.3.10",
                source=AttendanceSource.KIOSK,
                correction_reason=marker,
                created_at=now,
                updated_at=now,
            ))

        session.add_all(records)
        await session.flush()

        checked_in_count = sum(1 for r in records if r.check_out_at is None)
        completed_count = sum(1 for r in records if r.check_out_at is not None)
        corrected_count = sum(1 for r in records if r.corrected_at is not None)

        for r in records:
            status = "corrected" if r.corrected_at else ("checked_in" if r.check_out_at is None else "completed")
            print(f"✅ Attendance [{status}]: {r.work_date} — "
                  f"employee={r.employee_id}")

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! Attendance records ready.")
        print(f"\nSummary:")
        print(f"  Attendance Records: {len(records)} ({checked_in_count} checked_in, "
              f"{completed_count} completed, {corrected_count} corrected)")
        print(f"  Employees:          5")
        print(f"  Date range:         {last_week_dates[0] if last_week_dates else '?'} → {today}")
        print(f"\nActions to test on UI (/attendance, Records tab):")
        print(f"  Default view (this month): ~14 records")
        print(f"  Filter: employee, date range, status (checked_in/completed)")
        print(f"  Table: check in/out times, IP, status badge, correction flag")
        print(f"  Correction: click pencil → edit times + reason → save")
        print(f"    - Employee Mai (corrected record): shows 'đã sửa' badge + audit history")
        print(f"  Export CSV button")
        print(f"\nNetwork Tab: runtime config (Redis), test manually via UI")


if __name__ == "__main__":
    import sys
    asyncio.run(seed())
