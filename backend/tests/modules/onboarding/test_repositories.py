"""Unit tests for the onboarding infrastructure repositories.

Covers the behaviour called out in task 3.5:

* ``OnboardingProcessRepository.list`` — pagination (page-size cap + page
  offset) and that the returned total is the true count of matching processes,
  independent of the page contents; status filtering; and the empty-list /
  zero-total result when nothing matches (R6.1, R6.2, R6.4).
* ``OnboardingTaskRepository.count_by_status`` — counts grouped by status, and
  the empty mapping for a process with zero tasks.
* ``OnboardingProcessRepository.get_by_candidate_id`` — the idempotency lookup
  returns the existing process for a candidate and ``None`` otherwise.
* ``OnboardingAuditRepository`` exposes no mutation methods — only ``append`` is
  public, with no ``update``/``delete`` surface (R8.4).

The process/task/audit-roundtrip tests run against a real PostgreSQL 15 (via
``testcontainers``) because the onboarding entities use PostgreSQL-specific
column types (``JSONB``) and the assertions exercise real SQL semantics
(``LIMIT``/``OFFSET`` pagination, ``COUNT``, and ``GROUP BY``) that an in-memory
fake could not faithfully reproduce. They mirror the migration smoke test:
``alembic upgrade head`` builds the schema once per module, and each test gets a
fresh, rolled-back async session so the tests stay isolated. These tests are
marked ``integration`` and skip cleanly when Docker is unavailable.

The append-only reflection test needs no database and always runs.

Requirements: 6.1, 6.2, 6.4, 8.4
"""

from __future__ import annotations

import inspect as inspect_module
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.modules.employee.domain.entities import Employee
from src.modules.onboarding.domain.entities import OnboardingProcess, OnboardingTask
from src.modules.onboarding.domain.enums import OnboardingStatus, OnboardingTaskStatus
from src.modules.onboarding.infrastructure.audit_repository import OnboardingAuditRepository
from src.modules.onboarding.infrastructure.process_repository import OnboardingProcessRepository
from src.modules.onboarding.infrastructure.task_repository import OnboardingTaskRepository
from tests.postgres_support import make_postgres_container

# backend/ — the directory that holds alembic.ini and the alembic/ package.
# test file: backend/tests/modules/onboarding/test_repositories.py
BACKEND_DIR = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Migration / container helpers (mirrors test_migration.py)
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

    The repositories only ever ``flush`` (the service owns the commit), so the
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


# ---------------------------------------------------------------------------
# Entity construction helpers
# ---------------------------------------------------------------------------
async def _make_employee(db_session: AsyncSession) -> Employee:
    """Insert and flush a minimal valid employee, returning it.

    ``onboarding_processes.employee_id`` is a NOT NULL FK to ``employees.id``,
    so a parent employee must exist before a process row is created.
    """
    suffix = uuid4().hex[:12]
    employee = Employee(
        employee_code=f"NV-{suffix}",
        full_name="Onboarding Test Employee",
        email=f"emp-{suffix}@example.com",
    )
    db_session.add(employee)
    await db_session.flush()
    return employee


async def _make_process(
    repo: OnboardingProcessRepository,
    db_session: AsyncSession,
    status: str = OnboardingStatus.IN_PROGRESS.value,
    candidate_id: UUID | None = None,
) -> OnboardingProcess:
    """Create (and flush) one onboarding process with its own employee."""
    employee = await _make_employee(db_session)
    process = OnboardingProcess(
        candidate_id=candidate_id or uuid4(),
        employee_id=employee.id,
        status=status,
    )
    return await repo.create(process)


def _make_task(
    process_id: UUID,
    status: str,
    order_index: int,
) -> OnboardingTask:
    """Build an onboarding task (not yet persisted)."""
    return OnboardingTask(
        process_id=process_id,
        task_key=f"task_{order_index}",
        name=f"Task {order_index}",
        status=status,
        order_index=order_index,
    )


