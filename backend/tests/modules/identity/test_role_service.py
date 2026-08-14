"""Unit tests for RoleService using mocked AsyncSession.

Covers the three-role transitions (SYSTEM_ADMIN / HR / USER) and the four
lockout guards enforced by ``change_role``: unknown user, self-change,
super-admin protection, and last-system-admin protection.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.identity.application.role_service import (
    LastAdminError,
    RoleService,
    SelfDemotionError,
    SuperAdminProtectedError,
    UserNotFoundError,
)
from src.modules.identity.domain.entities import User, UserRole


def _make_user(
    email: str = "user@example.com",
    role: UserRole = UserRole.USER,
) -> User:
    """Create a User entity for testing."""
    return User(
        id=uuid4(),
        email=email,
        name="Test User",
        avatar_url=None,
        google_sub=f"google-sub-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
        last_login=datetime.now(UTC),
        is_active=True,
        role=role,
    )


def _make_mock_session(user_result=None, admin_count: int = 2):
    """Create a mock AsyncSession.

    Args:
        user_result: The user to return from select queries.
        admin_count: The count to return from system-admin count queries.
    """
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    # We need to handle multiple execute calls differently:
    # - First call is typically the user lookup (returns scalars().first())
    # - Second call may be the admin count (returns scalar_one())
    call_count = 0

    async def mock_execute(statement):
        nonlocal call_count
        call_count += 1

        result_mock = MagicMock()

        # Determine if this is a count query by checking if scalar_one is needed
        # We use a simple heuristic: first call is user lookup, second is count
        if call_count == 1:
            scalars_mock = MagicMock()
            scalars_mock.first.return_value = user_result
            result_mock.scalars.return_value = scalars_mock
        else:
            result_mock.scalar_one.return_value = admin_count
            # Also provide scalars for ensure_super_admin which only does user lookup
            scalars_mock = MagicMock()
            scalars_mock.first.return_value = user_result
            result_mock.scalars.return_value = scalars_mock

        return result_mock

    session.execute = AsyncMock(side_effect=mock_execute)
    return session


def _make_actor(email: str = "sysadmin@example.com") -> User:
    """Create the acting system admin performing a role change."""
    return _make_user(email=email, role=UserRole.SYSTEM_ADMIN)


class TestChangeRole:
    """Tests for RoleService.change_role, the single transition entry point."""

    @pytest.mark.parametrize(
        ("from_role", "to_role"),
        [
            (UserRole.USER, UserRole.HR),
            (UserRole.USER, UserRole.SYSTEM_ADMIN),
            (UserRole.HR, UserRole.SYSTEM_ADMIN),
            (UserRole.HR, UserRole.USER),
            (UserRole.SYSTEM_ADMIN, UserRole.HR),
            (UserRole.SYSTEM_ADMIN, UserRole.USER),
        ],
    )
    async def test_assigns_every_role_transition(
        self, from_role: UserRole, to_role: UserRole
    ) -> None:
        user = _make_user(email="target@example.com", role=from_role)
        session = _make_mock_session(user_result=user, admin_count=2)
        service = RoleService(session)

        result, previous = await service.change_role(user.id, to_role, _make_actor())

        assert result.role == to_role
        assert previous == from_role
        session.add.assert_called_once_with(user)
        session.flush.assert_called_once()

    async def test_noop_when_role_unchanged(self) -> None:
        user = _make_user(role=UserRole.HR)
        session = _make_mock_session(user_result=user)
        service = RoleService(session)

        result, previous = await service.change_role(user.id, UserRole.HR, _make_actor())

        assert result.role == UserRole.HR
        assert previous == UserRole.HR
        session.add.assert_not_called()

    async def test_raises_user_not_found(self) -> None:
        session = _make_mock_session(user_result=None)
        service = RoleService(session)

        with pytest.raises(UserNotFoundError):
            await service.change_role(uuid4(), UserRole.HR, _make_actor())

    async def test_rejects_self_role_change(self) -> None:
        """An admin changing their own role can lock themselves out."""
        actor = _make_user(email="self@example.com", role=UserRole.SYSTEM_ADMIN)
        session = _make_mock_session(user_result=actor, admin_count=3)
        service = RoleService(session)

        with pytest.raises(SelfDemotionError):
            await service.change_role(actor.id, UserRole.HR, actor)

        session.add.assert_not_called()

    async def test_rejects_changing_super_admin(self) -> None:
        super_admin = _make_user(email="super@example.com", role=UserRole.SYSTEM_ADMIN)
        session = _make_mock_session(user_result=super_admin, admin_count=3)
        service = RoleService(session, super_admin_email="super@example.com")

        with pytest.raises(SuperAdminProtectedError):
            await service.change_role(super_admin.id, UserRole.HR, _make_actor())

    async def test_super_admin_check_is_case_insensitive(self) -> None:
        super_admin = _make_user(email="Super@Example.COM", role=UserRole.SYSTEM_ADMIN)
        session = _make_mock_session(user_result=super_admin, admin_count=3)
        service = RoleService(session, super_admin_email="super@example.com")

        with pytest.raises(SuperAdminProtectedError):
            await service.change_role(super_admin.id, UserRole.USER, _make_actor())

    @pytest.mark.parametrize("to_role", [UserRole.HR, UserRole.USER])
    async def test_rejects_removing_the_last_system_admin(self, to_role: UserRole) -> None:
        """Demoting to HR strands the deployment exactly as demoting to USER does."""
        only_admin = _make_user(email="only@example.com", role=UserRole.SYSTEM_ADMIN)
        session = _make_mock_session(user_result=only_admin, admin_count=1)
        service = RoleService(session)

        with pytest.raises(LastAdminError):
            await service.change_role(only_admin.id, to_role, _make_actor())

        session.add.assert_not_called()

    async def test_allows_demoting_a_system_admin_when_others_remain(self) -> None:
        target = _make_user(email="target@example.com", role=UserRole.SYSTEM_ADMIN)
        session = _make_mock_session(user_result=target, admin_count=2)
        service = RoleService(session)

        result, previous = await service.change_role(target.id, UserRole.HR, _make_actor())

        assert result.role == UserRole.HR
        assert previous == UserRole.SYSTEM_ADMIN


class TestNamedTransitionHelpers:
    """The promote_*/demote_* helpers must delegate to change_role."""

    async def test_promote_to_system_admin(self) -> None:
        user = _make_user(role=UserRole.HR)
        session = _make_mock_session(user_result=user, admin_count=2)
        service = RoleService(session)

        result = await service.promote_to_system_admin(user.id, _make_actor())

        assert result.role == UserRole.SYSTEM_ADMIN

    async def test_promote_to_hr(self) -> None:
        user = _make_user(role=UserRole.USER)
        session = _make_mock_session(user_result=user, admin_count=2)
        service = RoleService(session)

        result = await service.promote_to_hr(user.id, _make_actor())

        assert result.role == UserRole.HR

    async def test_demote_to_user(self) -> None:
        user = _make_user(email="target@example.com", role=UserRole.HR)
        session = _make_mock_session(user_result=user, admin_count=2)
        service = RoleService(session)

        result = await service.demote_to_user(user.id, _make_actor())

        assert result.role == UserRole.USER

    async def test_helpers_enforce_the_same_guards(self) -> None:
        """A guard must not be bypassable by picking a different helper."""
        only_admin = _make_user(email="only@example.com", role=UserRole.SYSTEM_ADMIN)
        session = _make_mock_session(user_result=only_admin, admin_count=1)
        service = RoleService(session)

        with pytest.raises(LastAdminError):
            await service.promote_to_hr(only_admin.id, _make_actor())


class TestEnsureSuperAdmin:
    """Tests for RoleService.ensure_super_admin."""

    async def test_assigns_system_admin_role_to_existing_user(self) -> None:
        user = _make_user(email="super@example.com", role=UserRole.USER)
        session = _make_mock_session(user_result=user)
        service = RoleService(session, super_admin_email="super@example.com")

        await service.ensure_super_admin("super@example.com")

        assert user.role == UserRole.SYSTEM_ADMIN
        session.add.assert_called_once_with(user)
        session.flush.assert_called_once()

    async def test_promotes_an_hr_account_left_behind_by_migration_082(self) -> None:
        """082 rewrote every 'admin' to 'hr'; the super admin must be restored."""
        user = _make_user(email="super@example.com", role=UserRole.HR)
        session = _make_mock_session(user_result=user)
        service = RoleService(session, super_admin_email="super@example.com")

        await service.ensure_super_admin("super@example.com")

        assert user.role == UserRole.SYSTEM_ADMIN

    async def test_noop_when_already_system_admin(self) -> None:
        user = _make_user(email="super@example.com", role=UserRole.SYSTEM_ADMIN)
        session = _make_mock_session(user_result=user)
        service = RoleService(session, super_admin_email="super@example.com")

        await service.ensure_super_admin("super@example.com")

        assert user.role == UserRole.SYSTEM_ADMIN
        session.add.assert_not_called()

    async def test_logs_info_when_user_not_found(self) -> None:
        session = _make_mock_session(user_result=None)
        service = RoleService(session, super_admin_email="super@example.com")

        # Should not raise, just log
        await service.ensure_super_admin("super@example.com")

        session.add.assert_not_called()
        session.flush.assert_not_called()
