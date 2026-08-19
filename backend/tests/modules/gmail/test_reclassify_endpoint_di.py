"""``POST /review/emails/{id}/reclassify`` builds a real ClassificationService (#332).

The handler resolved its classification service by calling a FastAPI provider
by hand and passing two of its three parameters::

    classification_service = await get_classification_service(
        email_repo=email_repo,
        audit_logger=audit_logger_instance,
    )

``session`` was left to its ``Depends(get_db_session)`` default. FastAPI is the
only thing that ever turns that default into a session; a direct call binds the
``fastapi.params.Depends`` instance itself. The very first statement of the
provider that touches it, ``OrganizationAIConfigRepository(session).get()``,
therefore raised ``AttributeError`` -- so the endpoint returned 500 on every
request from the day the ``session`` parameter was added.

Nothing caught it because nothing ran it: ``rg -n reclassify tests`` matched
zero test files before this one. The endpoint had no coverage at all, not weak
coverage.

Why this test is heavy on purpose
---------------------------------

It would be far cheaper to override the classification-service provider and
assert the handler calls it. That is exactly the test shape that let the
identical bug in #327 live for three months: overriding a provider proves the
handler *asks* for a service and proves nothing about whether the real code path
can *build* one. So the only overrides here are ``get_db_session`` (pointed at
this test's session, as ``tests/modules/identity/test_password_reset_service_di.py``
does) and ``get_current_user`` (there is no token to present; authentication is
not the seam under test). Everything from the route's ``Depends`` graph down
through the container to the repositories runs for real, against real
PostgreSQL.

The one stub is ``AIClassifier.classify``, which is an outbound HTTP call to a
third-party LLM. It sits well past the wiring this file is about; the service
must already be fully constructed for it to be reached at all.

Companion guard: ``tests/test_depends_provider_call_census.py`` is the static
half, and it needs no Docker. This file proves the endpoint works; the census
proves no other call site in the repository has the same defect.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import delete, select

from src.modules.gmail.api.router import router as gmail_router
from src.modules.gmail.domain.entities import EmailMessage
from src.modules.gmail.domain.enums import EmailCategory
from src.modules.gmail.infrastructure.ai_classifier import AIClassifier, ClassificationResult
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import OrganizationAIConfiguration, User, UserRole
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils

# Matches the key ``tests/env_isolation.py`` pins for AUTH_OAUTH_TOKEN_ENCRYPTION_KEY,
# so ciphertext built here decrypts through the real, unpatched container too.
_TEST_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

# Deliberately not a recruitment category: a job-application result would drag
# the CV pipeline and the Job Application callback into a test about DI wiring.
_CLASSIFIED_AS = EmailCategory.internal


@dataclass(frozen=True)
class Seeded:
    """The rows one reclassify request needs to exist.

    Identifiers are kept as plain ``UUID``s rather than as attributes of the
    entities: the handler commits and the fixture rolls back, and a rollback
    expires every loaded instance, so reading ``email.id`` afterwards would try
    to refresh from the database outside the async context.
    """

    user: User
    email_id: UUID


@asynccontextmanager
async def _session_on(url: str) -> AsyncGenerator[AsyncSession]:
    """Yield one ``AsyncSession`` on a private engine, disposed on the way out.

    Each caller gets its own engine so the second test can observe the
    committed row over a connection the request never touched.
    """
    engine = create_async_engine(url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(postgres_async_url: str) -> AsyncIterator[AsyncSession]:
    """One real session on the migrated database, matching the request's session."""
    async with _session_on(postgres_async_url) as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()


