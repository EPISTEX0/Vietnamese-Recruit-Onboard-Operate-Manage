"""Proves ``count_active_system_admins()`` filters by role AND ``is_active``.

A mocked session can answer ``scalar_one()`` with whatever a test hands it, so
it cannot tell a working ``WHERE role = ... AND is_active = true`` from a query
that always returns the row count. The rescue CLI's ``create-admin`` guard
(#419) depends on this filter excluding deactivated system_admin accounts --
a deployment that deactivated its only admin instead of deleting it must still
be rescuable -- so this runs the real query against PostgreSQL 15.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.identity.domain.entities import User, UserRole
from src.modules.identity.infrastructure.user_repository import UserRepository


@pytest_asyncio.fixture
async def session(postgres_async_url: str) -> AsyncIterator[AsyncSession]:
    """Provide a fresh async session per test, rolled back on teardown."""
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


async def _insert_user(db_session: AsyncSession, *, role: UserRole, is_active: bool) -> User:
    suffix = uuid4().hex[:12]
    user = User(
        email=f"active-admin-count-{suffix}@example.com",
        name="Active Admin Count Fixture",
        role=role,
        is_active=is_active,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_counts_only_active_system_admins(session: AsyncSession) -> None:
    """Diffed against a baseline: the shared session-scoped database may
    already hold committed rows from other test modules, so asserting an
    absolute count would be flaky rather than wrong.
    """
    repo = UserRepository(session)
    baseline = await repo.count_active_system_admins()

    await _insert_user(session, role=UserRole.SYSTEM_ADMIN, is_active=True)
    await _insert_user(session, role=UserRole.SYSTEM_ADMIN, is_active=False)
    await _insert_user(session, role=UserRole.HR, is_active=True)

    assert await repo.count_active_system_admins() == baseline + 1
