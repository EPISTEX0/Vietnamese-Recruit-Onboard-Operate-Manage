"""Property-based tests for idempotent (no-op) onboarding task completion.

Feature: onboarding, Property 10: Completing an already-done task /
already-complete process is a no-op

This module verifies that re-issuing a mark-done request against a task that is
already ``done`` is a pure no-op: the task stays ``done``, the linked process
status and the employee ``is_active`` flag are left exactly as they were, the
call returns the task (success), and no additional status-change
(``task_completed``) or activation (``employee_activated``) audit entry is
written. This holds both for a single already-done task within an in-progress
process and for an already-complete process whose every task is ``done`` and
whose employee is already active.

The test drives the real :class:`OnboardingService` against in-memory fakes so
it executes as a fast, pure-logic check (per the design: "property tests run
against in-memory fakes or mocked repositories"). The fakes are defined inline
in this file to stay self-contained and avoid colliding with other modules'
fixtures.

Validates: Requirements 4.3, 5.8
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding.application.onboarding_service import (
    _OP_EMPLOYEE_ACTIVATED,
    _OP_TASK_COMPLETED,
    OnboardingService,
)
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


# ---------------------------------------------------------------------------
# In-memory fakes (inline, self-contained)
# ---------------------------------------------------------------------------
class _NoOpSavepoint:
    """Async context manager standing in for ``session.begin_nested()``.

    The no-op completion path never opens a SAVEPOINT, but the fake provides it
    so the session faithfully mirrors the real one.
    """

    async def __aenter__(self) -> _NoOpSavepoint:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeSession:
    """Minimal async session tracking commit/rollback counts and SAVEPOINTs."""

    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    def begin_nested(self) -> _NoOpSavepoint:
        return _NoOpSavepoint()


class FakeProcessRepo:
    """In-memory OnboardingProcess store."""

    def __init__(self) -> None:
        self.processes: list[OnboardingProcess] = []

    async def create(self, process: OnboardingProcess) -> OnboardingProcess:
        self.processes.append(process)
        return process

    async def get_by_id(self, process_id: UUID) -> OnboardingProcess | None:
        for process in self.processes:
            if process.id == process_id:
                return process
        return None

    async def get_for_update(self, process_id: UUID) -> OnboardingProcess | None:
        return await self.get_by_id(process_id)

    async def set_status(self, process: OnboardingProcess, status: str) -> OnboardingProcess:
        now = datetime.now(UTC)
        process.status = status
        process.updated_at = now
        if status == OnboardingStatus.COMPLETE:
            process.completed_at = now
        return process


class FakeTaskRepo:
    """In-memory OnboardingTask store."""

    def __init__(self) -> None:
        self.tasks: list[OnboardingTask] = []

    async def create_many(self, tasks: list[OnboardingTask]) -> list[OnboardingTask]:
        self.tasks.extend(tasks)
        return tasks

    async def get_by_id(self, task_id: UUID) -> OnboardingTask | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    async def list_by_process(self, process_id: UUID) -> list[OnboardingTask]:
        return sorted(
            (task for task in self.tasks if task.process_id == process_id),
            key=lambda task: task.order_index,
        )

    async def set_status(
        self,
        task: OnboardingTask,
        status: OnboardingTaskStatus,
        completed_at: datetime | None = None,
        completed_by_user_id: UUID | None = None,
    ) -> OnboardingTask:
        task.status = status.value
        task.completed_at = completed_at
        task.completed_by_user_id = completed_by_user_id
        return task

    async def count_by_status(self, process_id: UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.tasks:
            if task.process_id == process_id:
                counts[task.status] = counts.get(task.status, 0) + 1
        return counts


class FakeAuditRepo:
    """In-memory append-only audit store that also tracks its call count."""

    def __init__(self) -> None:
        self.entries: list[OnboardingAuditLog] = []
        self.append_calls = 0

    async def append(self, entry: OnboardingAuditLog) -> OnboardingAuditLog:
        self.append_calls += 1
        self.entries.append(entry)
        return entry


class FakeEmployeeRepo:
    """In-memory Employee store with sequential ``NV-XXX`` code generation."""

    def __init__(self) -> None:
        self.employees: list[Employee] = []
        self._counter = 0

    async def get_next_code(self) -> str:
        self._counter += 1
        return f"NV-{self._counter:03d}"

    async def create(self, employee: Employee) -> Employee:
        self.employees.append(employee)
        return employee

    async def update(self, employee_id: UUID, data: dict) -> Employee | None:
        for employee in self.employees:
            if employee.id == employee_id:
                for key, value in data.items():
                    setattr(employee, key, value)
                return employee
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _admin_actor() -> User:
    """Build an admin (HR) actor authorized to change onboarding state."""
    return User(
        email="hr.admin@example.com",
        name="HR Admin",
        role=UserRole.HR,
    )


def _build_service(
    process_repo: FakeProcessRepo,
    task_repo: FakeTaskRepo,
    audit_repo: FakeAuditRepo,
    employee_repo: FakeEmployeeRepo,
    session: FakeSession,
) -> OnboardingService:
    """Wire the real service against the in-memory fakes."""
    return OnboardingService(
        process_repo=process_repo,  # type: ignore[arg-type]
        task_repo=task_repo,  # type: ignore[arg-type]
        audit_repo=audit_repo,  # type: ignore[arg-type]
        employee_repo=employee_repo,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )


def _seed_audit(audit_repo: FakeAuditRepo, count: int) -> None:
    """Pre-populate the audit store with neutral entries.

    These make the before/after count comparison meaningful: a no-op must leave
    whatever entries already existed untouched, regardless of how many there
    were. The op type is deliberately not a completion/activation type.
    """
    for index in range(count):
        audit_repo.entries.append(
            OnboardingAuditLog(
                operation_type="seed",
                entity_type="process",
                entity_id=uuid4(),
                change_summary=f"pre-existing audit entry {index}",
                success=True,
            )
        )


def _make_tasks(process_id: UUID, statuses: list[str]) -> list[OnboardingTask]:
    """Build a checklist of tasks for ``process_id`` with the given statuses.

    The first ``len(statuses)`` checklist-template entries are used so task
    keys/names/order match the canonical checklist; any tasks beyond the
    template length wrap around its order index harmlessly (the no-op path does
    not depend on template uniqueness).
    """
    tasks: list[OnboardingTask] = []
    for index, status in enumerate(statuses):
        template = CHECKLIST_TEMPLATE[index % len(CHECKLIST_TEMPLATE)]
        _, task_key, name = template
        tasks.append(
            OnboardingTask(
                process_id=process_id,
                task_key=task_key.value,
                name=name,
                status=status,
                order_index=index,
                completed_at=(
                    datetime.now(UTC) if status == OnboardingTaskStatus.DONE.value else None
                ),
            )
        )
    return tasks


# ---------------------------------------------------------------------------
# Async drivers
# ---------------------------------------------------------------------------
async def _run_noop(
    task_statuses: list[str],
    target_index: int,
    process_status: str,
    employee_active: bool,
    pre_audit_count: int,
) -> None:
    """Set up a process whose target task is already ``done`` and assert no-op.

    Snapshots the audit entry count, the process status, and the employee's
    ``is_active`` flag, re-issues the mark-done request against an already-done
    task, then asserts every snapshot is unchanged, no completion/activation
    audit entry was appended, no commit/rollback occurred, and the returned
    task is the still-``done`` target.
    """
    process_repo = FakeProcessRepo()
    task_repo = FakeTaskRepo()
    audit_repo = FakeAuditRepo()
    employee_repo = FakeEmployeeRepo()
    session = FakeSession()

    employee = Employee(
        employee_code="NV-001",
        full_name="Existing Employee",
        email="employee@example.com",
        candidate_id=uuid4(),
        is_active=employee_active,
    )
    await employee_repo.create(employee)

    process = OnboardingProcess(
        candidate_id=employee.candidate_id or uuid4(),
        employee_id=employee.id,
        status=process_status,
    )
    await process_repo.create(process)

    tasks = _make_tasks(process.id, task_statuses)
    await task_repo.create_many(tasks)
    target_task = tasks[target_index]
    # Precondition for Property 10: the target task is already done.
    assert target_task.status == OnboardingTaskStatus.DONE.value

    _seed_audit(audit_repo, pre_audit_count)

    service = _build_service(process_repo, task_repo, audit_repo, employee_repo, session)

    # Snapshot state before the no-op request.
    audit_count_before = len(audit_repo.entries)
    append_calls_before = audit_repo.append_calls
    process_status_before = process.status
    employee_active_before = employee.is_active
    task_status_before = target_task.status

    actor = _admin_actor()
    returned = await service.complete_task(target_task.id, actor, status="done")

    # The method returns the (still done) target task: success.
    assert returned is target_task
    assert returned.status == OnboardingTaskStatus.DONE.value
    assert target_task.status == task_status_before == OnboardingTaskStatus.DONE.value

    # No additional audit entry of any kind was appended.
    assert len(audit_repo.entries) == audit_count_before
    assert audit_repo.append_calls == append_calls_before
    # Specifically, no status-change or activation entry was written.
    assert not any(e.operation_type == _OP_TASK_COMPLETED for e in audit_repo.entries)
    assert not any(e.operation_type == _OP_EMPLOYEE_ACTIVATED for e in audit_repo.entries)

    # Process and employee are left exactly as they were.
    assert process.status == process_status_before
    assert employee.is_active == employee_active_before

    # No transaction was opened for a no-op.
    assert session.commit_count == 0
    assert session.rollback_count == 0


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------
# Feature: onboarding, Property 10: Completing an already-done task /
# already-complete process is a no-op
@settings(max_examples=150, deadline=None)
@given(
    task_count=st.integers(min_value=1, max_value=6),
    target_offset=st.integers(min_value=0, max_value=5),
    pre_audit_count=st.integers(min_value=0, max_value=5),
    other_done=st.booleans(),
)
def test_completing_single_already_done_task_is_noop(
    task_count: int,
    target_offset: int,
    pre_audit_count: int,
    other_done: bool,
) -> None:
    """Re-marking an already-done task in an in-progress process changes nothing.

    Scenario 1: a process that is still ``in_progress`` (employee inactive) with
    a target task already ``done`` (other tasks pending, or also done when
    ``other_done`` but at least one kept pending so the process stays
    in-progress). Re-issuing mark-done is a no-op: no audit entry, no state
    change, returns the task.

    Validates: Requirements 4.3, 5.8
    """
    target_index = target_offset % task_count
    statuses = [OnboardingTaskStatus.PENDING.value for _ in range(task_count)]
    statuses[target_index] = OnboardingTaskStatus.DONE.value
    if other_done:
        # Mark additional (non-target) tasks done while always keeping at least
        # one other task pending, so the target stays done and the process
        # legitimately remains in_progress with an inactive employee. The target
        # task is never touched here, preserving the Property 10 precondition.
        other_indices = [i for i in range(task_count) if i != target_index]
        for index in other_indices[:-1]:
            statuses[index] = OnboardingTaskStatus.DONE.value

    asyncio.run(
        _run_noop(
            task_statuses=statuses,
            target_index=target_index,
            process_status=OnboardingStatus.IN_PROGRESS.value,
            employee_active=False,
            pre_audit_count=pre_audit_count,
        )
    )


# Feature: onboarding, Property 10: Completing an already-done task /
# already-complete process is a no-op
@settings(max_examples=150, deadline=None)
@given(
    task_count=st.integers(min_value=1, max_value=6),
    target_offset=st.integers(min_value=0, max_value=5),
    pre_audit_count=st.integers(min_value=0, max_value=5),
)
def test_completing_task_in_already_complete_process_is_noop(
    task_count: int,
    target_offset: int,
    pre_audit_count: int,
) -> None:
    """Re-marking a done task in a complete process leaves everything unchanged.

    Scenario 2: a process whose every task is ``done``, whose status is
    ``complete``, and whose linked employee is already active. Re-issuing
    mark-done on one of the done tasks writes no additional audit entry and
    changes neither the process status nor the employee's active flag.

    Validates: Requirements 4.3, 5.8
    """
    target_index = target_offset % task_count
    statuses = [OnboardingTaskStatus.DONE.value for _ in range(task_count)]

    asyncio.run(
        _run_noop(
            task_statuses=statuses,
            target_index=target_index,
            process_status=OnboardingStatus.COMPLETE.value,
            employee_active=True,
            pre_audit_count=pre_audit_count,
        )
    )