@pytest.fixture(autouse=True)
def stubbed_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the one outbound LLM call without leaving the process.

    This is the only collaborator replaced anywhere in this module, and it is
    reached only once the whole container graph has already been built -- which
    is the thing under test.
    """

    async def classify(self: AIClassifier, **_: Any) -> ClassificationResult:
        return ClassificationResult(category=_CLASSIFIED_AS, confidence=0.95, source="ai")

    monkeypatch.setattr(AIClassifier, "classify", classify)


@pytest_asyncio.fixture
async def seeded(session: AsyncSession) -> AsyncIterator[Seeded]:
    """Insert an HR user, a reviewable email, and the singleton AI configuration.

    The handler commits, so these rows outlive the session's rollback and are
    removed explicitly; ``organization_ai_configurations`` in particular is a
    singleton keyed on ``"default"`` and would collide with the next test.
    """
    suffix = uuid4().hex[:12]
    user = User(
        email=f"reclassify-{suffix}@example.com",
        name="Reclassify HR",
        role=UserRole.HR,
    )
    email = EmailMessage(
        user_id=user.id,
        gmail_message_id=f"msg-{suffix}",
        gmail_thread_id=f"thread-{suffix}",
        subject="Weekly internal update",
        sender_email="colleague@example.com",
        sender_name="Colleague",
        snippet="Notes from the internal sync.",
        received_at=datetime.now(UTC),
        processing_status="needs_review",
    )
    config = OrganizationAIConfiguration(
        provider="openai",
        base_url="http://llm.invalid/v1",
        model="test-model",
        api_key_enc=CryptoUtils(_TEST_KEY_B64).encrypt("test-provider-key"),
    )

    await session.execute(delete(OrganizationAIConfiguration))
    session.add(user)
    # ``email_messages.user_id`` is a non-null FK, and the unit of work does not
    # order unrelated inserts, so the user has to reach the database first.
    await session.flush()
    session.add_all([email, config])
    await session.commit()
    email_id, user_id = email.id, user.id
    try:
        yield Seeded(user=user, email_id=email_id)
    finally:
        await session.rollback()
        await session.execute(delete(EmailMessage).where(EmailMessage.id == email_id))
        await session.execute(delete(OrganizationAIConfiguration))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


def _probe_app(session: AsyncSession, user: User) -> FastAPI:
    """Mount the real Gmail router, replacing only the session and the caller.

    The classification-service provider is *not* overridden. That override is
    the blind spot this file exists to close.
    """
    app = FastAPI()
    app.include_router(gmail_router)
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.mark.integration
async def test_reclassify_runs_the_real_classification_pipeline(
    session: AsyncSession, seeded: Seeded
) -> None:
    """The endpoint answers 200 and the email comes back classified.

    Under #332 this request never reaches the classifier: building the service
    raises ``AttributeError: 'Depends' object has no attribute 'execute'`` while
    querying the organization AI configuration, and the handler returns 500.

    Reverting the call site is therefore enough to turn this red, which is what
    makes it a guard rather than a description (verified by mutation).
    """
    app = _probe_app(session, seeded.user)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/gmail/review/emails/{seeded.email_id}/reclassify")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == _CLASSIFIED_AS.value
    assert body["processing_status"] == "classified"

    session.expunge_all()
    stored = (
        await session.execute(select(EmailMessage).where(EmailMessage.id == seeded.email_id))
    ).scalar_one()
    assert stored.processing_status == "classified", (
        "the handler answered 200 but the classification was never committed"
    )
    assert stored.category == _CLASSIFIED_AS.value


@pytest.mark.integration
async def test_reclassify_writes_through_the_request_session(
    session: AsyncSession, seeded: Seeded, postgres_async_url: str
) -> None:
    """Every write the pipeline makes lands in the caller's unit of work.

    Being merely *reachable* is not enough. A classification service built on
    some other session would update the email in a transaction the handler never
    commits, so the endpoint would report success over a row that never changed.
    Reading the row back through a second, independent connection is what
    separates "the object graph is plausible" from "the write happened".
    """
    app = _probe_app(session, seeded.user)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/gmail/review/emails/{seeded.email_id}/reclassify")

    assert response.status_code == 200, response.text

    async with _session_on(postgres_async_url) as observer:
        row = (
            await observer.execute(select(EmailMessage).where(EmailMessage.id == seeded.email_id))
        ).scalar_one()
        assert row.processing_status == "classified"
        assert row.category == _CLASSIFIED_AS.value
