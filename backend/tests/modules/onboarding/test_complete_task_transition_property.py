"""Property-based tests for marking a pending onboarding task done.

Feature: onboarding, Property 7: Marking a pending task done transitions it and
audits the change

This module verifies that when an HR (admin) actor marks a ``pending``
onboarding task done via :meth:`OnboardingService.complete_task`, the task's
status becomes ``done`` and exactly one status-change audit entry is written
for that task carrying the acting HR identity (``user_id`` + ``actor_email``),
the task identifier, the previous status (``pending``), the new status
(``done``), and a timestamp.

The test drives the real :class:`OnboardingService` against in-memory fakes so
it executes as a fast, pure-logic check (per the design: "property tests run
against in-memory fakes or mocked repositories"). The fakes are defined inline
in this file to stay self-contained and avoid colliding with the fakes other
property-test modules define for the same service.

Validates: Requirements 4.1, 4.2, 8.1
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding.application.onboarding_service import (
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

# Printable ASCII excluding the space and '@', used to build valid names and
# email local/domain parts.
_SAFE_ALPHABET = st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="@")


# ---------------------------------------------------------------------------
# In-memory fakes (inline, self-contained)
# ---------------------------------------------------------------------------
class _NoOpSavepoint:
    """Async context manager standing in for ``session.begin_nested()``."""

    async def __aenter__(self) -> _NoOpSavepoint:
        return self

    async def __aexit__(self, *args: object) -> bool:
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
    """In-memory OnboardingProcess store supporting locking and status updates."""

    def __init__(self) -> None:
        self.processes: list[OnboardingProcess] = []

    async def get_by_id(self, process_id: UUID) -> OnboardingProcess | None:
        for process in self.processes:
            if process.id == process_id:
                return process
        return None

    async def get_for_update(self, process_id: UUID) -> OnboardingProcess | None:
        return await self.get_by_id(process_id)

    async def set_status(self, process: OnboardingProcess, status: str) -> OnboardingProcess:
        process.status = str(status)
        return process


class FakeTaskRepo:
    """In-memory OnboardingTask store mirroring the real repository's mutations."""

    def __init__(self) -> None:
        self.tasks: list[OnboardingTask] = []

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
    """In-memory append-only audit store."""

    def __init__(self) -> None:
        self.entries: list[OnboardingAuditLog] = []

    async def append(self, entry: OnboardingAuditLog) -> OnboardingAuditLog:
        self.entries.append(entry)
        return entry


class FakeEmployeeRepo:
    """In-memory Employee store; ``update`` applies field changes (e.g. is_active)."""

    def __init__(self) -> None:
        self.employees: list[Employee] = []

    async def get_by_id(self, employee_id: UUID) -> Employee | None:
        for employee in self.employees:
            if employee.id == employee_id:
                return employee
        return None

    async def update(self, employee_id: UUID, changes: dict[str, object]) -> Employee | None:
        for employee in self.employees:
            if employee.id == employee_id:
                for key, value in changes.items():
                    setattr(employee, key, value)
                return employee
        return None


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
@st.composite
def _valid_emails(draw: st.DrawFn) -> str:
    """Syntactically valid email: one '@', non-empty local and domain."""
    local = draw(st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=64))
    domain = draw(st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=64))
    return f"{local}@{domain}"


_valid_names = st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=255)


def _build_admin_actor(actor_id: UUID, actor_email: str, actor_name: str) -> User:
    """Build an HR (admin) identity user to act on the onboarding task."""
    return User(
        id=actor_id,
        email=actor_email,
        name=actor_name,
        google_sub=f"google-{actor_id}",
        role=UserRole.HR,
    )


