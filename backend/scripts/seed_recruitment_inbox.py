"""Seed test data for Recruitment Inbox (Hộp thư Tuyển dụng) UI testing.

Creates 4 inbox items covering all 4 InboxStatus values across 2 threads,
plus a pre-existing JobApplication for cross-thread link proposal testing.

Usage:
    cd backend && uv run python scripts/seed_recruitment_inbox.py

Pre-requisites:
    - PostgreSQL running (docker compose up -d)
    - Migrations applied (uv run alembic upgrade head)
    - At least one user exists (admin@vroomhr.com)

Actions testable with this seed data:
    - List inbox (all 4 states visible)
    - Filter by each InboxStatus
    - Get single inbox item detail
    - Correct intent (items #1, #2, #3)
    - Dismiss (items #1, #2, #3)
    - Split (item #3 → creates 2 JobApplications)
    - Propose cross-thread link (item #3 → JobApp in thread_other)
    - Resolve link proposal (confirm/reject)
    - View resolved item (item #4)
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# ── Constants ──────────────────────────────────────────────────────────
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr"

THREAD_MAIN = "seed_thread_main"
THREAD_OTHER = "seed_thread_other"

# ── Helpers ────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


# ── Main ───────────────────────────────────────────────────────────────


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with AsyncSession(engine) as session:
        # ── 0. Look up admin user ──────────────────────────────────
        from src.modules.identity.domain.entities import User

        admin_result = await session.execute(
            select(User).where(User.email == "admin@vroomhr.com").limit(1)
        )
        admin = admin_result.scalar_one_or_none()
        if admin is None:
            print("❌ Admin user not found. Ensure admin@vroomhr.com exists.")
            sys.exit(1)
        admin_id: UUID = admin.id
        print(f"✅ Admin user: {admin_id}")

        # ── 1. Create email messages (FK prerequisite) ─────────────
        from src.modules.gmail.domain.entities import EmailMessage

        # Check if seed emails already exist
        existing = await session.execute(
            select(EmailMessage).where(
                EmailMessage.gmail_message_id.in_(
                    ["seed_email_main", "seed_email_other"]
                )
            )
        )
        if existing.scalars().first():
            print("⚠️  Seed emails already exist. Skipping creation.")
            await session.close()
            return

        now = _now()
        email_main = EmailMessage(
            id=uuid4(),
            user_id=admin_id,
            gmail_message_id="seed_email_main",
            gmail_thread_id=THREAD_MAIN,
            subject="Ứng tuyển vị trí Senior Developer",
            sender_email="candidate@test.com",
            sender_name="Nguyen Van A",
            recipient_emails=["admin@vroomhr.com"],
            cc_emails=[],
            received_at=now,
            snippet="Kính gửi HR, tôi muốn ứng tuyển vị trí Senior...",
            label_ids=["INBOX"],
            processing_status="completed",
            category="recruitment",
        )
        email_other = EmailMessage(
            id=uuid4(),
            user_id=admin_id,
            gmail_message_id="seed_email_other",
            gmail_thread_id=THREAD_OTHER,
            subject="Báo giá dịch vụ đào tạo nhân sự",
            sender_email="vendor@test.com",
            sender_name="Pham Thi D",
            recipient_emails=["admin@vroomhr.com"],
            cc_emails=[],
            received_at=now,
            snippet="Chúng tôi cung cấp dịch vụ đào tạo...",
            label_ids=["INBOX"],
            processing_status="completed",
            category="vendor",
        )
        session.add_all([email_main, email_other])
        await session.flush()
        print(f"✅ Email main: {email_main.id}")
        print(f"✅ Email other: {email_other.id}")

        # ── 2. Create Recruitment Inbox Items ──────────────────────
        from src.modules.recruitment.domain.entities import RecruitmentInboxItem
        from src.modules.recruitment.domain.enums import InboxStatus

        inbox_items: list[RecruitmentInboxItem] = []

        # Item #1: needs_classification — test correct-intent + dismiss
        inbox_items.append(
            RecruitmentInboxItem(
                source_email_message_id=email_main.id,
                gmail_message_id="seed_inbox_needs_class",
                gmail_thread_id=THREAD_MAIN,
                sender_name="Nguyen Van A",
                sender_email="candidate_a@test.com",
                subject="Ứng tuyển Senior Developer",
                snippet="Tôi có 5 năm kinh nghiệm Python, Django, FastAPI...",
                has_attachments=True,
                attachments_metadata=[
                    {"name": "CV_NguyenVanA.pdf", "type": "application/pdf", "size_bytes": 245000}
                ],
                inbox_status=InboxStatus.NEEDS_CLASSIFICATION,
                prediction_intent="job_application",
                confidence_raw=0.35,
                confidence_calibrated=0.32,
                evidence=[
                    {"signal": "application_language", "weight": 0.7},
                    {"signal": "cv_attached", "weight": 0.8},
                ],
                source_hints=[
                    {"key": "sender_role", "value": "candidate"},
                    {"key": "detected_language", "value": "vi"},
                ],
            )
        )

        # Item #2: needs_information — test correct-intent (ask for more info)
        inbox_items.append(
            RecruitmentInboxItem(
                source_email_message_id=email_main.id,
                gmail_message_id="seed_inbox_needs_info",
                gmail_thread_id=THREAD_MAIN,
                sender_name="Tran Thi B",
                sender_email="unknown_b@test.com",
                subject="Hỏi về chính sách tuyển dụng",
                snippet="Cho tôi hỏi công ty có nhận fresher không ạ?",
                has_attachments=False,
                inbox_status=InboxStatus.NEEDS_INFORMATION,
                prediction_intent="job_application",
                confidence_raw=0.45,
                confidence_calibrated=0.41,
                evidence=[
                    {"signal": "question_keywords", "weight": 0.6},
                    {"signal": "no_cv", "weight": -0.3},
                ],
                source_hints=[
                    {"key": "sender_role", "value": "candidate"},
                    {"key": "missing_cv", "value": "true"},
                ],
            )
        )

        # Item #3: ready_for_review — test split + propose-link + dismiss
        inbox_items.append(
            RecruitmentInboxItem(
                source_email_message_id=email_main.id,
                gmail_message_id="seed_inbox_ready",
                gmail_thread_id=THREAD_MAIN,
                sender_name="Le Van C",
                sender_email="applicant_c@test.com",
                subject="CV ứng tuyển Fullstack Developer",
                snippet="Đính kèm CV và portfolio của tôi...",
                has_attachments=True,
                attachments_metadata=[
                    {"name": "CV_LeVanC.pdf", "type": "application/pdf", "size_bytes": 320000},
                    {"name": "portfolio.pdf", "type": "application/pdf", "size_bytes": 1500000},
                ],
                inbox_status=InboxStatus.READY_FOR_REVIEW,
                prediction_intent="job_application",
                corrected_intent="job_application",
                corrected_by_user_id=admin_id,
                corrected_at=now,
                confidence_raw=0.82,
                confidence_calibrated=0.79,
                evidence=[
                    {"signal": "application_language", "weight": 0.9},
                    {"signal": "cv_attached", "weight": 0.8},
                    {"signal": "portfolio_mention", "weight": 0.6},
                ],
                source_hints=[
                    {"key": "sender_role", "value": "candidate"},
                    {"key": "detected_language", "value": "vi"},
                    {"key": "experience_level", "value": "mid-senior"},
                ],
                correction_history=[
                    {
                        "action": "correct_intent",
                        "previous_intent": "other",
                        "new_intent": "job_application",
                        "performed_by_user_id": str(admin_id),
                        "occurred_at": now.isoformat(),
                    }
                ],
            )
        )

        # Item #4: resolved — view only (no actions available)
        inbox_items.append(
            RecruitmentInboxItem(
                source_email_message_id=email_other.id,
                gmail_message_id="seed_inbox_resolved",
                gmail_thread_id=THREAD_OTHER,
                sender_name="Pham Thi D",
                sender_email="vendor_d@test.com",
                subject="Báo giá dịch vụ đào tạo nhân sự",
                snippet="Bên tôi cung cấp khóa đào tạo kỹ năng mềm...",
                has_attachments=True,
                attachments_metadata=[
                    {"name": "bao_gia_2026.pdf", "type": "application/pdf", "size_bytes": 500000}
                ],
                inbox_status=InboxStatus.RESOLVED,
                prediction_intent="partner",
                corrected_intent="partner",
                corrected_by_user_id=admin_id,
                corrected_at=now,
                dismissed=False,
                confidence_raw=0.90,
                confidence_calibrated=0.88,
                evidence=[
                    {"signal": "vendor_language", "weight": 0.9},
                    {"signal": "pricing_keywords", "weight": 0.7},
                ],
                source_hints=[
                    {"key": "sender_role", "value": "partner"},
                ],
                correction_history=[
                    {
                        "action": "correct_intent",
                        "previous_intent": "partner",
                        "new_intent": "partner",
                        "performed_by_user_id": str(admin_id),
                        "occurred_at": now.isoformat(),
                    }
                ],
            )
        )

        session.add_all(inbox_items)
        await session.flush()
        for item in inbox_items:
            print(f"✅ Inbox item [{item.inbox_status}]: {item.id} — {item.subject}")

        # ── 3. Create JobApplication in thread_other for link proposal ──
        from src.modules.recruitment.domain.entities import JobApplication
        from src.modules.recruitment.domain.enums import ApplicationSource, JobApplicationStatus

        job_app = JobApplication(
            id=uuid4(),
            source_email_message_id=email_other.id,
            gmail_message_id="seed_jobapp_other",
            gmail_thread_id=THREAD_OTHER,
            intent="job_application",
            has_cv=True,
            source=ApplicationSource.DIRECT,
            applicant_name="Pham Van E",
            applicant_email="applicant_e@test.com",
            sender_name="Pham Van E",
            sender_email="applicant_e@test.com",
            evidence=[{"signal": "application_language"}],
            source_hints=[{"key": "sender_role", "value": "candidate"}],
            message_references=[
                {
                    "email_message_id": str(email_other.id),
                    "gmail_message_id": "seed_email_other",
                    "gmail_thread_id": THREAD_OTHER,
                    "link_type": "direct_source",
                }
            ],
            audit_history=[],
            status=JobApplicationStatus.NEW,
        )
        session.add(job_app)
        await session.flush()
        job_app_id: UUID = job_app.id  # save before commit (session expires objects)
        print(f"✅ JobApplication (for link proposal target): {job_app_id}")

        # ── Commit ────────────────────────────────────────────────
        await session.commit()
        print("\n🎉 Seed complete! All Recruitment Inbox test data ready.")
        print(f"\nSummary:")
        print(f"  Email messages:      2 (thread_main + thread_other)")
        print(f"  Inbox items:         4 (all 4 InboxStatus values)")
        print(f"  JobApplication:      1 (thread_other, for link proposal)")
        print(f"\nActions to test on UI:")
        print(f"  /recruitment/inbox — list all items")
        print(f"  Filter by each status tab")
        print(f"  Item #1 (needs_classification): correct-intent, dismiss")
        print(f"  Item #2 (needs_information):    correct-intent")
        print(f"  Item #3 (ready_for_review):     split → 2 applicants, propose-link → job_app ({job_app_id}), dismiss")
        print(f"  Item #4 (resolved):             view only")
        print(f"\nLink proposal target JobApplication ID: {job_app_id}")
        print(f"  Use this ID when proposing cross-thread link from item #3.")


if __name__ == "__main__":
    asyncio.run(seed())
