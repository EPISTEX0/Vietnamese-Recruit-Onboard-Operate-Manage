"""The reset-token row survives a failed sent-message insert, on real PostgreSQL (#334).

``create_reset_token`` inserts the reset token, then sends the email. Sending
calls ``EmailRepository.batch_upsert`` with the just-sent message as a
side-effect (``SendService._store_sent_metadata``). On PostgreSQL a failed
statement aborts the whole transaction; without a SAVEPOINT around each
message, ``batch_upsert``'s own ``except Exception: continue`` does not
recover from that -- it only postpones the failure to the unconditional
``flush()`` right after the loop, which re-raises as an unrelated
``PendingRollbackError``. ``AuditLogger.log_send`` swallows that too, so the
send path itself never raises -- the failure only resurfaces when
``create_reset_token`` tries to commit, and by then the token row inserted
earlier in the same transaction is lost along with it.

This module forces that exact statement failure for real -- an oversized
``gmail_message_id`` that PostgreSQL's ``VARCHAR(255)`` column rejects -- and
proves the SAVEPOINT (src/modules/gmail/infrastructure/email_repository.py)
keeps the reset-token row committed and redeemable. Nothing here is mocked
below the database: real ``EmailRepository``, real ``AuditLogger``, real
``PasswordResetTokenRepository``, one real Postgres session. Only the Gmail
network call and the Organization Google Connection are stubbed, since
neither one is the seam under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.modules.gmail.application.send_service import SendService
from src.modules.gmail.infrastructure.audit_logger import AuditLogger
from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.gmail.infrastructure.email_repository import EmailRepository
from src.modules.gmail.infrastructure.gmail_adapter import SentMessageInfo
from src.modules.identity.application import password_reset_service as password_reset_service_module
from src.modules.identity.application.password_reset_service import PasswordResetService
from src.modules.identity.domain.entities import User
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.password_reset_token_repository import (
    PasswordResetTokenRepository,
)
from src.modules.identity.infrastructure.refresh_token_repository import RefreshTokenRepository
from src.modules.identity.infrastructure.user_repository import UserRepository
from tests.conftest import _create_probe_database

pytestmark = pytest.mark.integration

#: email_messages.gmail_message_id is VARCHAR(255) (alembic 008); this makes
#: the per-message INSERT inside batch_upsert fail for real.
_OVERSIZED_GMAIL_MESSAGE_ID = "m" * 300
_FIXED_RAW_TOKEN = "fixed-raw-token-for-savepoint-test"  # noqa: S105


class _StubGmailAdapter:
    """Gmail 'accepts' the send; its message id just won't fit the column.

    Mirrors what a malformed upstream message id would do in production --
    the mail genuinely goes out before the local bookkeeping insert fails.
    """

    async def send_message(self, access_token: str, mime_bytes: bytes) -> SentMessageInfo:
        return SentMessageInfo(message_id=_OVERSIZED_GMAIL_MESSAGE_ID, thread_id="thread-1")


class _StubConnectionRepo:
    """A permanently 'connected' Organization Google Connection, no DB needed.

    Not the seam under test -- ``SendService._get_access_token`` only needs
    a connected status and a token to decrypt.
    """

    async def get_singleton(self) -> SimpleNamespace:
        return SimpleNamespace(status="connected", access_token_enc="token", token_expires_at=None)


class _StubCrypto:
    """Decrypt/encrypt as no-ops; the access token content is never real."""

    def decrypt(self, value: str) -> str:
        return value

    def encrypt(self, value: str) -> str:
        return value


@pytest.fixture(scope="module")
def probe_db_url(postgres_async_url: str) -> str:
    """A database this module alone writes to, so row counts are unambiguous."""
    return _create_probe_database(postgres_async_url, "forgot_password_savepoint_probe")


@pytest_asyncio.fixture
async def probe_engine(probe_db_url: str) -> AsyncIterator[AsyncEngine]:
    """A second connection pool, so reads see only committed data."""
    engine = create_async_engine(probe_db_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(probe_db_url: str) -> AsyncIterator[AsyncSession]:
    """One real Postgres session, standing in for the request's session."""
    engine = create_async_engine(probe_db_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> User:
    await session.execute(text("DELETE FROM password_reset_tokens"))
    await session.execute(text("DELETE FROM gmail_audit_logs"))
    await session.execute(text("DELETE FROM email_messages"))
    await session.execute(text("DELETE FROM users"))
    record = User(
        id=uuid4(),
        email="reset-target@example.com",
        name="Reset Target",
        password_hash="irrelevant-hash",
    )
    session.add(record)
    await session.commit()
    return record


def _password_reset_service(session: AsyncSession) -> PasswordResetService:
    send_service = SendService(
        gmail_adapter=_StubGmailAdapter(),
        email_repo=EmailRepository(session),
        connection_repo=_StubConnectionRepo(),
        crypto=_StubCrypto(),
        audit_logger=AuditLogger(session, GmailSettings()),
        settings=GmailSettings(),
        client_id="test-client-id",
        client_secret="test-client-secret",
    )
    return PasswordResetService(
        settings=AuthSettings(
            google_client_id="test-client-id",
            google_client_secret="test-client-secret",
            google_redirect_uri="http://localhost:8000/api/auth/callback",
            jwt_secret_key="test-jwt-secret-key-at-least-32-chars-long",
            oauth_token_encryption_key="dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcyE=",
        ),
        user_repository=UserRepository(session),
        password_reset_token_repository=PasswordResetTokenRepository(session),
        refresh_token_repository=RefreshTokenRepository(session),
        send_service=send_service,
        session=session,
    )


async def _token_row_count(engine: AsyncEngine, user_id: object) -> int:
    """Count usable reset-token rows for ``user_id`` on a connection of its own."""
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT count(*) FROM password_reset_tokens "
                "WHERE user_id = :user_id AND used_at IS NULL"
            ),
            {"user_id": user_id},
        )
    return result.scalar_one()


async def test_reset_token_survives_a_failed_sent_message_insert(
    session: AsyncSession, user: User, probe_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token inserted before the failing send is still committed and redeemable.

    Proof by mutation: remove ``begin_nested()`` from ``batch_upsert`` and this
    goes red -- either ``create_reset_token`` raises the ``PendingRollbackError``
    that used to reach the (pre-#334) router uncaught, or, if it doesn't, the
    token row it appeared to create was never durably committed.
    """
    monkeypatch.setattr(
        password_reset_service_module.secrets, "token_urlsafe", lambda _n: _FIXED_RAW_TOKEN
    )
    service = _password_reset_service(session)

    sent = await service.create_reset_token(user.email, "127.0.0.1")

    assert sent is True
    assert await _token_row_count(probe_engine, user.id) == 1
    assert await service.validate_token(_FIXED_RAW_TOKEN) is True
