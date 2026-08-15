"""``get_password_reset_service`` builds a *working* SendService, not a sentinel one (#327).

Every existing test of this path hands the router an ``AsyncMock`` in place of
``SendService`` -- ``test_password_reset_routes.py`` overrides
``get_password_reset_service`` wholesale, and ``test_password_reset_service.py``
constructs ``PasswordResetService`` directly. Both prove the service is
*called*. Neither can prove it was *built*, and #327 is a construction bug: the
object arrives, has the right type, and is inert.

The mechanism: ``get_send_service`` is a FastAPI provider whose parameters
default to ``Depends(...)`` sentinels. Called bare -- outside a request, with no
resolver -- Python simply binds those defaults, so ``SendService`` ends up
holding three ``fastapi.params.Depends`` instances where its repositories
belong. The first attribute access on one raises ``AttributeError``, which
``create_reset_token`` swallows in a bare ``except Exception`` and reports as
``False``; the handler then answers 200 with the anti-enumeration generic
message (ADR 0010). That message is deliberately identical for "sent" and "no
such account", so a total outage of password reset is indistinguishable from
normal operation at every layer the client can see.

Mocking ``SendService`` here would reintroduce exactly the blindness the ticket
is about, so this module mocks nothing on the seam. It drives FastAPI's real
dependency resolution against the real providers and inspects what
``PasswordResetService`` was actually handed:

* ``test_send_service_collaborators_are_real_repositories`` is the direct
  statement of the bug -- no collaborator may be a ``Depends`` sentinel.
* ``test_send_service_shares_the_request_session`` is the stronger property the
  first one only implies. Being the right *type* is not enough; a ``SendService``
  bound to some other session would write the sent-message row and the audit
  entry outside the request's transaction. Identity of the session object is
  what ties the send path to the same unit of work as the token insert.
* ``test_connection_repository_can_actually_query`` closes the gap between
  "correctly shaped" and "functional" by issuing the exact call that fails in
  production, ``_get_access_token``'s ``connection_repo.get_singleton()``,
  against real PostgreSQL.

No database is needed for the first two: the bug is entirely in how the object
graph is assembled, so they run in the default suite rather than behind the
``integration`` marker and a Docker probe that CI may skip.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.params import Depends as DependsSentinel
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.gmail.application.send_service import SendService
from src.modules.gmail.infrastructure.audit_logger import AuditLogger
from src.modules.gmail.infrastructure.email_repository import EmailRepository
from src.modules.identity.application.password_reset_service import PasswordResetService
from src.modules.identity.container import get_db_session, get_password_reset_service
from src.modules.identity.infrastructure.connection_state_repository import (
    OrganizationGoogleConnectionRepository,
)
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils

# Never dialed. Both non-integration tests assert on object wiring only, and
# SQLAlchemy does not open a connection until a statement runs, so a session
# built from this engine is a genuine ``AsyncSession`` without a live server.
_UNDIALED_URL = "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"

_TEST_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

# The collaborators #327 corrupts, paired with the type each one must have.
_SEND_SERVICE_COLLABORATORS = (
    ("_email_repo", EmailRepository),
    ("_connection_repo", OrganizationGoogleConnectionRepository),
    ("_audit_logger", AuditLogger),
)


@pytest.fixture(autouse=True)
def usable_crypto_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply a 32-byte key so building the graph gets as far as the assertions.

    ``tests/env_isolation.py`` seeds ``AUTH_OAUTH_TOKEN_ENCRYPTION_KEY`` with a
    value that decodes to 28 bytes, so the real ``get_crypto_utils`` raises
    ``ValueError`` before ``SendService`` is ever constructed --
    ``test_auth_router_commit_integration.py`` patches around the same wart.
    Crypto is not the seam under test here; it is only in the path because
    ``SendService`` takes it as a constructor argument.
    """
    monkeypatch.setattr(
        "src.modules.gmail.container.get_crypto_utils", lambda: CryptoUtils(_TEST_KEY_B64)
    )


