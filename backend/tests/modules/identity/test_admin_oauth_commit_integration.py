"""The write is durable before the client sees 200 (#312), on a real database.

This is the one case that runs the whole seam end to end: real PostgreSQL, the
real ``get_db_session`` generator with **no** ``dependency_overrides`` entry,
and the real router. The unit-level tests in
``test_admin_router_commit.py`` prove each handler calls ``commit()``; this one
proves that call is what makes the row visible to everyone else *before* the
response leaves, which is the property the ticket is actually about.

``POST /api/system-admin/oauth/config`` is the endpoint under test because it
is the one where the stale read was observed on this deployment (#307: a POST
answered ``updated_at=10:43:46`` while the very next GET still read
``10:41:36``), and because ``OAuthConfigRepository.upsert`` only ``flush()``es
-- nothing below the handler commits, so the assertion has exactly one thing
holding it up. Endpoints backed by ``WhitelistRepository`` or
``OrganizationSettingsRepository`` would have passed either way: those
repositories already commit for themselves.

The check has to happen *while the response is being sent*, not after
``client.post`` returns: by then FastAPI has drained its dependency stack and
the teardown commit has run, so a post-hoc query cannot tell the fix from the
bug. So the probe hangs off the ASGI ``send`` callable and reads through a
second, independent connection -- an uncommitted row is invisible to it.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import Any
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

from src.modules.identity.api.admin_router import admin_router, require_system_admin
from src.modules.identity.application.oauth_config_manager import OAuthConfigManager
from src.modules.identity.domain.entities import User, UserRole
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from tests.conftest import _create_probe_database

pytestmark = pytest.mark.integration

_CLIENT_ID = "312-explicit-commit.apps.googleusercontent.com"
_REDIRECT_URI = "https://app.example.com/api/auth/google/callback"
_TEST_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

_Scope = MutableMapping[str, Any]
_Message = MutableMapping[str, Any]
_Send = Callable[[_Message], Awaitable[None]]


@pytest.fixture(scope="module")
def probe_db_url(postgres_async_url: str) -> str:
    """A database this module alone writes to.

    The shared session database is written by other integration modules, and
    the assertion here is "this exact row became visible", so it needs a
    ``oauth_configs`` table nobody else touches.
    """
    return _create_probe_database(postgres_async_url, "admin_commit_probe")


@pytest_asyncio.fixture
async def probe_engine(probe_db_url: str) -> AsyncIterator[AsyncEngine]:
    """A second connection pool, so reads see only committed data."""
    engine = create_async_engine(probe_db_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def admin_user(probe_db_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[User]:
    """Point the real ``get_db_session`` at the probe database and seed an admin.

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

    user = User(id=uuid4(), email="admin@example.com", name="Admin", role=UserRole.SYSTEM_ADMIN)
    async with maker() as session:
        await session.execute(text("DELETE FROM audit_logs"))
        await session.execute(text("DELETE FROM oauth_configs"))
        await session.execute(text("DELETE FROM users"))
        session.add(user)
        await session.commit()

    try:
        yield user
    finally:
        await engine.dispose()


@pytest.fixture
def app(admin_user: User, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """The real admin router with only the auth guard and Google check swapped.

    ``validate_credentials`` is the single outbound call in this path; stubbing
    it keeps the test off the network without touching the session wiring. The
    crypto key is supplied here too, because the deployment ``.env`` this repo
    ships carries a key of the wrong length -- irrelevant to a transaction
    boundary, but it would stop the request before it reached one.
    """

    async def _always_valid(_self: OAuthConfigManager, _client_id: str) -> bool:
        return True

    monkeypatch.setattr(OAuthConfigManager, "validate_credentials", _always_valid)
    monkeypatch.setattr(
        "src.modules.identity.container.get_crypto_utils",
        lambda: CryptoUtils(_TEST_KEY_B64),
    )

    application = FastAPI()
    application.include_router(admin_router)
    application.dependency_overrides[require_system_admin] = lambda: admin_user
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
    """Count the OAuth audit entries on a connection of its own."""
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT count(*) FROM audit_logs WHERE action_type = 'oauth_update'")
        )
    return result.scalar_one()


async def _post_config_probing(
    app: FastAPI, probe: Callable[[], Awaitable[Any]]
) -> tuple[Any, list[Any]]:
    """POST the OAuth config, running ``probe`` as the response starts going out.

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
        response = await client.post(
            "/api/system-admin/oauth/config",
            json={
                "client_id": _CLIENT_ID,
                "client_secret": "super-secret-6789",
                "redirect_uri": _REDIRECT_URI,
            },
        )
    return response, probed


async def test_row_is_committed_before_the_response_is_sent(
    app: FastAPI, probe_engine: AsyncEngine
) -> None:
    """A concurrent reader sees the new credentials as the 200 goes out."""
    response, seen_at_send = await _post_config_probing(
        app, lambda: _active_client_id(probe_engine)
    )

    assert response.status_code == 200, response.text
    assert response.json()["client_id"] == _CLIENT_ID
    # Without the handler's own commit this is [None]: the row exists only
    # inside the request's still-open transaction while 200 is on the wire.
    assert seen_at_send == [_CLIENT_ID]


async def test_audit_row_shares_the_transaction(app: FastAPI, probe_engine: AsyncEngine) -> None:
    """The audit entry is durable by the same moment the credentials are.

    The commit sits after ``log_action`` so the trail cannot lag behind the
    action it describes.
    """
    response, audit_rows_at_send = await _post_config_probing(
        app, lambda: _audit_row_count(probe_engine)
    )

    assert response.status_code == 200, response.text
    assert audit_rows_at_send == [1]
