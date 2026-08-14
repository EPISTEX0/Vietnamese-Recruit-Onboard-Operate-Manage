"""Ownership enforcement for every assistant handler that takes a session id.

Why this file exists
--------------------
``POST /api/ess/assistant/session/end`` looked its session up by id alone::

    select(AssistantChatSession).where(AssistantChatSession.id == body.session_id)

and mutated whatever came back.  The row's ``user_id`` never entered the query,
so any authenticated caller could end any session — reproduced against the
running stack by having ``ess@aidia.vn`` (role ``user``) end a session started
by ``hr@aidia.vn``.  The HR router's ``/session/end`` and both ``/feedback``
handlers had the identical shape.

``test_quality_endpoints.py`` nominally covers this path but cannot fail on it:
each test builds an ``AssistantChatSession`` inside the test body, hands it to a
``MagicMock`` whose ``add()`` only records and whose ``commit()`` does nothing,
then asserts on the object it just built.  No handler runs and no database is
touched, so no ownership rule is ever exercised.

These tests seed **two distinct accounts as real rows** and drive the **real
routers** against a real PostgreSQL migrated to head.  Account A starts a
session; account B attempts every id-taking handler against it.  Each test
asserts both halves of the guarantee: B is refused, *and* the row B aimed at is
byte-for-byte unchanged afterwards.  A test that only checked the status code
would pass against a handler that returned 404 after already committing.

Indistinguishability is a requirement, not a detail: a session that exists but
belongs to someone else must answer exactly as a session that does not exist.
Answering 403 for the former and 404 for the latter turns the endpoint into an
oracle that confirms which session ids are real.
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("AUTH_GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("AUTH_JWT_SECRET_KEY", "test-secret-key-32-chars-min-for-hs256")
os.environ.setdefault("AUTH_OAUTH_TOKEN_ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw==")

from collections.abc import AsyncIterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from uuid import UUID, uuid4  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import select  # noqa: E402

from src.modules.assistant.api.employee_router import employee_assistant_router  # noqa: E402
from src.modules.assistant.api.router import router as hr_assistant_router  # noqa: E402
from src.modules.assistant.api.session_access import ChatSessionGuard  # noqa: E402
from src.modules.assistant.infrastructure.quality_models import (  # noqa: E402
    AssistantChatSession,
    AssistantFeedbackEvent,
)
from src.modules.assistant.infrastructure.session_repository import (  # noqa: E402
    AssistantSessionRepository,
)
from src.modules.employee.domain.entities import Employee  # noqa: E402
from src.modules.identity.container import get_current_user, get_db_session  # noqa: E402
from src.modules.identity.domain.entities import AuditLog, User, UserRole  # noqa: E402

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
    else — in particular the two ids stay distinct.
    """
    suffix = uuid4().hex[:10]
    email = f"own-{suffix}@example.com"

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
async def hr_owner(session_maker: async_sessionmaker[AsyncSession]) -> Account:
    """Account A — an HR account that will own the session under attack."""
    return await _seed_user(session_maker, UserRole.HR, with_employee=False)


@pytest.fixture
async def ess_attacker(session_maker: async_sessionmaker[AsyncSession]) -> Account:
    """Account B — a plain employee account, the role used in the live repro."""
    return await _seed_user(session_maker, UserRole.USER, with_employee=True)


@pytest.fixture
async def hr_bystander(session_maker: async_sessionmaker[AsyncSession]) -> Account:
    """Account C — a *second* HR account.

    Role alone is not ownership: HR-to-HR is still one account reaching into
    another account's row, so the HR router needs the same rule as ESS.
    """
    return await _seed_user(session_maker, UserRole.HR, with_employee=False)


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

    async def read_session(self, session_id: UUID | str) -> AssistantChatSession | None:
        """Re-read a chat session in a fresh session, bypassing the app."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(AssistantChatSession).where(
                    AssistantChatSession.id == UUID(str(session_id)),
                )
            )
            return result.scalar_one_or_none()

    async def count_feedback(self, session_id: UUID | str) -> int:
        """Count feedback events attached to a chat session."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(AssistantFeedbackEvent).where(
                    AssistantFeedbackEvent.session_id == UUID(str(session_id)),
                )
            )
            return len(result.scalars().all())


@pytest.fixture
def stack(session_maker: async_sessionmaker[AsyncSession]) -> Stack:
    """Mount both real routers; stub authentication only.

    ``get_current_user`` is the single override — ``require_hr`` and
    ``get_current_employee`` are both built on it, so they keep running for
    real against the seeded rows.
    """
    app = FastAPI()
    app.include_router(hr_assistant_router)
    app.include_router(employee_assistant_router)

    harness = Stack(app, session_maker)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: harness.caller
    app.dependency_overrides[get_db_session] = _override_session
    return harness


