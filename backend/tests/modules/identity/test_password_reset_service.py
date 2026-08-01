"""Tests for the password reset service.

Uses mocked repositories and a mocked Gmail SendService, following the
conventions of test_auth_service.py.
"""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.identity.application.password_reset_service import (
    PasswordResetService,
    build_reset_email,
)
from src.modules.identity.domain.entities import PasswordResetToken
from src.modules.identity.domain.exceptions import InvalidResetTokenError
from src.modules.identity.infrastructure.password_utils import verify_password

_FUTURE = datetime.now(UTC) + timedelta(minutes=15)
_PAST = datetime.now(UTC) - timedelta(minutes=1)


def _make_token_record(
    user_id,
    *,
    used_at=None,
    expires_at=_FUTURE,
    token_id=None,
) -> PasswordResetToken:
    """Create a PasswordResetToken record for service tests."""
    return PasswordResetToken(
        id=token_id or uuid4(),
        user_id=user_id,
        token_hash="irrelevant",
        expires_at=expires_at,
        used_at=used_at,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def service() -> PasswordResetService:
    settings = MagicMock(frontend_url="http://localhost:3000")
    user_repo = MagicMock()
    token_repo = MagicMock()
    refresh_repo = MagicMock()
    send_service = MagicMock()
    send_service.send_email = AsyncMock()
    token_repo.find_by_token_hash = AsyncMock(return_value=None)
    token_repo.invalidate_all_active_for_user = AsyncMock()
    token_repo.create = AsyncMock()
    token_repo.mark_used = AsyncMock()
    refresh_repo.revoke_all_for_user = AsyncMock()
    svc = PasswordResetService(
        settings=settings,
        user_repository=user_repo,
        password_reset_token_repository=token_repo,
        refresh_token_repository=refresh_repo,
        send_service=send_service,
    )
    svc._test_user_repo = user_repo
    svc._test_token_repo = token_repo
    svc._test_send_service = send_service
    svc._test_refresh_repo = refresh_repo
    return svc


def _make_user(*, active=True, password_hash="hashed-password", email="hr@example.com"):
    """Create a user mock for service tests."""
    return MagicMock(
        id=uuid4(),
        email=email,
        password_hash=password_hash,
        is_active=active,
    )


def _raw_token_from_email(send_service) -> str:
    """Extract the raw reset token from the sent email body."""
    params = send_service.send_email.await_args.kwargs["params"]
    body = f"{params.body_html}\n{params.body_text}"
    match = re.search(r"token=([A-Za-z0-9_\-]+)", body)
    assert match is not None, "reset link with raw token not found in email body"
    return match.group(1)


class TestCreateResetToken:
    """Tests for PasswordResetService.create_reset_token."""

    async def test_creates_token_and_sends_email(self, service) -> None:
        user = _make_user()
        service._test_user_repo.get_by_email = AsyncMock(return_value=user)

        result = await service.create_reset_token("hr@example.com", client_ip="203.0.113.7")

        assert result is True
        # Previous active tokens invalidated before the new one is stored.
        service._test_token_repo.invalidate_all_active_for_user.assert_awaited_once_with(
            user.id
        )
        # Raw token appears only in the email link; DB stores its SHA-256 hash.
        raw_token = _raw_token_from_email(service._test_send_service)
        stored_hash = service._test_token_repo.create.await_args.kwargs["token_hash"]
        assert stored_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert stored_hash != raw_token
        create_kwargs = service._test_token_repo.create.await_args.kwargs
        assert create_kwargs["user_id"] == user.id
        assert create_kwargs["created_by_ip"] == "203.0.113.7"
        assert create_kwargs["expires_at"] > datetime.now(UTC)

    async def test_email_mentions_15_minute_warning(self, service) -> None:
        user = _make_user()
        service._test_user_repo.get_by_email = AsyncMock(return_value=user)

        await service.create_reset_token("hr@example.com")

        params = service._test_send_service.send_email.await_args.kwargs["params"]
        assert params.to == ["hr@example.com"]
        assert "15 phút" in params.body_html
        assert "15 phút" in params.body_text
        assert "Đặt lại mật khẩu" in params.subject

    async def test_missing_user_returns_false_without_side_effects(self, service) -> None:
        service._test_user_repo.get_by_email = AsyncMock(return_value=None)

        result = await service.create_reset_token("ghost@example.com")

        assert result is False
        service._test_token_repo.create.assert_not_called()
        service._test_send_service.send_email.assert_not_called()

    async def test_inactive_user_returns_false(self, service) -> None:
        user = _make_user(active=False)
        service._test_user_repo.get_by_email = AsyncMock(return_value=user)

        result = await service.create_reset_token("hr@example.com")

        assert result is False
        service._test_token_repo.create.assert_not_called()
        service._test_send_service.send_email.assert_not_called()

    async def test_user_without_password_returns_false(self, service) -> None:
        user = _make_user(password_hash="")
        service._test_user_repo.get_by_email = AsyncMock(return_value=user)

        result = await service.create_reset_token("hr@example.com")

        assert result is False
        service._test_token_repo.create.assert_not_called()

    async def test_send_failure_is_graceful(self, service, caplog) -> None:
        user = _make_user()
        service._test_user_repo.get_by_email = AsyncMock(return_value=user)
        service._test_send_service.send_email = AsyncMock(side_effect=RuntimeError("gmail down"))

        result = await service.create_reset_token("hr@example.com")

        # Same non-distinguishing return value as a missing account.
        assert result is False
        assert any("Failed to send password reset email" in r.message for r in caplog.records)

    async def test_negative_path_burns_anti_enumeration_delay(
        self, service, monkeypatch
    ) -> None:
        """Missing/inactive/passwordless accounts sleep before answering so
        response timing does not reveal account existence (ADR 0010)."""
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(
            "src.modules.identity.application.password_reset_service.asyncio.sleep",
            _fake_sleep,
        )
        service._test_user_repo.get_by_email = AsyncMock(return_value=None)

        result = await service.create_reset_token("nobody@example.com")

        assert result is False
        assert sleep_calls, "negative path must apply the anti-enumeration delay"
        assert sleep_calls[0] > 0


class TestValidateToken:
    """Tests for PasswordResetService.validate_token."""

    async def test_valid_token_returns_true(self, service) -> None:
        user_id = uuid4()
        record = _make_token_record(user_id)
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)

        assert await service.validate_token("some-raw-token") is True

    async def test_unknown_token_returns_false(self, service) -> None:
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=None)

        assert await service.validate_token("unknown-token") is False

    async def test_used_token_returns_false(self, service) -> None:
        record = _make_token_record(uuid4(), used_at=datetime.now(UTC))
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)

        assert await service.validate_token("used-token") is False

    async def test_expired_token_returns_false(self, service) -> None:
        record = _make_token_record(uuid4(), expires_at=_PAST)
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)

        assert await service.validate_token("expired-token") is False


