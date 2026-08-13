"""Domain entities for the Employee Request module.

Defines SQLModel table classes for EmployeeRequest with shared lifecycle
fields and type-specific fields for overtime and leave.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

from src.modules.employee_request.domain.enums import LeaveType, RequestStatus, RequestType
from src.shared.sql_types import EnumAsText


class EmployeeRequest(SQLModel, table=True):
    """Shared lifecycle fields for an employee-submitted request.

    Employees submit; HR reviews.  The request_type discriminator
    separates overtime from leave.  Type-specific columns are
    nullable and validated by the service layer.
    """

    __tablename__ = "employee_requests"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    employee_id: UUID = Field(
        nullable=False,
        foreign_key="employees.id",
        index=True,
    )
    request_type: RequestType = Field(
        sa_column=Column(EnumAsText(RequestType), nullable=False),
    )
    status: RequestStatus = Field(
        default=RequestStatus.SUBMITTED,
        sa_column=Column(
            EnumAsText(RequestStatus), nullable=False, default=RequestStatus.SUBMITTED
        ),
    )

    # --- Timestamps ---
    submitted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    reviewed_by_user_id: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
    )
    review_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    # --- Cancellation ---
    cancellation_reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )

    # --- Overtime-specific fields (nullable, validated by service) ---
    work_date: date | None = Field(default=None, index=True)
    start_time: time | None = Field(default=None)
    end_time: time | None = Field(default=None)
    duration_minutes: int | None = Field(default=None)
    reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    project_or_task: str | None = Field(default=None, max_length=255)

    # --- Leave-specific fields (nullable, validated by service) ---
    leave_type: LeaveType | None = Field(
        default=None, sa_column=Column(EnumAsText(LeaveType), nullable=True)
    )
    start_date: date | None = Field(default=None, index=True)
    end_date: date | None = Field(default=None)

    # --- Audit ---
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    def derive_duration(self) -> int:
        """Calculate duration_minutes from start_time and end_time."""
        if self.start_time is None or self.end_time is None:
            return 0
        start_dt = datetime.combine(date.today(), self.start_time)
        end_dt = datetime.combine(date.today(), self.end_time)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        return int((end_dt - start_dt).total_seconds() // 60)
