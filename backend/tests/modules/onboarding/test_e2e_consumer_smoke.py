"""End-to-end consumer smoke test for the onboarding backbone chain.

This is an INTEGRATION smoke test (task 16.2). It drives the full onboarding
chain against a REAL PostgreSQL 15 (via ``testcontainers`` + ``alembic upgrade
head``) and asserts every observable stage of the backbone flow:

1. **Enqueue → consume.** A ``candidate_accepted`` event is "consumed" by
   calling the ARQ task function ``process_candidate_accepted(ctx, payload)``
   directly with a ``ctx`` whose ``session_maker`` is bound to the test engine.

   *Why drive the consumer this way?* ``process_candidate_accepted`` only reads
   ``ctx["session_maker"]`` (plus ``job_id`` / ``job_try``), opens its own
   session, and calls ``OnboardingService.start_from_event`` which owns and
   commits its transaction — exactly what the ARQ worker does at runtime (the
   worker's ``startup`` hook just puts an ``async_sessionmaker`` on ``ctx``).
   Invoking the task function against a session_maker bound to the
   testcontainers engine exercises the real consumer code path end to end
   against a real database. A full Redis/ARQ round-trip would add a broker and
   a worker process without exercising any additional *onboarding* logic, and
   would make the test slow and flaky, so it is deliberately omitted here (the
   publisher → enqueue contract is covered separately by
   ``test_publisher_enqueue_integration.py``).

2. **Initial state.** Exactly one inactive Employee, one ``in_progress``
   process, four ``pending`` tasks matching the checklist template, and a
   ``process_created`` audit entry (R1.1, R2.1, R3.1, R8.3).

3. **Completion.** Each of the four tasks is marked ``done`` via
   ``OnboardingService.complete_task(task_id, actor=admin)`` — built the same
   way the consumer builds the service (``container._build_service`` on a fresh
   session per call, each owning its own committed transaction).

4. **Activation.** After the last task is completed the process is ``complete``,
   the Employee is ``is_active = true`` (R5.1, R5.5), and the audit trail holds
   four ``task_completed`` entries and exactly one ``employee_activated`` entry
   (R4.1, R8.1).

Because each service call commits its own transaction, every assertion stage
re-reads committed state through a *fresh* session so we only observe durably
committed data. The async engine uses ``NullPool`` to match the other
onboarding integration tests and to keep connection lifecycle simple.

Requirements: 1.1, 2.1, 3.1, 4.1, 5.1, 5.5, 8.1, 8.3
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

from src.modules.employee.domain.entities import Department, Employee, Position
from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding import container
from src.modules.onboarding.container import process_candidate_accepted
from src.modules.onboarding.domain.entities import (
    OnboardingAuditLog,
    OnboardingProcess,
    OnboardingTask,
)
from src.modules.onboarding.domain.enums import (
    CHECKLIST_TEMPLATE,
    OnboardingStatus,
    OnboardingTaskStatus,
)
from tests.postgres_support import make_postgres_container

# Audit operation_type values written across the chain (mirrors the service /
# consumer constants without importing private names into assertions).
_OP_PROCESS_CREATED = "process_created"
_OP_TASK_COMPLETED = "task_completed"
_OP_EMPLOYEE_ACTIVATED = "employee_activated"

# backend/ — the directory that holds alembic.ini and the alembic/ package.
# test file: backend/tests/modules/onboarding/test_e2e_consumer_smoke.py
BACKEND_DIR = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Migration / container helpers (mirrors test_repositories.py / test_migration.py)
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

    Module-scoped so the (slow) container start + migration chain runs once.
    Skips cleanly if ``testcontainers``/``docker`` or a running Docker daemon is
    unavailable.
    """
    docker = pytest.importorskip("docker")

    if not _docker_available(docker):
        pytest.skip("Docker is not available for the e2e consumer smoke test")

    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        _run_alembic_upgrade_head(async_url)
        yield async_url


