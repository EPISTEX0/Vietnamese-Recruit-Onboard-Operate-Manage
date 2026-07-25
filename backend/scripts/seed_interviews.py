"""Seed test data for Interviews & Calendar Conflicts UI testing.

Creates interviews across all 3 statuses (scheduled, completed, cancelled)
linked to existing seed candidates, plus unresolved calendar conflicts
for the conflict resolution flow.

Usage:
    cd backend && uv run python scripts/seed_interviews.py

Pre-requisites:
    - seed_candidates.py must have been run first
    - PostgreSQL running (docker compose up -d)

Actions testable with this seed data:
    /recruitment/interviews page:
      - Calendar status check
      - Unresolved calendar conflicts list → resolve (keep_google / overwrite_vroom)
      - Candidates to schedule list (filter by reviewing/interview_scheduled)
    
    /recruitment/candidates/[id] detail page:
      - View scheduled interview → Complete, Cancel, Reschedule
      - View completed interview (read-only)
      - View cancelled interview → Create Replacement
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
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
            text("SELECT 1 FROM interviews WHERE calendar_event_id LIKE 'seed_cal_%' LIMIT 1")
        )
        if result.scalar_one_or_none():
            print("⚠️  Seed interviews already exist. Skipping creation.")
            await session.close()
            return

        # ── 1. Look up seed candidates ─────────────────────────────
        from src.modules.recruitment.domain.entities import Candidate

        candidates_result = await session.execute(
            select(Candidate).where(Candidate.email.like("%@example.com"))
        )
        candidates_by_email: dict[str, Candidate] = {
            c.email: c for c in candidates_result.scalars().all()
        }

        if len(candidates_by_email) < 6:
            print("❌ Seed candidates not found. Run seed_candidates.py first.")
            sys.exit(1)

        cand_reviewing = candidates_by_email["binh.tran@example.com"]
        cand_interview = candidates_by_email["chien.le@example.com"]
        cand_accepted = candidates_by_email["dung.pham@example.com"]
        cand_new = candidates_by_email["an.nguyen@example.com"]

        print(f"✅ Found seed candidates: reviewing={cand_reviewing.id}, "
              f"interview_scheduled={cand_interview.id}, "
              f"accepted={cand_accepted.id}")

        # ── 2. Look up admin user ──────────────────────────────────
        from src.modules.identity.domain.entities import User

        admin_result = await session.execute(
            select(User).where(User.email == "admin@vroomhr.com").limit(1)
        )
        admin = admin_result.scalar_one_or_none()
        if admin is None:
            print("❌ Admin user not found.")
            sys.exit(1)
        admin_id: UUID = admin.id

        # ── 3. Create Interviews ───────────────────────────────────
        from src.modules.recruitment.domain.entities import Interview, InterviewParticipant
        from src.modules.gmail.domain.entities import EmailMessage  # FK resolution
        from src.modules.employee.domain.entities import Employee  # FK resolution

        now = _now()
        tomorrow = now + timedelta(days=1)
        yesterday = now - timedelta(days=1)
        last_week = now - timedelta(days=7)

        interviews: list[Interview] = []

        # Interview #1: scheduled — for interview_scheduled candidate, tomorrow 9:00-10:00
        iv1_start = tomorrow.replace(hour=2, minute=0, second=0, microsecond=0)  # 9:00 VN
        iv1 = Interview(
            id=uuid4(),
            candidate_id=cand_interview.id,
            status="scheduled",
            round_name="Vòng 1 - Technical Screening",
            start_at=iv1_start,
            end_at=iv1_start + timedelta(hours=1),
            timezone="Asia/Ho_Chi_Minh",
            calendar_event_id="seed_cal_event_v1_tech",
            calendar_id="seed_cal_primary",
            meeting_mode="google_meet",
            meeting_link="https://meet.google.com/seed-tech-screening",
            created_at=now,
            updated_at=now,
        )
        interviews.append(iv1)

        # Interview #2: completed — for accepted candidate, last week
        iv2_start = last_week.replace(hour=3, minute=0, second=0, microsecond=0)  # 10:00 VN
        iv2 = Interview(
            id=uuid4(),
            candidate_id=cand_accepted.id,
            status="completed",
            round_name="Vòng 2 - Culture Fit",
            start_at=iv2_start,
            end_at=iv2_start + timedelta(hours=1, minutes=30),
            timezone="Asia/Ho_Chi_Minh",
            calendar_event_id="seed_cal_event_v2_culture",
            calendar_id="seed_cal_primary",
            meeting_mode="google_meet",
            meeting_link="https://meet.google.com/seed-culture-fit",
            created_at=last_week,
            updated_at=last_week,
        )
        interviews.append(iv2)

        # Interview #3: cancelled — for reviewing candidate, yesterday
        iv3_start = yesterday.replace(hour=1, minute=0, second=0, microsecond=0)  # 8:00 VN
        iv3 = Interview(
            id=uuid4(),
            candidate_id=cand_reviewing.id,
            status="cancelled",
            round_name="Vòng 1 - Phone Screen",
            start_at=iv3_start,
            end_at=iv3_start + timedelta(minutes=30),
            timezone="Asia/Ho_Chi_Minh",
            calendar_event_id="seed_cal_event_v3_phone",
            calendar_id="seed_cal_primary",
            meeting_mode="google_meet",
            meeting_link="https://meet.google.com/seed-phone-screen",
            needs_relink=False,
            created_at=yesterday,
            updated_at=now,
        )
        interviews.append(iv3)

        session.add_all(interviews)
        await session.flush()

        iv1_id: UUID = iv1.id
        iv2_id: UUID = iv2.id
        iv3_id: UUID = iv3.id

        for iv in interviews:
            print(f"✅ Interview [{iv.status}]: {iv.id} — {iv.round_name}")

        # ── 4. Create Interview Participants ───────────────────────
        participants: list[InterviewParticipant] = []

        # For interview #1 (scheduled): candidate + external interviewer
        participants.append(
            InterviewParticipant(
                interview_id=iv1_id,
                type="candidate",
                email=cand_interview.email,
                name=cand_interview.name,
                response_status="accepted",
            )
        )
        participants.append(
            InterviewParticipant(
                interview_id=iv1_id,
                type="employee",
                email=admin.email,
                name="Admin (HR)",
                employee_id=None,
                response_status="accepted",
            )
        )

        # For interview #2 (completed): candidate + admin
        participants.append(
            InterviewParticipant(
                interview_id=iv2_id,
                type="candidate",
                email=cand_accepted.email,
                name=cand_accepted.name,
                response_status="accepted",
            )
        )
        participants.append(
            InterviewParticipant(
                interview_id=iv2_id,
                type="employee",
                email=admin.email,
                name="Admin (HR)",
                employee_id=None,
                response_status="accepted",
            )
        )

        # For interview #3 (cancelled): candidate only
        participants.append(
            InterviewParticipant(
                interview_id=iv3_id,
                type="candidate",
                email=cand_reviewing.email,
                name=cand_reviewing.name,
                response_status="declined",
            )
        )

        session.add_all(participants)
        await session.flush()
        print(f"✅ Interview Participants: {len(participants)} created")

        # ── 5. Create Calendar Conflicts ───────────────────────────
        from src.modules.recruitment.domain.entities import CalendarConflict

        # Conflict #1: unresolved — interview #1 time differs from Google Calendar
        conflict1 = CalendarConflict(
            interview_id=iv1_id,
            candidate_id=cand_interview.id,
            calendar_event_id="seed_cal_event_v1_tech",
            local_snapshot={
                "start_at": iv1_start.isoformat(),
                "end_at": (iv1_start + timedelta(hours=1)).isoformat(),
                "round_name": "Vòng 1 - Technical Screening",
                "status": "scheduled",
            },
            remote_snapshot={
                "start_at": (iv1_start + timedelta(minutes=30)).isoformat(),
                "end_at": (iv1_start + timedelta(hours=1, minutes=30)).isoformat(),
                "summary": "Interview: Lê Văn Chiến (moved by candidate)",
                "status": "confirmed",
            },
            conflict_details={
                "differing_fields": ["start_at", "end_at"],
                "reason": "Candidate moved the event in Google Calendar",
                "detected_at": now.isoformat(),
            },
            status="unresolved",
            created_at=now,
            updated_at=now,
        )
        session.add(conflict1)
        await session.flush()
        print(f"✅ Calendar Conflict [unresolved]: {conflict1.id} — Time mismatch")

        # Conflict #2: unresolved — interview #2 status mismatch
        conflict2 = CalendarConflict(
            interview_id=iv2_id,
            candidate_id=cand_accepted.id,
            calendar_event_id="seed_cal_event_v2_culture",
            local_snapshot={
                "start_at": iv2_start.isoformat(),
                "end_at": (iv2_start + timedelta(hours=1, minutes=30)).isoformat(),
                "round_name": "Vòng 2 - Culture Fit",
                "status": "completed",
            },
            remote_snapshot={
                "start_at": iv2_start.isoformat(),
                "end_at": (iv2_start + timedelta(hours=1, minutes=30)).isoformat(),
                "summary": "Interview: Phạm Thị Dung",
                "status": "cancelled",
            },
            conflict_details={
                "differing_fields": ["status"],
                "reason": "Interviewer cancelled in Google Calendar after Vroom marked complete",
                "detected_at": now.isoformat(),
            },
            status="unresolved",
            created_at=now,
            updated_at=now,
        )
        session.add(conflict2)
        await session.flush()
        conflict2_id: UUID = conflict2.id
        print(f"✅ Calendar Conflict [unresolved]: {conflict2_id} — Status mismatch")
        # Save IDs before commit (session expires objects after commit)
        cand_reviewing_id: UUID = cand_reviewing.id
        cand_interview_id: UUID = cand_interview.id
        cand_accepted_id: UUID = cand_accepted.id

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! Interviews & Calendar Conflicts ready.")
        print(f"\nSummary:")
        print(f"  Interviews:           3 (1 scheduled + 1 completed + 1 cancelled)")
        print(f"  Participants:         5")
        print(f"  Calendar Conflicts:   2 (unresolved)")
        print(f"\nActions to test on UI:")
        print(f"  /recruitment/interviews:")
        print(f"    - Calendar status section")
        print(f"    - 2 unresolved conflicts → resolve (keep_google / overwrite_vroom)")
        print(f"    - Candidates to schedule list (search + filter)")
        print(f"")
        print(f"  /recruitment/candidates/{cand_interview_id} — Interview #1 (scheduled):")
        print(f"    - View → Complete, Cancel, Reschedule")
        print(f"  /recruitment/candidates/{cand_accepted_id} — Interview #2 (completed):")
        print(f"    - View completed (read-only)")
        print(f"  /recruitment/candidates/{cand_reviewing_id} — Interview #3 (cancelled):")
        print(f"    - View → Create Replacement")


if __name__ == "__main__":
    asyncio.run(seed())
