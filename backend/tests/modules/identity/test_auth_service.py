"""Tests for local Identity authentication."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from src.modules.identity.application.auth_service import AuthService
from src.modules.identity.domain.exceptions import SetupAlreadyCompletedError


@pytest.fixture
def service() -> AuthService:
    settings = MagicMock(refresh_token_expire_days=7)
    token_service = MagicMock()
    token_service.create_access_token.return_value = "access"
    token_service.create_refresh_token.return_value = ("refresh", "hash")
    token_service.revoke_user_tokens = AsyncMock()
    user = MagicMock(
        id=uuid4(),
        email="hr@example.com",
        password_hash=None,
        is_active=True,
        employee_id=None,
        must_change_password=False,
    )
    repository = MagicMock()
    repository.get_by_email = AsyncMock(return_value=user)
    repository.get_by_employee_id = AsyncMock(return_value=None)
    repository.create_local_account = AsyncMock(return_value=user)
    repository.update_password = AsyncMock(return_value=user)
    repository.record_login = AsyncMock(return_value=user)
    refresh_repository = MagicMock()
    refresh_repository.store = AsyncMock()
    refresh_repository.find_by_token_hash = AsyncMock(return_value=None)
    refresh_repository.revoke = AsyncMock()
    service = AuthService(
        settings=settings,
        token_service=token_service,
        user_repository=repository,
        refresh_token_repository=refresh_repository,
    )
    service._test_user = user
    return service


@pytest.mark.asyncio
async def test_login_uses_local_password(service: AuthService) -> None:
    service._test_user.password_hash = "not-a-real-hash"
    from src.modules.identity.application import auth_service as module

    module.verify_password = lambda password, password_hash: password == "secret"
    result = await service.login("hr@example.com", "secret")

    assert result.access_token == "access"
    service._token_service.revoke_user_tokens.assert_awaited_once_with(service._test_user.id)


@pytest.mark.asyncio
async def test_login_stamps_last_login_through_repository(service: AuthService) -> None:
    """login() must persist last_login via UserRepository.record_login, not
    by reaching into the repository's session directly. The session the
    stamped user carries is what ends up in the returned LocalAuthResult --
    if login() went back to poking ``repository.session`` this would either
    leave record_login uncalled or return the stale pre-login user.
    """
    service._test_user.password_hash = "not-a-real-hash"
    from src.modules.identity.application import auth_service as module

    module.verify_password = lambda password, password_hash: password == "secret"

    stamped_user = MagicMock(
        id=service._test_user.id,
        email=service._test_user.email,
        employee_id=None,
        must_change_password=False,
        last_login=datetime.now(UTC),
    )
    service._user_repository.record_login = AsyncMock(return_value=stamped_user)

    result = await service.login("hr@example.com", "secret")

    service._user_repository.record_login.assert_awaited_once_with(service._test_user.id)
    assert result.user is stamped_user


@pytest.mark.asyncio
async def test_logout_revokes_local_refresh_token(service: AuthService) -> None:
    record = MagicMock()
    service._refresh_token_repository.find_by_token_hash = AsyncMock(return_value=record)

    await service.logout("refresh")

    service._refresh_token_repository.revoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_race_rolls_back_and_returns_stable_error() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    organization = MagicMock()
    organization.create_for_setup = AsyncMock(
        side_effect=IntegrityError("insert", {}, Exception("singleton conflict"))
    )
    users = MagicMock()
    users.count_system_admins = AsyncMock(return_value=0)
    users.count_users = AsyncMock(return_value=0)
    service = AuthService(
        settings=MagicMock(refresh_token_expire_days=7),
        token_service=MagicMock(),
        user_repository=users,
        refresh_token_repository=MagicMock(),
        organization_repository=organization,
        session=session,
    )

    with pytest.raises(SetupAlreadyCompletedError) as error:
        await service.setup_first_run("Acme", "HR", "hr@example.com", "a" * 12)

    assert error.value.error_code == "AUTH_SETUP_ALREADY_COMPLETED"
    session.rollback.assert_awaited_once_with()
    users.create_local_account.assert_not_called()
