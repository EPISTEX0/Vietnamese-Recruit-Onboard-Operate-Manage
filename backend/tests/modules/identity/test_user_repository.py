"""Tests for local UserRepository operations."""

from unittest.mock import AsyncMock, MagicMock

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
