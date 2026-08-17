"""Repository for Employee entity CRUD operations.

Provides async database access for employee listing, search, filtering,
creation, update, soft-delete, and employee code generation using
SQLAlchemy async sessions with SQLModel.
"""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.employee.domain.entities import Employee


class EmployeeRepository:
    """Handles Employee entity persistence using async SQLAlchemy sessions.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def list(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        department_id: UUID | None = None,
        position_id: UUID | None = None,
        is_active: bool | None = True,
    ) -> tuple[list[Employee], int]:
        """Retrieve a paginated list of employees with optional filters.

        Args:
            page: The page number (1-indexed).
            page_size: Number of items per page.
            search: Optional text to search in full_name or email (case-insensitive).
            department_id: Optional filter by department UUID.
            position_id: Optional filter by position UUID.
            is_active: Optional filter by active status. Defaults to True.

        Returns:
            A tuple of (list of Employee entities, total count).
        """
        statement = select(Employee)
        count_statement = select(func.count()).select_from(Employee)

        # Apply is_active filter
        if is_active is not None:
            statement = statement.where(Employee.is_active == is_active)
            count_statement = count_statement.where(Employee.is_active == is_active)

        # Apply text search filter
        if search:
            search_filter = or_(
                func.lower(Employee.full_name).contains(search.lower()),
                func.lower(Employee.email).contains(search.lower()),
            )
            statement = statement.where(search_filter)
            count_statement = count_statement.where(search_filter)

        # Apply department filter
        if department_id is not None:
            statement = statement.where(Employee.department_id == department_id)
            count_statement = count_statement.where(Employee.department_id == department_id)

        # Apply position filter
        if position_id is not None:
            statement = statement.where(Employee.position_id == position_id)
            count_statement = count_statement.where(Employee.position_id == position_id)

        # Get total count
        count_result = await self.session.execute(count_statement)
        total = count_result.scalar() or 0

        # Apply pagination
        offset = (page - 1) * page_size
        statement = statement.offset(offset).limit(page_size)
        statement = statement.order_by(desc(Employee.created_at))  # type: ignore[arg-type]

        # Execute query
        result = await self.session.execute(statement)
        employees = list(result.scalars().all())

        return employees, total

    async def get_by_id(self, employee_id: UUID) -> Employee | None:
        """Retrieve an employee by their unique identifier.

        Args:
            employee_id: The UUID primary key of the employee.

        Returns:
            The Employee entity if found, None otherwise.
        """
        statement = select(Employee).where(Employee.id == employee_id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_ids(self, employee_ids: builtins.list[UUID]) -> dict[UUID, Employee]:
        """Retrieve multiple employees by their IDs in one query.

        Args:
            employee_ids: List of employee UUIDs to fetch.

        Returns:
            Dict mapping employee ID to Employee entity.
        """
        if not employee_ids:
            return {}
        statement = select(Employee).where(Employee.id.in_(employee_ids))  # type: ignore[attr-defined]
        result = await self.session.execute(statement)
        return {emp.id: emp for emp in result.scalars().all()}

    async def list_by_department(self, department_id: UUID) -> builtins.list[Employee]:
        """Retrieve every employee assigned to a department, active or not.

        Args:
            department_id: The UUID of the department to filter by.

        Returns:
            A list of Employee entities in the department.
        """
        statement = select(Employee).where(Employee.department_id == department_id)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def count_by_position(self, position_id: UUID) -> int:
        """Count employees holding a given position, active or not.

        Args:
            position_id: The UUID of the position to count.

        Returns:
            The number of employees holding the position.
        """
        statement = (
            select(func.count()).select_from(Employee).where(Employee.position_id == position_id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one() or 0

    async def get_by_email(self, email: str) -> Employee | None:
        """Retrieve an employee by email address (case-insensitive).

        Args:
            email: The email address to search for.

        Returns:
            The Employee entity if found, None otherwise.
        """
        statement = select(Employee).where(func.lower(Employee.email) == email.lower())
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def create(self, employee: Employee) -> Employee:
        """Persist a new employee entity to the database.

        Args:
            employee: The Employee entity to create.

        Returns:
            The persisted Employee entity with generated fields populated.
        """
        self.session.add(employee)
        await self.session.flush()
        return employee

    async def update(self, employee_id: UUID, data: dict[str, Any]) -> Employee | None:
        """Update an existing employee with partial data.

        Only the fields present in the data dict are updated.
        The updated_at timestamp is always refreshed.

        Args:
            employee_id: The UUID of the employee to update.
            data: A dictionary of field names to new values.

        Returns:
            The updated Employee entity if found, None otherwise.
        """
        statement = select(Employee).where(Employee.id == employee_id)
        result = await self.session.execute(statement)
        employee = result.scalars().first()

        if employee is None:
            return None

        for key, value in data.items():
            if hasattr(employee, key):
                setattr(employee, key, value)

        employee.updated_at = datetime.now(UTC)
        self.session.add(employee)
        await self.session.flush()
        return employee

    async def soft_delete(self, employee_id: UUID) -> Employee | None:
        """Soft-delete an employee by setting is_active=False.

        Args:
            employee_id: The UUID of the employee to soft-delete.

        Returns:
            The updated Employee entity if found, None otherwise.
        """
        statement = select(Employee).where(Employee.id == employee_id)
        result = await self.session.execute(statement)
        employee = result.scalars().first()

        if employee is None:
            return None

        employee.is_active = False
        employee.updated_at = datetime.now(UTC)
        self.session.add(employee)
        await self.session.flush()
        return employee

    async def get_next_code(self) -> str:
        """Generate the next sequential employee code in NV-XXX format.

        Queries the maximum existing employee code number and increments it.
        Codes are unique across all employees including soft-deleted ones.

        Returns:
            The next employee code string (e.g., "NV-001", "NV-002").
        """
        # Extract the numeric part from existing codes and find the max
        # employee_code format is "NV-XXX" where XXX is zero-padded
        statement = select(func.max(Employee.employee_code)).select_from(Employee)
        result = await self.session.execute(statement)
        max_code = result.scalar()

        if max_code is None:
            return "NV-001"

        # Parse the numeric suffix from the max code
        try:
            numeric_part = int(max_code.split("-")[1])
        except (IndexError, ValueError):
            return "NV-001"

        next_number = numeric_part + 1
        # Pad to at least 3 digits, but allow more if needed
        return f"NV-{next_number:03d}"
