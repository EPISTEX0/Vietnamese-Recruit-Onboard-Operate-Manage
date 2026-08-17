"""Integration coverage for ``POST /api/ess/assistant/session/start``.

Why this file exists
--------------------
``assistant_chat_sessions.user_id`` carries a foreign key to ``users.id`` while
``employee_id`` carries one to ``employees.id``.  The ESS handler used to write
the *employee* id into both columns, so every call raised
``ForeignKeyViolationError`` and the endpoint returned 500 — on every request,
since the first migration that created the table.

Nothing caught it because the existing session-lifecycle tests in
``test_quality_endpoints.py`` never invoke the handler: they construct an
``AssistantChatSession`` inside the test body, hand it to a ``MagicMock``
session whose ``add()`` records the object and whose ``commit()`` does nothing,
and then assert on the object the test itself just built.  No handler runs and
no database is touched, so no foreign key is ever enforced — the assertions hold
for any handler implementation, including a broken one.

These tests close that hole from the other side.  They run the real router
against a real PostgreSQL migrated to head, so the constraints production has
are the constraints under test.  The user and the employee are seeded as two
distinct rows with distinct ids, which is what makes ``user_id == user.id`` a
statement about *which* id the handler picked rather than a tautology.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlmodel import select

from src.modules.assistant.api.employee_router import employee_assistant_router
from src.modules.assistant.infrastructure.quality_models import (
    AssistantChatSession,
    AssistantFeedbackEvent,
    AssistantType,
    FeedbackType,
)
from src.modules.employee.domain.entities import Employee
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_maker(postgres_async_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory bound to the migrated test database."""
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def ess_account(
    session_maker: async_sessionmaker[AsyncSession],
) -> tuple[User, Employee]:
    """Seed one login account and its Employee record, sharing an email.

    ``get_current_employee`` resolves the Employee by the authenticated user's
    email, which is how production links the two.  ``users.employee_id`` is left
    NULL on purpose — that is the state the real ``ess@aidia.vn`` account is in,
    so the handler cannot lean on it to recover the user id.
    """
    suffix = uuid4().hex[:10]
    email = f"ess-{suffix}@example.com"

    employee = Employee(
        employee_code=f"E{suffix}",
        full_name="ESS Test Employee",
        email=email,
    )
    user = User(email=email, name="ESS Test User", role=UserRole.USER)

    async with session_maker() as session:
        session.add(employee)
        session.add(user)
        await session.commit()

    return user, employee


@pytest.fixture
def ess_app(
    session_maker: async_sessionmaker[AsyncSession],
    ess_account: tuple[User, Employee],
) -> FastAPI:
    """Mount the real ESS assistant router over the real database.

    Only authentication is stubbed.  ``get_current_employee`` is left alone so
    the User -> Employee resolution runs for real against the seeded rows — that
    resolution is exactly where the two ids diverge.
    """
    user, _employee = ess_account

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    app = FastAPI()
    app.include_router(employee_assistant_router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = _override_session
    return app


async def _start_session(app: FastAPI) -> tuple[int, dict]:
    """POST the session/start request and return (status_code, body)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/ess/assistant/session/start",
            json={"assistant_type": "employee"},
        )
    body = response.json() if response.content else {}
    return response.status_code, body


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEssSessionStart:
    """The ESS session/start handler against real foreign keys."""

    async def test_returns_2xx(self, ess_app: FastAPI) -> None:
        """The endpoint succeeds instead of dying on a foreign key violation."""
        status_code, body = await _start_session(ess_app)

        assert status_code == 200, body
        assert UUID(body["session_id"])

    async def test_persists_row_owned_by_the_login_user(
        self,
        ess_app: FastAPI,
        session_maker: async_sessionmaker[AsyncSession],
        ess_account: tuple[User, Employee],
    ) -> None:
        """The row records the ``users.id`` in ``user_id``, not the employee id.

        Both ids are real and distinct, so this pins down *which* one the
        handler chose.  The old code wrote ``employee.id`` here, which the
        ``user_id -> users.id`` foreign key rejected outright.
        """
        user, employee = ess_account
        assert user.id != employee.id, "fixture must keep the two ids distinct"

        status_code, body = await _start_session(ess_app)
        assert status_code == 200, body

        async with session_maker() as session:
            result = await session.execute(
                select(AssistantChatSession).where(
                    AssistantChatSession.id == UUID(body["session_id"]),
                )
            )
            row = result.scalar_one()

        assert row.user_id == user.id
        assert row.user_id != employee.id
        assert row.employee_id == employee.id
        assert row.assistant_type == AssistantType.EMPLOYEE

    async def test_user_id_references_a_real_users_row(
        self,
        ess_app: FastAPI,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """``user_id`` joins to ``users`` — the guarantee the FK is there to make.

        Asserted as a join rather than an id comparison so the row stays
        meaningful to anything that reports on sessions per account.
        """
        status_code, body = await _start_session(ess_app)
        assert status_code == 200, body

        async with session_maker() as session:
            result = await session.execute(
                select(User)
                .join(
                    AssistantChatSession,
                    AssistantChatSession.user_id == User.id,
                )
                .where(AssistantChatSession.id == UUID(body["session_id"]))
            )
            assert result.scalar_one() is not None


class TestEssFeedback:
    """Feedback attaches to a session started through the endpoint."""

    async def test_feedback_persists_against_a_started_session(
        self,
        ess_app: FastAPI,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Starting a session then rating a message stores the feedback event.

        ``assistant_feedback_events.session_id`` is a NOT NULL foreign key to
        ``assistant_chat_sessions.id``, so this only passes if session/start
        genuinely committed a row first — the two endpoints are tested as the
        pair the client uses them as.
        """
        status_code, body = await _start_session(ess_app)
        assert status_code == 200, body
        session_id = body["session_id"]

        transport = ASGITransport(app=ess_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/ess/assistant/feedback",
                json={
                    "session_id": session_id,
                    "message_index": 0,
                    "feedback_type": "down",
                    "optional_text": "khong dung y",
                },
            )

        assert response.status_code == 204, response.text

        async with session_maker() as session:
            result = await session.execute(
                select(AssistantFeedbackEvent).where(
                    AssistantFeedbackEvent.session_id == UUID(session_id),
                )
            )
            event = result.scalar_one()

        assert event.message_index == 0
        assert event.feedback_type == FeedbackType.DOWN
        assert event.optional_text == "khong dung y"
