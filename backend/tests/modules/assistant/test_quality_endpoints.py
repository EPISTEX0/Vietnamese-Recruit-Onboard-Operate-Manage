"""Quality metrics for the AI Assistant, asserted through the real endpoints.

Why this file was rewritten
---------------------------
Every test here used to build its own ``AssistantChatSession`` in the test body,
hand it to a ``MagicMock`` whose ``add()`` only recorded and whose ``commit()``
did nothing, and then assert on the object it had just constructed.  No handler
ran, no database was touched, and no foreign key was ever checked, so the
assertions held for any implementation — including one that was broken.

That is not a hypothetical.  ``POST /api/ess/assistant/session/start`` wrote the
*employee* id into ``user_id`` (a foreign key to ``users.id``) and returned 500
on every single call from the first migration onwards, while the four session
tests in this file stayed green through 2500+ runs of the suite.

So the rewrite drives the real routers against a real PostgreSQL migrated to
head, on the harness ``test_session_ownership_integration.py`` and
``test_ess_session_start_integration.py`` established.  Two properties do the
work the mocks could not:

* the **database is real**, so ``session_id`` foreign keys, NOT NULL columns and
  server-side defaults are enforced rather than assumed; and
* the login account and the Employee are seeded as **two distinct rows**, so
  ``user_id == user.id`` pins down *which* id a handler chose instead of
  comparing a value to itself.

What each test now claims, against what its predecessor claimed:

===========================  ==================================================
Old test                     What the rewrite asserts instead
===========================  ==================================================
feedback_persists_hr         the HR handler writes a row, keyed to a session
                             that exists, readable back from Postgres
feedback_persists_employee   likewise for the ESS handler
feedback_event_has_all_...   the fields survive a real INSERT, ``created_at``
                             included, rather than surviving a constructor
session_start_end            ``/session/end`` sets ``end_at`` on the row
                             ``/session/start`` created — not on a local object
session_model_has_requi...   the persisted row's ``user_id`` is the *users* id
                             and its ``employee_id`` the *employees* id
session_end_sets_end_at      folded into session_start_end, which now reads the
                             value back out of the database
employee_session_has_em...   the ESS handler fills ``employee_id``; the HR one
                             leaves it NULL, which is what makes ``user_id`` the
                             only column that can express ownership
message_count_* (4 tests)    the counter advances because a chat turn ran
                             through the endpoint, not because the test added 1
tool_call_event_fields       a tool call during a real turn writes a row whose
tool_call_event_with_error   ``session_id`` satisfies the foreign key, and a
                             failing tool is recorded as ``success=False``
===========================  ==================================================
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock
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

from src.modules.assistant.api.employee_router import (
    employee_assistant_router,
    get_employee_assistant_service,
)
from src.modules.assistant.api.router import router as hr_assistant_router
from src.modules.assistant.application import (
    employee_assistant_service as ess_module,
)
from src.modules.assistant.application.assistant_service import AssistantService
from src.modules.assistant.application.employee_assistant_service import (
    EmployeeAssistantService,
)
from src.modules.assistant.container import get_assistant_service
from src.modules.assistant.infrastructure.config import AssistantSettings
from src.modules.assistant.infrastructure.quality_models import (
    AssistantChatSession,
    AssistantFeedbackEvent,
    AssistantToolCallEvent,
    AssistantType,
    FeedbackType,
)
from src.modules.employee.domain.entities import Employee
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole
from tests.modules.assistant.assistant_support import (
    FakeLLMClient,
    FakeRegistry,
    text_turn,
    tool_turn,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@dataclass
class Account:
    """One login account, plus the Employee record ESS callers need."""

    user: User
    employee: Employee | None = None


async def _seed_user(
    session_maker: async_sessionmaker[AsyncSession],
    role: UserRole,
    *,
    with_employee: bool,
) -> Account:
    """Insert a user (and optionally its Employee twin) as committed rows.

    ``get_current_employee`` resolves the Employee by the *user's* email, which
    is how production links the two, so the twin shares the email and nothing
    else — in particular the two ids stay distinct, which is what lets these
    tests tell ``users.id`` and ``employees.id`` apart.
    """
    suffix = uuid4().hex[:10]
    email = f"qual-{suffix}@example.com"

    employee = (
        Employee(employee_code=f"E{suffix}", full_name=f"Emp {suffix}", email=email)
        if with_employee
        else None
    )
    user = User(email=email, name=f"User {suffix}", role=role)

    async with session_maker() as session:
        if employee is not None:
            session.add(employee)
        session.add(user)
        await session.commit()

    return Account(user=user, employee=employee)


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
async def hr_account(session_maker: async_sessionmaker[AsyncSession]) -> Account:
    """An HR account. HR sessions carry no Employee, so ``employee_id`` is NULL."""
    return await _seed_user(session_maker, UserRole.HR, with_employee=False)


@pytest.fixture
async def ess_account(session_maker: async_sessionmaker[AsyncSession]) -> Account:
    """An employee account, seeded as two rows with two distinct ids."""
    return await _seed_user(session_maker, UserRole.USER, with_employee=True)


def _settings() -> AssistantSettings:
    return AssistantSettings(
        base_url="http://localhost:8000",
        api_key="test-key",
        model="test-model",
        max_history=20,
        timeout_seconds=30,
    )


class Stack:
    """Both assistant routers over one database, with a switchable caller."""

    def __init__(self, app: FastAPI, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._app = app
        self._session_maker = session_maker
        self.caller: User | None = None

    def as_(self, account: Account) -> Stack:
        """Make subsequent requests authenticate as ``account``."""
        self.caller = account.user
        return self

    async def post(self, path: str, payload: dict) -> tuple[int, dict]:
        """POST ``payload`` to ``path`` and return (status_code, body)."""
        transport = ASGITransport(app=self._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(path, json=payload)
        return response.status_code, (response.json() if response.content else {})

    async def read_session(self, session_id: UUID | str) -> AssistantChatSession:
        """Re-read a chat session in a fresh connection, bypassing the app."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(AssistantChatSession).where(
                    AssistantChatSession.id == UUID(str(session_id)),
                )
            )
            return result.scalar_one()

    async def feedback_events(self, session_id: UUID | str) -> list[AssistantFeedbackEvent]:
        """Every feedback event attached to a chat session."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(AssistantFeedbackEvent).where(
                    AssistantFeedbackEvent.session_id == UUID(str(session_id)),
                )
            )
            return list(result.scalars().all())

    async def tool_events(self, session_id: UUID | str) -> list[AssistantToolCallEvent]:
        """Every tool call event attached to a chat session."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(AssistantToolCallEvent).where(
                    AssistantToolCallEvent.session_id == UUID(str(session_id)),
                )
            )
            return list(result.scalars().all())


