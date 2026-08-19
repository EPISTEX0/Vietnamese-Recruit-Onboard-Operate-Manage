"""Unit tests for the rescue CLI's ``create-admin``/``reset-password`` logic.

Covers the guard that makes ``create-admin`` a rescue command instead of a
standing backdoor -- refuse while an active ``system_admin`` exists (#419) --
and the QD-02 audit shape: actor is the affected user, origin is
``details={"actor": "cli"}``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.cli import RescueCliError, create_admin, reset_password
from src.modules.identity.domain.entities import AuditActionType, AuditLog, User, UserRole
from src.modules.identity.infrastructure.password_utils import hash_password, verify_password


def _make_mock_session(*, admin_count: int = 0, existing_user: User | None = None) -> MagicMock:
    """Stand in for AsyncSession: every ``execute()`` answers with the same
    canned count/row, which is enough since each function under test issues
    at most one distinct kind of query per call.
    """
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    async def mock_execute(_statement: object) -> MagicMock:
        result = MagicMock()
        result.scalar_one.return_value = admin_count
        result.scalars.return_value.first.return_value = existing_user
        return result

    session.execute = AsyncMock(side_effect=mock_execute)
    return session


class TestCreateAdmin:
    async def test_refuses_when_an_active_system_admin_exists(self) -> None:
        session = _make_mock_session(admin_count=1)

        with pytest.raises(RescueCliError, match="active system_admin"):
            await create_admin(session, email="new-admin@example.com", name="New Admin")

        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    async def test_creates_system_admin_when_none_is_active(self) -> None:
        session = _make_mock_session(admin_count=0)

        email, temp_password = await create_admin(
            session, email="New-Admin@Example.com", name="New Admin"
        )

        assert email == "new-admin@example.com"
        assert session.add.call_count == 2
        created_user = session.add.call_args_list[0].args[0]
        audit_log = session.add.call_args_list[1].args[0]

        assert isinstance(created_user, User)
        assert created_user.email == "new-admin@example.com"
        assert created_user.role is UserRole.SYSTEM_ADMIN
        assert created_user.must_change_password is True
        assert verify_password(temp_password, created_user.password_hash)

        assert isinstance(audit_log, AuditLog)
        assert audit_log.action_type is AuditActionType.CLI_CREATE_ADMIN
        assert audit_log.admin_user_id == created_user.id
        assert audit_log.admin_email == created_user.email
        assert audit_log.details == {"actor": "cli"}

        session.commit.assert_awaited_once()


class TestResetPassword:
    async def test_refuses_when_no_account_matches(self) -> None:
        session = _make_mock_session(existing_user=None)

        with pytest.raises(RescueCliError, match="no account found"):
            await reset_password(session, email="ghost@example.com")

        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    async def test_resets_password_on_an_existing_account(self) -> None:
        target = User(
            id=uuid4(),
            email="admin@example.com",
            name="Admin",
            role=UserRole.SYSTEM_ADMIN,
            password_hash=hash_password("old-password"),
            must_change_password=False,
        )
        session = _make_mock_session(existing_user=target)

        email, temp_password = await reset_password(session, email="admin@example.com")

        assert email == "admin@example.com"
        assert target.must_change_password is True
        assert verify_password(temp_password, target.password_hash)
        assert not verify_password("old-password", target.password_hash)

        audit_log = session.add.call_args_list[-1].args[0]
        assert isinstance(audit_log, AuditLog)
        assert audit_log.action_type is AuditActionType.CLI_RESET_PASSWORD
        assert audit_log.admin_user_id == target.id
        assert audit_log.admin_email == target.email
        assert audit_log.details == {"actor": "cli"}

        session.commit.assert_awaited_once()
