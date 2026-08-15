"""The write is durable before the client sees the response (#320), on real Postgres.

``test_router_commit.py`` proves each handler calls ``commit()``. It cannot
prove that call is what makes the row visible to everyone else *before* the
response leaves, because it hands the handler a mock session. This module runs
the whole seam end to end: real PostgreSQL, the real ``get_db_session``
generator with **no** ``dependency_overrides`` entry, and the real router.

It is the sibling of ``test_admin_oauth_commit_integration.py`` (#312) pointed
at the second write path into ``oauth_configs`` -- the one #320 was filed about,
which #312's fix did not reach.

Two endpoints are covered, because the two shapes fail differently:

* ``POST /api/auth/organization-google-connection`` writes ``oauth_configs``
  through ``OAuthConfigRepository.upsert``, which only ``flush()``es. Nothing
  below the handler commits, so the handler's own commit is the only thing
  holding the assertion up.
* ``DELETE /api/auth/organization-google-connection`` audits *inside*
  ``OrganizationGoogleConnectionService.disconnect`` rather than in the handler.
  That is the arrangement ``admin_router`` never had, so it needs its own
  evidence that the audit row is durable by the time the response starts.

The check has to happen *while the response is being sent*, not after the
request returns: by then FastAPI has drained its dependency stack and the
teardown commit has run, so a post-hoc query cannot tell the fix from the bug.
So the probe hangs off the ASGI ``send`` callable and reads through a second,
independent connection -- an uncommitted row is invisible to it.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, MutableMapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.modules.identity.api.admin_router import require_hr
from src.modules.identity.api.router import router
from src.modules.identity.application.oauth_config_manager import OAuthConfigManager
from src.modules.identity.domain.entities import User, UserRole
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from tests.conftest import _run_alembic_upgrade_head

pytestmark = pytest.mark.integration

_CLIENT_ID = "320-explicit-commit.apps.googleusercontent.com"
_REDIRECT_URI = "https://app.example.com/api/auth/callback"
_TEST_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

_Scope = MutableMapping[str, Any]
_Message = MutableMapping[str, Any]
_Send = Callable[[_Message], Awaitable[None]]


@pytest.fixture(scope="module")
def probe_db_url(postgres_async_url: str) -> Iterator[str]:
    """A database this module alone writes to.

    The shared session database is written by other integration modules, and
    the assertion here is "this exact row became visible", so it needs tables
    nobody else touches.
    """
    parts = urlsplit(postgres_async_url)
    db_name = "auth_router_commit_probe"
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
async def probe_engine(probe_db_url: str) -> AsyncIterator[AsyncEngine]:
    """A second connection pool, so reads see only committed data."""
    engine = create_async_engine(probe_db_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def hr_user(probe_db_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[User]:
    """Point the real ``get_db_session`` at the probe database and seed an HR user.

    Only the session *factory* is swapped. ``get_db_session`` itself -- and in
    particular its ``yield session; await session.commit()`` body -- runs
    exactly as it does in production, which is the whole point of this module.
    """
    engine = create_async_engine(probe_db_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        "src.modules.identity.container._get_async_session_maker",
        lambda: maker,
    )

    user = User(id=uuid4(), email="hr@example.com", name="HR", role=UserRole.HR)
    async with maker() as session:
        await session.execute(text("DELETE FROM audit_logs"))
        await session.execute(text("DELETE FROM oauth_configs"))
        await session.execute(text("DELETE FROM organization_google_connections"))
        await session.execute(text("DELETE FROM users"))
        session.add(user)
        await session.commit()

    try:
        yield user
    finally:
        await engine.dispose()


@pytest.fixture
def app(hr_user: User, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """The real auth router with only the HR guard and Google check swapped.

    ``validate_credentials`` is the single outbound call in this path; stubbing
    it keeps the test off the network without touching the session wiring. The
    crypto key is supplied in both namespaces that resolve it -- the container's
    own, used by ``get_oauth_config_manager``, and the router's module global,
    which ``_get_connection_service`` calls directly -- because the deployment
    ``.env`` this repo ships carries a key of the wrong length. Irrelevant to a
    transaction boundary, but it would stop the request before it reached one.
    """

    async def _always_valid(_self: OAuthConfigManager, _client_id: str) -> bool:
        return True

    monkeypatch.setattr(OAuthConfigManager, "validate_credentials", _always_valid)
    for target in (
        "src.modules.identity.container.get_crypto_utils",
        "src.modules.identity.api.router.get_crypto_utils",
    ):
        monkeypatch.setattr(target, lambda: CryptoUtils(_TEST_KEY_B64))

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[require_hr] = lambda: hr_user
    return application


async def _active_client_id(engine: AsyncEngine) -> str | None:
    """Read the active OAuth client_id on a connection of its own."""
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT client_id FROM oauth_configs WHERE is_active IS TRUE")
        )
        row = result.first()
    return None if row is None else row[0]


async def _audit_row_count(engine: AsyncEngine) -> int:
    """Count the Google-disconnect audit entries on a connection of its own."""
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT count(*) FROM audit_logs WHERE action_type = 'org_google_disconnect'")
        )
    return result.scalar_one()


async def _request_probing(
    app: FastAPI,
    probe: Callable[[], Awaitable[Any]],
    send_request: Callable[[AsyncClient], Awaitable[Any]],
) -> tuple[Any, list[Any]]:
    """Issue a request, running ``probe`` as the response starts going out.

    ``probe`` fires from the ASGI ``send`` callable on ``http.response.start``:
    the handler has returned and the status line is on its way, but FastAPI has
    not yet drained the dependency stack. That is the window the ticket is
    about -- a teardown-only commit is still invisible here.

    Returns the response and every value the probe produced.
    """
    probed: list[Any] = []

    async def probing_app(scope: _Scope, receive: Any, send: _Send) -> None:
        async def probe_send(message: _Message) -> None:
            if message["type"] == "http.response.start":
                probed.append(await probe())
            await send(message)

        await app(scope, receive, probe_send)

    transport = ASGITransport(app=probing_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await send_request(client)
    return response, probed


async def test_oauth_config_is_committed_before_the_response_is_sent(
    app: FastAPI, probe_engine: AsyncEngine
) -> None:
    """A concurrent reader sees the new credentials as the 200 goes out.

    This is the exact endpoint #320 was filed about: the second write path into
    ``oauth_configs``, which #312 left on the teardown commit.
    """
    response, seen_at_send = await _request_probing(
        app,
        lambda: _active_client_id(probe_engine),
        lambda client: client.post(
            "/api/auth/organization-google-connection",
            json={
                "client_id": _CLIENT_ID,
                "client_secret": "super-secret-6789",
                "redirect_uri": _REDIRECT_URI,
            },
        ),
    )

    assert response.status_code == 200, response.text
    # Without the handler's own commit this is [None]: the row exists only
    # inside the request's still-open transaction while 200 is on the wire.
    assert seen_at_send == [_CLIENT_ID]


async def test_service_owned_audit_row_is_durable_before_the_response_is_sent(
    app: FastAPI, probe_engine: AsyncEngine
) -> None:
    """The audit row ``disconnect`` writes is durable by the time 200 goes out.

    ``OrganizationGoogleConnectionService.disconnect`` logs the audit entry
    itself, after the repository write. The handler commits after that whole
    call, so the trail cannot lag behind the action it describes -- the property
    #312 asserted for handler-level audits, restated where the audit lives one
    layer down.
    """
    response, audit_rows_at_send = await _request_probing(
        app,
        lambda: _audit_row_count(probe_engine),
        lambda client: client.delete("/api/auth/organization-google-connection"),
    )

    assert response.status_code == 200, response.text
    assert audit_rows_at_send == [1]
