"""The six endpoints classified read-only really emit no writes (#320).

``test_router_commit.py`` partitions all twenty routes into writers,
service-owned committers, and ``READ_ONLY_ENDPOINTS``. That partition is what
lets the module claim full coverage -- but nothing there checks the read-only
claim itself. Its ``test_read_only_endpoints_do_not_commit`` asserts only that
the handler's *source* has no ``commit()``, which is a weaker proposition than
the one the classification rests on, and it fails in the wrong direction: for an
endpoint that has silently started writing, the *absence* of a commit is the
bug, not the evidence of correctness.

Concretely: ``list_calendars_for_selection`` calls
``OrganizationGoogleConnectionService.list_calendars``. Its two sibling methods
on that same service, ``get_status`` and ``initiate``, both open with
``await self._reconcile_legacy_grants()`` -- which revokes grants, clears the
sync cursor and rewrites the connection row. Adding that one line to
``list_calendars`` for consistency would turn the endpoint into a silent writer
living on the teardown commit, while the census stayed green (the name is still
in ``READ_ONLY_ENDPOINTS``) and the source check stayed green (still no
``commit()``). #320 would come back inside a file advertising full coverage.

So this module asserts the property directly, on real PostgreSQL with the real
``get_db_session``: drive each read-only endpoint and assert the request emitted
no ``INSERT``/``UPDATE``/``DELETE``.

The detector is a Core ``before_cursor_execute`` listener rather than an
inspection of ``session.new``/``dirty``/``deleted`` after the fact. Those three
sets are emptied by any ``flush()`` while the transaction still holds
uncommitted changes, so a repository that does ``add()`` then ``flush()`` --
which is exactly what ``OrganizationGoogleConnectionRepository`` does -- would
be invisible to a post-hoc check. Watching the statements as they go to the
driver is independent of when the flush happens.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.modules.identity.api.admin_router import require_hr
from src.modules.identity.api.router import router
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import (
    OAuthGrant,
    OrganizationGoogleConnection,
    RefreshToken,
    User,
    UserRole,
)
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from src.modules.recruitment.infrastructure.calendar_adapter import CalendarAdapter
from tests.conftest import _run_alembic_upgrade_head

pytestmark = pytest.mark.integration

_TEST_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
_WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE")


@dataclass(frozen=True)
class _Fixtures:
    """What ``seeded`` hands to a test, named rather than positional."""

    hr: User
    raw_refresh_token: str
    recorder: _WriteRecorder


class _WriteRecorder:
    """Records write statements sent to the driver while ``armed``.

    Hooks ``before_cursor_execute``, so it sees the SQL itself regardless of
    which ORM path produced it or when the session chose to flush.
    """

    def __init__(self) -> None:
        self.armed = False
        self.statements: list[str] = []

    def __call__(
        self,
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if self.armed and statement.lstrip().upper().startswith(_WRITE_PREFIXES):
            self.statements.append(" ".join(statement.split())[:120])


@pytest.fixture(scope="module")
def probe_db_url(postgres_async_url: str) -> Iterator[str]:
    """A database this module alone writes to."""
    parts = urlsplit(postgres_async_url)
    db_name = "auth_read_only_probe"
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    private_url = urlunsplit(parts._replace(path=f"/{db_name}"))

    async def _recreate() -> None:
        engine = create_async_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            await connection.execute(text(f'CREATE DATABASE "{db_name}"'))
        await engine.dispose()

    asyncio.run(_recreate())
    _run_alembic_upgrade_head(private_url)
    yield private_url


@pytest_asyncio.fixture
async def seeded(probe_db_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Fixtures]:
    """Seed the read fixtures and point the real ``get_db_session`` at them.

    Only the session *factory* is swapped, so ``get_db_session`` runs its real
    body. The recorder is attached to that same engine, which is what makes it
    the request's own traffic being observed and not some other pool's.

    The connection row is seeded ``connected`` with an access token because
    ``list_calendars`` refuses to run otherwise -- and an endpoint that refuses
    early would prove nothing about whether its working path writes.
    """
    engine = create_async_engine(probe_db_url, poolclass=NullPool)
    recorder = _WriteRecorder()
    event.listen(engine.sync_engine, "before_cursor_execute", recorder)

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        "src.modules.identity.container._get_async_session_maker",
        lambda: maker,
    )

    crypto = CryptoUtils(_TEST_KEY_B64)
    user = User(id=uuid4(), email="hr@example.com", name="HR", role=UserRole.HR)
    raw_refresh_token = secrets.token_urlsafe(32)

    async with maker() as session:
        for table in (
            "audit_logs",
            "oauth_configs",
            "organization_google_connections",
            "refresh_tokens",
            "oauth_grants",
            "users",
        ):
            await session.execute(text(f"DELETE FROM {table}"))
        session.add(user)
        # Both rows below carry a FK to this user and no ORM relationship
        # declares the dependency, so the insert order has to be forced.
        await session.flush()
        session.add(
            OrganizationGoogleConnection(
                status="connected",
                email="workspace@example.com",
                access_token_enc=crypto.encrypt("access-token"),
                connected_by_user_id=user.id,
            )
        )
        session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hashlib.sha256(raw_refresh_token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        # A live legacy grant, and the reason this module can detect anything at
        # all. ``_reconcile_legacy_grants`` -> ``OAuthGrantRepository.revoke_all``
        # only writes when a valid ``google`` grant exists (it guards the flush
        # on ``if grants``). Seed none and the very regression this module exists
        # to catch would run its write path over an empty table, emit no SQL, and
        # pass.
        session.add(
            OAuthGrant(
                user_id=user.id,
                provider="google",
                access_token_enc=crypto.encrypt("legacy-access"),
                refresh_token_enc=crypto.encrypt("legacy-refresh"),
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                is_valid=True,
            )
        )
        await session.commit()

    try:
        yield _Fixtures(hr=user, raw_refresh_token=raw_refresh_token, recorder=recorder)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", recorder)
        await engine.dispose()


@pytest.fixture
def app(seeded: _Fixtures, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """The real auth router with auth guards and the one outbound call stubbed."""
    user = seeded.hr

    async def _no_calendars(_self: CalendarAdapter, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(CalendarAdapter, "list_calendars", _no_calendars)
    # Every module that bound ``get_crypto_utils`` by name, not just the two in
    # identity: ``gmail.container`` imports it at module level, and
    # ``get_password_reset_service`` reaches it through there when FastAPI
    # resolves ``/reset-password-token-info``. Patching only identity's copies
    # passes this module in isolation and fails in the full suite, where
    # ``tests/env_isolation.py`` installs a 28-byte key the real
    # ``CryptoUtils`` rejects.
    for target in (
        "src.modules.identity.container.get_crypto_utils",
        "src.modules.identity.api.router.get_crypto_utils",
        "src.modules.gmail.container.get_crypto_utils",
    ):
        monkeypatch.setattr(target, lambda: CryptoUtils(_TEST_KEY_B64))

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[require_hr] = lambda: user
    application.dependency_overrides[get_current_user] = lambda: user
    return application


#: One entry per name in ``test_router_commit.READ_ONLY_ENDPOINTS``: the request
#: that drives it down its working path. ``test_every_read_only_endpoint_is_covered``
#: proves the two lists cannot drift apart.
READ_ONLY_REQUESTS: dict[str, tuple[str, str]] = {
    "setup_status": ("GET", "/api/auth/setup-status"),
    "list_calendars_for_selection": ("GET", "/api/auth/organization-google-connection/calendars"),
    "reset_password_token_info": ("GET", "/api/auth/reset-password-token-info?token=irrelevant"),
    "refresh": ("POST", "/api/auth/refresh"),
    "me": ("GET", "/api/auth/me"),
    "grant_status": ("GET", "/api/auth/grant-status"),
}


def test_every_read_only_endpoint_is_covered() -> None:
    """Every endpoint the census calls read-only is actually exercised here.

    Without this, an endpoint could be added to ``READ_ONLY_ENDPOINTS`` -- which
    is what lets the census claim full coverage -- while never being driven
    against a real database.
    """
    from tests.modules.identity.test_router_commit import READ_ONLY_ENDPOINTS

    assert set(READ_ONLY_REQUESTS) == READ_ONLY_ENDPOINTS


@pytest.mark.parametrize("name", sorted(READ_ONLY_REQUESTS))
async def test_read_only_endpoint_emits_no_write(
    name: str, app: FastAPI, seeded: _Fixtures
) -> None:
    """Driving the endpoint sends no INSERT/UPDATE/DELETE to the database."""
    recorder = seeded.recorder
    method, url = READ_ONLY_REQUESTS[name]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("refresh_token", seeded.raw_refresh_token)
        recorder.armed = True
        try:
            outcome: Any = await client.request(method, url)
        except Exception as exc:
            # Held rather than propagated: an endpoint that starts writing
            # frequently breaks itself too -- adding ``_reconcile_legacy_grants``
            # to ``list_calendars`` leaves the connection not-connected, so the
            # handler raises straight out of the request. Letting that escape
            # here would end the test before the recorder is ever read, and the
            # failure would name an unrelated exception while the SQL that
            # actually matters went unmentioned.
            outcome = exc
        finally:
            recorder.armed = False

    assert recorder.statements == [], (
        f"{name} is classified read-only but wrote: {recorder.statements}"
    )
    if isinstance(outcome, BaseException):
        raise AssertionError(f"{name} did not complete: {outcome!r}") from outcome
    # Not decoration: a handler that errored out early emits no writes for the
    # boring reason, and would otherwise pass this module vacuously.
    assert outcome.status_code == 200, outcome.text
