"""Property-based test for atomic task completion + employee activation.

Feature: onboarding, Property 11: Task completion and activation are atomic with respect to audit

This module verifies that ``OnboardingService.complete_task`` applies the task
status change, the process completion, and the employee activation as a single
all-or-nothing transaction *together with* their mandatory audit writes. If a
failure is injected at *any* step — including a failure of an audit append —
the whole transaction must roll back so that the target task's status, the
process status, and the linked employee's ``is_active`` flag are left exactly as
they were before the call, and an error (an :class:`OnboardingError`) is raised
to the caller.

Modelling commit / rollback
---------------------------
The repositories mutate the *live* domain objects (the fake ``set_status`` /
``update`` methods change ``task.status`` / ``process.status`` /
``employee.is_active`` in place, mirroring the real SQLModel session's
identity-map behaviour where a flushed-but-uncommitted change is visible on the
object). Atomicity is modelled by the :class:`FakeSession`:

* When the session is constructed (before ``complete_task`` runs) it captures a
  snapshot of every mutable field it owns: each task's status / completion
  metadata, the process status / timestamps, the employee's ``is_active`` flag,
  and the current length of the audit log.
* ``session.commit()`` simply finalises the in-place mutations (records that a
  commit happened).
* ``session.rollback()`` restores every field from the captured snapshot and
  truncates the audit log back to its pre-call length, exactly as a database
  rollback would discard the uncommitted changes.

Because ``complete_task`` calls ``session.rollback()`` on any exception, the
restore runs and we can assert the post-failure state equals the independently
captured pre-call snapshot — proving rollback atomicity (R5.6, R8.2).

A positive control (``fail_on=None``, single-task process) confirms the commit
path: the task becomes ``done``, the process ``complete``, the employee active,
and exactly the expected audit entries (one status-change + one activation) are
written.

Validates: Requirements 5.6, 8.2
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
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
from src.modules.onboarding.domain.exceptions import OnboardingError

# The points at which a failure can be injected while completing a task.
#   * task_set_status   -> FakeTaskRepo.set_status raises (before any audit)
#   * status_audit      -> FakeAuditRepo.append raises on the status-change entry
#   * process_set_status-> FakeProcessRepo.set_status raises (activation path)
#   * employee_update   -> FakeEmployeeRepo.update raises (activation path)
#   * activation_audit  -> FakeAuditRepo.append raises on the activation entry
_FAILURE_STEPS = (
    "task_set_status",
    "status_audit",
    "process_set_status",
    "employee_update",
    "activation_audit",
)

# Failure steps that are only reached when completing the task activates the
# employee (i.e. every task in the process becomes ``done``).
_ACTIVATION_STEPS = frozenset({"process_set_status", "employee_update", "activation_audit"})

# Printable ASCII excluding the space and '@', used to build varied identities.
_SAFE_ALPHABET = st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="@")


class _InjectedFailure(RuntimeError):
    """Raised by a fake repo to simulate a failure at a specific step."""


class FakeTaskRepo:
    """Fake OnboardingTaskRepository mutating live task objects in place."""

    def __init__(self, tasks: list[OnboardingTask], fail_on: str | None) -> None:
        self._tasks = tasks
        self.fail_on = fail_on

    async def get_by_id(self, task_id: UUID) -> OnboardingTask | None:
        for task in self._tasks:
            if task.id == task_id:
                return task
        return None

    async def set_status(
        self,
        task: OnboardingTask,
        status: OnboardingTaskStatus,
        completed_at: datetime | None = None,
        completed_by_user_id: UUID | None = None,
    ) -> OnboardingTask:
        if self.fail_on == "task_set_status":
            raise _InjectedFailure("injected failure at task set_status step")
        task.status = status.value
        task.completed_at = completed_at
        task.completed_by_user_id = completed_by_user_id
        return task

    async def count_by_status(self, process_id: UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self._tasks:
            if task.process_id == process_id:
                counts[task.status] = counts.get(task.status, 0) + 1
        return counts


class FakeProcessRepo:
    """Fake OnboardingProcessRepository mutating the live process in place."""

    def __init__(self, process: OnboardingProcess, fail_on: str | None) -> None:
        self._process = process
        self.fail_on = fail_on

    async def get_for_update(self, process_id: UUID) -> OnboardingProcess | None:
        if self._process.id == process_id:
            return self._process
        return None

    async def set_status(self, process: OnboardingProcess, status: str) -> OnboardingProcess:
        if self.fail_on == "process_set_status":
            raise _InjectedFailure("injected failure at process set_status step")
        now = datetime.now(UTC)
        process.status = status
        process.updated_at = now
        if status == OnboardingStatus.COMPLETE:
            process.completed_at = now
        return process


class FakeEmployeeRepo:
    """Fake EmployeeRepository mutating the live employee in place."""

    def __init__(self, employee: Employee, fail_on: str | None) -> None:
        self._employee = employee
        self.employees: list[Employee] = [employee]
        self.fail_on = fail_on

    async def get_by_id(self, employee_id: UUID) -> Employee | None:
        for e in self.employees:
            if e.id == employee_id:
                return e
        return None

    async def update(self, employee_id: UUID, fields: dict[str, object]) -> Employee | None:
        if self.fail_on == "employee_update":
            raise _InjectedFailure("injected failure at employee update step")
        if self._employee.id != employee_id:
            return None
        for key, value in fields.items():
            setattr(self._employee, key, value)
        return self._employee


class FakeAuditRepo:
    """Fake append-only audit repository that can fail a chosen append."""

    def __init__(self, fail_on: str | None) -> None:
        self.fail_on = fail_on
        self.entries: list[OnboardingAuditLog] = []

    async def append(self, entry: OnboardingAuditLog) -> OnboardingAuditLog:
        if self.fail_on == "status_audit" and entry.operation_type == _OP_TASK_COMPLETED:
            raise _InjectedFailure("injected failure at status-change audit step")
        if self.fail_on == "activation_audit" and entry.operation_type == _OP_EMPLOYEE_ACTIVATED:
            raise _InjectedFailure("injected failure at activation audit step")
        self.entries.append(entry)
        return entry


class FakeSession:
    """Async session modelling commit/rollback by snapshot + restore.

    Captures a snapshot of all mutable state it owns at construction time
    (before ``complete_task`` runs). ``rollback`` restores that snapshot and
    truncates the audit log, mirroring a database transaction rollback.
    """

    def __init__(
        self,
        tasks: list[OnboardingTask],
        process: OnboardingProcess,
        employee: Employee,
        audit_repo: FakeAuditRepo,
    ) -> None:
        self._tasks = tasks
        self._process = process
        self._employee = employee
        self._audit_repo = audit_repo
        self.commit_count = 0
        self.rollback_count = 0
        # Pre-call snapshot of every mutable field this session owns.
        self._task_snapshot: dict[UUID, tuple[str, datetime | None, UUID | None]] = {
            task.id: (task.status, task.completed_at, task.completed_by_user_id) for task in tasks
        }
        self._process_status = process.status
        self._process_updated_at = process.updated_at
        self._process_completed_at = process.completed_at
        self._employee_is_active = employee.is_active
        self._audit_len = len(audit_repo.entries)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1
        for task in self._tasks:
            status, completed_at, completed_by = self._task_snapshot[task.id]
            task.status = status
            task.completed_at = completed_at
            task.completed_by_user_id = completed_by
        self._process.status = self._process_status
        self._process.updated_at = self._process_updated_at
        self._process.completed_at = self._process_completed_at
        self._employee.is_active = self._employee_is_active
        del self._audit_repo.entries[self._audit_len :]


def _build_world(
    fail_on: str | None,
    task_count: int,
    triggers_activation: bool,
    candidate_id: UUID,
    actor_email: str,
) -> tuple[
    OnboardingService,
    list[OnboardingTask],
    OnboardingTask,
    OnboardingProcess,
    Employee,
    User,
    FakeAuditRepo,
    FakeSession,
]:
    """Build a service over fakes plus the live task/process/employee objects.

    The target task (``tasks[0]``) is ``pending``. When ``triggers_activation``
    is true every other task is already ``done`` so completing the target marks
    the whole checklist done and drives the activation path; otherwise a second
    task is left ``pending`` so activation is not reached.
    """
    from datetime import date
    from uuid import uuid4

    manager = Employee(
        employee_code="MGR-001",
        full_name="Manager",
        email="manager@example.com",
        is_active=True,
    )
    employee = Employee(
        employee_code="NV-001",
        full_name="Test Employee",
        email="employee@example.com",
        candidate_id=candidate_id,
        is_active=False,
        department_id=uuid4(),
        position_id=uuid4(),
        manager_id=manager.id,
        start_date=date(2026, 1, 1),
    )
    process = OnboardingProcess(
        candidate_id=candidate_id,
        employee_id=employee.id,
        status=OnboardingStatus.IN_PROGRESS.value,
    )
    tasks = [
        OnboardingTask(
            process_id=process.id,
            task_key=task_key.value,
            name=name,
            status=OnboardingTaskStatus.PENDING.value,
            order_index=order_index,
        )
        for order_index, task_key, name in CHECKLIST_TEMPLATE[:task_count]
    ]
    if triggers_activation:
        # Every task other than the target is already done -> completing the
        # target finishes the checklist and activates the employee.
        for task in tasks[1:]:
            task.status = OnboardingTaskStatus.DONE.value
    else:
        # Keep a second task pending so the checklist stays incomplete.
        for task in tasks[2:]:
            task.status = OnboardingTaskStatus.DONE.value

    actor = User(
        email=actor_email,
        name="HR Admin",
        google_sub=f"sub-{actor_email}",
        role=UserRole.HR,
    )
    audit_repo = FakeAuditRepo(fail_on)
    session = FakeSession(tasks, process, employee, audit_repo)
    employee_repo = FakeEmployeeRepo(employee, fail_on)
    employee_repo.employees.append(manager)
    service = OnboardingService(
        process_repo=FakeProcessRepo(process, fail_on),  # type: ignore[arg-type]
        task_repo=FakeTaskRepo(tasks, fail_on),  # type: ignore[arg-type]
        audit_repo=audit_repo,  # type: ignore[arg-type]
        employee_repo=employee_repo,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )
    return service, tasks, tasks[0], process, employee, actor, audit_repo, session


@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[str, int, bool]:
    """Generate (fail_on, task_count, triggers_activation) scenarios.

    Activation-path failure steps force ``triggers_activation`` so the failing
    step is actually reached; a single-task process always activates on
    completion.
    """
    fail_on = draw(st.sampled_from(_FAILURE_STEPS))
    task_count = draw(st.integers(min_value=1, max_value=4))
    if fail_on in _ACTIVATION_STEPS or task_count == 1:
        triggers_activation = True
    else:
        triggers_activation = draw(st.booleans())
    return fail_on, task_count, triggers_activation


async def _run_atomicity_case(
    fail_on: str,
    task_count: int,
    triggers_activation: bool,
    candidate_id: UUID,
    actor_email: str,
) -> None:
    """Inject a failure during completion and assert nothing changed."""
    (
        service,
        tasks,
        target_task,
        process,
        employee,
        actor,
        audit_repo,
        session,
    ) = _build_world(fail_on, task_count, triggers_activation, candidate_id, actor_email)

    # Snapshot the pre-call state independently of the session's own snapshot.
    pre_task_statuses = {task.id: task.status for task in tasks}
    pre_process_status = process.status
    pre_employee_active = employee.is_active
    pre_audit_len = len(audit_repo.entries)

    with pytest.raises(OnboardingError):
        await service.complete_task(target_task.id, actor, status="done")

    # Rollback restored every field to exactly its pre-call value (R5.6, R8.2).
    assert {task.id: task.status for task in tasks} == pre_task_statuses
    assert process.status == pre_process_status
    assert employee.is_active == pre_employee_active
    # The audit append(s) were rolled back too: no new entries persist.
    assert len(audit_repo.entries) == pre_audit_len
    # The transaction was rolled back exactly once and never committed.
    assert session.rollback_count == 1
    assert session.commit_count == 0


# Feature: onboarding, Property 11: Task completion and activation are atomic with respect to audit
@settings(max_examples=200, deadline=None)
@given(
    scenario=_scenarios(),
    candidate_id=st.uuids(),
    actor_email=st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=24),
)
def test_completion_and_activation_are_atomic_under_injected_failure(
    scenario: tuple[str, int, bool],
    candidate_id: UUID,
    actor_email: str,
) -> None:
    """Any injected failure (incl. audit) rolls back task/process/employee.

    Validates: Requirements 5.6, 8.2
    """
    fail_on, task_count, triggers_activation = scenario
    asyncio.run(
        _run_atomicity_case(fail_on, task_count, triggers_activation, candidate_id, actor_email)
    )


async def _run_positive_control_case(candidate_id: UUID, actor_email: str) -> None:
    """With no failure injected, completion + activation commits cleanly."""
    (
        service,
        _tasks,
        target_task,
        process,
        employee,
        actor,
        audit_repo,
        session,
    ) = _build_world(
        fail_on=None,
        task_count=1,
        triggers_activation=True,
        candidate_id=candidate_id,
        actor_email=actor_email,
    )

    result = await service.complete_task(target_task.id, actor, status="done")

    # The single task is done, the process complete, and the employee active.
    assert result.status == OnboardingTaskStatus.DONE.value
    assert target_task.status == OnboardingTaskStatus.DONE.value
    assert process.status == OnboardingStatus.COMPLETE
    assert employee.is_active is True

    # Exactly one status-change audit entry and one activation audit entry.
    op_types = [entry.operation_type for entry in audit_repo.entries]
    assert op_types.count(_OP_TASK_COMPLETED) == 1
    assert op_types.count(_OP_EMPLOYEE_ACTIVATED) == 1
    assert len(audit_repo.entries) == 2

    # The transaction committed exactly once and never rolled back.
    assert session.commit_count == 1
    assert session.rollback_count == 0


# Feature: onboarding, Property 11: Task completion and activation are atomic with respect to audit
@settings(max_examples=100, deadline=None)
@given(
    candidate_id=st.uuids(),
    actor_email=st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=24),
)
def test_completion_and_activation_commit_cleanly_without_failure(
    candidate_id: UUID,
    actor_email: str,
) -> None:
    """Positive control: the commit path completes the task and activates.

    Validates: Requirements 5.6, 8.2
    """
    asyncio.run(_run_positive_control_case(candidate_id, actor_email))
