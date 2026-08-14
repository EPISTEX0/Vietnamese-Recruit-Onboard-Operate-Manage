"""Domain entities for the Onboarding module.

Defines the SQLModel table classes for OnboardingProcess, OnboardingTask, and
OnboardingAuditLog that map to PostgreSQL tables used to drive an accepted
Candidate through a checklist-driven process to an active Employee.

The ``Employee`` entity is reused unchanged from the employee module; this
module only owns the process, task, and audit tables. The unique constraint on
``OnboardingProcess.candidate_id`` is the backbone of idempotent event
consumption (exactly one process per candidate), and the append-only
``OnboardingAuditLog`` records both system-driven (event consumption) and
HR-driven (task completion, activation) state changes.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class OnboardingProcess(SQLModel, table=True):
    """Tracks one Employee's onboarding from acceptance to activation.

    Created exactly once per accepted Candidate when a ``candidate_accepted``
    event is consumed. The ``candidate_id`` column is unique and indexed so
    that "exactly one process per candidate" is a database invariant rather
    than only application logic, enabling safe idempotent event re-delivery.
    The linked Employee starts inactive and is activated when every task in the
    checklist reaches ``done``.
    """

    __tablename__ = "onboarding_processes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    candidate_id: UUID = Field(unique=True, nullable=False, index=True)
    employee_id: UUID = Field(foreign_key="employees.id", nullable=False, index=True)
    status: str = Field(default="in_progress", max_length=20, nullable=False, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
    )


class OnboardingTask(SQLModel, table=True):
    """A single item in an OnboardingProcess checklist.

    Each process is created with a fixed four-item checklist in a stable order
    (tracked by ``order_index``). A task has a ``task_key`` (stable enum key)
    and a human-readable ``name`` (display name), and its status is restricted
    by the application to ``pending`` or ``done``. Completion metadata records
    when and by which HR user the task was marked done.
    """

    __tablename__ = "onboarding_tasks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    process_id: UUID = Field(foreign_key="onboarding_processes.id", nullable=False, index=True)
    task_key: str = Field(max_length=40, nullable=False)
    name: str = Field(max_length=100, nullable=False)
    status: str = Field(default="pending", max_length=10, nullable=False)
    order_index: int = Field(nullable=False)
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
    )
    completed_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class OnboardingAuditLog(SQLModel, table=True):
    """Append-only audit entry for onboarding operations.

    Records both system-driven events (event consumption, duplicate detection,
    rejection, failure) and HR-driven actions (task completion, activation).
    ``user_id`` is nullable because system/consumer events have no acting HR
    user. The audit write participates in the same transaction as the state
    change it records, so a failed audit append rolls back the state change.
    UPDATE/DELETE on this table are not permitted by the application.
    """

    __tablename__ = "onboarding_audit_logs"
    # 070 set this comment on the table; it is the append-only rule stated where
    # someone reading the schema with psql will see it, not just here. Without it
    # in the model, autogenerate proposes clearing it off the database.
    __table_args__ = {
        "comment": (
            "Append-only audit trail for the onboarding module. UPDATE/DELETE are "
            "not permitted by the application; entries are an immutable record of "
            "onboarding state changes."
        )
    }

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID | None = Field(default=None, foreign_key="users.id", index=True)
    actor_email: str | None = Field(default=None, max_length=255)
    operation_type: str = Field(max_length=50, nullable=False, index=True)
    entity_type: str = Field(max_length=50, nullable=False)
    entity_id: UUID | None = Field(default=None, index=True)
    candidate_id: UUID | None = Field(default=None, index=True)
    event_id: str | None = Field(default=None, max_length=255)
    previous_value: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    new_value: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    change_summary: str | None = Field(default=None, max_length=500)
    success: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
