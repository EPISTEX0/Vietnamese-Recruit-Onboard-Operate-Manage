"""Integration coverage for ``POST /api/ess/assistant/chat/stream``.

Why this file exists
--------------------
``AiChat`` posts every employee message to ``/chat/stream``; it never calls
``/chat``.  The ESS router had no such handler, so employee chat answered 404
to every turn — not a missing streaming nicety but a feature that had never
worked.  ``test_ess_assistant_routes.py`` now asserts the route is registered;
this file asserts what it does once it is.

It follows the harness ``test_ess_session_start_integration.py`` established:
the real router over a real PostgreSQL migrated to head, with the login account
and the Employee seeded as two distinct rows so that ``user_id == user.id`` is a
statement about *which* id was chosen rather than a tautology.

Only two things are substituted.  Authentication is stubbed, as it is there.
And the assistant service is built on a scripted LLM instead of a live one —
the events under test are produced by the loop from the model's chunks, so a
real model would make the assertions non-deterministic without making them
stronger.  Everything between the HTTP request and the database is real: the
route, the ownership guard, the SSE framing, and the telemetry writes.
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("AUTH_GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("AUTH_JWT_SECRET_KEY", "test-secret-key-32-chars-min-for-hs256")
os.environ.setdefault("AUTH_OAUTH_TOKEN_ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw==")

import json  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402
from uuid import UUID, uuid4  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient, Response  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import select  # noqa: E402

from src.modules.assistant.api.employee_router import (  # noqa: E402
    employee_assistant_router,
    get_employee_assistant_service,
)
from src.modules.assistant.api.sse import STREAM_ERROR_MESSAGE  # noqa: E402
from src.modules.assistant.application import (  # noqa: E402
    employee_assistant_service as ess_module,
)
from src.modules.assistant.application.employee_assistant_service import (  # noqa: E402
    EmployeeAssistantService,
)
from src.modules.assistant.infrastructure.config import AssistantSettings  # noqa: E402
from src.modules.assistant.infrastructure.quality_models import (  # noqa: E402
    AssistantChatSession,
    AssistantToolCallEvent,
)
from src.modules.employee.domain.entities import Employee  # noqa: E402
from src.modules.identity.container import get_current_user, get_db_session  # noqa: E402
from src.modules.identity.domain.entities import User, UserRole  # noqa: E402
from tests.modules.assistant.assistant_support import (  # noqa: E402
    ExplodingLLMClient,
    FakeLLMClient,
    FakeRegistry,
    text_turn,
    tool_turn,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass
class Account:
    """One seeded login account and the Employee record it resolves to."""

    user: User
    employee: Employee


@pytest.fixture
async def session_maker(postgres_async_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory bound to the migrated test database."""
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed_account(
    session_maker: async_sessionmaker[AsyncSession],
    label: str,
) -> Account:
    """Seed a User and its matching Employee, linked the way production links them."""
    suffix = uuid4().hex[:10]
    email = f"{label}-{suffix}@example.com"

    employee = Employee(
        employee_code=f"E{suffix}",
        full_name=f"{label} Employee",
        email=email,
    )
    user = User(email=email, name=f"{label} User", role=UserRole.USER)

    async with session_maker() as session:
        session.add(employee)
        session.add(user)
        await session.commit()

    return Account(user=user, employee=employee)


@pytest.fixture
async def caller(session_maker: async_sessionmaker[AsyncSession]) -> Account:
    """The employee making the chat request."""
    return await _seed_account(session_maker, "caller")


@pytest.fixture
async def stranger(session_maker: async_sessionmaker[AsyncSession]) -> Account:
    """A second account, whose sessions the caller must not be able to use."""
    return await _seed_account(session_maker, "stranger")


def _settings() -> AssistantSettings:
    return AssistantSettings(
        base_url="http://localhost:8000",
        api_key="test-key",
        model="test-model",
        max_history=20,
        timeout_seconds=30,
    )


@dataclass
class Stack:
    """The mounted app plus the doubles its assistant was built from."""

    app: FastAPI
    llm: Any
    registry: FakeRegistry


