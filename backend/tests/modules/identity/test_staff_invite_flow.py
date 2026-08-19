"""Integration tests wiring AuthService.create_staff_account to the real
PasswordResetService redemption flow (#421).

Unlike the mocked unit tests in test_auth_service.py and
test_password_reset_service.py, these tests share one in-memory fake
PasswordResetTokenRepository between a real AuthService and a real
PasswordResetService -- the same pairing FastAPI's DI wires in production
via container.get_auth_service / get_password_reset_service. That is the
seam this ticket adds: a token *issued* by create_staff_account must be
*redeemable* by the pre-existing forgot-password flow, exactly once, only
before it expires. None of that is exercised by either service's own
mocked unit tests, which never call the other service.
"""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.modules.identity.application.auth_service import AuthService
from src.modules.identity.application.password_reset_service import PasswordResetService
from src.modules.identity.domain.entities import PasswordResetToken, User, UserRole
from src.modules.identity.domain.exceptions import InvalidResetTokenError
from src.modules.identity.infrastructure.password_utils import verify_password


class _FakeUserRepository:
    """Minimal in-memory stand-in for UserRepository."""

    def __init__(self) -> None:
        self._by_email: dict[str, User] = {}
        self._by_id: dict[UUID, User] = {}

    async def get_by_email(self, email: str) -> User | None:
        return self._by_email.get(email)

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self._by_id.get(user_id)

    async def create_local_account(
        self,
        *,
        email: str,
        name: str,
        password_hash: str,
        role: UserRole,
        must_change_password: bool,
        employee_id: UUID | None = None,
    ) -> User:
        user = User(
            id=uuid4(),
            email=email,
            name=name,
            password_hash=password_hash,
            role=role,
            must_change_password=must_change_password,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        self._by_email[email] = user
        self._by_id[user.id] = user
        return user

    async def update_password(
        self, user_id: UUID, password_hash: str, *, must_change_password: bool
    ) -> User:
        user = self._by_id[user_id]
        user.password_hash = password_hash
        user.must_change_password = must_change_password
        return user


class _FakeTokenRepository:
    """Minimal in-memory stand-in for PasswordResetTokenRepository."""

    def __init__(self) -> None:
        self.created_calls: list[PasswordResetToken] = []
        self._by_hash: dict[str, PasswordResetToken] = {}
        self._by_id: dict[UUID, PasswordResetToken] = {}

    async def create(
        self,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
        created_by_ip: str | None = None,
    ) -> PasswordResetToken:
        token = PasswordResetToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by_ip=created_by_ip,
            created_at=datetime.now(UTC),
        )
        self.created_calls.append(token)
        self._by_hash[token_hash] = token
        self._by_id[token.id] = token
        return token

    async def find_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        return self._by_hash.get(token_hash)

    async def mark_used(self, token_id: UUID) -> None:
        token = self._by_id.get(token_id)
        if token is not None:
            token.used_at = datetime.now(UTC)

    async def invalidate_all_active_for_user(self, user_id: UUID) -> None:
        for token in self._by_id.values():
            if token.user_id == user_id and token.used_at is None:
                token.used_at = datetime.now(UTC)


def _extract_token(invite_link: str) -> str:
    match = re.search(r"token=([A-Za-z0-9_\-]+)", invite_link)
    assert match is not None, f"invite link missing raw token: {invite_link}"
    return match.group(1)


@pytest.fixture
def wiring() -> tuple[AuthService, PasswordResetService, _FakeTokenRepository]:
    settings = MagicMock(frontend_url="http://localhost:3000")
    user_repo = _FakeUserRepository()
    token_repo = _FakeTokenRepository()
    refresh_repo = MagicMock()
    refresh_repo.revoke_all_for_user = AsyncMock()

    auth_service = AuthService(
        settings=settings,
        token_service=MagicMock(),
        user_repository=user_repo,
        refresh_token_repository=MagicMock(),
        password_reset_token_repository=token_repo,
    )
    reset_service = PasswordResetService(
        settings=settings,
        user_repository=user_repo,
        password_reset_token_repository=token_repo,
        refresh_token_repository=refresh_repo,
        send_service=MagicMock(),
    )
    return auth_service, reset_service, token_repo


@pytest.mark.asyncio
async def test_invite_link_redeems_once_then_is_rejected(wiring) -> None:
    auth_service, reset_service, _token_repo = wiring

    user, invite_link = await auth_service.create_staff_account(
        email="hr@example.com", name="HR One", role=UserRole.HR
    )
    token = _extract_token(invite_link)

    await reset_service.reset_password(token, "N3w-Secure-Pass!")
    assert verify_password("N3w-Secure-Pass!", user.password_hash)
    assert user.must_change_password is False

    with pytest.raises(InvalidResetTokenError):
        await reset_service.reset_password(token, "An0ther-Pass!2")


@pytest.mark.asyncio
async def test_invite_link_is_valid_immediately_after_issuance(wiring) -> None:
    """A freshly issued 72-hour invite must not already be expired.

    Reversing the sign in the expiry computation (``now - 72h`` instead of
    ``now + 72h``) is a concrete failure mode this test alone catches: the
    token would be born already expired, but every mocked test in
    test_password_reset_service.py constructs its own ``expires_at`` and
    never exercises create_staff_account's arithmetic.
    """
    auth_service, reset_service, _token_repo = wiring

    _user, invite_link = await auth_service.create_staff_account(
        email="hr2@example.com", name="HR Two", role=UserRole.HR
    )
    token = _extract_token(invite_link)

    assert await reset_service.validate_token(token) is True


@pytest.mark.asyncio
async def test_invite_link_stores_the_sha256_hash_of_its_own_token(wiring) -> None:
    """The link's raw token and the persisted hash must be the same pair --
    a copy/hash mismatch would silently make every invite unredeemable
    without raising anywhere.
    """
    auth_service, _reset_service, token_repo = wiring

    _user, invite_link = await auth_service.create_staff_account(
        email="hr3@example.com", name="HR Three", role=UserRole.HR
    )
    token = _extract_token(invite_link)

    stored = token_repo.created_calls[-1]
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()


@pytest.mark.asyncio
async def test_invite_link_ttl_is_72_hours(wiring) -> None:
    auth_service, _reset_service, token_repo = wiring
    before = datetime.now(UTC)

    await auth_service.create_staff_account(
        email="hr4@example.com", name="HR Four", role=UserRole.HR
    )

    ttl = token_repo.created_calls[-1].expires_at - before
    assert timedelta(hours=71) < ttl < timedelta(hours=73)