@pytest.fixture
def make_stack(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Mount both real routers, with a scripted model behind the real services.

    ``get_current_user`` is the only auth override — ``require_hr`` and
    ``get_current_employee`` are built on it, so they keep running for real
    against the seeded rows. The assistant services are the real classes; only
    the LLM and the tool registry behind them are scripted, because the
    quantities under test (message counts, tool call rows) are produced by the
    service loop from the model's output.
    """

    def _make(
        turns: list[Any] | None = None,
        results: dict[str, str] | None = None,
        failing: frozenset[str] = frozenset(),
        employee_id: UUID | None = None,
    ) -> Stack:
        script = turns if turns is not None else [text_turn("Xong")]
        registry = FakeRegistry(results or {}, failing=failing)
        monkeypatch.setattr(ess_module, "EmployeeToolRegistry", lambda **_kw: registry)

        app = FastAPI()
        app.include_router(hr_assistant_router)
        app.include_router(employee_assistant_router)
        harness = Stack(app, session_maker)

        async def _override_session() -> AsyncIterator[AsyncSession]:
            # Mirrors ``get_db_session``, trailing commit included — that commit
            # is what persists telemetry the service only staged.
            async with session_maker() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        def _hr_service() -> AssistantService:
            return AssistantService(
                llm_client=FakeLLMClient(script),  # type: ignore[arg-type]
                tool_registry=registry,  # type: ignore[arg-type]
                settings=_settings(),
            )

        def _ess_service() -> EmployeeAssistantService:
            return EmployeeAssistantService(
                llm_client=FakeLLMClient(script),  # type: ignore[arg-type]
                employee_id=employee_id or uuid4(),
                employee_service=MagicMock(),
                document_service=MagicMock(),
                attendance_repo=MagicMock(),
                leave_service=MagicMock(),
                overtime_service=MagicMock(),
                payslip_service=MagicMock(),
                settings=_settings(),
                context_builder=None,
            )

        app.dependency_overrides[get_current_user] = lambda: harness.caller
        app.dependency_overrides[get_db_session] = _override_session
        app.dependency_overrides[get_assistant_service] = _hr_service
        app.dependency_overrides[get_employee_assistant_service] = _ess_service
        return harness

    return _make


@pytest.fixture
def stack(make_stack: Any) -> Stack:
    """A stack whose model answers in plain text and calls no tool."""
    return make_stack()


async def _start_hr(stack: Stack, account: Account) -> str:
    """Start an HR assistant session through the endpoint."""
    status, body = await stack.as_(account).post(
        "/api/assistant/session/start", {"assistant_type": "hr"}
    )
    assert status == 200, body
    return str(body["session_id"])


async def _start_ess(stack: Stack, account: Account) -> str:
    """Start an ESS assistant session through the endpoint."""
    status, body = await stack.as_(account).post(
        "/api/ess/assistant/session/start", {"assistant_type": "employee"}
    )
    assert status == 200, body
    return str(body["session_id"])


TURN = {"messages": [{"role": "user", "content": "Còn bao nhiêu ngày phép?"}]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFeedbackPersistence:
    """Feedback reaches ``assistant_feedback_events`` as a real row."""

    async def test_hr_feedback_is_written_to_the_database(
        self, stack: Stack, hr_account: Account
    ) -> None:
        """The HR handler persists the rating against the session it names.

        ``session_id`` is a NOT NULL foreign key, so this passes only if
        ``/session/start`` committed a row first — the two endpoints are
        exercised as the pair the client uses them as.
        """
        session_id = await _start_hr(stack, hr_account)

        status, _ = await stack.as_(hr_account).post(
            "/api/assistant/feedback",
            {
                "session_id": session_id,
                "message_index": 0,
                "feedback_type": "up",
                "optional_text": "Great response",
            },
        )
        assert status == 204

        events = await stack.feedback_events(session_id)
        assert len(events) == 1
        assert events[0].message_index == 0
        assert events[0].feedback_type == FeedbackType.UP
        assert events[0].optional_text == "Great response"

    async def test_employee_feedback_is_written_to_the_database(
        self, stack: Stack, ess_account: Account
    ) -> None:
        """The ESS handler persists the rating too, with its own auth path."""
        session_id = await _start_ess(stack, ess_account)

        status, _ = await stack.as_(ess_account).post(
            "/api/ess/assistant/feedback",
            {
                "session_id": session_id,
                "message_index": 2,
                "feedback_type": "down",
                "optional_text": None,
            },
        )
        assert status == 204

        events = await stack.feedback_events(session_id)
        assert len(events) == 1
        assert events[0].message_index == 2
        assert events[0].feedback_type == FeedbackType.DOWN
        assert events[0].optional_text is None

    async def test_stored_feedback_carries_a_server_side_timestamp(
        self, stack: Stack, hr_account: Account
    ) -> None:
        """``created_at`` is populated by the insert, not by the constructor.

        The old version asserted ``event.created_at is not None`` on an object
        that had never been near a database, which a default on the Python
        model satisfies whether or not the column exists.
        """
        session_id = await _start_hr(stack, hr_account)
        await stack.as_(hr_account).post(
            "/api/assistant/feedback",
            {
                "session_id": session_id,
                "message_index": 1,
                "feedback_type": "up",
                "optional_text": "text",
            },
        )

        event = (await stack.feedback_events(session_id))[0]

        assert event.id is not None
        assert event.session_id == UUID(session_id)
        assert event.created_at is not None


class TestSessionLifecycle:
    """Starting and ending a session, read back from the database."""

    async def test_end_sets_end_at_on_the_started_row(
        self, stack: Stack, hr_account: Account
    ) -> None:
        """``/session/end`` mutates the row ``/session/start`` created.

        The old test set ``end_at`` on an object it had constructed itself and
        asserted the attribute it had just assigned, which no handler could
        fail.
        """
        session_id = await _start_hr(stack, hr_account)
        assert (await stack.read_session(session_id)).end_at is None

        status, _ = await stack.as_(hr_account).post(
            "/api/assistant/session/end", {"session_id": session_id}
        )
        assert status == 204

        assert (await stack.read_session(session_id)).end_at is not None

    async def test_hr_session_records_the_login_account_and_no_employee(
        self, stack: Stack, hr_account: Account
    ) -> None:
        """An HR session owns by ``user_id`` and leaves ``employee_id`` NULL.

        That NULL is why ``employee_id`` cannot be the ownership column, which
        is the premise the whole ``ChatSessionGuard`` design rests on.
        """
        session_id = await _start_hr(stack, hr_account)

        row = await stack.read_session(session_id)

        assert row.user_id == hr_account.user.id
        assert row.employee_id is None
        assert row.assistant_type == AssistantType.HR

    async def test_employee_session_records_both_ids_from_their_own_tables(
        self, stack: Stack, ess_account: Account
    ) -> None:
        """The ESS handler picks ``users.id`` and ``employees.id`` separately.

        Both are real, committed and distinct, so this states *which* id landed
        in each column. Writing the employee id into ``user_id`` — the bug that
        made this endpoint 500 on every call — violates the foreign key and
        fails here rather than passing against a mock.
        """
        assert ess_account.employee is not None
        assert ess_account.user.id != ess_account.employee.id

        session_id = await _start_ess(stack, ess_account)
        row = await stack.read_session(session_id)

        assert row.user_id == ess_account.user.id
        assert row.employee_id == ess_account.employee.id
        assert row.user_id != ess_account.employee.id
        assert row.assistant_type == AssistantType.EMPLOYEE


class TestMessageCount:
    """The counter moves because a chat turn ran, not because a test added 1."""

    async def test_starts_at_zero_in_the_database(self, stack: Stack, hr_account: Account) -> None:
        """A freshly started session has counted no exchanges."""
        session_id = await _start_hr(stack, hr_account)

        assert (await stack.read_session(session_id)).message_count == 0

    async def test_hr_chat_turn_increments_it(self, stack: Stack, hr_account: Account) -> None:
        """One request to ``/api/assistant/chat`` counts as one exchange."""
        session_id = await _start_hr(stack, hr_account)

        status, body = await stack.as_(hr_account).post(
            "/api/assistant/chat", {**TURN, "session_id": session_id}
        )
        assert status == 200, body

        assert (await stack.read_session(session_id)).message_count == 1

    async def test_successive_turns_accumulate(self, stack: Stack, hr_account: Account) -> None:
        """Two exchanges count as two, through two real requests."""
        session_id = await _start_hr(stack, hr_account)

        for _ in range(2):
            status, body = await stack.as_(hr_account).post(
                "/api/assistant/chat", {**TURN, "session_id": session_id}
            )
            assert status == 200, body

        assert (await stack.read_session(session_id)).message_count == 2

    async def test_employee_chat_turn_increments_it(
        self, stack: Stack, ess_account: Account
    ) -> None:
        """The ESS assistant counts exchanges the same way."""
        session_id = await _start_ess(stack, ess_account)

        status, body = await stack.as_(ess_account).post(
            "/api/ess/assistant/chat", {**TURN, "session_id": session_id}
        )
        assert status == 200, body

        assert (await stack.read_session(session_id)).message_count == 1

    async def test_a_turn_without_a_session_id_counts_nothing(
        self, stack: Stack, hr_account: Account
    ) -> None:
        """Telemetry needs a session; a turn without one still answers.

        The frontend can send a turn before ``/session/start`` resolves, so this
        has to stay a working request that records nothing rather than an error.
        """
        session_id = await _start_hr(stack, hr_account)

        status, body = await stack.as_(hr_account).post("/api/assistant/chat", TURN)
        assert status == 200, body

        assert (await stack.read_session(session_id)).message_count == 0


class TestToolCallEvents:
    """Tool calls during a real turn are recorded against the session."""

    async def test_a_successful_tool_call_is_recorded(
        self, make_stack: Any, hr_account: Account
    ) -> None:
        """The row names the tool and satisfies the ``session_id`` foreign key.

        The old test constructed an ``AssistantToolCallEvent`` and read its
        attributes back, so it asserted that dataclass assignment works. This
        one only passes if the assistant actually called the tool and the row
        reached a table that enforces its foreign key.
        """
        stack = make_stack(
            turns=[tool_turn(name="a_tool"), text_turn("Xong")],
            results={"a_tool": '{"total": 5}'},
        )
        session_id = await _start_hr(stack, hr_account)

        status, body = await stack.as_(hr_account).post(
            "/api/assistant/chat", {**TURN, "session_id": session_id}
        )
        assert status == 200, body

        events = await stack.tool_events(session_id)
        assert [e.tool_name for e in events] == ["a_tool"]
        assert events[0].success is True
        assert events[0].duration_ms >= 0

    async def test_a_failing_tool_call_is_recorded_as_a_failure(
        self, make_stack: Any, hr_account: Account
    ) -> None:
        """A tool that fails still leaves a row, marked unsuccessful.

        Without this the metric would silently under-report exactly the calls
        anyone reading it cares about.
        """
        stack = make_stack(
            turns=[tool_turn(name="a_tool"), text_turn("Công cụ lỗi")],
            failing=frozenset({"a_tool"}),
        )
        session_id = await _start_hr(stack, hr_account)

        status, body = await stack.as_(hr_account).post(
            "/api/assistant/chat", {**TURN, "session_id": session_id}
        )
        assert status == 200, body

        events = await stack.tool_events(session_id)
        assert [(e.tool_name, e.success) for e in events] == [("a_tool", False)]

    async def test_no_tool_events_when_the_model_calls_no_tool(
        self, stack: Stack, hr_account: Account
    ) -> None:
        """A plain answer records nothing, so the metric is not inflated."""
        session_id = await _start_hr(stack, hr_account)

        await stack.as_(hr_account).post("/api/assistant/chat", {**TURN, "session_id": session_id})

        assert await stack.tool_events(session_id) == []
