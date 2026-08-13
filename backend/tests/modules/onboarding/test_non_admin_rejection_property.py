"""Property-based test that non-admin actors cannot change onboarding state.

Feature: onboarding, Property 14: Non-admin actors cannot change onboarding
state

This module drives ``OnboardingService.complete_task`` for any authenticated
actor whose role is **not** ``admin`` acting on an EXISTING ``pending`` task and
asserts the request is rejected with an authorization error
(:class:`OnboardingAuthorizationError`) and that *no* onboarding state changes:
the task status stays ``pending``, the process status stays ``in_progress``, the
linked employee stays ``is_active = False``, no audit entry is appended, and the
transaction is never committed.

The service performs its checks in a mandated order — status validity (R3.5) →
task existence (R4.4) → authorization (R4.5) — so this test sets up a fully
valid, existing ``pending`` task and a non-admin actor: existence succeeds, then
authorization must reject *before* any mutating repository call. The fakes are
defined inline in this module (no shared conftest / fakes module, to avoid
collisions with the other onboarding property-test modules) and record whether
their mutating methods were invoked so the test can assert none of them ran.

Validates: Requirements 4.5
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID, uuid4

import pytest
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
from src.modules.onboarding.domain.exceptions import OnboardingAuthorizationError

# Every defined role that is NOT ``admin``. Built dynamically so the property
# automatically covers any future non-admin role; Hypothesis varies across it.
_NON_ADMIN_ROLES = [role for role in UserRole if role != UserRole.HR]

# Printable ASCII excluding the space and '@' so generated names/emails are
# non-empty and emails carry exactly one '@'.
_SAFE_ALPHABET = st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="@")


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

    The authorization rejection happens before any transaction work, so both
    counters must remain zero for a non-admin actor.
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
    """Returns an existing ``pending`` task; flags any mutating call.

    ``get_by_id`` returns the pre-seeded existing task so the existence check
    passes. ``set_status``, ``get_for_update`` analogue, and ``count_by_status``
    must never be reached once authorization rejects, so they raise if called.
    """

    def __init__(self, task: OnboardingTask) -> None:
        self._task = task
        self.set_status_called = False
        self.count_by_status_called = False

    async def get_by_id(self, task_id: UUID) -> OnboardingTask | None:
        if task_id == self._task.id:
            return self._task
        return None

    async def set_status(
        self,
        task: OnboardingTask,
        status: OnboardingTaskStatus,
        completed_at: datetime | None = None,
        completed_by_user_id: UUID | None = None,
    ) -> OnboardingTask:
        self.set_status_called = True
        raise AssertionError("set_status must not be called for a non-admin actor")

    async def count_by_status(self, process_id: UUID) -> dict[str, int]:
        self.count_by_status_called = True
        raise AssertionError("count_by_status must not be called for a non-admin actor")


class FakeProcessRepo:
    """Provides the locked-row lookup; flags any invocation.

    ``get_for_update`` / ``set_status`` must never be reached for a non-admin
    actor (the row is never locked, the process never changes), so they raise.
    """

    def __init__(self, process: OnboardingProcess) -> None:
        self._process = process
        self.get_for_update_called = False
        self.set_status_called = False

    async def get_for_update(self, process_id: UUID) -> OnboardingProcess | None:
        self.get_for_update_called = True
        raise AssertionError("get_for_update must not be called for a non-admin actor")

    async def set_status(self, process: OnboardingProcess, status: str) -> OnboardingProcess:
        self.set_status_called = True
        raise AssertionError("process set_status must not be called for a non-admin actor")


class FakeAuditRepo:
    """Append-only audit sink; flags any append (none must happen)."""

    def __init__(self) -> None:
        self.entries: list[OnboardingAuditLog] = []

    async def append(self, entry: OnboardingAuditLog) -> OnboardingAuditLog:
        self.entries.append(entry)
        raise AssertionError("audit append must not be called for a non-admin actor")


class FakeEmployeeRepo:
    """Employee repo whose ``update`` must never run for a non-admin actor."""

    def __init__(self) -> None:
        self.update_called = False

    async def update(self, employee_id: UUID, values: dict[str, object]) -> Employee | None:
        self.update_called = True
        raise AssertionError("employee update must not be called for a non-admin actor")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
def _non_empty_text(max_size: int) -> st.SearchStrategy[str]:
    """Non-empty printable ASCII text without spaces or ``@``."""
    return st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=max_size)


@st.composite
def _emails(draw: st.DrawFn) -> str:
    """Syntactically valid emails: one '@', non-empty local/domain."""
    local = draw(_non_empty_text(32))
    domain = draw(_non_empty_text(32))
    return f"{local}@{domain}"


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------
async def _run_non_admin_rejection(
    actor: User,
) -> tuple[
    OnboardingTask,
    OnboardingProcess,
    Employee,
    FakeAuditRepo,
    FakeSession,
    FakeTaskRepo,
    FakeProcessRepo,
    FakeEmployeeRepo,
]:
    """Set up an existing pending task and attempt completion by a non-admin."""
    candidate_id = uuid4()
    employee = Employee(
        employee_code="NV-001",
        full_name="Inactive Person",
        email="inactive@example.com",
        candidate_id=candidate_id,
        is_active=False,
    )
    process = OnboardingProcess(
        candidate_id=candidate_id,
        employee_id=employee.id,
        status=OnboardingStatus.IN_PROGRESS.value,
    )
    task = OnboardingTask(
        process_id=process.id,
        task_key="sign_contract",
        name="Sign Contract",
        status=OnboardingTaskStatus.PENDING.value,
        order_index=0,
    )

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

    with pytest.raises(OnboardingAuthorizationError):
        await service.complete_task(task.id, actor, status=OnboardingTaskStatus.DONE.value)

    return (
        task,
        process,
        employee,
        audit_repo,
        session,
        task_repo,
        process_repo,
        employee_repo,
    )


# Feature: onboarding, Property 14: Non-admin actors cannot change onboarding
# state
@settings(max_examples=200, deadline=None)
@given(
    role=st.sampled_from(_NON_ADMIN_ROLES),
    email=_emails(),
    name=_non_empty_text(255),
    google_sub=_non_empty_text(64),
)
def test_non_admin_actor_cannot_change_onboarding_state(
    role: UserRole,
    email: str,
    name: str,
    google_sub: str,
) -> None:
    """A non-admin actor on an existing pending task is rejected with no change.

    Validates: Requirements 4.5
    """
    # Sanity: the generated role is genuinely not admin.
    assert role != UserRole.HR

    actor = User(email=email, name=name, google_sub=google_sub, role=role)

    (
        task,
        process,
        employee,
        audit_repo,
        session,
        task_repo,
        process_repo,
        employee_repo,
    ) = asyncio.run(_run_non_admin_rejection(actor))

    # No task status change: still pending.
    assert task.status == OnboardingTaskStatus.PENDING.value
    # No process state change: still in_progress.
    assert process.status == OnboardingStatus.IN_PROGRESS.value
    # No employee activation: still inactive.
    assert employee.is_active is False

    # No audit entry was appended.
    assert audit_repo.entries == []
    # The transaction was never committed (nor rolled back — nothing started).
    assert session.commit_count == 0
    assert session.rollback_count == 0

    # No mutating repository path was reached.
    assert task_repo.set_status_called is False
    assert task_repo.count_by_status_called is False
    assert process_repo.get_for_update_called is False
    assert process_repo.set_status_called is False
    assert employee_repo.update_called is False
