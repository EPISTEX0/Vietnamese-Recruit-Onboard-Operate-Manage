"""Unit tests for OrganizationSettingsRepository.

Covers the behaviour called out in task 3.3:

* Default-timezone seeding on first read — ``get_timezone()`` with no row yet
  validates, seeds, and returns the configured default (``Asia/Ho_Chi_Minh``),
  persisting exactly one row.
* A custom configured default is honoured when seeding.
* Single-row semantics — a second ``get_timezone()`` returns the same stored
  value without creating a second row.
* ``set_timezone`` round-trips a valid IANA timezone through the single row.
* Rejection of invalid IANA timezone strings — ``set_timezone("Not/AZone")``
  and seeding from an invalid configured default both raise ``ValueError``.

The seeding / single-row / round-trip behaviours assert real SQL semantics
(``SELECT ... LIMIT 1``, ``INSERT`` + ``flush`` visibility, and ``COUNT``), so
they run against a real PostgreSQL 15 (via ``testcontainers``), mirroring the
onboarding repository tests: ``alembic upgrade head`` builds the schema once per
module and each test gets a fresh, rolled-back async session for isolation.
These tests are marked ``integration`` and skip cleanly when Docker is
unavailable.

The validation tests need no database — timezone strings are checked against
``zoneinfo.available_timezones()`` before any row is touched — so they use a
lightweight mocked session and always run.

Requirements: 11.1, 11.2
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

from src.modules.recruitment.domain.entities import OrganizationSettings
from src.modules.recruitment.infrastructure.config import RecruitmentSettings
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)
from tests.postgres_support import make_postgres_container

# backend/ — the directory that holds alembic.ini and the alembic/ package.
# test file: backend/tests/modules/recruitment/test_org_settings_repository.py
BACKEND_DIR = Path(__file__).resolve().parents[3]

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"


# ---------------------------------------------------------------------------
# Migration / container helpers (mirrors onboarding/test_repositories.py)
# ---------------------------------------------------------------------------
def _docker_available(docker_module: object) -> bool:
    """Return True if a Docker daemon is reachable, else False."""
    try:
        client = docker_module.from_env()  # type: ignore[attr-defined]
        client.ping()
    except Exception:  # noqa: BLE001 - any docker error means "not available"
        return False
    return True


def _run_alembic_upgrade_head(async_url: str) -> None:
    """Run ``alembic upgrade head`` against ``async_url`` using the real env."""
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", async_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="module")
def postgres_async_url() -> Iterator[str]:
    """Start PostgreSQL 15, apply all migrations, yield the asyncpg URL.

    Module-scoped so the (slow) container start + migration chain runs once for
    every test in this module. Skips cleanly if ``testcontainers``/``docker`` or
    a running Docker daemon is unavailable.
    """
    docker = pytest.importorskip("docker")

    if not _docker_available(docker):
        pytest.skip("Docker is not available for the repository integration tests")

    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _run_alembic_upgrade_head(async_url)
        yield async_url


@pytest_asyncio.fixture
async def session(postgres_async_url: str) -> AsyncIterator[AsyncSession]:
    """Provide a fresh async session per test, rolled back on teardown.

    The repository only ever ``flush``es (the service owns the commit), so the
    test never commits and the closing ``rollback`` discards everything written,
    keeping each test isolated without truncating tables.
    """
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


async def _count_rows(session: AsyncSession) -> int:
    """Return the number of organization_settings rows visible to ``session``."""
    result = await session.execute(select(func.count()).select_from(OrganizationSettings))
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Default seeding on first read (R11.1)
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestGetTimezoneSeeding:
    """get_timezone() seeds the configured default on first access (R11.1)."""

    async def test_first_read_seeds_configured_default(self, session: AsyncSession) -> None:
        """First read with no row returns the configured default timezone."""
        repo = OrganizationSettingsRepository(session)

        assert await _count_rows(session) == 0  # precondition: empty table

        timezone = await repo.get_timezone()

        assert timezone == DEFAULT_TIMEZONE

    async def test_first_read_persists_exactly_one_row(self, session: AsyncSession) -> None:
        """Seeding on first read persists a single settings row."""
        repo = OrganizationSettingsRepository(session)

        await repo.get_timezone()

        assert await _count_rows(session) == 1

    async def test_first_read_honours_custom_configured_default(
        self, session: AsyncSession
    ) -> None:
        """A custom configured default timezone is seeded and returned."""
        settings = RecruitmentSettings(
            default_organization_timezone="Europe/Paris",
            default_allowed_domains=["Gmail.com"],
        )
        repo = OrganizationSettingsRepository(session, settings=settings)

        timezone = await repo.get_timezone()

        assert timezone == "Europe/Paris"
        assert await repo.get_allowed_domains() == ["gmail.com"]
        assert await _count_rows(session) == 1


# ---------------------------------------------------------------------------
# Single-row semantics (R11.1)
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestSingleRowSemantics:
    """The repository keeps at most one settings row (R11.1)."""

    async def test_second_read_returns_same_value_without_new_row(
        self, session: AsyncSession
    ) -> None:
        """A second get_timezone() returns the stored value, creating no new row."""
        repo = OrganizationSettingsRepository(session)

        first = await repo.get_timezone()
        second = await repo.get_timezone()

        assert first == second == DEFAULT_TIMEZONE
        assert await _count_rows(session) == 1

    async def test_set_then_get_round_trips_without_new_row(self, session: AsyncSession) -> None:
        """set_timezone persists a value that a later get_timezone returns."""
        repo = OrganizationSettingsRepository(session)

        # Seed first, then update — both operate on the same single row.
        await repo.get_timezone()
        stored = await repo.set_timezone("America/New_York")

        assert stored == "America/New_York"
        assert await repo.get_timezone() == "America/New_York"
        assert await _count_rows(session) == 1

    async def test_set_timezone_on_empty_table_creates_single_row(
        self, session: AsyncSession
    ) -> None:
        """set_timezone with no existing row creates exactly one row."""
        repo = OrganizationSettingsRepository(session)

        stored = await repo.set_timezone("Asia/Tokyo")

        assert stored == "Asia/Tokyo"
        assert await _count_rows(session) == 1


# ---------------------------------------------------------------------------
# Timezone validation (R11.2) — no database needed
# ---------------------------------------------------------------------------
def _mock_empty_session() -> AsyncMock:
    """Build a mocked AsyncSession whose single-row lookup returns no row."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    scalars = MagicMock()
    scalars.first.return_value = None
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars
    session.execute.return_value = execute_result
    return session


