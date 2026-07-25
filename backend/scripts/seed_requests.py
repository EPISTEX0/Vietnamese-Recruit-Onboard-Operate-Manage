"""Seed test data for Employee Requests (Review Queue) UI testing.

Creates leave and overtime requests across all statuses for testing the
admin review queue with filtering, individual approve/reject, and bulk actions.

Usage:
    cd backend && uv run python scripts/seed_requests.py

Pre-requisites:
    - seed_employees.py must have been run first
    - PostgreSQL running (docker compose up -d)

Actions testable with this seed data:
    /requests page:
      - Filter: request type (leave / overtime)
      - Filter: status (submitted / approved / rejected / cancelled)
      - Filter: date range, employee
      - Individual: Approve (with confirmation modal), Reject (with reason modal)
      - Bulk: Select all submitted → Bulk Approve / Bulk Reject
      - View request details (leave dates, overtime hours)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, date, time, timedelta
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
            text("SELECT 1 FROM employee_requests WHERE project_or_task = 'seed_request_marker' LIMIT 1")
        )
        if result.scalar_one_or_none():
            print("⚠️  Seed requests already exist. Skipping creation.")
            await session.close()
            return

        # ── 1. Look up employees and admin ─────────────────────────
        from src.modules.employee.domain.entities import Employee
        from src.modules.identity.domain.entities import User

        emp_result = await session.execute(
            select(Employee).order_by(Employee.employee_code)
        )
        employees: list[Employee] = list(emp_result.scalars().all())

        if len(employees) < 6:
            print("❌ Not enough employees. Run seed_employees.py first.")
            sys.exit(1)

        admin = (await session.execute(
            select(User).where(User.email == "admin@vroomhr.com").limit(1)
        )).scalar_one()
        admin_id: UUID = admin.id

        # Pick diverse employees
        emp_hai = next(e for e in employees if e.employee_code == "NV-003")
        emp_kim = next(e for e in employees if e.employee_code == "NV-004")
        emp_tuan = next(e for e in employees if e.employee_code == "NV-005")
        emp_mai = next(e for e in employees if e.employee_code == "NV-006")
        emp_son = next(e for e in employees if e.employee_code == "NV-007")
        emp_long = next(e for e in employees if e.employee_code == "NV-011")

        print(f"✅ Found {len(employees)} employees, using 6")

        # ── 2. Create Requests ─────────────────────────────────────
        from src.modules.employee_request.domain.entities import EmployeeRequest
        from src.modules.employee_request.domain.enums import (
            RequestType, LeaveType, RequestStatus,
        )
        from src.modules.gmail.domain.entities import EmailMessage  # FK resolution

        now = _now()
        today = date.today()
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)
        last_week = today - timedelta(days=7)
        next_week = today + timedelta(days=7)

        requests: list[EmployeeRequest] = []

        # ── Leave Requests ────────────────────────────────────────

        # #1: submitted leave — test Approve / Reject / Bulk
        requests.append(
            EmployeeRequest(
                employee_id=emp_hai.id,
                request_type=RequestType.LEAVE,
                status=RequestStatus.SUBMITTED,
                submitted_at=now,
                leave_type=LeaveType.ANNUAL,
                start_date=next_week,
                end_date=next_week + timedelta(days=2),
                reason="Nghỉ phép năm đi du lịch Đà Lạt cùng gia đình",
                project_or_task="seed_request_marker",
                created_at=now,
                updated_at=now,
            )
        )

        # #2: submitted leave — test Bulk selection (second one for bulk)
        requests.append(
            EmployeeRequest(
                employee_id=emp_kim.id,
                request_type=RequestType.LEAVE,
                status=RequestStatus.SUBMITTED,
                submitted_at=now - timedelta(hours=2),
                leave_type=LeaveType.SICK,
                start_date=yesterday,
                end_date=today,
                reason="Bị sốt, cần nghỉ 2 ngày để hồi phục",
                project_or_task="seed_request_marker",
                created_at=now,
                updated_at=now,
            )
        )

        # #3: approved leave — test view
        requests.append(
            EmployeeRequest(
                employee_id=emp_mai.id,
                request_type=RequestType.LEAVE,
                status=RequestStatus.APPROVED,
                submitted_at=last_week,
                reviewed_at=last_week + timedelta(hours=4),
                reviewed_by_user_id=admin_id,
                review_reason="OK, đã sắp xếp người backup",
                leave_type=LeaveType.ANNUAL,
                start_date=two_days_ago,
                end_date=yesterday,
                reason="Nghỉ phép năm cá nhân",
                project_or_task="seed_request_marker",
                created_at=last_week,
                updated_at=last_week + timedelta(hours=4),
            )
        )

        # #4: rejected leave — test view with reason
        requests.append(
            EmployeeRequest(
                employee_id=emp_long.id,
                request_type=RequestType.LEAVE,
                status=RequestStatus.REJECTED,
                submitted_at=last_week,
                reviewed_at=last_week + timedelta(days=1),
                reviewed_by_user_id=admin_id,
                review_reason="Thời điểm này team đang cao điểm release, đề nghị dời sang tuần sau",
                leave_type=LeaveType.UNPAID,
                start_date=next_week,
                end_date=next_week + timedelta(days=5),
                reason="Cần nghỉ việc cá nhân 1 tuần",
                project_or_task="seed_request_marker",
                created_at=last_week,
                updated_at=last_week + timedelta(days=1),
            )
        )

        # #5: cancelled leave — test view
        requests.append(
            EmployeeRequest(
                employee_id=emp_son.id,
                request_type=RequestType.LEAVE,
                status=RequestStatus.CANCELLED,
                submitted_at=two_days_ago,
                cancellation_reason="Không cần nghỉ nữa, đã giải quyết xong việc cá nhân",
                leave_type=LeaveType.OTHER,
                start_date=yesterday,
                end_date=yesterday,
                reason="Việc cá nhân",
                project_or_task="seed_request_marker",
                created_at=two_days_ago,
                updated_at=yesterday,
            )
        )

        # ── Overtime Requests ─────────────────────────────────────

        # #6: submitted overtime — test Approve / Reject
        requests.append(
            EmployeeRequest(
                employee_id=emp_tuan.id,
                request_type=RequestType.OVERTIME,
                status=RequestStatus.SUBMITTED,
                submitted_at=now - timedelta(hours=1),
                work_date=today,
                start_time=time(18, 0),
                end_time=time(22, 0),
                duration_minutes=240,
                reason="Hoàn thành gấp sprint deliverables trước deadline",
                project_or_task="seed_request_marker",
                created_at=now,
                updated_at=now,
            )
        )

        # #7: approved overtime — test view
        requests.append(
            EmployeeRequest(
                employee_id=emp_hai.id,
                request_type=RequestType.OVERTIME,
                status=RequestStatus.APPROVED,
                submitted_at=last_week,
                reviewed_at=last_week + timedelta(hours=2),
                reviewed_by_user_id=admin_id,
                review_reason="Xác nhận, cần hoàn thành release tối nay",
                work_date=yesterday,
                start_time=time(17, 30),
                end_time=time(21, 30),
                duration_minutes=240,
                reason="Release production version 2.5.0",
                project_or_task="seed_request_marker",
                created_at=last_week,
                updated_at=last_week + timedelta(hours=2),
            )
        )

        session.add_all(requests)
        await session.flush()

        submitted_count = sum(1 for r in requests if r.status == RequestStatus.SUBMITTED)
        approved_count = sum(1 for r in requests if r.status == RequestStatus.APPROVED)
        rejected_count = sum(1 for r in requests if r.status == RequestStatus.REJECTED)
        cancelled_count = sum(1 for r in requests if r.status == RequestStatus.CANCELLED)
        leave_count = sum(1 for r in requests if r.request_type == RequestType.LEAVE)
        overtime_count = sum(1 for r in requests if r.request_type == RequestType.OVERTIME)

        for r in requests:
            print(f"✅ Request [{r.request_type.value}/{r.status.value}]: {r.id}")

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! Employee Requests test data ready.")
        print(f"\nSummary:")
        print(f"  Employee Requests:  {len(requests)} ({leave_count} leave + {overtime_count} overtime)")
        print(f"  Statuses:           {submitted_count} submitted, "
              f"{approved_count} approved, "
              f"{rejected_count} rejected, "
              f"{cancelled_count} cancelled")
        print(f"\nActions to test on UI (/requests):")
        print(f"  Default view: {submitted_count} submitted requests")
        print(f"  Individual: Approve → confirmation modal → approved")
        print(f"  Individual: Reject → reason modal (textarea) → rejected")
        print(f"  Bulk: Select submitted items → Bulk Approve / Bulk Reject")
        print(f"  Filter: request_type (leave={leave_count}, overtime={overtime_count})")
        print(f"  Filter: status (submitted={submitted_count}, approved={approved_count}, rejected={rejected_count}, cancelled={cancelled_count})")
        print(f"  Filter: date range, employee")


if __name__ == "__main__":
    import sys
    asyncio.run(seed())