@pytest.fixture
def make_stack(
    session_maker: async_sessionmaker[AsyncSession],
    caller: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Mount the real ESS router with a scripted LLM behind the real service."""

    def _make(llm: Any, results: dict[str, str] | None = None) -> Stack:
        registry = FakeRegistry(results or {})
        monkeypatch.setattr(ess_module, "EmployeeToolRegistry", lambda **_kw: registry)

        async def _override_session() -> AsyncIterator[AsyncSession]:
            # Mirrors ``get_db_session``, trailing commit included. That commit
            # is what persists streaming telemetry: the loop only stages rows,
            # and the dependency is finalised after the last SSE frame is sent,
            # so an override that merely yielded would drop every write the
            # stream made and look exactly like a broken endpoint.
            async with session_maker() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        def _override_service() -> EmployeeAssistantService:
            # The real service class, so the real loop runs; only the model and
            # the tool registry behind it are scripted.
            return EmployeeAssistantService(
                llm_client=llm,
                employee_id=caller.employee.id,
                employee_service=MagicMock(),
                document_service=MagicMock(),
                attendance_repo=MagicMock(),
                leave_service=MagicMock(),
                overtime_service=MagicMock(),
                payslip_service=MagicMock(),
                settings=_settings(),
                context_builder=None,
            )

        app = FastAPI()
        app.include_router(employee_assistant_router)
        app.dependency_overrides[get_current_user] = lambda: caller.user
        app.dependency_overrides[get_db_session] = _override_session
        app.dependency_overrides[get_employee_assistant_service] = _override_service
        return Stack(app=app, llm=llm, registry=registry)

    return _make


async def _post_stream(app: FastAPI, body: dict[str, Any]) -> Response:
    """POST to the streaming endpoint and read the whole body."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/ess/assistant/chat/stream", json=body)


def _parse_sse(payload: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into ``(event_name, data)`` pairs.

    Written against the wire format rather than the generator's dicts, so a
    change to the framing that the frontend's reader could not parse fails
    here instead of in a browser.
    """
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in payload.split("\n\n"):
        name: str | None = None
        data: str | None = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if name is not None and data is not None:
            events.append((name, json.loads(data)))
    return events


async def _start_session(app: FastAPI) -> str:
    """Start a chat session through the real endpoint and return its id."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/ess/assistant/session/start",
            json={"assistant_type": "employee"},
        )
    assert response.status_code == 200, response.text
    return str(response.json()["session_id"])


async def _seed_foreign_session(
    session_maker: async_sessionmaker[AsyncSession],
    owner: Account,
) -> AssistantChatSession:
    """Create a chat session owned by somebody other than the caller."""
    chat_session = AssistantChatSession(
        user_id=owner.user.id,
        assistant_type="employee",
        employee_id=owner.employee.id,
    )
    async with session_maker() as session:
        session.add(chat_session)
        await session.commit()
        await session.refresh(chat_session)
    return chat_session


