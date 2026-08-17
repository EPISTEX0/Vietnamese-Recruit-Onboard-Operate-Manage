"""Repository for Department entity CRUD operations.

Provides async database access for department listing, lookup,
creation, update, hard-delete, and active-employee checks using
SQLAlchemy async sessions with SQLModel.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.employee.domain.entities import Department, Employee


class DepartmentRepository:
    """Handles Department entity persistence using async SQLAlchemy sessions.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def list_all(self) -> list[Department]:
        """Retrieve all departments ordered by name.

        Returns:
            A list of all Department entities sorted alphabetically by name.
        """
        statement = select(Department).order_by(Department.name)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, department_id: UUID) -> Department | None:
        """Retrieve a department by its unique identifier.

        Args:
            department_id: The UUID primary key of the department.

        Returns:
            The Department entity if found, None otherwise.
        """
        statement = select(Department).where(Department.id == department_id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_ids(self, department_ids: list[UUID]) -> dict[UUID, Department]:
        """Retrieve multiple departments by their IDs in one query.

        Args:
            department_ids: List of department UUIDs to fetch.

        Returns:
            Dict mapping department ID to Department entity.
        """
        if not department_ids:
            return {}
        statement = select(Department).where(Department.id.in_(department_ids))  # type: ignore[attr-defined]
        result = await self.session.execute(statement)
        return {d.id: d for d in result.scalars().all()}

    async def get_by_name(self, name: str) -> Department | None:
        """Retrieve a department by name (case-insensitive).

        Used for uniqueness checks when creating or updating departments.

        Args:
            name: The department name to search for.

        Returns:
            The Department entity if found, None otherwise.
        """
        statement = select(Department).where(func.lower(Department.name) == name.lower())
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def create(self, department: Department) -> Department:
        """Persist a new department entity to the database.

        Args:
            department: The Department entity to create.

        Returns:
            The persisted Department entity with generated fields populated.
        """
        self.session.add(department)
        await self.session.flush()
        return department

    async def update(self, department_id: UUID, data: dict[str, Any]) -> Department | None:
        """Update an existing department with partial data.

        Only the fields present in the data dict are updated.

        Args:
            department_id: The UUID of the department to update.
            data: A dictionary of field names to new values.

        Returns:
            The updated Department entity if found, None otherwise.
        """
        statement = select(Department).where(Department.id == department_id)
        result = await self.session.execute(statement)
        department = result.scalars().first()

        if department is None:
            return None

        for key, value in data.items():
            if hasattr(department, key):
                setattr(department, key, value)

        self.session.add(department)
        await self.session.flush()
        return department

    async def delete(self, department_id: UUID) -> bool:
        """Hard-delete a department from the database.

        Args:
            department_id: The UUID of the department to delete.

        Returns:
            True if the department was found and deleted, False otherwise.
        """
        statement = select(Department).where(Department.id == department_id)
        result = await self.session.execute(statement)
        department = result.scalars().first()

        if department is None:
            return False

        await self.session.delete(department)
        await self.session.flush()
        return True

    async def has_active_employees(self, department_id: UUID) -> bool:
        """Check if any active employee is assigned to this department.

        Used for cascade protection — departments cannot be deleted
        if active employees reference them.

        Args:
            department_id: The UUID of the department to check.

        Returns:
            True if at least one active employee belongs to this department.
        """
        statement = (
            select(func.count())
            .select_from(Employee)
            .where(
                Employee.department_id == department_id,
                Employee.is_active == True,  # noqa: E712
            )
        )
        result = await self.session.execute(statement)
        count = result.scalar() or 0
        return count > 0
