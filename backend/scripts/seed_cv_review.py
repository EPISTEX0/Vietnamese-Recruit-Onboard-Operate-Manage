"""Seed test data for CV Review (Parse Queue) UI testing.

Creates CVDocuments across key processing statuses, some linked to
existing seed candidates. Tests the review queue, correction, retry,
and dismiss workflows.

Usage:
    cd backend && uv run python scripts/seed_cv_review.py

Pre-requisites:
    - seed_candidates.py must have been run first (for candidate_id FK)
    - PostgreSQL running (docker compose up -d)

Actions testable with this seed data:
    /recruitment/review page:
      - List CVs with various processing statuses
      - Correction: edit parsed name/email/phone/skills/summary → submit
      - Retry Parse: for failed / needs_review / permanently_failed
      - Dismiss: any item
      - View parsed_cv_data preview (name, email, skills, validation errors)
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
        result = await session.execute(
            text("SELECT 1 FROM cv_documents WHERE gmail_message_id LIKE 'seed_cv_%' LIMIT 1")
        )
        if result.scalar_one_or_none():
            print("⚠️  Seed CV documents already exist. Skipping creation.")
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

        cand_new = candidates_by_email["an.nguyen@example.com"]
        cand_reviewing = candidates_by_email["binh.tran@example.com"]
        cand_interview = candidates_by_email["chien.le@example.com"]
        cand_accepted = candidates_by_email["dung.pham@example.com"]
        cand_rejected = candidates_by_email["em.hoang@example.com"]
        cand_archived = candidates_by_email["phuong.vu@example.com"]

        print(f"✅ Found {len(candidates_by_email)} seed candidates")

        # ── 2. Import models ───────────────────────────────────────
        from src.modules.recruitment.domain.entities import CVDocument
        from src.modules.gmail.domain.entities import EmailMessage  # FK resolution

        now = _now()

        # ── 3. Create CV Documents ─────────────────────────────────
        cvs: list[CVDocument] = []

        # CV #1: needs_review — parsed with low confidence, linked to new candidate
        # Actions: Correct, Retry Parse, Dismiss
        cvs.append(
            CVDocument(
                candidate_id=cand_new.id,
                gmail_message_id="seed_cv_needs_review",
                original_filename="CV_NguyenVanAn_2026.pdf",
                mime_type="application/pdf",
                size_bytes=245000,
                file_path="cv-documents/seed_cv_needs_review.pdf",
                checksum="sha256:seed_needs_review_aaa",
                ocr_output=(
                    "NGUYỄN VĂN AN\n"
                    "Email: an.nguyen@gmail.com | Phone: 0901234567\n"
                    "Skills: Python, Django, FastAPI, PostgreSQL, Docker\n"
                    "Experience: Backend Developer at FPT Software (2021-2024)\n"
                ),
                parsed_cv_data={
                    "name": "Nguyễn Văn An",
                    "email": "an.nguyen@gmail.com",
                    "phone": "0901234567",
                    "skills": ["Python", "Django", "FastAPI", "PostgreSQL", "Docker"],
                    "experience": [
                        {"role": "Backend Developer", "company": "FPT Software", "duration": "2021-2024"}
                    ],
                    "education": [
                        {"degree": "Kỹ sư CNTT", "institution": "ĐH Bách Khoa TP.HCM", "year": "2019"}
                    ],
                    "summary": "Backend developer 5 năm kinh nghiệm Python/Django.",
                },
                field_provenance={
                    "name": {"source": "ocr", "confidence": 0.95},
                    "email": {"source": "ocr", "confidence": 0.90},
                    "phone": {"source": "ocr", "confidence": 0.85},
                    "skills": {"source": "llm", "confidence": 0.72},
                    "experience": {"source": "llm", "confidence": 0.60},
                },
                confirmed_fields=["name", "email", "phone"],
                confidence_score=0.48,
                processing_status="needs_review",
                validation_errors=[
                    {"field": "experience", "message": "Low confidence parse - please verify company names"},
                    {"field": "skills", "message": "Possible missing skills from project descriptions"},
                ],
                retry_count=2,
                last_retry_at=now,
                uploaded_at=now,
                created_at=now,
                updated_at=now,
            )
        )

        # CV #2: failed — parsing errored out, unlinked
        # Actions: Retry Parse, Dismiss
        cvs.append(
            CVDocument(
                candidate_id=None,
                gmail_message_id="seed_cv_failed",
                original_filename="resume_tran_thi_b.pdf",
                mime_type="application/pdf",
                size_bytes=180000,
                file_path="cv-documents/seed_cv_failed.pdf",
                checksum="sha256:seed_failed_bbb",
                ocr_output=None,
                parsed_cv_data=None,
                confidence_score=None,
                processing_status="failed",
                processing_error="LLM parse timeout after 120s — document may be scanned image with poor quality",
                retry_count=3,
                last_retry_at=now,
                uploaded_at=now,
                created_at=now,
                updated_at=now,
            )
        )

        # CV #3: permanently_failed — exhausted retries, linked to candidate
        # Actions: Retry Parse, Dismiss
        cvs.append(
            CVDocument(
                candidate_id=cand_reviewing.id,
                gmail_message_id="seed_cv_permanently_failed",
                original_filename="CV_LeVanC_corrupted.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=52000,
                file_path="cv-documents/seed_cv_permanently_failed.docx",
                checksum="sha256:seed_permafail_ccc",
                ocr_output=None,
                parsed_cv_data=None,
                confidence_score=None,
                processing_status="permanently_failed",
                processing_error="Document is corrupt — unable to extract text after 5 retry attempts",
                retry_count=5,
                last_retry_at=now,
                uploaded_at=now,
                created_at=now,
                updated_at=now,
            )
        )

        # CV #4: completed — successful parse, linked to accepted candidate
        # Actions: View only (parsed data visible), Correction
        cvs.append(
            CVDocument(
                candidate_id=cand_accepted.id,
                gmail_message_id="seed_cv_completed",
                original_filename="CV_PhamThiDung_DevOps.pdf",
                mime_type="application/pdf",
                size_bytes=310000,
                file_path="cv-documents/seed_cv_completed.pdf",
                checksum="sha256:seed_completed_ddd",
                ocr_output=(
                    "PHẠM THỊ DUNG\n"
                    "Email: dung.pham@gmail.com | Phone: 0934567890\n"
                    "Skills: DevOps, Kubernetes, Terraform, CI/CD, Prometheus, Grafana\n"
                ),
                parsed_cv_data={
                    "name": "Phạm Thị Dung",
                    "email": "dung.pham@gmail.com",
                    "phone": "0934567890",
                    "skills": ["DevOps", "Kubernetes", "Terraform", "CI/CD", "Prometheus", "Grafana"],
                    "experience": [
                        {"role": "DevOps Engineer", "company": "VNG Cloud", "duration": "2020-2024"},
                        {"role": "System Admin", "company": "CMC Telecom", "duration": "2017-2020"},
                    ],
                    "education": [
                        {"degree": "Kỹ sư Mạng", "institution": "Học viện Công nghệ Bưu chính Viễn thông", "year": "2017"}
                    ],
                    "summary": "DevOps engineer 7 năm kinh nghiệm, CKA/CKAD certified.",
                },
                field_provenance={
                    "name": {"source": "ocr", "confidence": 0.98},
                    "email": {"source": "ocr", "confidence": 0.95},
                    "skills": {"source": "llm", "confidence": 0.88},
                    "experience": {"source": "llm", "confidence": 0.85},
                },
                confirmed_fields=["name", "email", "phone", "skills", "experience", "education", "summary"],
                confidence_score=0.88,
                processing_status="completed",
                retry_count=0,
                uploaded_at=now,
                created_at=now,
                updated_at=now,
            )
        )

        # CV #5: pending — fresh upload awaiting processing
        # Actions: None yet (still processing)
        cvs.append(
            CVDocument(
                candidate_id=None,
                gmail_message_id="seed_cv_pending",
                original_filename="CV_VuThiPhuong_DS.pdf",
                mime_type="application/pdf",
                size_bytes=420000,
                file_path="cv-documents/seed_cv_pending.pdf",
                checksum="sha256:seed_pending_eee",
                ocr_output=None,
                parsed_cv_data=None,
                confidence_score=None,
                processing_status="pending",
                retry_count=0,
                uploaded_at=now,
                created_at=now,
                updated_at=now,
            )
        )

        # CV #6: llm_parsing — currently being parsed by AI
        # Actions: None yet (in progress)
        cvs.append(
            CVDocument(
                candidate_id=cand_rejected.id,
                gmail_message_id="seed_cv_llm_parsing",
                original_filename="CV_HoangVanEm_Marketing.pdf",
                mime_type="application/pdf",
                size_bytes=195000,
                file_path="cv-documents/seed_cv_llm_parsing.pdf",
                checksum="sha256:seed_llm_parsing_fff",
                ocr_output=(
                    "HOÀNG VĂN EM\n"
                    "Email: em.hoang@gmail.com\n"
                    "Skills: Content Marketing, SEO, Google Ads, Facebook Ads\n"
                ),
                parsed_cv_data=None,
                confidence_score=None,
                processing_status="llm_parsing",
                retry_count=0,
                uploaded_at=now,
                created_at=now,
                updated_at=now,
            )
        )

        session.add_all(cvs)
        await session.flush()

        for cv in cvs:
            print(f"✅ CVDocument [{cv.processing_status}]: {cv.id} — {cv.original_filename}")

        # Save IDs before commit
        cv_ids = [(cv.processing_status, cv.id, cv.original_filename) for cv in cvs]

        # ── Commit ────────────────────────────────────────────────
        await session.commit()

        print("\n🎉 Seed complete! CV Review queue test data ready.")
        print(f"\nSummary:")
        print(f"  CV Documents:       6 (covering 6 processing statuses)")
        print(f"  Linked to candidates: 4")
        print(f"\nActions to test on UI (/recruitment/review):")

        action_map = {
            "needs_review": "🔧 Correct, 🔄 Retry Parse, 🗑️ Dismiss",
            "failed": "🔄 Retry Parse, 🗑️ Dismiss",
            "permanently_failed": "🔄 Retry Parse, 🗑️ Dismiss",
            "completed": "🔧 Correct (view parsed data preview)",
            "pending": "👁️ View only (awaiting processing)",
            "llm_parsing": "👁️ View only (AI is parsing)",
        }

        for status, cid, fname in cv_ids:
            action = action_map.get(status, "")
            print(f"  [{status}] {fname}: {action}")


if __name__ == "__main__":
    asyncio.run(seed())
