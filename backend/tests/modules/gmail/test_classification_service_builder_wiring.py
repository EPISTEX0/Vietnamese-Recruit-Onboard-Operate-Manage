"""Three manual ``ClassificationService`` constructors, one seam (#336).

``build_classification_service`` (``src/modules/gmail/container.py``) is the one
place that wires ``on_uncertain_classification`` -- the callback that turns a
low-confidence recruitment email into a ``RecruitmentInboxItem`` HR can see.
Before #336, three other call sites built ``ClassificationService`` by hand and
left that callback out:

* ``api/router.py:_evaluate_rules`` (``POST /api/gmail/classify``) -- already
  resolved AI credentials from Organization AI Config, but never passed the
  callback.
* ``application/import_service.py:_classify_recent_emails`` (historical import,
  runs on the ARQ worker) -- built its own ``AIClassifier(settings)`` from
  ``GMAIL_*`` env vars, ignoring Organization AI Config entirely.
* ``application/email_sync_service.py:_classify_new_emails`` (live sync, both
  the manual-trigger endpoint and the ``poll_gmail_emails`` cron job) -- same
  env-var classifier fallback.

The visible symptom was `not there`: a recruitment email routed below the
confidence threshold got marked ``needs_classification`` same as always, but no
``RecruitmentInboxItem`` ever appeared in the Recruitment Inbox for it. Nothing
raised, nothing logged as an error -- the applicant just went missing. This is
the same blind spot #332 and #327 hit for other providers: unit tests around
``ClassificationService`` itself (mocking ``on_uncertain_classification``
directly) can't see a call site that never wires the callback at all.

Each test below calls the exact private function containing the affected
construction, against a real Postgres session, with only the outbound LLM call
stubbed -- so a passing test proves the callback reaches the database, not
just that a mock was invoked. Mutation-tested: removing
``on_uncertain_classification=...`` from ``build_classification_service``
turns all three red (verified by hand, see #336 handback).
"""

from __future__ import annotations

import base64
import re
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import delete, select

from src.modules.gmail.api.router import _evaluate_rules
from src.modules.gmail.application.email_sync_service import EmailSyncService
from src.modules.gmail.application.import_service import HistoricalImportService
from src.modules.gmail.domain.entities import EmailMessage
from src.modules.gmail.domain.enums import EmailCategory
from src.modules.gmail.infrastructure.ai_classifier import AIClassifier, ClassificationResult
from src.modules.gmail.infrastructure.audit_logger import AuditLogger
from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.gmail.infrastructure.email_repository import EmailRepository
from src.modules.identity.domain.entities import OrganizationAIConfiguration, User, UserRole
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from src.modules.recruitment.domain.entities import RecruitmentInboxItem

# Matches the key ``tests/env_isolation.py`` pins for AUTH_OAUTH_TOKEN_ENCRYPTION_KEY,
# so ciphertext built here decrypts through the real, unpatched container too.
_TEST_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

# Below GmailSettings' default 0.5 review threshold, so every site under test
# takes the "recruitment email, uncertain confidence" branch in
# ``ClassificationService._apply_classification`` and calls
# ``on_uncertain_classification`` if -- and only if -- the collaborator was
# wired in the first place.
_UNCERTAIN_RESULT = ClassificationResult(
    category=EmailCategory.recruitment,
    confidence=0.2,
    source="ai",
)


