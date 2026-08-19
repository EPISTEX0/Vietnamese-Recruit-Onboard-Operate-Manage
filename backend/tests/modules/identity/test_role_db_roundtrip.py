"""Round-trip tests proving ``users.role`` loads back as a real ``UserRole``.

The existing ``change_role`` tests build ``User`` objects in memory, so
``user.role`` is whatever the test handed the constructor -- always a genuine
``UserRole``. That hides the defect these tests exist for: the column is a
plain ``VARCHAR``, so SQLAlchemy has nothing to coerce a loaded value back
with, and every row read from the database yielded a bare ``str``. Because
``UserRole`` subclasses ``str``, every ``==`` guard still passed and only
attribute access (``role.value``) blew up -- which is why
``PATCH /api/system-admin/users/{id}/role`` returned 500 on every call while
the whole unit suite stayed green.

Proving that requires a real load from PostgreSQL: write the row, drop it from
the identity map, and read it back. An in-memory session would just hand back
the same Python object and prove nothing, so these tests run against a real
PostgreSQL 15 via ``testcontainers`` and skip cleanly without Docker, mirroring
``tests/modules/onboarding/test_repositories.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

from src.modules.identity.application.role_service import RoleService
from src.modules.identity.domain.entities import User, UserRole


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


async def _insert_user(db_session: AsyncSession, role: UserRole) -> User:
    """Insert and flush one user holding ``role``, returning it."""
    suffix = uuid4().hex[:12]
    user = User(
        email=f"role-roundtrip-{suffix}@example.com",
        name="Role Round-trip User",
        role=role,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _reload_user(db_session: AsyncSession, user_id: UUID) -> User:
    """Re-read a user straight from the database, bypassing the identity map.

    ``expunge_all`` is the point of the whole exercise: without it the session
    returns the very object the test constructed, whose ``role`` never went
    through SQLAlchemy's result processing.
    """
    db_session.expunge_all()
    result = await db_session.execute(select(User).where(User.id == user_id))
    loaded = result.scalars().one()
    return loaded


@pytest.mark.integration
@pytest.mark.parametrize("role", list(UserRole))
async def test_role_loads_from_db_as_userrole(session: AsyncSession, role: UserRole) -> None:
    """A role read back from PostgreSQL must be a ``UserRole``, not a ``str``."""
    user = await _insert_user(session, role)

    loaded = await _reload_user(session, user.id)

    assert isinstance(loaded.role, UserRole), (
        f"users.role loaded as {type(loaded.role).__name__}; the UserRole "
        "annotation is a lie at runtime and every .value access on it crashes."
    )
    assert loaded.role is role


@pytest.mark.integration
async def test_role_loaded_from_db_supports_value_access(session: AsyncSession) -> None:
    """``.value`` on a database-loaded role must work, not raise AttributeError."""
    user = await _insert_user(session, UserRole.HR)

    loaded = await _reload_user(session, user.id)

    assert loaded.role.value == "hr"


@pytest.mark.integration
async def test_change_role_on_db_loaded_user(session: AsyncSession) -> None:
    """``change_role`` must survive a target loaded from the database.

    This is the exact path behind ``PATCH /api/system-admin/users/{id}/role``:
    the service loads the target with its own ``select``, so ``previous_role``
    is whatever SQLAlchemy produced -- and the audit log line then reads
    ``.value`` off it.
    """
    target = await _insert_user(session, UserRole.USER)
    admin = await _insert_user(session, UserRole.SYSTEM_ADMIN)
    session.expunge_all()

    service = RoleService(session)
    updated, previous_role = await service.change_role(target.id, UserRole.HR, admin)

    assert previous_role is UserRole.USER
    assert updated.role is UserRole.HR

    reloaded = await _reload_user(session, target.id)
    assert reloaded.role is UserRole.HR


@pytest.mark.integration
async def test_longest_role_survives_the_real_column(session: AsyncSession) -> None:
    """The widest role value must fit the deployed column.

    ``'system_admin'`` is 12 characters and the model declared ``VARCHAR(10)``;
    migration 084 widened the real column. Writing the longest role through the
    real schema pins that the two agree.
    """
    longest = max(UserRole, key=lambda candidate: len(candidate.value))

    user = await _insert_user(session, longest)
    loaded = await _reload_user(session, user.id)

    assert loaded.role is longest