# ---------------------------------------------------------------------------
# OnboardingProcessRepository.list — pagination, total, filtering
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestProcessRepositoryListPagination:
    """list() pagination cap and true-total behaviour (R6.1, R6.2)."""

    async def test_list_caps_results_to_page_size(self, session: AsyncSession) -> None:
        """A page returns at most ``page_size`` rows while total reports the full count."""
        repo = OnboardingProcessRepository(session)
        for _ in range(5):
            await _make_process(repo, session)

        rows, total = await repo.list(page=1, page_size=2)

        assert len(rows) == 2
        assert total == 5

    async def test_list_page_offset_partitions_results(self, session: AsyncSession) -> None:
        """Successive pages cover every row exactly once (offset advances correctly)."""
        repo = OnboardingProcessRepository(session)
        created_ids = set()
        for _ in range(5):
            process = await _make_process(repo, session)
            created_ids.add(process.id)

        page1, total1 = await repo.list(page=1, page_size=2)
        page2, total2 = await repo.list(page=2, page_size=2)
        page3, total3 = await repo.list(page=3, page_size=2)

        assert (total1, total2, total3) == (5, 5, 5)
        assert (len(page1), len(page2), len(page3)) == (2, 2, 1)

        seen = {row.id for row in (*page1, *page2, *page3)}
        assert seen == created_ids  # no overlaps, no gaps

    async def test_list_total_is_independent_of_empty_page(self, session: AsyncSession) -> None:
        """A page past the end is empty, yet total still reports the true count."""
        repo = OnboardingProcessRepository(session)
        for _ in range(3):
            await _make_process(repo, session)

        rows, total = await repo.list(page=99, page_size=10)

        assert rows == []
        assert total == 3


@pytest.mark.integration
class TestProcessRepositoryListFiltering:
    """list() status filtering behaviour (R6.4)."""

    async def test_status_filter_returns_only_matching(self, session: AsyncSession) -> None:
        """Filtering by status returns only processes whose status is identical."""
        repo = OnboardingProcessRepository(session)
        for _ in range(3):
            await _make_process(repo, session, status=OnboardingStatus.IN_PROGRESS.value)
        for _ in range(2):
            await _make_process(repo, session, status=OnboardingStatus.COMPLETE.value)

        rows, total = await repo.list(status=OnboardingStatus.COMPLETE.value)

        assert total == 2
        assert len(rows) == 2
        assert all(row.status == OnboardingStatus.COMPLETE.value for row in rows)

    async def test_unfiltered_list_returns_all_statuses(self, session: AsyncSession) -> None:
        """Without a status filter every process is counted regardless of status."""
        repo = OnboardingProcessRepository(session)
        await _make_process(repo, session, status=OnboardingStatus.IN_PROGRESS.value)
        await _make_process(repo, session, status=OnboardingStatus.COMPLETE.value)

        rows, total = await repo.list()

        assert total == 2
        assert len(rows) == 2

    async def test_filter_with_no_matches_returns_empty_and_zero_total(
        self, session: AsyncSession
    ) -> None:
        """A status with no matching rows yields an empty list and a zero total (R6.2)."""
        repo = OnboardingProcessRepository(session)
        await _make_process(repo, session, status=OnboardingStatus.IN_PROGRESS.value)

        rows, total = await repo.list(status=OnboardingStatus.COMPLETE.value)

        assert rows == []
        assert total == 0


