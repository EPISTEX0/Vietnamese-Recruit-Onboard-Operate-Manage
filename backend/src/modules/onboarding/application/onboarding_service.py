"""Application service for the Onboarding module.

Hosts :class:`OnboardingService`, the application service that drives an
accepted Candidate through onboarding. This file implements the
event-consumption creation flow (``start_from_event``) and the HR-facing task
completion + automatic activation flow (``complete_task``); the read models are
added by later tasks to the same class.

Transaction ownership
----------------------
The repositories used here (``OnboardingProcessRepository``,
``OnboardingTaskRepository``, ``OnboardingAuditRepository`` and the reused
``EmployeeRepository``) ``add``/``flush`` but never ``commit``. This service
owns the transaction boundary: ``start_from_event`` performs the whole
new-process creation (employee + process + checklist + creation audit) in a
single transaction and commits once, rolling back on any failure so no partial
employee or process is ever persisted (R1.5). The duplicate-detection path
writes its audit entry in its own committed transaction and returns the
existing process unchanged (R1.3, R1.4, R2.7). ``complete_task`` locks the
process row, marks the task ``done``, writes the status-change audit, and (when
all tasks are done) completes the process and activates the employee with an
activation audit entry — all in one transaction committed once, rolling back
and raising on any failure (R5.5, R5.6, R8.2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.employee.domain.entities import Employee
from src.modules.employee.infrastructure.employee_repository import EmployeeRepository
from src.modules.identity.domain.entities import User, UserRole
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
from src.modules.onboarding.domain.exceptions import (
    AuditWriteError,
    InvalidTaskStatusError,
    OnboardingActivationError,
    OnboardingAuthorizationError,
    OnboardingError,
    OnboardingProcessAlreadyCompletedError,
    OnboardingProcessNotFoundError,
    OnboardingTaskNotFoundError,
)
from src.modules.onboarding.infrastructure.audit_repository import OnboardingAuditRepository
from src.modules.onboarding.infrastructure.process_repository import OnboardingProcessRepository
from src.modules.onboarding.infrastructure.task_repository import OnboardingTaskRepository

logger = logging.getLogger(__name__)

# Number of attempts to allocate a unique ``NV-XXX`` employee code before
# giving up. ``EmployeeRepository.get_next_code`` derives the next code from the
# current MAX code; under a race two concurrent creations can derive the same
# code and one INSERT loses on the ``employee_code`` unique constraint. Each
# retry re-derives the code (the committed colliding row is now visible under
# READ COMMITTED), so a small number of attempts is sufficient in practice.
_EMPLOYEE_CODE_MAX_ATTEMPTS = 5

# Audit operation types written by the creation flow.
_OP_PROCESS_CREATED = "process_created"
_OP_DUPLICATE_DETECTED = "duplicate_detected"

# Audit operation types written by the completion / activation flow.
_OP_TASK_COMPLETED = "task_completed"
_OP_EMPLOYEE_ACTIVATED = "employee_activated"

# Maximum number of OnboardingProcess records returned per list response (the
# pagination cap, R6.2). Mirrors ``OnboardingSettings.list_page_size_max`` /
# the design's ``ONBOARDING_LIST_PAGE_SIZE_MAX``; kept as a module constant so
# the read model enforces the cap without changing the service constructor.
_LIST_PAGE_SIZE_MAX = 50


@dataclass(frozen=True)
class ProcessListItem:
    """A single onboarding process entry in the paginated list read model.

    Projects one ``OnboardingProcess`` together with its checklist progress for
    the list endpoint (R6.1). ``completed_count`` is the number of ``done``
    tasks and ``total_count`` is the total number of tasks in the process's
    checklist.

    Attributes:
        process_id: The OnboardingProcess identifier.
        status: The process status (``in_progress`` or ``complete``).
        employee_id: The linked Employee record identifier.
        employee_full_name: The Employee's display name.
        employee_email: The Employee's email address.
        employee_code: The Employee code, if assigned.
        completed_count: Number of tasks with status ``done``.
        total_count: Total number of tasks in the checklist.
        missing_setup_fields: List of missing setup field names.
    """

    process_id: UUID
    status: str
    employee_id: UUID
    employee_full_name: str
    employee_email: str
    employee_code: str | None
    completed_count: int
    total_count: int
    missing_setup_fields: list[str]


@dataclass(frozen=True)
class PaginatedProcesses:
    """A page of onboarding process list items plus pagination metadata.

    ``items`` holds at most ``page_size`` (capped at 50, R6.2) list items for
    the requested page, while ``total`` is the true count of processes matching
    the request regardless of pagination, so callers can report accurate totals
    even when a page is empty (an empty ``items`` list with ``total == 0`` when
    nothing matches).

    Attributes:
        items: The list items for the requested page (length ``<= page_size``).
        total: The true count of matching processes (ignoring pagination).
        page: The 1-indexed page number that was requested.
        page_size: The effective page size applied (capped at 50).
    """

    items: list[ProcessListItem]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class ProcessTaskDetail:
    """A single task projected for the process detail read model.

    Attributes:
        id: The OnboardingTask identifier.
        name: The task's human-readable display name.
        status: The task status (``pending`` or ``done``).
        order_index: The task's position in the fixed checklist (0-based).
    """

    id: UUID
    name: str
    status: str
    order_index: int
    completed_at: str | None = None
    completed_by_name: str | None = None


@dataclass(frozen=True)
class ProcessDetail:
    """Full detail of one onboarding process including its checklist.

    Exposes the same summary fields as :class:`ProcessListItem` plus the
    ordered list of tasks (each with name and status) for the detail endpoint
    (R6.3).

    Attributes:
        process_id: The OnboardingProcess identifier.
        status: The process status (``in_progress`` or ``complete``).
        employee_id: The linked Employee record identifier.
        candidate_id: The linked Candidate record identifier.
        completed_count: Number of tasks with status ``done``.
        total_count: Total number of tasks in the checklist.
        missing_setup_fields: List of missing setup field names.
        tasks: The process's tasks ordered by ``order_index`` ascending.
    """

    process_id: UUID
    status: str
    employee_id: UUID
    candidate_id: UUID
    completed_count: int
    total_count: int
    missing_setup_fields: list[str]
    completed_at: str | None = None
    accepted_at: str | None = None
    job_opening: str | None = None
    department_id: UUID | None = None
    position_id: UUID | None = None
    manager_id: UUID | None = None
    start_date: date | None = None
    tasks: list[ProcessTaskDetail] = field(default_factory=list)


class OnboardingService:
    """Creates and advances onboarding processes and employee activation.

    This file implements the constructor, the event-driven creation flow
    (:meth:`start_from_event`), and the HR-facing task completion + automatic
    activation flow (:meth:`complete_task`). Read-model methods are added by
    subsequent tasks.

    Attributes:
        process_repo: Repository for OnboardingProcess persistence.
        task_repo: Repository for OnboardingTask persistence.
        audit_repo: Append-only repository for OnboardingAuditLog entries.
        employee_repo: Reused employee-module repository (Employee persistence
            and ``NV-XXX`` code generation).
        session: The shared async session whose transaction this service owns.
    """

    def __init__(
        self,
        process_repo: OnboardingProcessRepository,
        task_repo: OnboardingTaskRepository,
        audit_repo: OnboardingAuditRepository,
        employee_repo: EmployeeRepository,
        session: AsyncSession,
    ) -> None:
        """Initialize the service with its repositories and session.

        Args:
            process_repo: Repository for OnboardingProcess persistence.
            task_repo: Repository for OnboardingTask persistence.
            audit_repo: Append-only repository for OnboardingAuditLog entries.
            employee_repo: Reused employee-module repository for Employee
                persistence and ``NV-XXX`` employee code generation.
            session: The shared :class:`~sqlalchemy.ext.asyncio.AsyncSession`
                whose transaction boundary (commit/rollback) this service owns.
        """
        self.process_repo = process_repo
        self.task_repo = task_repo
        self.audit_repo = audit_repo
        self.employee_repo = employee_repo
        self.session = session

    async def start_from_event(
        self,
        candidate_id: UUID,
        full_name: str,
        email: str,
        event_id: str,
    ) -> OnboardingProcess:
        """Start an onboarding process for an accepted candidate.

        Idempotent per ``candidate_id``. If a process already exists for the
        candidate, this records a duplicate-detection audit entry in its own
        committed transaction and returns the existing process unchanged,
        creating no additional employee or process (R1.3, R1.4, R2.7). On a new
        candidate, it creates — within a single transaction — an inactive
        Employee (``is_active = false``, ``NV-XXX`` code with a uniqueness
        retry), the OnboardingProcess (``in_progress``), the fixed four-task
        checklist (all ``pending``), and the creation audit entry, then commits
        once. Any failure during creation rolls back so no partial employee or
        process remains, and the exception propagates to the caller (the
        consumer records the failure / ARQ retries) (R1.5).

        Args:
            candidate_id: The originating candidate identifier (already parsed
                and validated by the caller).
            full_name: The candidate's full name (1-255 chars), used as the
                Employee ``full_name``.
            email: The candidate's syntactically valid email (1-320 chars).
            event_id: The originating ``candidate_accepted`` event identifier,
                recorded on the audit entry.

        Returns:
            The newly created OnboardingProcess, or the pre-existing
            OnboardingProcess when the event is a duplicate.

        Raises:
            Exception: Propagates any error raised during creation after the
                transaction is rolled back, so the caller can record the
                failure and/or retry.
        """
        existing = await self.process_repo.get_by_candidate_id(candidate_id)
        if existing is not None:
            logger.info(
                "Duplicate candidate_accepted event for candidate %s (event %s); "
                "leaving existing process %s unchanged",
                candidate_id,
                event_id,
                existing.id,
            )
            await self._record_duplicate(existing, candidate_id, event_id)
            return existing

        try:
            process = await self._create_new_process(candidate_id, full_name, email, event_id)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            logger.exception(
                "Failed to start onboarding for candidate %s (event %s); rolled back",
                candidate_id,
                event_id,
            )
            raise

        logger.info(
            "Started onboarding process %s for candidate %s (event %s)",
            process.id,
            candidate_id,
            event_id,
        )
        return process

    async def _record_duplicate(
        self,
        existing: OnboardingProcess,
        candidate_id: UUID,
        event_id: str,
    ) -> None:
        """Append a duplicate-detection audit entry in its own transaction.

        The existing process and its checklist are left untouched; only an audit
        entry recording the duplicate (with the originating ``candidate_id`` and
        ``event_id``) is written and committed (R1.4, R2.7).

        Args:
            existing: The pre-existing OnboardingProcess for the candidate.
            candidate_id: The originating candidate identifier.
            event_id: The originating event identifier.

        Raises:
            Exception: Propagates any error after rolling back the audit write.
        """
        entry = OnboardingAuditLog(
            operation_type=_OP_DUPLICATE_DETECTED,
            entity_type="process",
            entity_id=existing.id,
            candidate_id=candidate_id,
            event_id=event_id,
            success=True,
            change_summary=(
                f"Duplicate candidate_accepted event for candidate {candidate_id}; "
                f"existing process {existing.id} left unchanged"
            ),
        )
        try:
            await self.audit_repo.append(entry)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            logger.exception(
                "Failed to record duplicate-detection audit for candidate %s (event %s)",
                candidate_id,
                event_id,
            )
            raise

    async def _create_new_process(
        self,
        candidate_id: UUID,
        full_name: str,
        email: str,
        event_id: str,
    ) -> OnboardingProcess:
        """Create the employee, process, checklist, and creation audit entry.

        All writes participate in the caller's single transaction; the caller
        (:meth:`start_from_event`) commits once on success or rolls back on any
        failure so nothing partial persists.

        Args:
            candidate_id: The originating candidate identifier.
            full_name: The Employee ``full_name``.
            email: The Employee ``email``.
            event_id: The originating event identifier (recorded on the audit).

        Returns:
            The created OnboardingProcess (flushed, with its id populated).
        """
        employee = await self._create_inactive_employee(candidate_id, full_name, email)

        process = OnboardingProcess(
            candidate_id=candidate_id,
            employee_id=employee.id,
            status=OnboardingStatus.IN_PROGRESS.value,
        )
        await self.process_repo.create(process)

        tasks = [
            OnboardingTask(
                process_id=process.id,
                task_key=task_key.value,
                name=name,
                status=OnboardingTaskStatus.PENDING.value,
                order_index=order_index,
            )
            for order_index, task_key, name in CHECKLIST_TEMPLATE
        ]
        await self.task_repo.create_many(tasks)

        creation_audit = OnboardingAuditLog(
            operation_type=_OP_PROCESS_CREATED,
            entity_type="process",
            entity_id=process.id,
            candidate_id=candidate_id,
            event_id=event_id,
            success=True,
            new_value={
                "process_id": str(process.id),
                "employee_id": str(employee.id),
                "candidate_id": str(candidate_id),
                "employee_code": employee.employee_code,
                "status": process.status,
            },
            change_summary=(
                f"Onboarding process {process.id} created for candidate {candidate_id} "
                f"(employee {employee.id})"
            ),
        )
        await self.audit_repo.append(creation_audit)

        return process

    async def _create_inactive_employee(
        self,
        candidate_id: UUID,
        full_name: str,
        email: str,
    ) -> Employee:
        """Create the inactive Employee with a unique ``NV-XXX`` code.

        The Employee starts inactive (``is_active = false``, R2.1/R2.5) and is
        linked to the originating candidate (R2.3). The ``employee_code`` is
        derived from :meth:`EmployeeRepository.get_next_code`; the insert is
        wrapped in a SAVEPOINT so that a collision on the ``employee_code``
        unique constraint can be retried with a freshly derived code without
        poisoning the surrounding transaction (R2.4).

        Args:
            candidate_id: The originating candidate identifier (link, R2.3).
            full_name: The Employee ``full_name``.
            email: The Employee ``email``.

        Returns:
            The persisted (flushed) inactive Employee with its id and
            ``employee_code`` populated.

        Raises:
            IntegrityError: If the ``employee_code`` (or other) unique
                constraint still fails on the final attempt; re-raised so the
                surrounding transaction rolls back.
        """
        for attempt in range(_EMPLOYEE_CODE_MAX_ATTEMPTS):
            employee_code = await self.employee_repo.get_next_code()
            employee = Employee(
                employee_code=employee_code,
                full_name=full_name,
                email=email,
                candidate_id=candidate_id,
                is_active=False,
            )
            try:
                async with self.session.begin_nested():
                    await self.employee_repo.create(employee)
            except IntegrityError:
                is_last_attempt = attempt == _EMPLOYEE_CODE_MAX_ATTEMPTS - 1
                logger.warning(
                    "Employee code collision on %s (attempt %d/%d) for candidate %s%s",
                    employee_code,
                    attempt + 1,
                    _EMPLOYEE_CODE_MAX_ATTEMPTS,
                    candidate_id,
                    "; giving up" if is_last_attempt else "; retrying",
                )
                if is_last_attempt:
                    # Re-raise so the caller's transaction rolls back and the
                    # failure propagates (consumer records failure / ARQ retries).
                    raise
                continue
            return employee

        # Unreachable: the final attempt either returns or re-raises above.
        raise RuntimeError("employee code allocation loop exited unexpectedly")

    async def complete_task(
        self,
        task_id: UUID,
        actor: User,
        status: str = OnboardingTaskStatus.DONE.value,
    ) -> OnboardingTask:
        """Mark an onboarding task done, activating the employee when complete.

        This is the HR-facing mark-done path behind ``PATCH
        /api/onboarding/tasks/{task_id}``. The checks are performed in the exact
        order the requirements mandate:

        1. **Status validity (R3.5):** the requested ``status`` must be one of
           the defined values ``{pending, done}``; any other value is rejected
           with :class:`InvalidTaskStatusError` naming the value, and no state
           changes.
        2. **Existence before authorization (R4.4):** the task is loaded first;
           a missing task raises :class:`OnboardingTaskNotFoundError` (404) even
           when the actor is not an admin.
        3. **Authorization (R4.5):** an actor whose role is not ``admin`` is
           rejected with :class:`OnboardingAuthorizationError` (403) and nothing
           changes.
        4. **Idempotent no-op (R4.3, R5.8):** a task that is already ``done``
           (so a process that is already ``complete`` with its employee active)
           returns unchanged with no audit entry.

        Otherwise the task is currently ``pending``. ``complete_task`` only
        advances a task to ``done`` — the sole transition the requirements and
        the router contract define — so a ``pending`` target on a still-pending
        task is a no-op. For the ``done`` target the service locks the process
        row (``SELECT ... FOR UPDATE``) to serialize concurrent completions
        (R5.5), sets the task ``done`` (with ``completed_at`` and
        ``completed_by_user_id``), writes the status-change audit entry (R4.2,
        R8.1), and — when every task in the process is now ``done`` and the
        process has at least one task (R5.1, R5.2, R5.7) — sets the process
        ``complete`` and the employee ``is_active = true`` with an activation
        audit entry (R5.3, R5.4). All of this happens in one transaction
        committed once. Any failure rolls the whole transaction back, leaving
        the task, process, and employee unchanged, and raises
        :class:`AuditWriteError` (audit append failed, R8.2) or
        :class:`OnboardingActivationError` (any other completion/activation
        failure, R5.6).

        Args:
            task_id: The identifier of the task to mark done.
            actor: The authenticated user performing the action. Must have the
                ``admin`` role to change onboarding state.
            status: The requested task status from the PATCH body. Defaults to
                ``done``; any value outside ``{pending, done}`` is rejected.

        Returns:
            The OnboardingTask in its resulting state (``done`` after a
            successful completion, or unchanged for an idempotent no-op).

        Raises:
            InvalidTaskStatusError: If ``status`` is not ``pending`` or ``done``.
            OnboardingTaskNotFoundError: If no task exists for ``task_id``.
            OnboardingAuthorizationError: If ``actor`` is not an ``admin``.
            AuditWriteError: If a mandatory audit append fails (state unchanged).
            OnboardingActivationError: If any other completion/activation write
                fails (state unchanged).
        """
        # Step 1: validate the requested status value (R3.5, Property 12).
        valid_statuses = {OnboardingTaskStatus.PENDING.value, OnboardingTaskStatus.DONE.value}
        if status not in valid_statuses:
            raise InvalidTaskStatusError(status)

        # Step 2: existence — checked before authorization (R4.4).
        task = await self.task_repo.get_by_id(task_id)
        if task is None:
            raise OnboardingTaskNotFoundError()

        # Step 3: authorization — HR only (R4.5).
        if actor.role != UserRole.HR:
            raise OnboardingAuthorizationError()

        # Step 4: idempotent no-op when the target status matches current status
        if task.status == status:
            logger.info("Task %s is already %s; no-op by %s", task.id, status, actor.email)
            return task

        # Step 5: locked, atomic completion + activation in a single transaction.
        try:
            process = await self.process_repo.get_for_update(task.process_id)
            if process is None:
                # Defensive: a task always references an existing process via FK.
                raise OnboardingActivationError(
                    f"Onboarding process {task.process_id} for task {task.id} not found"
                )
            if process.status == OnboardingStatus.COMPLETE.value:
                raise OnboardingProcessAlreadyCompletedError()

            updated_task = await self._update_task_status(task, actor, process, status)
            if status == OnboardingTaskStatus.DONE.value:
                await self._activate_if_complete(process, actor)
            await self.session.commit()
        except OnboardingError:
            await self.session.rollback()
            logger.exception(
                "Failed to complete task %s (actor %s); rolled back",
                task_id,
                actor.email,
            )
            raise
        except Exception as exc:
            await self.session.rollback()
            logger.exception(
                "Failed to complete task %s (actor %s); rolled back",
                task_id,
                actor.email,
            )
            raise OnboardingActivationError(f"Failed to complete task {task_id}") from exc

        logger.info("Task %s marked done by %s", updated_task.id, actor.email)
        return updated_task

    async def _update_task_status(
        self,
        task: OnboardingTask,
        actor: User,
        process: OnboardingProcess,
        new_status: str,
    ) -> OnboardingTask:
        """Set a task's status and write its status-change audit entry.

        Participates in the caller's locked transaction; the caller commits or
        rolls back. Records the acting HR identity, the task id, the previous
        and new status, and (via the audit entry's ``created_at``) the timestamp
        of the change.

        Args:
            task: The OnboardingTask being updated.
            actor: The acting admin user.
            process: The locked OnboardingProcess owning the task.
            new_status: The new status to apply.

        Returns:
            The updated OnboardingTask.

        Raises:
            AuditWriteError: If appending the status-change audit entry fails.
        """
        now = datetime.now(UTC) if new_status == OnboardingTaskStatus.DONE.value else None
        user_id = actor.id if new_status == OnboardingTaskStatus.DONE.value else None
        previous_status = task.status

        updated_task = await self.task_repo.set_status(
            task,
            OnboardingTaskStatus(new_status),
            completed_at=now,
            completed_by_user_id=user_id,
        )

        op_type = (
            _OP_TASK_COMPLETED if new_status == OnboardingTaskStatus.DONE.value else "task_reverted"
        )

        status_audit = OnboardingAuditLog(
            user_id=actor.id,
            actor_email=actor.email,
            operation_type=op_type,
            entity_type="task",
            entity_id=task.id,
            candidate_id=process.candidate_id,
            previous_value={"status": previous_status},
            new_value={"status": new_status},
            change_summary=f"Task {task.id} marked {new_status} by {actor.email}",
            success=True,
        )
        await self._append_audit(status_audit)
        return updated_task

    async def _activate_if_complete(
        self,
        process: OnboardingProcess,
        actor: User,
    ) -> None:
        """Activate the employee when every task in the process is ``done``.

        Counts the process's tasks by status (the just-completed task is already
        flushed, so it counts as ``done``). Activation happens if and only if
        the process has at least one task and every task is ``done`` (R5.1,
        R5.2, R5.7): a zero-task process never activates. On activation the
        process is set ``complete``, the linked employee is set
        ``is_active = true``, and an activation audit entry is written (R5.3,
        R5.4) — all within the caller's locked transaction so it commits
        atomically with the task completion (R5.5).

        Args:
            process: The locked OnboardingProcess being evaluated.
            actor: The acting admin user (recorded on the activation audit).

        Raises:
            AuditWriteError: If appending the activation audit entry fails.
            OnboardingActivationError: If the linked employee cannot be found.
        """
        employee = await self.employee_repo.get_by_id(process.employee_id)
        if employee is None:
            raise OnboardingActivationError(
                f"Employee {process.employee_id} for process {process.id} not found"
            )

        setup_complete = (
            employee.department_id is not None
            and employee.position_id is not None
            and employee.manager_id is not None
            and employee.start_date is not None
        )

        counts = await self.task_repo.count_by_status(process.id)
        total = sum(counts.values())
        done = counts.get(OnboardingTaskStatus.DONE.value, 0)
        if total == 0 or done != total or not setup_complete:
            # Incomplete (or zero-task): leave in_progress, employee inactive.
            return

        await self.process_repo.set_status(process, OnboardingStatus.COMPLETE)
        employee = await self.employee_repo.update(process.employee_id, {"is_active": True})
        if employee is None:
            # Defensive: the process always references an existing employee.
            raise OnboardingActivationError(
                f"Employee {process.employee_id} for process {process.id} not found"
            )
        activation_audit = OnboardingAuditLog(
            user_id=actor.id,
            actor_email=actor.email,
            operation_type=_OP_EMPLOYEE_ACTIVATED,
            entity_type="employee",
            entity_id=employee.id,
            candidate_id=process.candidate_id,
            previous_value={"is_active": False},
            new_value={"is_active": True},
            change_summary=(
                f"Employee {employee.id} activated on completion of process {process.id}"
            ),
            success=True,
        )
        await self._append_audit(activation_audit)
        logger.info(
            "Employee %s activated on completion of onboarding process %s",
            employee.id,
            process.id,
        )

    async def _append_audit(self, entry: OnboardingAuditLog) -> None:
        """Append a mandatory audit entry, mapping failures to ``AuditWriteError``.

        The audit append participates in the caller's transaction; if it fails
        the exception is converted to :class:`AuditWriteError` so the caller
        rolls back and rejects the action with the state left unchanged (R8.2).

        Args:
            entry: The fully constructed audit entry to append.

        Raises:
            AuditWriteError: If the underlying audit append fails.
        """
        try:
            await self.audit_repo.append(entry)
        except Exception as exc:
            raise AuditWriteError() from exc

    async def update_employee_setup(
        self,
        process_id: UUID,
        actor: User,
        data: dict[str, Any],
    ) -> OnboardingProcess:
        if actor.role != UserRole.HR:
            raise OnboardingAuthorizationError()

        try:
            process = await self.process_repo.get_for_update(process_id)
            if process is None:
                raise OnboardingProcessNotFoundError()

            if process.status == OnboardingStatus.COMPLETE.value:
                raise OnboardingProcessAlreadyCompletedError(
                    "Cannot edit setup for a completed process"
                )

            employee = await self.employee_repo.get_by_id(process.employee_id)
            if not employee:
                raise OnboardingError("Employee not found")

            # Validate manager is active
            if "manager_id" in data and data["manager_id"] is not None:
                manager = await self.employee_repo.get_by_id(data["manager_id"])
                if not manager or not manager.is_active:
                    raise OnboardingError("Manager must be an active employee")

            # Validate department exists
            dep_id = data.get("department_id", employee.department_id)
            if dep_id:
                from src.modules.employee.domain.entities import Department

                dep = await self.session.get(Department, dep_id)
                if not dep:
                    raise OnboardingError("Department not found")

            # Validate position exists and matches department
            pos_id = data.get("position_id", employee.position_id)
            if pos_id:
                from src.modules.employee.domain.entities import Position

                pos = await self.session.get(Position, pos_id)
                if not pos:
                    raise OnboardingError("Position not found")
                if pos.department_id is not None and dep_id != pos.department_id:
                    raise OnboardingError("Department must match Position's department")

            # Convert string start_date to date object if needed
            if "start_date" in data and isinstance(data["start_date"], str):
                from datetime import date

                data["start_date"] = date.fromisoformat(data["start_date"])

            previous_state = {k: getattr(employee, k) for k in data.keys()}

            # Serialize UUID and date for audit log
            def _serialize(v: Any) -> Any:
                if isinstance(v, UUID):
                    return str(v)
                from datetime import date

                if isinstance(v, date):
                    return v.isoformat()
                return v

            audit_prev = {k: _serialize(v) for k, v in previous_state.items()}
            audit_new = {k: _serialize(v) for k, v in data.items()}

            await self.employee_repo.update(employee.id, data)

            setup_audit = OnboardingAuditLog(
                user_id=actor.id,
                actor_email=actor.email,
                operation_type="employee_setup_updated",
                entity_type="employee",
                entity_id=employee.id,
                candidate_id=process.candidate_id,
                previous_value=audit_prev,
                new_value=audit_new,
                change_summary=f"Employee {employee.id} setup fields updated by {actor.email}",
                success=True,
            )
            await self._append_audit(setup_audit)

            await self._activate_if_complete(process, actor)
            await self.session.commit()
            return process
        except OnboardingError:
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise OnboardingError(f"Failed to update employee setup: {exc}") from exc

    async def list_processes(
        self,
        status: str | None,
        page: int,
        page_size: int,
    ) -> PaginatedProcesses:
        """List onboarding processes with progress, paginated and capped.

        Read-only projection backing ``GET /api/onboarding/processes``. Returns
        at most 50 items per response (the page size is clamped to
        ``[1, 50]``) together with the true total of processes matching the
        request, so callers can report accurate totals even when a page is
        empty — yielding an empty ``items`` list with ``total == 0`` when
        nothing matches (R6.2). When ``status`` is supplied it is passed through
        to the repository, which returns only processes whose status is
        identical to it (R6.4); ``None`` applies no filter. For each returned
        process the checklist progress is computed from
        :meth:`OnboardingTaskRepository.count_by_status`, exposing
        ``completed_count`` (number of ``done`` tasks) out of ``total_count``
        (total tasks) (R6.1).

        Status filter *validation* (rejecting an undefined value with 422,
        R6.5) is handled at the API/schema layer by typing the query parameter
        as ``OnboardingStatus``; this method accepts ``status`` as ``str |
        None`` and does not raise for ``None`` or for any value.

        Args:
            status: Optional status value to filter by (``in_progress`` or
                ``complete``); ``None`` returns processes of every status.
            page: The 1-indexed page number; values below 1 are clamped to 1.
            page_size: Requested items per page; clamped to ``[1, 50]`` so the
                response never exceeds the cap (R6.2).

        Returns:
            A :class:`PaginatedProcesses` with the page's list items, the true
            matching total, and the effective page / page size.
        """
        effective_page = max(page, 1)
        effective_page_size = max(1, min(page_size, _LIST_PAGE_SIZE_MAX))

        processes, total = await self.process_repo.list(
            status=status,
            page=effective_page,
            page_size=effective_page_size,
        )

        items: list[ProcessListItem] = []
        if not processes:
            return PaginatedProcesses(
                items=items,
                total=total,
                page=effective_page,
                page_size=effective_page_size,
            )

        process_ids = [process.id for process in processes]
        employee_ids = [process.employee_id for process in processes]

        # Bulk fetch data
        if hasattr(self.task_repo, "count_by_status_for_processes"):
            fetch_bulk = getattr(self.task_repo, "count_by_status_for_processes")
            task_counts: dict[UUID, dict[str, int]] = await fetch_bulk(process_ids)
        else:
            task_counts = {p.id: await self.task_repo.count_by_status(p.id) for p in processes}

        get_employees_by_ids = getattr(self.employee_repo, "get_by_ids", None)
        employees: dict[UUID, Employee] = {}
        if callable(get_employees_by_ids):
            employees_result = await get_employees_by_ids(employee_ids)
            if isinstance(employees_result, dict):
                employees = employees_result
            else:
                employees = {emp.id: emp for emp in employees_result}
        else:
            get_employee_by_id = getattr(self.employee_repo, "get_by_id", None)
            if callable(get_employee_by_id):
                for emp_id in set(employee_ids):
                    emp = await get_employee_by_id(emp_id)
                    if emp:
                        employees[emp_id] = emp

        for process in processes:
            counts = task_counts.get(process.id, {})
            total_count = sum(counts.values())
            completed_count = counts.get(OnboardingTaskStatus.DONE.value, 0)
            employee = employees.get(process.employee_id)

            missing_setup_fields = []
            if employee:
                if not employee.department_id:
                    missing_setup_fields.append("department")
                if not employee.position_id:
                    missing_setup_fields.append("position")
                if not employee.manager_id:
                    missing_setup_fields.append("manager")
                if not employee.start_date:
                    missing_setup_fields.append("start_date")

            items.append(
                ProcessListItem(
                    process_id=process.id,
                    status=process.status,
                    employee_id=process.employee_id,
                    employee_full_name=employee.full_name if employee else "",
                    employee_email=employee.email if employee else "",
                    employee_code=employee.employee_code if employee else None,
                    completed_count=completed_count,
                    total_count=total_count,
                    missing_setup_fields=missing_setup_fields,
                )
            )

        return PaginatedProcesses(
            items=items,
            total=total,
            page=effective_page,
            page_size=effective_page_size,
        )

    async def get_process(self, process_id: UUID) -> ProcessDetail:
        """Return one onboarding process with its full checklist.

        Read-only projection backing ``GET
        /api/onboarding/processes/{process_id}``. Loads the process by id and,
        when found, projects its summary fields plus the ordered checklist —
        each task's id, name, status, and order index (R6.3). The
        ``completed_count`` / ``total_count`` progress is derived from the
        loaded tasks so the detail is internally consistent with its checklist.

        Args:
            process_id: The identifier of the OnboardingProcess to load.

        Returns:
            A :class:`ProcessDetail` describing the process and its tasks
            ordered by ``order_index`` ascending.

        Raises:
            OnboardingProcessNotFoundError: If no process exists for
                ``process_id`` (R6.6).
        """
        process = await self.process_repo.get_by_id(process_id)
        if process is None:
            raise OnboardingProcessNotFoundError()

        tasks = await self.task_repo.list_by_process(process_id)

        user_ids = {t.completed_by_user_id for t in tasks if t.completed_by_user_id}
        users_map = {}
        if user_ids:
            from sqlmodel import select

            statement = select(User).where(User.id.in_(user_ids))  # type: ignore[attr-defined]
            result = await self.session.execute(statement)
            for user in result.scalars().all():
                users_map[user.id] = user.name

        task_details = [
            ProcessTaskDetail(
                id=task.id,
                name=task.name,
                status=task.status,
                order_index=task.order_index,
                completed_at=task.completed_at.isoformat() if task.completed_at else None,
                completed_by_name=(
                    users_map.get(task.completed_by_user_id) if task.completed_by_user_id else None
                ),
            )
            for task in tasks
        ]
        completed_count = sum(1 for task in tasks if task.status == OnboardingTaskStatus.DONE.value)

        employee = None
        get_employee_by_id = getattr(self.employee_repo, "get_by_id", None)
        if callable(get_employee_by_id):
            employee = await get_employee_by_id(process.employee_id)

        missing_setup_fields = []
        if employee:
            if not employee.department_id:
                missing_setup_fields.append("department")
            if not employee.position_id:
                missing_setup_fields.append("position")
            if not employee.manager_id:
                missing_setup_fields.append("manager")
            if not employee.start_date:
                missing_setup_fields.append("start_date")

        return ProcessDetail(
            process_id=process.id,
            status=process.status,
            employee_id=process.employee_id,
            candidate_id=process.candidate_id,
            completed_count=completed_count,
            total_count=len(tasks),
            missing_setup_fields=missing_setup_fields,
            completed_at=process.completed_at.isoformat() if process.completed_at else None,
            department_id=employee.department_id if employee else None,
            position_id=employee.position_id if employee else None,
            manager_id=employee.manager_id if employee else None,
            start_date=employee.start_date if employee else None,
            tasks=task_details,
        )

    async def get_counts(self) -> dict[str, int]:
        """Return process counts grouped by status for tab badges.

        Read-only projection backing ``GET /api/onboarding/counts``.
        Delegates to the process repository's ``counts_by_status`` and
        adds a ``total`` key summing all statuses.

        Returns:
            A dict with keys ``total``, ``in_progress``, and ``complete``.
            Missing statuses default to 0.
        """
        counts = await self.process_repo.counts_by_status()
        in_progress = counts.get(OnboardingStatus.IN_PROGRESS.value, 0)
        complete = counts.get(OnboardingStatus.COMPLETE.value, 0)
        return {
            "total": in_progress + complete,
            "in_progress": in_progress,
            "complete": complete,
        }