@asynccontextmanager
async def _session_on(url: str) -> AsyncGenerator[AsyncSession]:
    """Yield one ``AsyncSession`` on a private engine, disposed on the way out."""
    engine = create_async_engine(url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(postgres_async_url: str) -> AsyncIterator[AsyncSession]:
    """One real session on the migrated database."""
    async with _session_on(postgres_async_url) as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()


@pytest.fixture
def settings() -> GmailSettings:
    """Plain GmailSettings; nothing under test reads GMAIL_* credentials."""
    return GmailSettings()


@pytest.fixture(autouse=True)
def stubbed_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the one outbound LLM call without leaving the process."""

    async def classify(self: AIClassifier, **_: Any) -> ClassificationResult:
        return _UNCERTAIN_RESULT

    monkeypatch.setattr(AIClassifier, "classify", classify)


@dataclass(frozen=True)
class Seeded:
    """The rows every test in this module needs to exist."""

    user: User


@pytest_asyncio.fixture
async def seeded(session: AsyncSession) -> AsyncIterator[Seeded]:
    """Insert an HR user and the singleton Organization AI Configuration.

    ``organization_ai_configurations`` is a singleton keyed on ``"default"``
    and would collide with the next test, so it is removed explicitly.
    """
    suffix = uuid4().hex[:12]
    user = User(
        email=f"builder-wiring-{suffix}@example.com",
        name="Builder Wiring HR",
        role=UserRole.HR,
    )
    config = OrganizationAIConfiguration(
        provider="openai",
        base_url="http://llm.invalid/v1",
        model="test-model",
        api_key_enc=CryptoUtils(_TEST_KEY_B64).encrypt("test-provider-key"),
    )

    await session.execute(delete(OrganizationAIConfiguration))
    session.add(user)
    await session.flush()
    session.add(config)
    await session.commit()
    user_id = user.id
    try:
        yield Seeded(user=user)
    finally:
        await session.rollback()
        await session.execute(delete(OrganizationAIConfiguration))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def _seed_email(session: AsyncSession, user_id: UUID, label: str) -> EmailMessage:
    """Insert one unprocessed recruitment-shaped email and return it."""
    suffix = uuid4().hex[:12]
    email = EmailMessage(
        user_id=user_id,
        gmail_message_id=f"msg-{label}-{suffix}",
        gmail_thread_id=f"thread-{label}-{suffix}",
        subject="Ứng tuyển vị trí Backend Developer",
        sender_email="candidate@example.com",
        sender_name="Candidate",
        snippet="Em xin ứng tuyển vị trí Backend Developer.",
        received_at=datetime.now(UTC),
        processing_status="unprocessed",
    )
    session.add(email)
    await session.flush()
    return email


async def _inbox_item_for(
    session: AsyncSession, gmail_message_id: str
) -> RecruitmentInboxItem | None:
    result = await session.execute(
        select(RecruitmentInboxItem).where(
            RecruitmentInboxItem.gmail_message_id == gmail_message_id
        )
    )
    return result.scalars().first()


@pytest.mark.integration
async def test_router_classify_emails_creates_a_recruitment_inbox_item(
    session: AsyncSession, seeded: Seeded
) -> None:
    """``POST /api/gmail/classify`` (via ``_evaluate_rules``) wires the callback.

    Before #336 this site resolved AI credentials from Organization AI Config
    correctly but never passed ``on_uncertain_classification``, so this
    assertion is the one that would have caught it: the email still gets
    marked, but the inbox item that lets HR see it never appears.
    """
    email = await _seed_email(session, seeded.user.id, "router")

    classified_count = await _evaluate_rules(
        current_user_id=seeded.user.id,
        unclassified_emails=[email],
        session=session,
    )

    assert classified_count == 1
    item = await _inbox_item_for(session, email.gmail_message_id)
    assert item is not None, (
        "on_uncertain_classification was not wired for router.py:_evaluate_rules"
    )


@pytest.mark.integration
async def test_import_service_classify_recent_emails_creates_a_recruitment_inbox_item(
    session: AsyncSession, seeded: Seeded, settings: GmailSettings
) -> None:
    """Historical import's ``_classify_recent_emails`` wires the callback.

    Before #336 this site built ``AIClassifier(settings)`` from ``GMAIL_*`` env
    vars -- ignoring Organization AI Config -- and never passed
    ``on_uncertain_classification`` either.
    """
    email = await _seed_email(session, seeded.user.id, "import")
    import_service = HistoricalImportService(
        session=session,
        gmail_adapter=MagicMock(),
        email_repo=EmailRepository(session),
        sync_cursor_repo=MagicMock(),
        connection_repo=MagicMock(),
        crypto=MagicMock(),
        audit_logger=AuditLogger(session, settings),
        settings=settings,
        redis_client=MagicMock(),
        http_client=MagicMock(),
        client_id="test-client-id",
        client_secret="test-client-secret",
    )

    job_application_count = await import_service._classify_recent_emails(  # noqa: SLF001
        seeded.user.id, limit=1
    )

    # ``_classify_recent_emails`` counts by assigned category, not by whether an
    # inbox item was also created -- confirm that unrelated counting logic
    # first, since the assertion under test is about the callback below.
    assert job_application_count == 1
    item = await _inbox_item_for(session, email.gmail_message_id)
    assert item is not None, (
        "on_uncertain_classification was not wired for import_service.py:_classify_recent_emails"
    )


@pytest.mark.integration
async def test_email_sync_service_classify_new_emails_creates_a_recruitment_inbox_item(
    session: AsyncSession, seeded: Seeded, settings: GmailSettings
) -> None:
    """Live sync's ``_classify_new_emails`` wires the callback.

    Before #336 this site fell back to ``AIClassifier(settings)`` whenever no
    ``ai_classifier`` was injected -- the exact shape of the
    ``poll_gmail_emails`` cron job, which constructs ``EmailSyncService``
    directly and never passes one -- and never passed
    ``on_uncertain_classification``.
    """
    email = await _seed_email(session, seeded.user.id, "sync")
    email_repo = EmailRepository(session)
    sync_service = EmailSyncService(
        session=session,
        gmail_adapter=MagicMock(),
        email_repo=email_repo,
        sync_cursor_repo=MagicMock(),
        connection_repo=MagicMock(),
        crypto=MagicMock(),
        audit_logger=AuditLogger(session, settings),
        settings=settings,
        redis_client=MagicMock(),
        client_id="test-client-id",
        client_secret="test-client-secret",
    )

    await sync_service._classify_new_emails(  # noqa: SLF001
        seeded.user.id, [email.gmail_message_id]
    )

    item = await _inbox_item_for(session, email.gmail_message_id)
    assert item is not None, (
        "on_uncertain_classification was not wired for email_sync_service.py:_classify_new_emails"
    )


def test_only_the_builder_constructs_classification_service_directly() -> None:
    """After #336, ``ClassificationService(`` is only constructed inside the builder.

    Known-positive: the census must still see the one call site it is allowed
    to see (inside ``build_classification_service``) -- a pattern that stopped
    matching anything would report "zero call sites" and look identical to a
    clean repository.
    """
    backend_root = Path(__file__).resolve().parents[3]
    pattern = re.compile(r"\bClassificationService\(")

    hits: list[str] = []
    for path in sorted((backend_root / "src").rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(backend_root)}:{lineno}")

    assert any(hit.startswith("src/modules/gmail/container.py") for hit in hits), (
        "known-positive failed: the builder's own construction is no longer found -- "
        "the census pattern is broken, not the codebase"
    )
    assert len(hits) == 1, "ClassificationService( constructed outside the builder:\n" + "\n".join(
        hits
    )
