"""Property-based test for the employee-activation invariant.

Feature: onboarding, Property 8: Employee activation reflects checklist
completion exactly

This test drives ``OnboardingService.complete_task`` over arbitrary sequences
of valid task-completion actions against a process that holds 0..5 pending
tasks, and asserts the exact activation invariant:

  * IF the process has at least one task AND every task is ``done``, THEN the
    process status is ``complete`` and the linked employee ``is_active`` is
    ``True``.
  * OTHERWISE the process stays ``in_progress`` and the employee ``is_active``
    remains ``False``.
  * A process with ZERO tasks can never have a task completed, so it never
    activates its employee (it stays ``in_progress`` / inactive).

The checks are fast, pure-logic checks against in-memory fakes defined inline in
this module (no shared conftest / fakes module, to avoid collisions with the
other onboarding property-test modules). The fakes faithfully model the
repository and session contracts ``complete_task`` depends on: task lookup /
status mutation / status counting, process row locking / status mutation,
employee activation, append-only audit, and no-op commit / rollback /
``begin_nested``.

Validates: Requirements 2.5, 5.1, 5.2, 5.3, 5.5, 5.7
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding.application.onboarding_service import OnboardingService
from src.modules.onboarding.domain.entities import (
    OnboardingAuditLog,
    OnboardingProcess,
    OnboardingTask,
)
from src.modules.onboarding.domain.enums import (
    OnboardingStatus,
    OnboardingTaskStatus,
)

# Upper bound on the number of tasks generated per process. Includes 0 so the
# zero-task case (which can never activate) is exercised, and goes beyond the
# canonical four-task checklist to stress arbitrary task counts.
_MAX_TASKS = 5

# Printable ASCII excluding the space and '@' so generated names/emails are
# non-empty after trimming and emails carry exactly one '@'.
_SAFE_ALPHABET = st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="@")


# ---------------------------------------------------------------------------
# In-memory fakes (inline, module-private — no shared fixtures)
# ---------------------------------------------------------------------------
class _FakeNestedTransaction:
    """No-op async context manager standing in for a SAVEPOINT.

    Mirrors the object returned by ``AsyncSession.begin_nested()`` so any
    ``async with self.session.begin_nested():`` block enters and exits cleanly
    without a real database savepoint.
    """

    async def __aenter__(self) -> _FakeNestedTransaction:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakeSession:
    """Minimal async session: no-op commit/rollback and nested transactions."""

    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction()


class FakeProcessRepo:
    """Stores the process; ``get_for_update`` / ``get_by_id`` return it.

    ``set_status`` mutates the stored process exactly as the real repository
    does: it updates ``status`` and ``updated_at`` and stamps ``completed_at``
    when the process transitions to ``complete``.
    """

    def __init__(self, process: OnboardingProcess) -> None:
        self.process = process

    async def get_by_id(self, process_id: UUID) -> OnboardingProcess | None:
        return self.process if self.process.id == process_id else None

    async def get_for_update(self, process_id: UUID) -> OnboardingProcess | None:
        return self.process if self.process.id == process_id else None

    async def set_status(self, process: OnboardingProcess, status: str) -> OnboardingProcess:
        now = datetime.now(UTC)
        process.status = status
        process.updated_at = now
        if status == OnboardingStatus.COMPLETE:
            process.completed_at = now
        return process


class FakeTaskRepo:
    """Stores the checklist tasks and faithfully models their mutations.

    ``get_by_id`` looks up a stored task, ``set_status`` mutates the stored
    task's status / completion metadata, ``count_by_status`` counts the stored
    tasks for a process grouped by status (a zero-task process yields an empty
    mapping), and ``list_by_process`` returns the tasks ordered by their
    checklist position.
    """

    def __init__(self, tasks: list[OnboardingTask]) -> None:
        self.tasks = tasks

    async def get_by_id(self, task_id: UUID) -> OnboardingTask | None:
        for task in self.tasks:
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

    async def list_by_process(self, process_id: UUID) -> list[OnboardingTask]:
        return sorted(
            (task for task in self.tasks if task.process_id == process_id),
            key=lambda task: task.order_index,
        )


class FakeEmployeeRepo:
    """Stores the linked employee; ``update`` applies fields and returns it."""

    def __init__(self, employee: Employee) -> None:
        self.employee = employee
        self.employees: list[Employee] = [employee]

    async def get_by_id(self, employee_id: UUID) -> Employee | None:
        for e in self.employees:
            if e.id == employee_id:
                return e
        return None

    async def update(self, employee_id: UUID, fields: dict[str, object]) -> Employee | None:
        if self.employee.id != employee_id:
            return None
        for key, value in fields.items():
            setattr(self.employee, key, value)
        return self.employee


class FakeAuditRepo:
    """Append-only audit sink collecting entries in a list."""

    def __init__(self) -> None:
        self.entries: list[OnboardingAuditLog] = []

    async def append(self, entry: OnboardingAuditLog) -> OnboardingAuditLog:
        self.entries.append(entry)
        return entry


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _admin_actor() -> User:
    """Build an admin identity authorized to complete onboarding tasks."""
    return User(
        email="hr-admin@example.com",
        name="HR Admin",
        google_sub="google-sub-hr-admin",
        role=UserRole.HR,
    )


def _build_world(
    candidate_id: UUID,
    full_name: str,
    email: str,
    task_count: int,
) -> tuple[
    OnboardingService,
    OnboardingProcess,
    Employee,
    list[OnboardingTask],
    FakeSession,
]:
    """Set up an in-progress process, inactive employee, and pending tasks."""
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
        full_name=full_name,
        email=email,
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
            task_key=f"task_{index}",
            name=f"Task {index}",
            status=OnboardingTaskStatus.PENDING.value,
            order_index=index,
        )
        for index in range(task_count)
    ]

    session = FakeSession()
    employee_repo = FakeEmployeeRepo(employee)
    employee_repo.employees.append(manager)
    service = OnboardingService(
        process_repo=FakeProcessRepo(process),  # type: ignore[arg-type]
        task_repo=FakeTaskRepo(tasks),  # type: ignore[arg-type]
        audit_repo=FakeAuditRepo(),  # type: ignore[arg-type]
        employee_repo=employee_repo,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )
    return service, process, employee, tasks, session


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[int, list[int]]:
    """Draw a task count (0..5) and a subset/order of task indices to complete.

    The selected indices are unique (each task is marked done at most once,
    since re-completing a ``done`` task is a no-op) and appear in an arbitrary
    order to exercise different completion sequences. A zero-task process
    yields an empty selection.
    """
    task_count = draw(st.integers(min_value=0, max_value=_MAX_TASKS))
    if task_count == 0:
        return 0, []
    selected = draw(
        st.lists(
            st.integers(min_value=0, max_value=task_count - 1),
            unique=True,
            max_size=task_count,
        )
    )
    return task_count, selected


def _full_names() -> st.SearchStrategy[str]:
    """Full names of 1-255 non-whitespace characters (R2.2 bounds)."""
    return st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=255)


@st.composite
def _emails(draw: st.DrawFn) -> str:
    """Syntactically valid emails: one '@', non-empty local/domain, <=320."""
    local = draw(st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=64))
    domain = draw(st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=64))
    return f"{local}@{domain}"


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------
async def _complete_sequence(
    candidate_id: UUID,
    full_name: str,
    email: str,
    task_count: int,
    selected: list[int],
) -> tuple[OnboardingProcess, Employee, int, int]:
    """Run the completion sequence and return the final process/employee state."""
    service, process, employee, tasks, _session = _build_world(
        candidate_id, full_name, email, task_count
    )
    actor = _admin_actor()

    for index in selected:
        await service.complete_task(tasks[index].id, actor)

    total = task_count
    done_count = len(selected)
    return process, employee, total, done_count


# Feature: onboarding, Property 8: Employee activation reflects checklist
# completion exactly
@settings(max_examples=200, deadline=None)
@given(
    candidate_id=st.uuids(),
    full_name=_full_names(),
    email=_emails(),
    scenario=_scenarios(),
)
def test_employee_activation_reflects_checklist_completion_exactly(
    candidate_id: UUID,
    full_name: str,
    email: str,
    scenario: tuple[int, list[int]],
) -> None:
    """Activation happens iff the process has tasks and all of them are done.

    Validates: Requirements 2.5, 5.1, 5.2, 5.3, 5.5, 5.7
    """
    task_count, selected = scenario

    process, employee, total, done_count = asyncio.run(
        _complete_sequence(candidate_id, full_name, email, task_count, selected)
    )

    if total > 0 and done_count == total:
        # Every task in a non-empty checklist is done: the process completes and
        # the linked employee is activated within the same transaction.
        assert process.status == OnboardingStatus.COMPLETE.value
        assert employee.is_active is True
    else:
        # Either no tasks at all (zero-task process never activates) or at least
        # one task is still pending: the process stays in progress and the
        # employee remains inactive.
        assert process.status == OnboardingStatus.IN_PROGRESS.value
        assert employee.is_active is False
