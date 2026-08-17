"""Tests for local UserRepository operations."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.identity.domain.entities import User, UserRole
from src.modules.identity.infrastructure.user_repository import UserRepository


@pytest.mark.asyncio
async def test_get_by_email_uses_case_insensitive_lookup() -> None:
    user = User(email="hr@example.com", name="HR", role=UserRole.SYSTEM_ADMIN)
    result = MagicMock()
    result.scalars.return_value.first.return_value = user
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    found = await UserRepository(session).get_by_email("HR@EXAMPLE.COM")

    assert found is user
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_local_account_persists_password_account() -> None:
    session = MagicMock()
    session.flush = AsyncMock()

    user = await UserRepository(session).create_local_account(
        email="hr@example.com",
        name="HR",
        password_hash="hashed",
        role=UserRole.SYSTEM_ADMIN,
    )

    assert user.email == "hr@example.com"
    assert user.password_hash == "hashed"
    assert user.role is UserRole.SYSTEM_ADMIN
    session.add.assert_called_once_with(user)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_login_stamps_last_login_and_flushes() -> None:
    from datetime import UTC, datetime

    user_id = uuid4()
    stale_login = datetime(2020, 1, 1, tzinfo=UTC)
    user = User(
        id=user_id,
        email="hr@example.com",
        name="HR",
        role=UserRole.SYSTEM_ADMIN,
        last_login=stale_login,
    )
    result = MagicMock()
    result.scalars.return_value.first.return_value = user
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    updated = await UserRepository(session).record_login(user_id)

    assert updated is user
    assert updated.last_login > stale_login
    session.add.assert_called_once_with(user)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_login_raises_for_missing_user() -> None:
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    with pytest.raises(ValueError, match="User not found"):
        await UserRepository(session).record_login(uuid4())
