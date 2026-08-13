"""Real-database integration test for Job Application ingestion.

Uses testcontainers with PostgreSQL 15, builds the schema via
alembic upgrade head, and exercises the full ClassificationService
pipeline with real repositories — not mocked.

Tests:
1. Confident AI classification creates exactly one JobApplication via DB
2. Replay of the same email does not create a second JobApplication (idempotency)
3. Candidate table remains empty (ingestion never promotes)

**Validates: Requirements for Issue #183 — idempotent ingestion**
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.employee.domain.entities import Employee  # noqa: F401
from src.modules.gmail.application.classification_service import ClassificationService
from src.modules.gmail.domain.entities import EmailMessage, GmailAuditLog
from src.modules.gmail.domain.enums import EmailCategory
from src.modules.gmail.infrastructure.ai_classifier import ClassificationResult
from src.modules.gmail.infrastructure.audit_logger import AuditLogger
from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.gmail.infrastructure.email_repository import EmailRepository
from src.modules.identity.domain.entities import User  # noqa: F401
from src.modules.recruitment.application.inbox_service import InboxService
from src.modules.recruitment.application.job_application_service import (
    JobApplicationService,
)
from src.modules.recruitment.domain.entities import Candidate, JobApplication, RecruitmentInboxItem
from src.modules.recruitment.infrastructure.repositories import (
    JobApplicationRepository,
    RecruitmentInboxItemRepository,
)
from tests.postgres_support import make_postgres_container

BACKEND_DIR = Path(__file__).resolve().parents[3]


def _docker_available(docker_module: object) -> bool:
    """Check if Docker daemon is reachable."""
    try:
        from docker import from_env  # type: ignore[import-untyped]

        client = from_env()
        client.ping()
        client.close()
        return True
    except Exception:
        return False


def _run_alembic_upgrade_head(async_url: str) -> None:
    """Run ``alembic upgrade head`` against the container."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env={"DATABASE_URL": async_url},
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\nstdout:{result.stdout}\nstderr:{result.stderr}")


@pytest.fixture(scope="module")
def postgres_async_url() -> Iterator[str]:
    """Module-scoped PostgreSQL container with schema applied."""
    docker = pytest.importorskip("docker")

    if not _docker_available(docker):
        pytest.skip("Docker is not available for the job application integration test")

    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _run_alembic_upgrade_head(async_url)
        yield async_url