# ---------------------------------------------------------------------------
# Async driver
# ---------------------------------------------------------------------------
async def _run_transition_property(
    candidate_id: UUID,
    task_count: int,
    target_index: int,
    actor_id: UUID,
    actor_email: str,
    actor_name: str,
) -> None:
    """Mark one pending task done and assert the transition + audit entry."""
    process_repo = FakeProcessRepo()
    task_repo = FakeTaskRepo()
    audit_repo = FakeAuditRepo()
    employee_repo = FakeEmployeeRepo()
    session = FakeSession()

    # Linked inactive employee.
    from datetime import date
    from uuid import uuid4

    manager = Employee(
        employee_code="MGR-001",
        full_name="Manager",
        email="manager@example.com",
        is_active=True,
    )
    employee_repo.employees.append(manager)

    employee = Employee(
        employee_code="NV-001",
        full_name="Onboarding Hire",
        email=f"hire-{candidate_id}@example.com",
        candidate_id=candidate_id,
        is_active=False,
        department_id=uuid4(),
        position_id=uuid4(),
        manager_id=manager.id,
        start_date=date(2026, 1, 1),
    )
    employee_repo.employees.append(employee)

    # In-progress process with `task_count` pending tasks from the template.
    process = OnboardingProcess(
        candidate_id=candidate_id,
        employee_id=employee.id,
        status=OnboardingStatus.IN_PROGRESS.value,
    )
    process_repo.processes.append(process)

    for order_index, task_key, name in CHECKLIST_TEMPLATE[:task_count]:
        task_repo.tasks.append(
            OnboardingTask(
                process_id=process.id,
                task_key=task_key.value,
                name=name,
                status=OnboardingTaskStatus.PENDING.value,
                order_index=order_index,
            )
        )

    target_task = task_repo.tasks[target_index % task_count]
    target_task_id = target_task.id

    actor = _build_admin_actor(actor_id, actor_email, actor_name)

    service = OnboardingService(
        process_repo=process_repo,  # type: ignore[arg-type]
        task_repo=task_repo,  # type: ignore[arg-type]
        audit_repo=audit_repo,  # type: ignore[arg-type]
        employee_repo=employee_repo,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )

    returned_task = await service.complete_task(target_task_id, actor)

    # 1. The targeted task transitions to `done`.
    assert returned_task.id == target_task_id
    assert returned_task.status == OnboardingTaskStatus.DONE.value
    assert target_task.status == OnboardingTaskStatus.DONE.value

    # 2. Exactly one status-change (task_completed) audit entry exists for this
    #    task id, carrying the acting HR identity, previous/new status, and a
    #    timestamp. (When task_count == 1 the process also fully completes and an
    #    employee_activated entry is written; we isolate Property 7 by counting
    #    only task_completed entries for the target task.)
    status_change_entries = [
        entry
        for entry in audit_repo.entries
        if entry.operation_type == _OP_TASK_COMPLETED and entry.entity_id == target_task_id
    ]
    assert len(status_change_entries) == 1

    entry = status_change_entries[0]
    assert entry.user_id == actor.id
    assert entry.actor_email == actor.email
    assert entry.entity_type == "task"
    assert entry.previous_value == {"status": OnboardingTaskStatus.PENDING.value}
    assert entry.new_value == {"status": OnboardingTaskStatus.DONE.value}
    assert entry.created_at is not None


# Feature: onboarding, Property 7: Marking a pending task done transitions it
# and audits the change
@settings(max_examples=150, deadline=None)
@given(
    candidate_id=st.uuids(),
    task_count=st.integers(min_value=1, max_value=len(CHECKLIST_TEMPLATE)),
    target_index=st.integers(min_value=0, max_value=len(CHECKLIST_TEMPLATE) - 1),
    actor_id=st.uuids(),
    actor_email=_valid_emails(),
    actor_name=_valid_names,
)
def test_marking_pending_task_done_transitions_and_audits(
    candidate_id: UUID,
    task_count: int,
    target_index: int,
    actor_id: UUID,
    actor_email: str,
    actor_name: str,
) -> None:
    """A pending task marked done becomes done and writes one status audit entry.

    Validates: Requirements 4.1, 4.2, 8.1
    """
    asyncio.run(
        _run_transition_property(
            candidate_id,
            task_count,
            target_index,
            actor_id,
            actor_email,
            actor_name,
        )
    )
