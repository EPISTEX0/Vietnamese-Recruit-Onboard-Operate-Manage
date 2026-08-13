"""Domain entities for the Attendance module.

Defines SQLModel table classes for AttendanceRecord.
"""

from datetime import UTC, date, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from src.shared.sql_types import EnumAsString


class AttendanceSource(str, Enum):
    """Source of attendance record."""

    WEB = "web"
    MOBILE = "mobile"
    KIOSK = "kiosk"


class AttendanceRecord(SQLModel, table=True):
    """Daily attendance record for one Employee on one work date.

    Captures check-in/check-out timestamps, client IP addresses,
    and user agents. Work date is derived from Organization timezone.
    Timestamps are stored in UTC.
    """

    __tablename__ = "attendance_records"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    employee_id: UUID = Field(
        nullable=False,
        foreign_key="employees.id",
        index=True,
    )
    work_date: date = Field(nullable=False, index=True)
    check_in_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    check_out_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    check_in_ip: str | None = Field(default=None, max_length=45)
    check_out_ip: str | None = Field(default=None, max_length=45)
    check_in_user_agent: str | None = Field(default=None, max_length=512)
    check_out_user_agent: str | None = Field(default=None, max_length=512)
    source: AttendanceSource = Field(
        default=AttendanceSource.WEB,
        sa_column=Column(
            EnumAsString(AttendanceSource, 20), nullable=False, default=AttendanceSource.WEB
        ),
    )
    # HR correction fields
    corrected_by_user_id: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
    )
    corrected_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    correction_reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    previous_check_in_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    previous_check_out_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        # One record per employee per work date. The comment describing this
        # sat here for a while over an empty tuple while the real constraint
        # only existed in the migration.
        UniqueConstraint("employee_id", "work_date", name="uq_attendance_employee_date"),
    )
