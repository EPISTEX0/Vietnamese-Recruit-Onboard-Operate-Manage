"""Property-based test for rejecting invalid task status values.

Feature: onboarding, Property 12: Invalid task status values are rejected
without changing state

This test drives ``OnboardingService.complete_task`` with a requested ``status``
value that is *not* exactly ``pending`` or ``done`` and asserts the request is
rejected with :class:`InvalidTaskStatusError` that identifies the offending
value, while the target task's current status (``pending``) is left unchanged.
Per the service contract the status validity check (R3.5) runs *before* any
state-changing work: no task ``set_status`` is performed, no audit entry is
appended, and the session is never committed.

The checks are fast, pure-logic checks against in-memory fakes defined inline in
this module (no shared conftest / fakes module, to avoid collisions with the
other onboarding property-test modules). The fakes additionally record whether
the mutating paths (``set_status`` / ``append`` / ``commit``) were ever invoked,
so the "no state change" guarantee is asserted directly rather than inferred.

Validates: Requirements 3.3, 3.5
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding.application.onboarding_service import OnboardingService
from src.modules.onboarding.domain.entities import (
    OnboardingAuditLog,
    OnboardingProcess,
    OnboardingTask,
)
from src.modules.onboarding.domain.enums import (
    OnboardingTaskKey,
    OnboardingTaskStatus,
)
from src.modules.onboarding.domain.exceptions import InvalidTaskStatusError

# The two — and only two — defined task status values (R3.3). Any requested
# status outside this set must be rejected (R3.5).
_VALID_STATUSES = {OnboardingTaskStatus.PENDING.value, OnboardingTaskStatus.DONE.value}

# Obvious invalid status values worth always exercising: the empty string, wrong
# casing of the valid values, near-miss words, and a different domain's status.
_OBVIOUS_INVALID = [
    "",
    "DONE",
    "Done",
    "PENDING",
    "Pending",
    "complete",
    "in_progress",
    "todo",
    "cancelled",
    "done ",
    " pending",
    "pending\n",
    "0",
    "null",
    "None",
]


# ---------------------------------------------------------------------------
# In-memory fakes (inline, module-private — no shared fixtures)
# ---------------------------------------------------------------------------
class _FakeNestedTransaction:
    """No-op async context manager standing in for a SAVEPOINT."""

    async def __aenter__(self) -> _FakeNestedTransaction:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakeSession:
    """Minimal async session recording commit/rollback invocations.

    For the invalid-status path neither ``commit`` nor ``rollback`` should ever
    be reached, so both counters must stay at zero.
    """

    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction()


class FakeTaskRepo:
    """Stores one task; tracks whether ``set_status`` was ever called.

    ``get_by_id`` returns the stored task (so the existence check would pass if
    it were reached). ``set_status`` mutates the task and flips
    ``set_status_called`` — but for an invalid requested status it must never be
    invoked because validation rejects the request first.
    """

    def __init__(self, task: OnboardingTask) -> None:
        self._task = task
        self.set_status_called = False

    async def get_by_id(self, task_id: UUID) -> OnboardingTask | None:
        if self._task.id == task_id:
            return self._task
        return None

    async def set_status(
        self,
        task: OnboardingTask,
        status: OnboardingTaskStatus,
        *,
        completed_at: datetime | None = None,
        completed_by_user_id: UUID | None = None,
    ) -> OnboardingTask:
        self.set_status_called = True
        task.status = status.value
        task.completed_at = completed_at
        task.completed_by_user_id = completed_by_user_id
        return task

    async def count_by_status(self, process_id: UUID) -> dict[str, int]:
        return {self._task.status: 1}


class FakeProcessRepo:
    """Stores one process; tracks whether mutating paths were called."""

    def __init__(self, process: OnboardingProcess) -> None:
        self._process = process
        self.get_for_update_called = False
        self.set_status_called = False

    async def get_for_update(self, process_id: UUID) -> OnboardingProcess | None:
        self.get_for_update_called = True
        if self._process.id == process_id:
            return self._process
        return None

    async def set_status(self, process: OnboardingProcess, status: object) -> OnboardingProcess:
        self.set_status_called = True
        return process


class FakeAuditRepo:
    """Append-only audit sink; records every appended entry."""

    def __init__(self) -> None:
        self.entries: list[OnboardingAuditLog] = []

    async def append(self, entry: OnboardingAuditLog) -> OnboardingAuditLog:
        self.entries.append(entry)
        return entry


class FakeEmployeeRepo:
    """Reused-employee-repo stand-in; activation must never reach it."""

    def __init__(self) -> None:
        self.update_called = False

    async def update(self, employee_id: UUID, values: dict[str, object]) -> None:
        self.update_called = True
        return None


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _admin_actor() -> User:
    """Build an admin (HR) actor so authorization would pass if reached."""
    return User(
        email="hr.admin@example.com",
        name="HR Admin",
        role=UserRole.HR,
    )


def _pending_task(process_id: UUID) -> OnboardingTask:
    """Build a stored ``pending`` task belonging to ``process_id``."""
    return OnboardingTask(
        process_id=process_id,
        task_key=OnboardingTaskKey.SIGN_CONTRACT.value,
        name="Sign Contract",
        status=OnboardingTaskStatus.PENDING.value,
        order_index=0,
    )


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
def _invalid_statuses() -> st.SearchStrategy[str]:
    """Status strings that are not exactly ``pending`` or ``done``."""
    return st.one_of(
        st.sampled_from(_OBVIOUS_INVALID),
        st.text().filter(lambda value: value not in _VALID_STATUSES),
    )


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Result:
    """The artefacts captured after rejecting an invalid-status request."""

    error: InvalidTaskStatusError
    task: OnboardingTask
    task_repo: FakeTaskRepo
    process_repo: FakeProcessRepo
    audit_repo: FakeAuditRepo
    employee_repo: FakeEmployeeRepo
    session: FakeSession


async def _complete_with_invalid_status(invalid_status: str) -> _Result:
    """Invoke ``complete_task`` with an invalid status and capture the result."""
    process = OnboardingProcess(candidate_id=uuid4(), employee_id=uuid4())
    task = _pending_task(process.id)

    task_repo = FakeTaskRepo(task)
    process_repo = FakeProcessRepo(process)
    audit_repo = FakeAuditRepo()
    employee_repo = FakeEmployeeRepo()
    session = FakeSession()
    service = OnboardingService(
        process_repo=process_repo,  # type: ignore[arg-type]
        task_repo=task_repo,  # type: ignore[arg-type]
        audit_repo=audit_repo,  # type: ignore[arg-type]
        employee_repo=employee_repo,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(InvalidTaskStatusError) as exc_info:
        await service.complete_task(task.id, _admin_actor(), status=invalid_status)

    return _Result(
        error=exc_info.value,
        task=task,
        task_repo=task_repo,
        process_repo=process_repo,
        audit_repo=audit_repo,
        employee_repo=employee_repo,
        session=session,
    )


# Feature: onboarding, Property 12: Invalid task status values are rejected
# without changing state
@settings(max_examples=200, deadline=None)
@given(invalid_status=_invalid_statuses())
def test_invalid_task_status_is_rejected_without_changing_state(invalid_status: str) -> None:
    """An invalid status is rejected naming the value and changes no state.

    Validates: Requirements 3.3, 3.5
    """
    # The generated status is genuinely outside the two defined values.
    assert invalid_status not in _VALID_STATUSES

    result = asyncio.run(_complete_with_invalid_status(invalid_status))

    # The error identifies the offending value (both on the attribute and in the
    # human-readable message, R3.5).
    assert result.error.value == invalid_status
    assert invalid_status in result.error.message

    # The target task's current status is left unchanged (still pending).
    assert result.task.status == OnboardingTaskStatus.PENDING.value
    assert result.task.completed_at is None
    assert result.task.completed_by_user_id is None

    # No state-changing work happened: no task/process mutation, no audit entry,
    # no activation, and the transaction was neither committed nor rolled back.
    assert result.task_repo.set_status_called is False
    assert result.process_repo.get_for_update_called is False
    assert result.process_repo.set_status_called is False
    assert result.audit_repo.entries == []
    assert result.employee_repo.update_called is False
    assert result.session.commit_count == 0
    assert result.session.rollback_count == 0
