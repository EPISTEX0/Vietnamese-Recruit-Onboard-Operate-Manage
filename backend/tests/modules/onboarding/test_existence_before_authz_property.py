"""Property-based test for existence-before-authorization ordering.

Feature: onboarding, Property 13: Task existence is evaluated before authorization

This module verifies that :meth:`OnboardingService.complete_task` evaluates task
existence *before* requester authorization (R4.4). For any task identifier that
does not correspond to an existing task, a mark-done request must return a
not-found error (``OnboardingTaskNotFoundError``) — even when the requesting
actor is **not** an admin — rather than the authorization error
(``OnboardingAuthorizationError``). Because the task is resolved before the role
check, the precise not-found signal is what the caller receives, and no task
status changes (no audit entry is written and the transaction is never
committed).

The test drives the real :class:`OnboardingService` against in-memory fakes so
it executes as a fast, pure-logic check (per the design: "property tests run
against in-memory fakes or mocked repositories"). The fakes are defined inline
in this file to stay self-contained and avoid colliding with parallel agents.

Validates: Requirements 4.4
"""

from __future__ import annotations

import asyncio
from uuid import UUID

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
from src.modules.onboarding.domain.exceptions import (
    OnboardingAuthorizationError,
    OnboardingTaskNotFoundError,
)

# Every role defined in the identity module that is NOT the admin role. The
# property must hold for any non-admin actor; Hypothesis varies the actor's
# role across this set so the existence check is proven to precede the role
# check regardless of which non-admin role makes the request.
_NON_ADMIN_ROLES = [role for role in UserRole if role != UserRole.HR]


# ---------------------------------------------------------------------------
# In-memory fakes (inline, self-contained)
# ---------------------------------------------------------------------------
class _NoOpSavepoint:
    """Async context manager standing in for ``session.begin_nested()``.

    Not exercised by this property (the not-found check returns before any
    nested transaction), but provided so the fake session matches the shape the
    service may use.
    """

    async def __aenter__(self) -> _NoOpSavepoint:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeSession:
    """Minimal async session: counts commit/rollback, no-op SAVEPOINT.

    For this property neither ``commit`` nor ``rollback`` should ever be called,
    because ``complete_task`` raises ``OnboardingTaskNotFoundError`` before
    reaching the transactional state-change block.
    """

    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    def begin_nested(self) -> _NoOpSavepoint:
        return _NoOpSavepoint()


class FakeTaskRepo:
    """Task repository whose ``get_by_id`` ALWAYS reports the task missing.

    Models the "task does not exist" precondition: every lookup returns None,
    so ``complete_task`` must take the not-found path.
    """

    def __init__(self) -> None:
        self.get_by_id_calls: list[UUID] = []
        self.set_status_calls = 0

    async def get_by_id(self, task_id: UUID) -> OnboardingTask | None:
        self.get_by_id_calls.append(task_id)
        return None

    async def set_status(self, *args: object, **kwargs: object) -> OnboardingTask:
        # The not-found path must never reach a status mutation.
        self.set_status_calls += 1
        raise AssertionError("set_status must not be called for a non-existent task")

    async def count_by_status(self, process_id: UUID) -> dict[str, int]:
        raise AssertionError("count_by_status must not be called for a non-existent task")


class FakeProcessRepo:
    """Process repository that must not be consulted on the not-found path."""

    def __init__(self) -> None:
        self.processes: list[OnboardingProcess] = []

    async def get_for_update(self, process_id: UUID) -> OnboardingProcess | None:
        raise AssertionError("get_for_update must not be called for a non-existent task")

    async def get_by_id(self, process_id: UUID) -> OnboardingProcess | None:
        raise AssertionError("get_by_id must not be called for a non-existent task")

    async def set_status(self, *args: object, **kwargs: object) -> OnboardingProcess:
        raise AssertionError("set_status must not be called for a non-existent task")


class FakeAuditRepo:
    """Append-only audit store; ``append`` must never be called here."""

    def __init__(self) -> None:
        self.entries: list[OnboardingAuditLog] = []

    async def append(self, entry: OnboardingAuditLog) -> OnboardingAuditLog:
        self.entries.append(entry)
        return entry


class FakeEmployeeRepo:
    """Employee repository that must not be touched on the not-found path."""

    def __init__(self) -> None:
        self.employees: list[Employee] = []

    async def update(self, employee_id: UUID, values: dict[str, object]) -> Employee | None:
        raise AssertionError("update must not be called for a non-existent task")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
def _build_non_admin_actor(role: UserRole, email_suffix: str) -> User:
    """Construct an authenticated non-admin user with the given role."""
    return User(
        email=f"user-{email_suffix}@example.com",
        name="Non Admin Actor",
        role=role,
    )


# ---------------------------------------------------------------------------
# Async driver
# ---------------------------------------------------------------------------
async def _run_existence_before_authz(task_id: UUID, role: UserRole, suffix: str) -> None:
    """Assert a non-existent task yields not-found even for a non-admin actor."""
    task_repo = FakeTaskRepo()
    process_repo = FakeProcessRepo()
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
    actor = _build_non_admin_actor(role, suffix)

    # The actor is explicitly NOT an admin, yet the result must be the
    # not-found error (existence precedes authorization), NOT the auth error.
    with pytest.raises(OnboardingTaskNotFoundError) as exc_info:
        await service.complete_task(task_id, actor, status="done")

    # Specifically the not-found error, not the authorization error.
    assert not isinstance(exc_info.value, OnboardingAuthorizationError)
    # The task was actually looked up (existence check happened).
    assert task_repo.get_by_id_calls == [task_id]
    # No task status change occurred: no audit appended, nothing committed.
    assert audit_repo.entries == []
    assert task_repo.set_status_calls == 0
    assert session.commit_count == 0


# Feature: onboarding, Property 13: Task existence is evaluated before authorization
@settings(max_examples=200, deadline=None)
@given(
    task_id=st.uuids(),
    role=st.sampled_from(_NON_ADMIN_ROLES),
    suffix=st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=12,
    ),
)
def test_task_existence_is_evaluated_before_authorization(
    task_id: UUID,
    role: UserRole,
    suffix: str,
) -> None:
    """A non-existent task id returns not-found even for non-admin actors.

    Validates: Requirements 4.4
    """
    asyncio.run(_run_existence_before_authz(task_id, role, suffix))


def test_non_admin_roles_strategy_excludes_admin() -> None:
    """Guard: the non-admin role set is non-empty and never includes admin."""
    assert _NON_ADMIN_ROLES, "expected at least one non-admin role to vary over"
    assert UserRole.HR not in _NON_ADMIN_ROLES