ONE_TURN = {"messages": [{"role": "user", "content": "Còn bao nhiêu ngày phép?"}]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStreamsAnAnswer:
    """The endpoint the client actually posts to returns a stream, not a 404."""

    async def test_returns_an_event_stream(self, make_stack: Any) -> None:
        """A chat turn succeeds and is served as ``text/event-stream``.

        The old behaviour was 404 on this path for every employee message, so
        the status code alone is the regression this file exists to hold.
        """
        stack = make_stack(FakeLLMClient([text_turn("Bạn còn ", "12 ngày")]))

        response = await _post_stream(stack.app, ONE_TURN)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")

    async def test_streams_deltas_then_done(self, make_stack: Any) -> None:
        """The body carries each text fragment, terminated by ``done``."""
        stack = make_stack(FakeLLMClient([text_turn("Bạn còn ", "12 ngày")]))

        response = await _post_stream(stack.app, ONE_TURN)
        events = _parse_sse(response.text)

        assert [name for name, _ in events] == ["text_delta", "text_delta", "done"]
        assert "".join(data["content"] for _, data in events[:2]) == "Bạn còn 12 ngày"

    async def test_tool_calls_are_narrated_to_the_client(self, make_stack: Any) -> None:
        """A tool round-trip emits ``tool_start`` and ``tool_end`` on the wire."""
        stack = make_stack(
            FakeLLMClient([tool_turn(name="a_tool"), text_turn("Xong")]),
            results={"a_tool": '{"days": 12}'},
        )

        response = await _post_stream(stack.app, ONE_TURN)
        names = [name for name, _ in _parse_sse(response.text)]

        assert names == ["tool_start", "tool_end", "text_delta", "done"]

    async def test_rejects_a_history_not_ending_with_the_user(self, make_stack: Any) -> None:
        """The same 422 guard the non-streaming endpoint applies."""
        stack = make_stack(FakeLLMClient([text_turn("Xong")]))

        response = await _post_stream(
            stack.app,
            {"messages": [{"role": "assistant", "content": "Xin chào"}]},
        )

        assert response.status_code == 422


class TestMidStreamFailure:
    """A failure after the first byte cannot become an HTTP status."""

    async def test_reports_an_error_event_rather_than_truncating(self, make_stack: Any) -> None:
        """The client is told the stream died instead of waiting out its timeout.

        The response has already committed 200 by the time the model fails, so
        the only channel left is an ``error`` event — which the frontend reader
        treats as terminal.  Without it the connection just stops and the UI
        hangs until the 60s abort fires.
        """
        stack = make_stack(ExplodingLLMClient("upstream died"))

        response = await _post_stream(stack.app, ONE_TURN)
        events = _parse_sse(response.text)

        assert response.status_code == 200
        assert [name for name, _ in events] == ["text_delta", "error"]

    async def test_the_error_payload_uses_the_key_the_client_reads(self, make_stack: Any) -> None:
        """``AiChat`` reads ``data.message``; anything else renders as unknown.

        Asserted on the wire rather than trusting the key name, because the two
        sides agreeing is the entire value of the event — a payload the reader
        cannot find is indistinguishable from no payload at all.
        """
        stack = make_stack(ExplodingLLMClient("upstream died"))

        events = _parse_sse((await _post_stream(stack.app, ONE_TURN)).text)
        payload = events[-1][1]

        assert payload["message"] == STREAM_ERROR_MESSAGE

    async def test_the_error_payload_does_not_leak_internals(self, make_stack: Any) -> None:
        """The upstream failure text stays in the log, not in the response.

        This stream is reachable by ordinary employee accounts, and an LLM
        client error carries the provider URL and its response body.
        """
        stack = make_stack(ExplodingLLMClient("connect to http://secret-gateway:9/v1 failed"))

        body = (await _post_stream(stack.app, ONE_TURN)).text

        assert "secret-gateway" not in body
        assert "connect to" not in body


class TestTelemetryOwnership:
    """Telemetry is attributed only to a session the caller owns."""

    async def test_tool_events_are_written_against_the_caller_own_session(
        self,
        make_stack: Any,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A tool call on an owned session persists an ``AssistantToolCallEvent``.

        ``assistant_tool_call_events.session_id`` is a real foreign key here, so
        this only passes if the row points at a session that genuinely exists.
        """
        stack = make_stack(
            FakeLLMClient([tool_turn(name="a_tool"), text_turn("Xong")]),
            results={"a_tool": "{}"},
        )
        session_id = await _start_session(stack.app)

        response = await _post_stream(stack.app, {**ONE_TURN, "session_id": session_id})
        assert response.status_code == 200

        async with session_maker() as session:
            rows = (
                (
                    await session.execute(
                        select(AssistantToolCallEvent).where(
                            AssistantToolCallEvent.session_id == UUID(session_id),
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert [row.tool_name for row in rows] == ["a_tool"]
        assert rows[0].success is True

    async def test_message_count_advances_on_the_owned_session(
        self,
        make_stack: Any,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """One streamed turn counts as one exchange, persisted."""
        stack = make_stack(FakeLLMClient([text_turn("Xong")]))
        session_id = await _start_session(stack.app)

        response = await _post_stream(stack.app, {**ONE_TURN, "session_id": session_id})
        assert response.status_code == 200

        async with session_maker() as session:
            row = (
                await session.execute(
                    select(AssistantChatSession).where(
                        AssistantChatSession.id == UUID(session_id),
                    )
                )
            ).scalar_one()

        assert row.message_count == 1

    async def test_a_foreign_session_id_records_nothing_against_it(
        self,
        make_stack: Any,
        session_maker: async_sessionmaker[AsyncSession],
        stranger: Account,
    ) -> None:
        """Somebody else's session id neither errors nor collects the caller's data.

        This is the ``/chat/stream`` half of the IDOR closed in 1c4e65f. The
        turn is allowed to proceed — chat has always tolerated an unusable id,
        because a client can hold a stale one across a restart — but the guard
        resolves it to ``None``, so nothing is attributed to the stranger's
        session: no tool events, and their message counter does not move.
        """
        foreign = await _seed_foreign_session(session_maker, stranger)
        stack = make_stack(
            FakeLLMClient([tool_turn(name="a_tool"), text_turn("Xong")]),
            results={"a_tool": "{}"},
        )

        response = await _post_stream(stack.app, {**ONE_TURN, "session_id": str(foreign.id)})
        assert response.status_code == 200

        async with session_maker() as session:
            events = (
                (
                    await session.execute(
                        select(AssistantToolCallEvent).where(
                            AssistantToolCallEvent.session_id == foreign.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            row = (
                await session.execute(
                    select(AssistantChatSession).where(AssistantChatSession.id == foreign.id)
                )
            ).scalar_one()

        assert events == []
        assert row.message_count == 0

    @pytest.mark.parametrize("bad_id", ["not-a-uuid", ""])
    async def test_an_unusable_session_id_still_answers(self, make_stack: Any, bad_id: str) -> None:
        """A malformed id degrades to "no telemetry", not to a 500.

        ``uuid.UUID(...)`` used to raise straight out of the handler body; the
        guard turns that into ``None`` so the employee still gets an answer.
        """
        stack = make_stack(FakeLLMClient([text_turn("Xong")]))

        response = await _post_stream(stack.app, {**ONE_TURN, "session_id": bad_id})

        assert response.status_code == 200
        assert [name for name, _ in _parse_sse(response.text)] == ["text_delta", "done"]