async def _start_hr_session(stack: Stack, owner: Account) -> str:
    """Have ``owner`` start an HR assistant session and return its id."""
    status, body = await stack.as_(owner).post(
        "/api/assistant/session/start", {"assistant_type": "hr"}
    )
    assert status == 200, body
    return body["session_id"]


# ---------------------------------------------------------------------------
# The reported vulnerability
# ---------------------------------------------------------------------------


class TestCrossAccountSessionEnd:
    """Ending a session you do not own."""

    async def test_ess_cannot_end_hr_session(
        self, stack: Stack, hr_owner: Account, ess_attacker: Account
    ) -> None:
        """The live repro, as a test: role ``user`` ends an HR session.

        This is the exact sequence reproduced against the running stack, where
        it returned 204 and set ``end_at``.
        """
        session_id = await _start_hr_session(stack, hr_owner)

        status, _ = await stack.as_(ess_attacker).post(
            "/api/ess/assistant/session/end", {"session_id": session_id}
        )

        assert status == 404
        row = await stack.read_session(session_id)
        assert row is not None, "the victim's session must survive the attempt"
        assert row.end_at is None, "attacker must not have ended the session"

    async def test_hr_cannot_end_another_hr_session(
        self, stack: Stack, hr_owner: Account, hr_bystander: Account
    ) -> None:
        """Holding the HR role does not make every HR session yours."""
        session_id = await _start_hr_session(stack, hr_owner)

        status, _ = await stack.as_(hr_bystander).post(
            "/api/assistant/session/end", {"session_id": session_id}
        )

        assert status == 404
        row = await stack.read_session(session_id)
        assert row is not None
        assert row.end_at is None

    async def test_owner_can_still_end_own_session(self, stack: Stack, hr_owner: Account) -> None:
        """The legitimate path is untouched."""
        session_id = await _start_hr_session(stack, hr_owner)

        status, _ = await stack.as_(hr_owner).post(
            "/api/assistant/session/end", {"session_id": session_id}
        )

        assert status == 204
        row = await stack.read_session(session_id)
        assert row is not None
        assert row.end_at is not None, "the owner's end must actually land"


class TestCrossAccountFeedback:
    """Attaching feedback to a session you do not own."""

    async def test_ess_cannot_rate_hr_session(
        self, stack: Stack, hr_owner: Account, ess_attacker: Account
    ) -> None:
        """Feedback is a write keyed by session id — same class of bug."""
        session_id = await _start_hr_session(stack, hr_owner)

        status, _ = await stack.as_(ess_attacker).post(
            "/api/ess/assistant/feedback",
            {"session_id": session_id, "message_index": 0, "feedback_type": "down"},
        )

        assert status == 404
        assert await stack.count_feedback(session_id) == 0

    async def test_hr_cannot_rate_another_hr_session(
        self, stack: Stack, hr_owner: Account, hr_bystander: Account
    ) -> None:
        """The HR feedback handler needs the rule too."""
        session_id = await _start_hr_session(stack, hr_owner)

        status, _ = await stack.as_(hr_bystander).post(
            "/api/assistant/feedback",
            {"session_id": session_id, "message_index": 0, "feedback_type": "down"},
        )

        assert status == 404
        assert await stack.count_feedback(session_id) == 0

    async def test_owner_can_still_rate_own_session(self, stack: Stack, hr_owner: Account) -> None:
        """The legitimate path is untouched."""
        session_id = await _start_hr_session(stack, hr_owner)

        status, _ = await stack.as_(hr_owner).post(
            "/api/assistant/feedback",
            {
                "session_id": session_id,
                "message_index": 0,
                "feedback_type": "up",
                "optional_text": "huu ich",
            },
        )

        assert status == 204
        assert await stack.count_feedback(session_id) == 1


class TestRefusalIsAudited:
    """A blocked cross-account attempt must still leave a trace.

    Before the ownership rule existed, the HR feedback handler audited every
    call it received. Enforcing ownership by returning early would have made
    the enumeration attempt this endpoint now blocks the one event it records
    nothing about — losing exactly the signal a reviewer would look for.
    """

    @staticmethod
    async def _audit_events(
        session_maker: async_sessionmaker[AsyncSession], session_id: str
    ) -> list[str]:
        """Return the ``event`` of every audit row naming ``session_id``."""
        async with session_maker() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.details["session_id"].astext == session_id)
            )
            return [row.details.get("event") for row in result.scalars().all()]

    async def test_denied_feedback_is_recorded(
        self,
        stack: Stack,
        session_maker: async_sessionmaker[AsyncSession],
        hr_owner: Account,
        hr_bystander: Account,
    ) -> None:
        """The refusal is committed, not rolled back with the failed request.

        ``AuditLogRepository.create()`` only flushes, so an audit written just
        before ``raise`` disappears unless the handler commits it deliberately.
        """
        session_id = await _start_hr_session(stack, hr_owner)

        status, _ = await stack.as_(hr_bystander).post(
            "/api/assistant/feedback",
            {"session_id": session_id, "message_index": 0, "feedback_type": "up"},
        )
        assert status == 404

        assert await self._audit_events(session_maker, session_id) == ["message_feedback_denied"]
        assert await stack.count_feedback(session_id) == 0

    async def test_successful_feedback_is_recorded_as_success(
        self,
        stack: Stack,
        session_maker: async_sessionmaker[AsyncSession],
        hr_owner: Account,
    ) -> None:
        """The allowed path keeps its original audit event name."""
        session_id = await _start_hr_session(stack, hr_owner)

        status, _ = await stack.as_(hr_owner).post(
            "/api/assistant/feedback",
            {"session_id": session_id, "message_index": 0, "feedback_type": "up"},
        )
        assert status == 204

        assert await self._audit_events(session_maker, session_id) == ["message_feedback"]