class TestResetPassword:
    """Tests for PasswordResetService.reset_password."""

    async def test_resets_password_and_revokes_sessions(self, service) -> None:
        user_id = uuid4()
        record = _make_token_record(user_id)
        user = _make_user()
        user.id = user_id
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)
        service._test_user_repo.get_by_id = AsyncMock(return_value=user)
        service._test_user_repo.update_password = AsyncMock(return_value=user)

        await service.reset_password("raw-token", "N3w-Secure-Pass!")

        update_args = service._test_user_repo.update_password.await_args
        assert update_args.args[0] == user_id
        assert update_args.kwargs["must_change_password"] is False
        # The stored value is a real PBKDF2 hash of the new password.
        assert verify_password("N3w-Secure-Pass!", update_args.args[1])
        service._test_token_repo.mark_used.assert_awaited_once_with(record.id)
        service._test_refresh_repo.revoke_all_for_user.assert_awaited_once_with(user_id)

    async def test_invalid_token_raises(self, service) -> None:
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=None)

        with pytest.raises(InvalidResetTokenError) as error:
            await service.reset_password("bad-token", "N3w-Secure-Pass!")

        assert error.value.error_code == "AUTH_INVALID_RESET_TOKEN"
        service._test_user_repo.update_password.assert_not_called()
        service._test_refresh_repo.revoke_all_for_user.assert_not_called()

    async def test_used_token_raises(self, service) -> None:
        record = _make_token_record(uuid4(), used_at=datetime.now(UTC))
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)

        with pytest.raises(InvalidResetTokenError):
            await service.reset_password("used-token", "N3w-Secure-Pass!")

        service._test_user_repo.update_password.assert_not_called()

    async def test_expired_token_raises(self, service) -> None:
        record = _make_token_record(uuid4(), expires_at=_PAST)
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)

        with pytest.raises(InvalidResetTokenError):
            await service.reset_password("expired-token", "N3w-Secure-Pass!")

        service._test_user_repo.update_password.assert_not_called()

    async def test_missing_user_raises(self, service) -> None:
        record = _make_token_record(uuid4())
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)
        service._test_user_repo.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(InvalidResetTokenError):
            await service.reset_password("orphan-token", "N3w-Secure-Pass!")

        service._test_user_repo.update_password.assert_not_called()

    async def test_inactive_user_raises(self, service) -> None:
        """A deactivated account must not be re-enabled through a reset link."""
        record = _make_token_record(uuid4())
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)
        service._test_user_repo.get_by_id = AsyncMock(return_value=_make_user(active=False))

        with pytest.raises(InvalidResetTokenError):
            await service.reset_password("deactivated-token", "N3w-Secure-Pass!")

        service._test_user_repo.update_password.assert_not_called()

    async def test_commits_session_when_provided(self, service) -> None:
        user_id = uuid4()
        record = _make_token_record(user_id)
        user = _make_user()
        user.id = user_id
        service._test_token_repo.find_by_token_hash = AsyncMock(return_value=record)
        service._test_user_repo.get_by_id = AsyncMock(return_value=user)
        service._test_user_repo.update_password = AsyncMock(return_value=user)
        session = MagicMock()
        session.commit = AsyncMock()
        service._session = session

        await service.reset_password("raw-token", "N3w-Secure-Pass!")

        session.commit.assert_awaited_once_with()


def test_build_reset_email_contains_link_and_warning() -> None:
    subject, body_html, body_text = build_reset_email(
        "http://localhost:3000/reset-password?token=rawtoken123"
    )

    assert "Đặt lại mật khẩu" in subject
    assert "http://localhost:3000/reset-password?token=rawtoken123" in body_html
    assert "http://localhost:3000/reset-password?token=rawtoken123" in body_text
    assert "15 phút" in body_html
    assert "15 phút" in body_text