# ---------------------------------------------------------------------------
# OnboardingProcessRepository.get_by_candidate_id — idempotency lookup
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestProcessRepositoryIdempotencyLookup:
    """get_by_candidate_id() backs the exactly-one-process-per-candidate guard."""

    async def test_returns_existing_process_for_candidate(self, session: AsyncSession) -> None:
        """The lookup returns the stored process for a known candidate id."""
        repo = OnboardingProcessRepository(session)
        candidate_id = uuid4()
        created = await _make_process(repo, session, candidate_id=candidate_id)

        found = await repo.get_by_candidate_id(candidate_id)

        assert found is not None
        assert found.id == created.id
        assert found.candidate_id == candidate_id

    async def test_returns_none_when_no_process_for_candidate(self, session: AsyncSession) -> None:
        """The lookup returns None for a candidate that has no process."""
        repo = OnboardingProcessRepository(session)
        await _make_process(repo, session)  # an unrelated process exists

        found = await repo.get_by_candidate_id(uuid4())

        assert found is None


# ---------------------------------------------------------------------------
# OnboardingTaskRepository.count_by_status — grouped counts
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestTaskRepositoryCountByStatus:
    """count_by_status() groups a process's tasks by status."""

    async def test_counts_are_grouped_by_status(self, session: AsyncSession) -> None:
        """The mapping reports the correct count for each present status."""
        process_repo = OnboardingProcessRepository(session)
        task_repo = OnboardingTaskRepository(session)
        process = await _make_process(process_repo, session)

        tasks = [
            _make_task(process.id, OnboardingTaskStatus.DONE.value, 0),
            _make_task(process.id, OnboardingTaskStatus.DONE.value, 1),
            _make_task(process.id, OnboardingTaskStatus.DONE.value, 2),
            _make_task(process.id, OnboardingTaskStatus.PENDING.value, 3),
        ]
        await task_repo.create_many(tasks)

        counts = await task_repo.count_by_status(process.id)

        assert counts == {
            OnboardingTaskStatus.DONE.value: 3,
            OnboardingTaskStatus.PENDING.value: 1,
        }

    async def test_counts_isolated_per_process(self, session: AsyncSession) -> None:
        """Only the requested process's tasks are counted."""
        process_repo = OnboardingProcessRepository(session)
        task_repo = OnboardingTaskRepository(session)
        target = await _make_process(process_repo, session)
        other = await _make_process(process_repo, session)

        await task_repo.create_many([_make_task(target.id, OnboardingTaskStatus.PENDING.value, 0)])
        await task_repo.create_many(
            [_make_task(other.id, OnboardingTaskStatus.DONE.value, 0) for _ in range(2)]
        )

        counts = await task_repo.count_by_status(target.id)

        assert counts == {OnboardingTaskStatus.PENDING.value: 1}

    async def test_zero_task_process_returns_empty_dict(self, session: AsyncSession) -> None:
        """A process with no tasks yields an empty mapping (no zero-count keys)."""
        process_repo = OnboardingProcessRepository(session)
        task_repo = OnboardingTaskRepository(session)
        process = await _make_process(process_repo, session)

        counts = await task_repo.count_by_status(process.id)

        assert counts == {}


# ---------------------------------------------------------------------------
# OnboardingAuditRepository — append-only surface (R8.4), no database needed
# ---------------------------------------------------------------------------
class TestAuditRepositoryAppendOnly:
    """The audit repository exposes only append; no mutation surface (R8.4)."""

    def test_append_is_the_only_public_async_method(self) -> None:
        """``append`` is the single public coroutine method on the repository."""
        public_methods = {
            name
            for name, member in inspect_module.getmembers(
                OnboardingAuditRepository, predicate=inspect_module.isfunction
            )
            if not name.startswith("_")
        }

        assert public_methods == {"append"}

    @pytest.mark.parametrize(
        "forbidden",
        ["update", "delete", "remove", "edit", "modify", "set_status", "save", "merge"],
    )
    def test_no_mutation_method_is_exposed(self, forbidden: str) -> None:
        """No update/delete-style mutation method exists on the class."""
        assert not hasattr(OnboardingAuditRepository, forbidden)

    def test_append_is_a_coroutine(self) -> None:
        """The single exposed method is async, matching the repository contract."""
        assert inspect_module.iscoroutinefunction(OnboardingAuditRepository.append)
