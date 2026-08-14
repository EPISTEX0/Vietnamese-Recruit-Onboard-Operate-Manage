"""Domain entities for the Employee Management module.

Defines the SQLModel table classes for Employee, Department, Position,
and EmployeeDocument that map to PostgreSQL tables used for HR personnel
management.
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

# ``sa_type=Text`` below is load-bearing: a bare ``str`` renders SQLModel's
# ``AutoString``, i.e. ``VARCHAR`` with no length. That behaves exactly like
# ``TEXT`` but is a different type name, so autogenerate proposes an ALTER for
# every such column. The migrations wrote ``sa.Text()`` and the database has
# ``TEXT`` -- the model is the imprecise side. docs/schema-drift-audit.md 5.5.


class Department(SQLModel, table=True):
    """Represents an organizational unit (e.g., Engineering, HR, Sales).

    Departments group employees and serve as a reference for positions.
    Cannot be deleted if active employees are assigned to it.
    """

    __tablename__ = "departments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # ``index=True`` alongside ``unique=True`` is what makes SQLAlchemy render a
    # unique *index* named ``ix_{table}_{column}`` instead of an unnamed UNIQUE
    # constraint. The database has the index (ix_departments_name); without ``index=True``
    # autogenerate proposes dropping it and adding a constraint in its place.
    name: str = Field(max_length=100, unique=True, nullable=False, index=True)
    description: str | None = Field(default=None, sa_type=Text)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class Position(SQLModel, table=True):
    """Represents a job title within a department (e.g., Senior Developer, Manager).

    Positions are optionally linked to a department. Cannot be deleted
    if active employees hold this position.
    """

    __tablename__ = "positions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # ``index=True`` alongside ``unique=True`` is what makes SQLAlchemy render a
    # unique *index* named ``ix_{table}_{column}`` instead of an unnamed UNIQUE
    # constraint. The database has the index (ix_positions_name); without ``index=True``
    # autogenerate proposes dropping it and adding a constraint in its place.
    name: str = Field(max_length=100, unique=True, nullable=False, index=True)
    department_id: UUID | None = Field(default=None, foreign_key="departments.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class Employee(SQLModel, table=True):
    """Represents an employed person managed in the HR system.

    Employees are created manually, via Excel import, or promoted from
    candidates. Each employee receives an auto-generated employee_code
    in NV-XXX format. Deletion is soft (is_active=false).
    """

    __tablename__ = "employees"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # ``index=True`` alongside ``unique=True`` is what makes SQLAlchemy render a
    # unique *index* named ``ix_{table}_{column}`` instead of an unnamed UNIQUE
    # constraint. The database has the index (ix_employees_employee_code); without ``index=True``
    # autogenerate proposes dropping it and adding a constraint in its place.
    employee_code: str = Field(max_length=20, unique=True, nullable=False, index=True)
    full_name: str = Field(max_length=255, nullable=False)
    email: str = Field(max_length=255, unique=True, nullable=False, index=True)
    phone: str | None = Field(default=None, max_length=20)
    date_of_birth: date | None = Field(default=None)
    gender: str | None = Field(default=None, max_length=10)
    address: str | None = Field(default=None, sa_type=Text)
    # 006 indexed both FKs; the model never said so. These carry the "who is in
    # this department / this position" reads across the app, and a missing index
    # on an FK only shows up as the employee list getting slower.
    department_id: UUID | None = Field(default=None, foreign_key="departments.id", index=True)
    position_id: UUID | None = Field(default=None, foreign_key="positions.id", index=True)
    manager_id: UUID | None = Field(default=None, foreign_key="employees.id")
    start_date: date | None = Field(default=None)
    id_number: str | None = Field(default=None, max_length=20)
    tax_code: str | None = Field(default=None, max_length=20)
    contract_type: str | None = Field(default=None, max_length=20)
    candidate_id: UUID | None = Field(default=None)
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class EmployeeDocument(SQLModel, table=True):
    """Represents a document stored in the employee document vault.

    Documents are stored in MinIO at path employees/{employee_id}/{document_type}/{filename}.
    The vault is append-only — uploading a new version keeps previous versions.
    Documents are retained even when an employee is soft-deleted.
    """

    __tablename__ = "employee_documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    employee_id: UUID = Field(foreign_key="employees.id", nullable=False, index=True)
    document_type: str = Field(max_length=50, nullable=False)
    file_name: str = Field(max_length=255, nullable=False)
    storage_path: str = Field(nullable=False, sa_type=Text)
    file_size: int = Field(nullable=False)
    mime_type: str = Field(max_length=100, nullable=False)
    description: str | None = Field(default=None, sa_type=Text)
    uploaded_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