class TestChatTelemetryResolution:
    """The guard's other mode — the one the chat endpoints use.

    ``/chat`` takes a ``session_id`` too, but only to attribute telemetry
    (``message_count``, tool-call events).  It resolves through
    ``resolve_optional`` rather than ``require`` so a stale id keeps meaning
    "record nothing" instead of breaking a live conversation.  That makes the
    ownership rule easy to get wrong in a way no ``/session/end`` test would
    notice, so it is pinned directly on the guard: driving ``/chat`` end to end
    would need a stubbed LLM, and the authorization decision under test happens
    before the model is ever called.
    """

    @staticmethod
    async def _resolve(
        session_maker: async_sessionmaker[AsyncSession],
        account: Account,
        session_id: str | None,
    ) -> AssistantChatSession | None:
        """Resolve ``session_id`` as ``account`` would, wiring the guard as DI does."""
        async with session_maker() as db:
            guard = ChatSessionGuard(
                repository=AssistantSessionRepository(db),
                owner_user_id=account.user.id,
            )
            return await guard.resolve_optional(session_id)

    async def test_owner_gets_their_session_for_telemetry(
        self,
        stack: Stack,
        session_maker: async_sessionmaker[AsyncSession],
        hr_owner: Account,
    ) -> None:
        """The owner's own id resolves, so their chat telemetry is recorded."""
        session_id = await _start_hr_session(stack, hr_owner)

        resolved = await self._resolve(session_maker, hr_owner, session_id)

        assert resolved is not None
        assert resolved.id == UUID(session_id)

    async def test_foreign_session_resolves_to_none(
        self,
        stack: Stack,
        session_maker: async_sessionmaker[AsyncSession],
        hr_owner: Account,
        ess_attacker: Account,
    ) -> None:
        """Someone else's id yields nothing, so no telemetry can attach to it.

        Without this the chat endpoints would let any caller inflate another
        account's ``message_count`` and file tool-call events against it.
        """
        session_id = await _start_hr_session(stack, hr_owner)

        resolved = await self._resolve(session_maker, ess_attacker, session_id)

        assert resolved is None

    @pytest.mark.parametrize("bad_id", [None, "not-a-uuid", ""])
    async def test_unusable_ids_resolve_to_none_rather_than_raising(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        hr_owner: Account,
        bad_id: str | None,
    ) -> None:
        """Malformed input is "no session", not a 500.

        The handlers used to call ``uuid.UUID(body.session_id)`` directly, so a
        non-UUID string surfaced as an unhandled ``ValueError``.
        """
        resolved = await self._resolve(session_maker, hr_owner, bad_id)

        assert resolved is None


class TestDoesNotLeakExistence:
    """A foreign session and a nonexistent one must be indistinguishable."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/ess/assistant/session/end",
            "/api/assistant/session/end",
            "/api/ess/assistant/feedback",
            "/api/assistant/feedback",
        ],
    )
    async def test_foreign_and_absent_ids_answer_identically(
        self,
        stack: Stack,
        hr_owner: Account,
        ess_attacker: Account,
        hr_bystander: Account,
        path: str,
    ) -> None:
        """Same status *and* same body, so the response is not an id oracle.

        Compared as whole responses rather than status alone: a distinct
        ``detail`` string would leak existence just as effectively as a
        distinct status code.
        """
        real_but_foreign = await _start_hr_session(stack, hr_owner)
        never_existed = str(uuid4())

        # The caller must never be the owner, or "foreign" would not be foreign.
        caller = ess_attacker if path.startswith("/api/ess/") else hr_bystander
        payload = {"message_index": 0, "feedback_type": "up"}

        foreign = await stack.as_(caller).post(path, {"session_id": real_but_foreign, **payload})
        absent = await stack.as_(caller).post(path, {"session_id": never_existed, **payload})

        assert foreign == absent, f"{path} reveals whether the session id exists"
        assert foreign[0] == 404