@pytest.fixture
async def session(postgres_async_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test async session."""
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, must_change_password, "
                "created_at, last_login, is_active, role) "
                "VALUES ('00000000-0000-0000-0000-000000000001', "
                "'integration@example.com', 'Integration HR', '', false, now(), now(), true, "
                "'admin') ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.commit()
        yield session
    await engine.dispose()


def _create_email() -> EmailMessage:
    """Create a realistic EmailMessage suitable for insert."""
    from datetime import UTC, datetime

    return EmailMessage(
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        gmail_message_id=f"integration_msg_{uuid4().hex[:12]}",
        gmail_thread_id=f"integration_thread_{uuid4().hex[:12]}",
        subject="Ung tuyen vi tri Python Developer - Nguyen Van A",
        sender_email="candidate@example.com",
        sender_name="Nguyen Van A",
        snippet="Toi xin gui CV ung tuyen vi tri Python Developer. Kinh nghiem 5 nam.",
        has_attachments=False,
        received_at=datetime.now(UTC),
        processing_status="unprocessed",
        retry_count=0,
    )


def _make_high_confidence_recruitment_result() -> ClassificationResult:
    """Match what the integration test expects."""
    return ClassificationResult(
        category=EmailCategory.recruitment,
        confidence=0.85,
        source="ai",
        matched_signals=[
            "subject:ung tuyen",
            "sender_domain:example.com",
            "llm_response:recruitment",
        ],
    )


@pytest.mark.integration
class TestJobApplicationPersistenceIntegration:
    """Real-DB integration for Job Application ingestion."""

    async def test_confident_recruitment_persists_job_application(
        self,
        session: AsyncSession,
    ) -> None:
        """Send an email through ClassificationService and assert JobApplication in DB."""
        # --- Arrange --------------------------------------------------------
        email = _create_email()
        session.add(email)
        await session.flush()

        settings = GmailSettings(
            classification_batch_concurrency=1,
            classification_confidence_threshold=0.75,
            classification_needs_review_threshold=0.5,
        )

        # Real repos
        email_repo = EmailRepository(session)
        job_app_repo = JobApplicationRepository(session)
        job_app_service = JobApplicationService(session=session, job_application_repo=job_app_repo)

        # Mocked classifier (deterministic AI result)
        rules_classifier = MagicMock()
        rules_classifier.classify = MagicMock(
            return_value=ClassificationResult(
                category=EmailCategory.recruitment,
                confidence=0.75,
                source="rules",
                matched_signals=["subject:ung tuyen", "sender_domain:example.com"],
            )
        )

        ai_classifier = AsyncMock()
        ai_classifier.classify = AsyncMock(return_value=_make_high_confidence_recruitment_result())

        audit_logger = AuditLogger(session, settings)

        service = ClassificationService(
            rules_classifier=rules_classifier,
            ai_classifier=ai_classifier,
            email_repo=email_repo,
            audit_logger=audit_logger,
            settings=settings,
            session=session,
            on_application_created=job_app_service.create_from_classification,
        )

        user_id = email.user_id

        # --- Act ------------------------------------------------------------
        classified_count = await service.classify_batch(user_id=user_id, emails=[email])
        await session.commit()

        # --- Assert ---------------------------------------------------------
        # Classification succeeded
        assert classified_count == 1

        # Email was updated
        assert email.category == "recruitment"
        assert email.processing_status == "classified"

        # JobApplication was created in DB
        result = await session.execute(
            __import__("sqlmodel")
            .select(JobApplication)
            .where(JobApplication.gmail_message_id == email.gmail_message_id)
        )
        job_app = result.scalars().first()
        assert job_app is not None
        assert job_app.source == "direct"
        assert job_app.applicant_name == "Nguyen Van A"
        assert job_app.applicant_email == "candidate@example.com"
        assert job_app.sender_name == "Nguyen Van A"
        assert job_app.sender_email == "candidate@example.com"
        assert job_app.status == "new"

        # Candidate count is 0 (never promoted by ingestion)
        candidate_count = await session.execute(
            __import__("sqlmodel")
            .select(__import__("sqlalchemy").func.count())
            .select_from(Candidate)
        )
        assert candidate_count.scalar() == 0

    async def test_uncertain_no_cv_email_reaches_inbox_idempotently(
        self,
        session: AsyncSession,
    ) -> None:
        """Uncertain Job Application without CV is reviewable once, never promoted."""
        email = _create_email()
        email.has_attachments = False
        session.add(email)
        await session.flush()

        settings = GmailSettings(
            classification_batch_concurrency=1,
            classification_confidence_threshold=0.75,
            classification_needs_review_threshold=0.5,
        )
        email_repo = EmailRepository(session)
        job_app_repo = JobApplicationRepository(session)
        inbox_repo = RecruitmentInboxItemRepository(session)
        job_app_service = JobApplicationService(session=session, job_application_repo=job_app_repo)
        inbox_service = InboxService(session=session, inbox_repo=inbox_repo)
        uncertain = ClassificationResult(
            category=EmailCategory.recruitment,
            confidence=0.49,
            source="ai",
            matched_signals=["subject:ung tuyen"],
            has_cv=False,
        )
        rules_classifier = MagicMock()
        rules_classifier.classify = MagicMock(return_value=uncertain)
        ai_classifier = AsyncMock()
        ai_classifier.classify = AsyncMock(return_value=uncertain)
        service = ClassificationService(
            rules_classifier=rules_classifier,
            ai_classifier=ai_classifier,
            email_repo=email_repo,
            audit_logger=AuditLogger(session, settings),
            settings=settings,
            session=session,
            on_application_created=job_app_service.create_from_classification,
            on_uncertain_classification=inbox_service.create_from_classification,
        )

        await service.classify_batch(user_id=email.user_id, emails=[email])
        await service.classify_batch(user_id=email.user_id, emails=[email])
        await session.commit()

        inbox_result = await session.execute(
            __import__("sqlmodel")
            .select(RecruitmentInboxItem)
            .where(RecruitmentInboxItem.gmail_message_id == email.gmail_message_id)
        )
        assert len(inbox_result.scalars().all()) == 1
        assert email.processing_status == "needs_classification"

        application_result = await session.execute(
            __import__("sqlmodel")
            .select(JobApplication)
            .where(JobApplication.gmail_message_id == email.gmail_message_id)
        )
        assert application_result.scalars().first() is None

        audit_result = await session.execute(
            __import__("sqlmodel")
            .select(GmailAuditLog)
            .where(GmailAuditLog.operation_type == "classify_batch")
            .where(GmailAuditLog.user_id == email.user_id)
        )
        assert len(audit_result.scalars().all()) >= 2

    async def test_replay_does_not_create_second_job_application(
        self,
        session: AsyncSession,
    ) -> None:
        """Classify the same email twice → still exactly one JobApplication."""
        # --- Arrange --------------------------------------------------------
        email = _create_email()
        session.add(email)
        await session.flush()

        settings = GmailSettings(
            classification_batch_concurrency=1,
            classification_confidence_threshold=0.75,
            classification_needs_review_threshold=0.5,
        )

        email_repo = EmailRepository(session)
        job_app_repo = JobApplicationRepository(session)
        job_app_service = JobApplicationService(session=session, job_application_repo=job_app_repo)

        result = _make_high_confidence_recruitment_result()

        rules_classifier = MagicMock()
        rules_classifier.classify = MagicMock(
            return_value=ClassificationResult(
                category=EmailCategory.recruitment,
                confidence=0.75,
                source="rules",
                matched_signals=["subject:ung tuyen"],
            )
        )

        ai_classifier = AsyncMock()
        ai_classifier.classify = AsyncMock(return_value=result)

        audit_logger = AuditLogger(session, settings)

        service = ClassificationService(
            rules_classifier=rules_classifier,
            ai_classifier=ai_classifier,
            email_repo=email_repo,
            audit_logger=audit_logger,
            settings=settings,
            session=session,
            on_application_created=job_app_service.create_from_classification,
        )

        user_id = email.user_id

        # --- Act: classify twice --------------------------------------------
        count1 = await service.classify_batch(user_id=user_id, emails=[email])
        await session.commit()

        # Second classify — email already has category, but we can call again
        # (classify_batch sends emails regardless of current category)
        count2 = await service.classify_batch(user_id=user_id, emails=[email])
        await session.commit()

        # --- Assert ---------------------------------------------------------
        assert count1 == 1
        assert count2 == 1

        # Exactly one JobApplication
        stmt = (
            __import__("sqlmodel")
            .select(JobApplication)
            .where(JobApplication.gmail_message_id == email.gmail_message_id)
        )
        result = await session.execute(stmt)
        apps = list(result.scalars().all())
        assert len(apps) == 1, f"Expected exactly 1 JobApplication, got {len(apps)}"

        # Candidate count is still 0
        candidate_result = await session.execute(
            __import__("sqlmodel")
            .select(__import__("sqlalchemy").func.count())
            .select_from(Candidate)
        )
        assert candidate_result.scalar() == 0