class TestTimezoneValidation:
    """Invalid IANA timezone strings are rejected with ValueError (R11.2)."""

    async def test_set_timezone_rejects_invalid_string(self) -> None:
        """set_timezone with an unknown IANA zone raises ValueError."""
        repo = OrganizationSettingsRepository(_mock_empty_session())

        with pytest.raises(ValueError, match="Not/AZone"):
            await repo.set_timezone("Not/AZone")

    async def test_set_timezone_rejects_empty_string(self) -> None:
        """set_timezone with an empty string raises ValueError."""
        repo = OrganizationSettingsRepository(_mock_empty_session())

        with pytest.raises(ValueError):
            await repo.set_timezone("")

    async def test_set_timezone_invalid_string_persists_nothing(self) -> None:
        """A rejected timezone is validated before any write is attempted."""
        session = _mock_empty_session()
        repo = OrganizationSettingsRepository(session)

        with pytest.raises(ValueError):
            await repo.set_timezone("Not/AZone")

        session.add.assert_not_called()
        session.flush.assert_not_awaited()

    async def test_get_timezone_rejects_invalid_configured_default(self) -> None:
        """Seeding from an invalid configured default raises ValueError."""
        settings = RecruitmentSettings(default_organization_timezone="Not/AZone")
        repo = OrganizationSettingsRepository(_mock_empty_session(), settings=settings)

        with pytest.raises(ValueError, match="Not/AZone"):
            await repo.get_timezone()

    def test_default_timezone_property_returns_configured_value(self) -> None:
        """The default_timezone property exposes the configured default."""
        settings = RecruitmentSettings(default_organization_timezone="Europe/Paris")
        repo = OrganizationSettingsRepository(_mock_empty_session(), settings=settings)

        assert repo.default_timezone == "Europe/Paris"


class TestGetName:
    """``get_name`` reads the Organization name without seeding a row (#370)."""

    async def test_returns_empty_string_when_no_row_exists(self) -> None:
        """Unlike get_timezone, an absent row is not seeded -- just reported as ''."""
        session = _mock_empty_session()
        repo = OrganizationSettingsRepository(session)

        name = await repo.get_name()

        assert name == ""
        session.add.assert_not_called()
        session.flush.assert_not_awaited()

    async def test_returns_the_stored_name(self) -> None:
        """A row with a name returns it verbatim."""
        row = OrganizationSettings(name="Acme Corp", timezone="Asia/Ho_Chi_Minh")
        scalars = MagicMock()
        scalars.first.return_value = row
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars
        session = AsyncMock()
        session.execute.return_value = execute_result
        repo = OrganizationSettingsRepository(session)

        name = await repo.get_name()

        assert name == "Acme Corp"

    async def test_returns_empty_string_when_row_has_no_name(self) -> None:
        """A row whose name was never set (the entity default) reports '' too."""
        row = OrganizationSettings(timezone="Asia/Ho_Chi_Minh")
        scalars = MagicMock()
        scalars.first.return_value = row
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars
        session = AsyncMock()
        session.execute.return_value = execute_result
        repo = OrganizationSettingsRepository(session)

        name = await repo.get_name()

        assert name == ""