@pytest_asyncio.fixture
async def session_maker(
    postgres_async_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Provide an async session factory bound to the test engine.

    Mirrors the worker's session factory (``expire_on_commit=False``) and uses
    ``NullPool`` like the other onboarding integration tests. The same factory
    is handed to the consumer via ``ctx["session_maker"]`` and used by the test
    to open fresh sessions for setup and for re-reading committed state.
    """
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------
async def _insert_admin_user(maker: async_sessionmaker[AsyncSession]) -> User:
    """Insert and commit an admin User row, returning a detached actor copy.

    ``complete_task`` sets ``task.completed_by_user_id = actor.id`` and writes
    the audit ``user_id = actor.id`` — both FKs to ``users.id`` — so a real
    admin user must be committed before any completion runs. A plain (un-session)
    ``User`` actor carrying the same id/email/role is returned for use as the
    acting HR identity.
    """
    suffix = uuid4().hex[:8]
    user = User(
        email=f"hr-admin-{suffix}@example.com",
        name="HR Admin",
        google_sub=f"google-sub-{suffix}",
        role=UserRole.HR,
    )
    async with maker() as db_session:
        db_session.add(user)
        await db_session.commit()
        admin_id = user.id
        admin_email = user.email

    # A detached actor with the committed id + admin role (matches how the API
    # passes the authenticated user into the service).
    return User(
        id=admin_id,
        email=admin_email,
        name="HR Admin",
        google_sub=f"google-sub-{suffix}",
        role=UserRole.HR,
    )


async def _insert_setup_dependencies(
    maker: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID]:
    suffix = uuid4().hex[:8]
    dept = Department(name=f"Engineering-{suffix}")
    pos = Position(name=f"Software Engineer-{suffix}", department_id=dept.id)
    mgr = Employee(
        employee_code=f"MGR-{suffix}",
        full_name="Manager",
        email=f"manager-{suffix}@example.com",
        is_active=True,
    )
    async with maker() as db_session:
        db_session.add(dept)
        db_session.add(pos)
        db_session.add(mgr)
        await db_session.commit()
        return dept.id, pos.id, mgr.id


# ---------------------------------------------------------------------------
# Re-read helpers (each opens a fresh session to observe committed state only)
# ---------------------------------------------------------------------------
async def _load_employee_by_candidate(
    maker: async_sessionmaker[AsyncSession], candidate_id: UUID
) -> list[Employee]:
    """Return all employees linked to ``candidate_id`` (expected exactly one)."""
    async with maker() as db_session:
        result = await db_session.execute(
            select(Employee).where(Employee.candidate_id == candidate_id)
        )
        return list(result.scalars().all())


async def _load_processes_by_candidate(
    maker: async_sessionmaker[AsyncSession], candidate_id: UUID
) -> list[OnboardingProcess]:
    """Return all onboarding processes for ``candidate_id`` (expected exactly one)."""
    async with maker() as db_session:
        result = await db_session.execute(
            select(OnboardingProcess).where(OnboardingProcess.candidate_id == candidate_id)
        )
        return list(result.scalars().all())


async def _load_tasks_for_process(
    maker: async_sessionmaker[AsyncSession], process_id: UUID
) -> list[OnboardingTask]:
    """Return a process's tasks ordered by ``order_index`` ascending."""
    async with maker() as db_session:
        result = await db_session.execute(
            select(OnboardingTask)
            .where(OnboardingTask.process_id == process_id)
            .order_by(OnboardingTask.order_index)
        )
        return list(result.scalars().all())


async def _load_audit_by_candidate(
    maker: async_sessionmaker[AsyncSession], candidate_id: UUID
) -> list[OnboardingAuditLog]:
    """Return all audit entries carrying ``candidate_id`` ordered by creation."""
    async with maker() as db_session:
        result = await db_session.execute(
            select(OnboardingAuditLog)
            .where(OnboardingAuditLog.candidate_id == candidate_id)
            .order_by(OnboardingAuditLog.created_at)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# The end-to-end smoke test
# ---------------------------------------------------------------------------
@pytest.mark.integration
async def test_candidate_accepted_drives_full_chain_to_active_employee(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Enqueue → consume → complete all tasks → employee active, fully audited.

    Asserts the whole onboarding backbone against a real database:

    * consuming a valid ``candidate_accepted`` event creates one inactive
      Employee, one ``in_progress`` process, and four ``pending`` tasks matching
      the checklist template, with a ``process_created`` audit entry (R1.1,
      R2.1, R3.1, R8.3);
    * completing all four tasks via ``complete_task`` flips the process to
      ``complete`` and the Employee to ``is_active = true`` (R4.1, R5.1, R5.5);
    * the audit trail holds four ``task_completed`` entries and exactly one
      ``employee_activated`` entry (R8.1).

    Requirements: 1.1, 2.1, 3.1, 4.1, 5.1, 5.5, 8.1, 8.3
    """
    admin = await _insert_admin_user(session_maker)

    candidate_id = uuid4()
    job_id = f"job-{uuid4().hex}"
    payload = {
        "candidate_id": str(candidate_id),
        "name": "Nguyen Van A",
        "email": "nguyen.van.a@example.com",
    }
    ctx = {"session_maker": session_maker, "job_id": job_id, "job_try": 1}

    # --- Stage 1: enqueue → consume the event (drives start_from_event). ------
    await process_candidate_accepted(ctx, payload)

    # --- Stage 2: assert the well-formed initial state. -----------------------
    employees = await _load_employee_by_candidate(session_maker, candidate_id)
    assert len(employees) == 1, "exactly one Employee must be created for the candidate"
    employee = employees[0]
    assert employee.is_active is False, "Employee starts inactive (R2.1)"
    assert employee.full_name == "Nguyen Van A"
    assert employee.email == "nguyen.van.a@example.com"
    assert employee.candidate_id == candidate_id

    processes = await _load_processes_by_candidate(session_maker, candidate_id)
    assert len(processes) == 1, "exactly one OnboardingProcess per candidate (R1.1)"
    process = processes[0]
    assert process.status == OnboardingStatus.IN_PROGRESS.value
    assert process.employee_id == employee.id

    tasks = await _load_tasks_for_process(session_maker, process.id)
    assert len(tasks) == 4, "the fixed checklist has exactly four tasks (R3.1)"
    assert all(t.status == OnboardingTaskStatus.PENDING.value for t in tasks)
    # Names and order must match the checklist template exactly.
    expected = [(order, key.value, name) for order, key, name in CHECKLIST_TEMPLATE]
    actual = [(t.order_index, t.task_key, t.name) for t in tasks]
    assert actual == expected

    # Creation audit entry names the event, process, employee, and candidate (R8.3).
    audit_after_creation = await _load_audit_by_candidate(session_maker, candidate_id)
    creation_entries = [e for e in audit_after_creation if e.operation_type == _OP_PROCESS_CREATED]
    assert len(creation_entries) == 1, "exactly one process_created audit entry (R8.3)"
    creation = creation_entries[0]
    assert creation.entity_id == process.id
    assert creation.candidate_id == candidate_id
    assert creation.event_id == job_id
    assert creation.new_value is not None
    assert creation.new_value["process_id"] == str(process.id)
    assert creation.new_value["employee_id"] == str(employee.id)

    # --- Stage 2.5: update employee setup so completion triggers activation. --
    dept_id, pos_id, mgr_id = await _insert_setup_dependencies(session_maker)
    async with session_maker() as svc_session:
        service = container._build_service(svc_session)
        from datetime import date

        today = date.today()
        await service.update_employee_setup(
            process_id=process.id,
            actor=admin,
            data={
                "department_id": dept_id,
                "position_id": pos_id,
                "manager_id": mgr_id,
                "start_date": today,
            },
        )

    # Verify setup updates
    employees_after_setup = await _load_employee_by_candidate(session_maker, candidate_id)
    assert len(employees_after_setup) == 1
    employee_after_setup = employees_after_setup[0]
    assert employee_after_setup.department_id == dept_id
    assert employee_after_setup.position_id == pos_id
    assert employee_after_setup.manager_id == mgr_id
    assert employee_after_setup.start_date == today
    assert employee_after_setup.is_active is False

    processes_after_setup = await _load_processes_by_candidate(session_maker, candidate_id)
    assert len(processes_after_setup) == 1
    assert processes_after_setup[0].status == OnboardingStatus.IN_PROGRESS.value

    # --- Stage 3: complete all four tasks via the service (admin actor). ------
    # Build the service the same way the consumer does: a fresh session per
    # call, each committing its own transaction (matches the real model).
    for index, task in enumerate(tasks):
        async with session_maker() as svc_session:
            service = container._build_service(svc_session)
            updated = await service.complete_task(task_id=task.id, actor=admin)
            assert updated.status == OnboardingTaskStatus.DONE.value

        # Intermediate invariant: until the LAST task is done, the process stays
        # in_progress and the employee stays inactive (R5.2).
        is_last = index == len(tasks) - 1
        mid_process = (await _load_processes_by_candidate(session_maker, candidate_id))[0]
        mid_employee = (await _load_employee_by_candidate(session_maker, candidate_id))[0]
        if not is_last:
            assert mid_process.status == OnboardingStatus.IN_PROGRESS.value
            assert mid_employee.is_active is False

    # --- Stage 4: assert completion + activation committed state. -------------
    final_tasks = await _load_tasks_for_process(session_maker, process.id)
    assert all(t.status == OnboardingTaskStatus.DONE.value for t in final_tasks)

    final_process = (await _load_processes_by_candidate(session_maker, candidate_id))[0]
    assert final_process.status == OnboardingStatus.COMPLETE.value, "process completes (R5.5)"

    final_employee = (await _load_employee_by_candidate(session_maker, candidate_id))[0]
    assert final_employee.is_active is True, "Employee activated on completion (R5.1)"

    # Audit trail: one creation, four completions, exactly one activation (R8.1).
    final_audit = await _load_audit_by_candidate(session_maker, candidate_id)

    completion_entries = [e for e in final_audit if e.operation_type == _OP_TASK_COMPLETED]
    assert len(completion_entries) == 4, "one task_completed audit per task (R8.1)"
    assert all(e.user_id == admin.id for e in completion_entries)
    assert all(e.entity_type == "task" for e in completion_entries)
    completed_task_ids = {e.entity_id for e in completion_entries}
    assert completed_task_ids == {t.id for t in final_tasks}

    activation_entries = [e for e in final_audit if e.operation_type == _OP_EMPLOYEE_ACTIVATED]
    assert len(activation_entries) == 1, "exactly one employee_activated audit entry (R8.1)"
    activation = activation_entries[0]
    assert activation.entity_id == employee.id
    assert activation.user_id == admin.id
    assert activation.entity_type == "employee"

    # The creation entry must still be present and singular alongside the rest.
    assert sum(1 for e in final_audit if e.operation_type == _OP_PROCESS_CREATED) == 1
