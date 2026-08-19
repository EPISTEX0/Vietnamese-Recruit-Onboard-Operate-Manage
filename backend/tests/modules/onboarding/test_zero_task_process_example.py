"""Example edge-case test: a zero-task process never activates its employee.

This is an EXAMPLE edge-case test (not a property/Hypothesis test). It pins
down the boundary clause of the activation invariant (Property 8): activation
happens if and only if a process has *at least one* task and every task is
``done``. A process containing zero tasks therefore must never flip its
employee to active, must stay ``in_progress``, and must write no activation
audit entry.

``complete_task`` cannot be exercised for this case because it requires an
existing task to act on, and a zero-task process has none. The clearest way to
pin the zero-task boundary is to drive the private activation helper
:meth:`OnboardingService._activate_if_complete` directly with a zero-task
process. Accessing the private method here is deliberate and acceptable for an
edge-case test: it isolates exactly the branch under test
(``total == 0 -> return`` without activation) with no surrounding noise. A
black-box ``count_by_status`` check (``total == 0`` for a zero-task process) is
added as a complementary sanity assertion.

The test drives the real :class:`OnboardingService` against in-memory fakes so
it runs as a fast, pure-logic check. The fakes are defined inline in this file
to stay self-contained and avoid collisions with the other onboarding test
modules (no shared conftest / fakes module).

Validates: Requirements 5.7
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from src.modules.employee.domain.entities import Employee
from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding.application.onboarding_service import (
    _OP_EMPLOYEE_ACTIVATED,
    OnboardingService,
)
from src.modules.onboarding.domain.entities import (
    OnboardingAuditLog,
    OnboardingProcess,
    OnboardingTask,
)
from src.modules.onboarding.domain.enums import OnboardingStatus, OnboardingTaskStatus


# ---------------------------------------------------------------------------
# In-memory fakes (inline, self-contained — no shared fixtures)
# ---------------------------------------------------------------------------
class _NoOpSavepoint:
    """Async context manager standing in for ``session.begin_nested()``."""

    async def __aenter__(self) -> _NoOpSavepoint:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakeSession:
    """Minimal async session: no-op commit/rollback and a no-op SAVEPOINT."""

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
    """In-memory OnboardingProcess store that records ``set_status`` calls."""

    def __init__(self) -> None:
        self.processes: list[OnboardingProcess] = []
        self.set_status_calls: list[tuple[UUID, str]] = []

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

    async def set_status(self, process: OnboardingProcess, status: object) -> OnboardingProcess:
        # Record the call so the test can assert the process was never moved to
        # COMPLETE for a zero-task process. ``status`` may be an enum or str.
        status_value = getattr(status, "value", status)
        self.set_status_calls.append((process.id, str(status_value)))
        process.status = status_value  # type: ignore[assignment]
        return process


class FakeTaskRepo:
    """In-memory OnboardingTask store; a zero-task process yields empty counts."""

    def __init__(self) -> None:
        self.tasks: list[OnboardingTask] = []

    async def count_by_status(self, process_id: UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.tasks:
            if task.process_id == process_id:
                counts[task.status] = counts.get(task.status, 0) + 1
        return counts

    async def list_by_process(self, process_id: UUID) -> list[OnboardingTask]:
        return sorted(
            (task for task in self.tasks if task.process_id == process_id),
            key=lambda task: task.order_index,
        )


class FakeEmployeeRepo:
    """In-memory Employee store; ``update`` records calls and applies values."""

    def __init__(self) -> None:
        self.employees: list[Employee] = []
        self.update_calls: list[tuple[UUID, dict[str, object]]] = []

    async def get_by_id(self, employee_id: UUID) -> Employee | None:
        for employee in self.employees:
            if employee.id == employee_id:
                return employee
        return None

    async def create(self, employee: Employee) -> Employee:
        self.employees.append(employee)
        return employee

    async def update(self, employee_id: UUID, values: dict[str, object]) -> Employee | None:
        self.update_calls.append((employee_id, dict(values)))
        for employee in self.employees:
            if employee.id == employee_id:
                for key, value in values.items():
                    setattr(employee, key, value)
                return employee
        return None


class FakeAuditRepo:
    """In-memory append-only audit store."""

    def __init__(self) -> None:
        self.entries: list[OnboardingAuditLog] = []

    async def append(self, entry: OnboardingAuditLog) -> OnboardingAuditLog:
        self.entries.append(entry)
        return entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_admin_actor() -> User:
    """Build an admin (HR) actor identity."""
    suffix = uuid4().hex
    return User(
        email=f"hr-{suffix}@example.com",
        name="HR Admin",
        role=UserRole.HR,
    )


_Fixture = tuple[
    OnboardingService,
    FakeProcessRepo,
    FakeTaskRepo,
    FakeEmployeeRepo,
    FakeAuditRepo,
    OnboardingProcess,
    Employee,
]


def _build_zero_task_fixture() -> _Fixture:
    """Wire a service with an in_progress, zero-task process + inactive employee."""
    process_repo = FakeProcessRepo()
    task_repo = FakeTaskRepo()
    employee_repo = FakeEmployeeRepo()
    audit_repo = FakeAuditRepo()
    session = FakeSession()

    candidate_id = uuid4()
    from datetime import date

    manager = Employee(
        employee_code="MGR-001",
        full_name="Manager",
        email="manager@example.com",
        is_active=True,
    )
    employee_repo.employees.append(manager)

    employee = Employee(
        employee_code="NV-001",
        full_name="No Task Employee",
        email="no.task@example.com",
        candidate_id=candidate_id,
        is_active=False,
        department_id=uuid4(),
        position_id=uuid4(),
        manager_id=manager.id,
        start_date=date(2026, 1, 1),
    )
    employee_repo.employees.append(employee)

    process = OnboardingProcess(
        candidate_id=candidate_id,
        employee_id=employee.id,
        status=OnboardingStatus.IN_PROGRESS.value,
    )
    process_repo.processes.append(process)
    # Intentionally create NO tasks for this process.

    service = OnboardingService(
        process_repo=process_repo,  # type: ignore[arg-type]
        task_repo=task_repo,  # type: ignore[arg-type]
        audit_repo=audit_repo,  # type: ignore[arg-type]
        employee_repo=employee_repo,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )
    return service, process_repo, task_repo, employee_repo, audit_repo, process, employee


# ---------------------------------------------------------------------------
# Example edge-case tests
# ---------------------------------------------------------------------------
def test_zero_task_process_never_activates_employee() -> None:
    """A zero-task process leaves the employee inactive and process in_progress.

    Drives the activation helper directly on a process that has no tasks and
    asserts the no-activation branch (R5.7): the employee stays inactive, the
    process stays ``in_progress``, the process is never moved to ``COMPLETE``,
    the employee is never updated, and no activation audit entry is written.

    Validates: Requirements 5.7
    """
    service, process_repo, _task_repo, employee_repo, audit_repo, process, employee = (
        _build_zero_task_fixture()
    )
    actor = _make_admin_actor()

    asyncio.run(service._activate_if_complete(process, actor))

    # Employee stays inactive and the process stays in_progress.
    assert employee.is_active is False
    assert process.status == OnboardingStatus.IN_PROGRESS.value
    assert process_repo.processes[0].status == OnboardingStatus.IN_PROGRESS.value

    # The process was never transitioned to COMPLETE, and the employee was
    # never updated (no activation side effects at all).
    assert not any(
        status == OnboardingStatus.COMPLETE.value
        for _process_id, status in process_repo.set_status_calls
    )
    assert employee_repo.update_calls == []

    # No activation audit entry was written.
    activation_entries = [
        entry for entry in audit_repo.entries if entry.operation_type == _OP_EMPLOYEE_ACTIVATED
    ]
    assert activation_entries == []


def test_zero_task_process_has_empty_status_counts() -> None:
    """Black-box sanity: a zero-task process reports a total task count of zero.

    The activation gate is ``total == 0 or done != total``; this confirms the
    ``total == 0`` precondition that drives the no-activation branch (R5.7).

    Validates: Requirements 5.7
    """
    _service, _process_repo, task_repo, _employee_repo, _audit_repo, process, _employee = (
        _build_zero_task_fixture()
    )

    counts = asyncio.run(task_repo.count_by_status(process.id))

    assert counts == {}
    assert sum(counts.values()) == 0
    assert counts.get(OnboardingTaskStatus.DONE.value, 0) == 0
