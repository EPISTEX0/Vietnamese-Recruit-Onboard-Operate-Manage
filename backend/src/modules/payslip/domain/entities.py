"""Domain entities for the Payslip module.

Defines the Payslip SQLModel table for HR manual payslip management.
HR creates drafts, updates them, and publishes. Employees view only
their own published Payslips (ADR-0012).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

from src.shared.sql_types import EnumAsString


class PayslipStatus(str, Enum):
    """Status of a Payslip."""

    DRAFT = "draft"
    PUBLISHED = "published"


class Payslip(SQLModel, table=True):
    """Payroll statement for one Employee and one pay period.

    Stores explicit payroll amounts set by HR. No automatic calculation
    from attendance or overtime (see ADR-0012). Employees can view only
    their own published Payslips.
    """

    __tablename__ = "payslips"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    employee_id: UUID = Field(
        nullable=False,
        foreign_key="employees.id",
        index=True,
    )

    # Pay period as year-month (e.g., 2026-06 for June 2026)
    period_month: date = Field(nullable=False)

    # Payroll amounts — explicit values set by HR
    gross_salary: Decimal = Field(
        nullable=False,
        max_digits=12,
        decimal_places=2,
    )
    deductions: Decimal = Field(
        default=Decimal("0"),
        max_digits=12,
        decimal_places=2,
    )
    insurance_employee: Decimal = Field(
        default=Decimal("0"),
        max_digits=12,
        decimal_places=2,
    )
    taxable_income: Decimal = Field(
        default=Decimal("0"),
        max_digits=12,
        decimal_places=2,
    )
    pit_amount: Decimal = Field(
        default=Decimal("0"),
        max_digits=12,
        decimal_places=2,
    )
    net_salary: Decimal = Field(
        nullable=False,
        max_digits=12,
        decimal_places=2,
    )

    currency: str = Field(default="VND", max_length=3)

    # Publication state
    status: PayslipStatus = Field(
        default=PayslipStatus.DRAFT,
        sa_column=Column(EnumAsString(PayslipStatus, 10), nullable=False),
    )
    published_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # Optional PDF reference
    pdf_url: str | None = Field(default=None, max_length=500)

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        UniqueConstraint(
            "employee_id",
            "period_month",
            name="uq_payslips_employee_period_month",
        ),
        CheckConstraint(
            "EXTRACT(DAY FROM period_month) = 1",
            name="ck_payslips_period_month_first_day",
        ),
        CheckConstraint(
            "currency = 'VND'",
            name="ck_payslips_currency_vnd",
        ),
        CheckConstraint(
            "(status = 'draft' AND published_at IS NULL)"
            " OR (status = 'published' AND published_at IS NOT NULL)",
            name="ck_payslips_status_published_at_consistent",
        ),
    )
