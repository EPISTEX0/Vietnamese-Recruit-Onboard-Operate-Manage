"""forgot_password's bare ``rollback()`` does not turn into a 500 on a real dead connection (#350).

``router.py`` wraps ``session.commit()`` in a bare try/except and, on failure,
calls ``session.rollback()`` *unwrapped* inside that except (ADR 0010 requires
the generic 200 even then, so callers can never distinguish "no such account"
from "the backend broke"). The existing unit test
(``test_router_commit.py::test_forgot_password_answers_generic_200_when_commit_fails``)
uses an ``AsyncMock`` session whose ``rollback`` defaults to succeeding, so it
cannot see whether a *real* dead connection makes ``rollback()`` itself raise
-- which is exactly why this narrower failure survived #334.

Measured here against a live asyncpg connection whose backend has actually
been killed mid-transaction (``pg_terminate_backend``, the same shape a
dropped network link or a Postgres restart produces): SQLAlchemy's
``AsyncSession.rollback()`` invalidates the broken connection instead of
re-raising the DBAPI error a literal ROLLBACK over a closed socket would
produce. The same measurement holds whether only the one backend is killed or
the whole database becomes unreachable (see the handback for the standalone
script run). So the router's unwrapped ``rollback()`` is safe as written --
no code change was made for #350. This test is the tripwire: if a future
SQLAlchemy/asyncpg upgrade changes that swallowing behaviour, it goes red and
the router needs its own try/except around ``rollback()``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.identity.api.router import _FORGOT_PASSWORD_GENERIC_MESSAGE, forgot_password
from src.modules.identity.api.schemas import ForgotPasswordRequest

pytestmark = pytest.mark.integration


def _request() -> MagicMock:
    """A ``Request`` carrying the client IP the handler reads."""
    request = MagicMock()
    request.client.host = "198.51.100.7"
    return request


def _rate_limiter() -> AsyncMock:
    limiter = AsyncMock()
    limiter.check_rate_limit_for = AsyncMock(return_value=True)
    return limiter


def _settings() -> MagicMock:
    return MagicMock(
        rate_limit_forgot_password_ip_max=3,
        rate_limit_forgot_password_ip_window_seconds=900,
        rate_limit_forgot_password_email_max=2,
        rate_limit_forgot_password_email_window_seconds=900,
    )


async def test_forgot_password_answers_generic_200_when_connection_actually_dies(
    postgres_async_url: str,
) -> None:
    """The handler still answers generic-200 when its session's connection is truly dead.

    Proof by mutation: wrap the router's ``session.rollback()`` in a bare
    ``raise`` (or otherwise make it re-raise) and this goes red -- the
    exception escapes ``forgot_password`` as a 500 instead of the generic
    response.
    """
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = maker()
    try:
        # Give the session an open transaction with real work in it, mirroring
        # what create_reset_token leaves behind before the router's own commit.
        await session.execute(text("SELECT 1"))
        pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()

        killer = create_async_engine(postgres_async_url, poolclass=NullPool)
        try:
            async with killer.connect() as connection:
                await connection.execute(text(f"SELECT pg_terminate_backend({pid})"))
        finally:
            await killer.dispose()

        reset_service = AsyncMock()
        reset_service.create_reset_token = AsyncMock(return_value=True)

        response = await forgot_password(
            _request(),
            ForgotPasswordRequest(email="hr@example.com"),
            reset_service,
            _rate_limiter(),
            _settings(),
            session=session,
        )

        assert response.message == _FORGOT_PASSWORD_GENERIC_MESSAGE
    finally:
        await engine.dispose()