@asynccontextmanager
async def _session_for(url: str) -> AsyncGenerator[AsyncSession]:
    """Yield one ``AsyncSession`` on a private engine, disposed on the way out."""
    engine = create_async_engine(url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """One real ``AsyncSession`` instance, standing in for the request's session."""
    async with _session_for(_UNDIALED_URL) as db_session:
        yield db_session


async def _resolve_password_reset_service(session: AsyncSession) -> PasswordResetService:
    """Return the ``PasswordResetService`` FastAPI builds for a real request.

    The provider under test is *not* overridden -- that override is precisely
    what let #327 hide. Only ``get_db_session`` is replaced, so the session is
    one this test can compare against, and every provider above it runs exactly
    as it does in production.
    """
    app = FastAPI()
    captured: list[PasswordResetService] = []

    @app.get("/probe")
    async def probe(
        service: Annotated[PasswordResetService, Depends(get_password_reset_service)],
    ) -> dict[str, bool]:
        captured.append(service)
        return {"ok": True}

    app.dependency_overrides[get_db_session] = lambda: session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")

    assert response.status_code == 200, (
        f"the probe request failed before it could capture the service: {response.text}"
    )
    return captured[0]


async def test_send_service_collaborators_are_real_repositories(session: AsyncSession) -> None:
    """None of ``SendService``'s three collaborators is an unresolved ``Depends``.

    This is #327 stated directly, against the code as it stood when the ticket
    was filed: the provider took three ``Depends`` parameters, so a bare call
    put a sentinel in each repository slot.

    On its own this assertion is *not* enough to keep the bug out. Now that
    ``get_send_service`` takes a single ``session`` parameter, a bare call would
    build ``EmailRepository(Depends(...))`` -- right type, unusable session --
    and this test would pass. ``test_send_service_shares_the_request_session``
    is what closes that gap; both are needed.
    """
    service = await _resolve_password_reset_service(session)
    send_service = service._send_service

    assert isinstance(send_service, SendService)

    for attribute, expected_type in _SEND_SERVICE_COLLABORATORS:
        collaborator = getattr(send_service, attribute)
        assert not isinstance(collaborator, DependsSentinel), (
            f"SendService.{attribute} is an unresolved fastapi Depends sentinel; "
            "the password-reset path builds a SendService that cannot reach the "
            "database, and every failure it causes is swallowed (#327)"
        )
        assert isinstance(collaborator, expected_type)


async def test_send_service_shares_the_request_session(session: AsyncSession) -> None:
    """The send path is bound to the same session as the rest of the request.

    Right type, wrong session would still be a bug: the sent-message row and the
    audit entry would land in a transaction the handler never commits, so they
    could survive a rolled-back reset or vanish after a committed one.

    This is also the assertion that survives the provider's shape changing.
    Reverting only the call site in ``get_password_reset_service`` back to
    ``await get_send_service()`` leaves the repositories correctly *typed* but
    holding a ``Depends`` sentinel where the session belongs, which only this
    test detects (verified by mutation).
    """
    service = await _resolve_password_reset_service(session)
    send_service = service._send_service

    for attribute, _ in _SEND_SERVICE_COLLABORATORS:
        assert getattr(send_service, attribute).session is session, (
            f"SendService.{attribute} is bound to a different session than the "
            "request's, so the email and audit writes fall outside the "
            "handler's unit of work"
        )


@pytest.mark.integration
async def test_connection_repository_can_actually_query(postgres_async_url: str) -> None:
    """The wired connection repository answers the query that fails in production.

    ``SendService._get_access_token`` opens with
    ``connection_repo.get_singleton()``. Under #327 that call raises
    ``AttributeError`` on a ``Depends`` object before any Gmail credential is
    consulted, which is why the outage is total rather than
    configuration-dependent. Asserting the same call returns cleanly against
    real PostgreSQL separates "the graph is correctly shaped" from "the graph
    works".
    """
    async with _session_for(postgres_async_url) as db_session:
        service = await _resolve_password_reset_service(db_session)
        connection_repo = service._send_service._connection_repo
        # No connection row is seeded, so ``None`` is the correct answer;
        # the point is that the call completes instead of raising.
        assert await connection_repo.get_singleton() is None
