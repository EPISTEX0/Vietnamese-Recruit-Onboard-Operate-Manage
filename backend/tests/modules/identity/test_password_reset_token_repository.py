"""Unit tests for PasswordResetTokenRepository using mocked AsyncSession.

Mirrors the RefreshTokenRepository test approach: the async session is
mocked so repository logic can be tested without a real database.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from src.modules.identity.domain.entities import PasswordResetToken
from src.modules.identity.infrastructure.password_reset_token_repository import (
    PasswordResetTokenRepository,
)


def _make_mock_session(query_result=None):
    """Create a mock AsyncSession that returns the given query result.

    For single-row results (e.g., find_by_token_hash), set query_result
    to the value that result.first() should return.
    """
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.first.return_value = query_result
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = query_result
    scalars_mock.all.return_value = query_result if isinstance(query_result, list) else []
    result_mock.scalars.return_value = scalars_mock
    session.execute.return_value = result_mock
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


def _make_reset_token(
    user_id: UUID | None = None,
    token_hash: str = "abcd1234hash",
    expires_at: datetime | None = None,
    used_at: datetime | None = None,
    created_by_ip: str | None = "203.0.113.7",
) -> PasswordResetToken:
    """Create a PasswordResetToken entity for testing."""
    return PasswordResetToken(
        id=uuid4(),
        user_id=user_id or uuid4(),
        token_hash=token_hash,
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=15),
        used_at=used_at,
        created_at=datetime.now(UTC),
        created_by_ip=created_by_ip,
    )


class TestCreate:
    """Tests for PasswordResetTokenRepository.create."""

    async def test_creates_token_with_all_fields(self) -> None:
        session = _make_mock_session()
        repo = PasswordResetTokenRepository(session)
        user_id = uuid4()
        expires_at = datetime.now(UTC) + timedelta(minutes=15)

        result = await repo.create(
            user_id=user_id,
            token_hash="sha256hexdigest",
            expires_at=expires_at,
            created_by_ip="203.0.113.7",
        )

        assert result.user_id == user_id
        assert result.token_hash == "sha256hexdigest"
        assert result.expires_at == expires_at
        assert result.created_by_ip == "203.0.113.7"
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_creates_token_without_client_ip(self) -> None:
        session = _make_mock_session()
        repo = PasswordResetTokenRepository(session)

        result = await repo.create(
            user_id=uuid4(),
            token_hash="anotherhash",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

        assert result.created_by_ip is None
        assert isinstance(result.id, UUID)
        assert result.used_at is None

    async def test_created_token_has_uuid_id(self) -> None:
        session = _make_mock_session()
        repo = PasswordResetTokenRepository(session)

        result = await repo.create(
            user_id=uuid4(),
            token_hash="somehash",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

        assert isinstance(result.id, UUID)


class TestFindByTokenHash:
    """Tests for PasswordResetTokenRepository.find_by_token_hash."""

    async def test_returns_none_when_token_not_found(self) -> None:
        session = _make_mock_session(query_result=None)
        repo = PasswordResetTokenRepository(session)

        result = await repo.find_by_token_hash("nonexistenthash")

        assert result is None
        session.execute.assert_called_once()

    async def test_returns_token_when_found(self) -> None:
        token = _make_reset_token(token_hash="foundhash")
        session = _make_mock_session(query_result=token)
        repo = PasswordResetTokenRepository(session)

        result = await repo.find_by_token_hash("foundhash")

        assert result is token
        assert result.token_hash == "foundhash"
        assert result.used_at is None

    async def test_returns_used_token_with_used_at(self) -> None:
        used_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        token = _make_reset_token(token_hash="usedhash", used_at=used_time)
        session = _make_mock_session(query_result=token)
        repo = PasswordResetTokenRepository(session)

        result = await repo.find_by_token_hash("usedhash")

        assert result is token
        assert result.used_at == used_time


class TestInvalidateAllActiveForUser:
    """Tests for PasswordResetTokenRepository.invalidate_all_active_for_user."""

    async def test_invalidates_all_active_tokens_for_user(self) -> None:
        user_id = uuid4()
        token1 = _make_reset_token(user_id=user_id, token_hash="hash1")
        token2 = _make_reset_token(user_id=user_id, token_hash="hash2")

        session = _make_mock_session(query_result=[token1, token2])
        repo = PasswordResetTokenRepository(session)

        await repo.invalidate_all_active_for_user(user_id)

        assert token1.used_at is not None
        assert token2.used_at is not None
        assert session.add.call_count == 2
        session.flush.assert_called_once()

    async def test_sets_used_at_to_current_time(self) -> None:
        user_id = uuid4()
        token = _make_reset_token(user_id=user_id)

        session = _make_mock_session(query_result=[token])
        repo = PasswordResetTokenRepository(session)

        before = datetime.now(UTC)
        await repo.invalidate_all_active_for_user(user_id)
        after = datetime.now(UTC)

        assert token.used_at is not None
        assert before <= token.used_at <= after

    async def test_does_nothing_when_no_active_tokens(self) -> None:
        session = _make_mock_session(query_result=[])
        repo = PasswordResetTokenRepository(session)

        await repo.invalidate_all_active_for_user(uuid4())

        session.add.assert_not_called()
        session.flush.assert_not_called()


class TestMarkUsed:
    """Tests for PasswordResetTokenRepository.mark_used."""

    async def test_marks_token_used(self) -> None:
        token = _make_reset_token(token_hash="markme")
        session = _make_mock_session(query_result=token)
        repo = PasswordResetTokenRepository(session)

        before = datetime.now(UTC)
        await repo.mark_used(token.id)
        after = datetime.now(UTC)

        assert token.used_at is not None
        assert before <= token.used_at <= after
        session.flush.assert_called_once()

    async def test_noop_when_token_not_found(self) -> None:
        session = _make_mock_session(query_result=None)
        repo = PasswordResetTokenRepository(session)

        await repo.mark_used(uuid4())

        session.add.assert_not_called()
        session.flush.assert_not_called()
