"""Seed test data for Candidates (Ứng viên) UI testing.

Creates 6 candidates covering all 6 CandidateStatus values, plus 2 job
openings for assignment testing.

Usage:
    cd backend && uv run python scripts/seed_candidates.py

Pre-requisites:
    - PostgreSQL running (docker compose up -d)
    - Migrations applied (uv run alembic upgrade head)
    - At least one user exists (admin@vroomhr.com)

Actions testable with this seed data:
    - List candidates (all 6 statuses visible)
    - Filter by each CandidateStatus
    - Search by name/email
    - Candidate detail page for each status
    - Accept → onboarding (items #1, #2, #3)
    - Reject with reason (items #1, #2, #3)
    - Archive (items #1, #2, #3)
    - Assign/Reassign/Unassign to job opening (items #1, #2)
    - Create interview (items #2, #3)
    - View terminal states (#4 accepted, #5 rejected, #6 archived)
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


def _now() -> datetime:
    return datetime.now(UTC)


# ── Main ───────────────────────────────────────────────────────────────


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with AsyncSession(engine) as session:
        # ── 0. Check if already seeded ────────────────────────────
        from src.modules.recruitment.domain.entities import Candidate

        existing = await session.execute(
            select(Candidate).where(Candidate.email.like("%@example.com"))
        )
        if existing.scalars().first():
            print("⚠️  Seed candidates already exist. Skipping creation.")
            await session.close()
            return

        # ── 1. Look up admin user ──────────────────────────────────
        from src.modules.identity.domain.entities import User

        admin_result = await session.execute(
            select(User).where(User.email == "admin@vroomhr.com").limit(1)
        )
        from src.modules.employee.domain.entities import Department, Position
        admin = admin_result.scalar_one_or_none()
        if admin is None:
            print("❌ Admin user not found.")
            sys.exit(1)
        admin_id: UUID = admin.id
        print(f"✅ Admin user: {admin_id}")

        # ── 2. Create Departments → Positions → Job Openings ──────
        from src.modules.recruitment.domain.entities import JobOpening
        from src.modules.recruitment.domain.enums import JobOpeningStatus

        # Departments
        dept_tech = Department(
            id=uuid4(),
            name="Công nghệ",
            created_at=_now(),
        )
        dept_product = Department(
            id=uuid4(),
            name="Sản phẩm",
            created_at=_now(),
        )
        session.add_all([dept_tech, dept_product])
        await session.flush()
        print(f"✅ Dept Tech: {dept_tech.id}")
        print(f"✅ Dept Product: {dept_product.id}")

        # Positions
        pos_python = Position(
            id=uuid4(),
            name="Senior Python Developer",
            department_id=dept_tech.id,
            created_at=_now(),
        )
        pos_react = Position(
            id=uuid4(),
            name="Frontend React Developer",
            department_id=dept_product.id,
            created_at=_now(),
        )
        session.add_all([pos_python, pos_react])
        await session.flush()
        print(f"✅ Position Python: {pos_python.id}")
        print(f"✅ Position React: {pos_react.id}")

        # Job Openings
        jo_python = JobOpening(
            id=uuid4(),
            title="Senior Python Developer",
            position_id=pos_python.id,
            target_headcount=3,
            status=JobOpeningStatus.OPEN,
            created_at=_now(),
            updated_at=_now(),
        )
        jo_react = JobOpening(
            id=uuid4(),
            title="Frontend React Developer",
            position_id=pos_react.id,
            target_headcount=2,
            status=JobOpeningStatus.OPEN,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add_all([jo_python, jo_react])
        await session.flush()
        jo_python_id: UUID = jo_python.id
        jo_react_id: UUID = jo_react.id
        print(f"✅ Job Opening (Python): {jo_python_id}")
        print(f"✅ Job Opening (React): {jo_react_id}")

        # ── 3. Create Candidates ───────────────────────────────────
        from src.modules.gmail.domain.entities import EmailMessage  # needed for FK resolution
        from src.modules.recruitment.domain.enums import CandidateStatus

        now = _now()

        candidates: list[Candidate] = []

        # #1: new — test Accept, Reject, Archive, Assign
        candidates.append(
            Candidate(
                name="Nguyễn Văn An",
                email="an.nguyen@example.com",
                phone="0901234567",
                skills=["Python", "Django", "FastAPI", "PostgreSQL", "Docker"],
                experience=[
                    {"role": "Backend Developer", "company": "FPT Software", "duration": "2021-2024"},
                    {"role": "Python Developer", "company": "VNG Corporation", "duration": "2019-2021"},
                ],
                education=[
                    {"degree": "Kỹ sư CNTT", "institution": "ĐH Bách Khoa TP.HCM", "year": "2019"},
                ],
                summary=(
                    "Backend developer với 5 năm kinh nghiệm Python/Django. "
                    "Có kinh nghiệm xây dựng REST API, microservices, và làm việc "
                    "với PostgreSQL, Redis, Docker."
                ),
                status=CandidateStatus.NEW,
                confidence_score=0.85,
            )
        )

        # #2: reviewing + assigned to Python job — test Accept, Reject, Archive, Interview, Reassign
        candidates.append(
            Candidate(
                name="Trần Thị Bình",
                email="binh.tran@example.com",
                phone="0912345678",
                skills=["React", "TypeScript", "Next.js", "Tailwind CSS", "GraphQL"],
                experience=[
                    {"role": "Frontend Developer", "company": "Tiki", "duration": "2020-2024"},
                    {"role": "Web Developer", "company": "Shopee", "duration": "2018-2020"},
                ],
                education=[
                    {"degree": "Cử nhân CNTT", "institution": "ĐH Khoa Học Tự Nhiên TP.HCM", "year": "2018"},
                ],
                summary=(
                    "Frontend developer 4 năm kinh nghiệm React/Next.js. "
                    "Đã từng lead team 3 người, có kinh nghiệm tối ưu performance và SEO."
                ),
                status=CandidateStatus.REVIEWING,
                confidence_score=0.78,
                job_opening_id=jo_python_id,
            )
        )

        # #3: interview_scheduled + assigned to React job — test Accept, Reject, Archive, Manage Interview
        candidates.append(
            Candidate(
                name="Lê Văn Chiến",
                email="chien.le@example.com",
                phone="0923456789",
                skills=["Java", "Spring Boot", "Microservices", "Kafka", "AWS"],
                experience=[
                    {"role": "Senior Java Developer", "company": "Vietcombank", "duration": "2019-2024"},
                    {"role": "Java Developer", "company": "Techcombank", "duration": "2016-2019"},
                ],
                education=[
                    {"degree": "Thạc sĩ CNTT", "institution": "ĐH Bách Khoa Hà Nội", "year": "2016"},
                    {"degree": "Kỹ sư CNTT", "institution": "ĐH Bách Khoa Hà Nội", "year": "2014"},
                ],
                summary=(
                    "Senior Java developer 8 năm kinh nghiệm trong lĩnh vực FinTech. "
                    "Chuyên sâu Spring Boot, microservices, Kafka. Có chứng chỉ AWS Solutions Architect."
                ),
                status=CandidateStatus.INTERVIEW_SCHEDULED,
                confidence_score=0.92,
                job_opening_id=jo_react_id,
            )
        )

        # #4: accepted — terminal, view only (shows onboarding link)
        candidates.append(
            Candidate(
                name="Phạm Thị Dung",
                email="dung.pham@example.com",
                phone="0934567890",
                skills=["DevOps", "Kubernetes", "Terraform", "CI/CD", "Prometheus", "Grafana"],
                experience=[
                    {"role": "DevOps Engineer", "company": "VNG Cloud", "duration": "2020-2024"},
                    {"role": "System Admin", "company": "CMC Telecom", "duration": "2017-2020"},
                ],
                education=[
                    {"degree": "Kỹ sư Mạng", "institution": "Học viện Công nghệ Bưu chính Viễn thông", "year": "2017"},
                ],
                summary=(
                    "DevOps engineer với kinh nghiệm triển khai Kubernetes cluster "
                    "cho hệ thống 500+ services. Có chứng chỉ CKA, CKAD, AWS DevOps Pro."
                ),
                status=CandidateStatus.ACCEPTED,
                confidence_score=0.88,
                accepted_at=now,
                job_opening_id=jo_python_id,
            )
        )

        # #5: rejected — terminal, view rejection reason
        candidates.append(
            Candidate(
                name="Hoàng Văn Em",
                email="em.hoang@example.com",
                phone="0945678901",
                skills=["Content Marketing", "SEO", "Google Ads", "Facebook Ads"],
                experience=[
                    {"role": "Marketing Specialist", "company": "Vingroup", "duration": "2019-2023"},
                ],
                education=[
                    {"degree": "Cử nhân Marketing", "institution": "ĐH Kinh tế TP.HCM", "year": "2019"},
                ],
                summary=(
                    "Marketing specialist với 4 năm kinh nghiệm mảng B2C. "
                    "Kỹ năng SEO, Google Ads, content strategy. "
                    "Không phù hợp với vị trí tech đang tuyển."
                ),
                status=CandidateStatus.REJECTED,
                confidence_score=0.55,
                rejection_reason="Ứng viên không có kỹ năng lập trình, không phù hợp với bất kỳ vị trí tech nào đang mở.",
                rejected_at=now,
            )
        )

        # #6: archived — terminal, view only
        candidates.append(
            Candidate(
                name="Vũ Thị Phương",
                email="phuong.vu@example.com",
                phone="0956789012",
                skills=["Python", "Pandas", "Scikit-learn", "TensorFlow", "SQL", "Tableau"],
                experience=[
                    {"role": "Data Scientist", "company": "MoMo", "duration": "2020-2024"},
                    {"role": "Data Analyst", "company": "Lazada", "duration": "2018-2020"},
                ],
                education=[
                    {"degree": "Thạc sĩ Khoa học Dữ liệu", "institution": "ĐH Bách Khoa TP.HCM", "year": "2018"},
                    {"degree": "Cử nhân Toán-Tin", "institution": "ĐH Khoa Học Tự Nhiên TP.HCM", "year": "2016"},
                ],
                summary=(
                    "Data Scientist 6 năm kinh nghiệm, đã từng triển khai "
                    "recommendation system cho MoMo với 20M+ users."
                ),
                status=CandidateStatus.ARCHIVED,
                confidence_score=0.91,
                archived_at=now,
            )
        )

        session.add_all(candidates)
        await session.flush()
        candidate_ids = [c.id for c in candidates]
        for c in candidates:
            print(f"✅ Candidate [{c.status}]: {c.id} — {c.name}")

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! All Candidate test data ready.")
        print(f"\nSummary:")
        print(f"  Job Openings:   2 (open)")
        print(f"  Candidates:     6 (all 6 CandidateStatus values)")
        print(f"\nActions to test on UI:")
        print(f"  /recruitment/candidates — list all, filter by status, search")
        print(f"  /recruitment/candidates/{candidate_ids[0]} — #1 new:        Accept, Reject, Archive, Assign")
        print(f"  /recruitment/candidates/{candidate_ids[1]} — #2 reviewing:  Accept, Reject, Archive, Interview, Reassign")
        print(f"  /recruitment/candidates/{candidate_ids[2]} — #3 interview:  Accept, Reject, Archive, Manage Interview")
        print(f"  /recruitment/candidates/{candidate_ids[3]} — #4 accepted:   View only (terminal)")
        print(f"  /recruitment/candidates/{candidate_ids[4]} — #5 rejected:   View only (terminal, see reason)")
        print(f"  /recruitment/candidates/{candidate_ids[5]} — #6 archived:   View only (terminal)")
        print(f"\nJob Opening IDs for assignment:")
        print(f"  Python: {jo_python_id}")
        print(f"  React:  {jo_react_id}")


if __name__ == "__main__":
    asyncio.run(seed())
