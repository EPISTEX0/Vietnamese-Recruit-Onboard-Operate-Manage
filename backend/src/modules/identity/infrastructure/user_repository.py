"""Repository for User entity CRUD operations.

Provides async database access for user lookup and upsert operations
using SQLAlchemy async sessions with SQLModel.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.identity.domain.entities import User, UserRole


class UserRepository:
    """Handles User entity persistence using async SQLAlchemy sessions.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        """Retrieve a user by email address (case-insensitive).

        Args:
            email: The email address to search for.

        Returns:
            The User entity if found, None otherwise.
        """
        statement = select(User).where(func.lower(User.email) == email.lower())
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Retrieve a user by their unique identifier.

        Args:
            user_id: The UUID primary key of the user.

        Returns:
            The User entity if found, None otherwise.
        """
        statement = select(User).where(User.id == user_id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_employee_id(self, employee_id: UUID) -> User | None:
        """Retrieve a user by linked employee ID."""
        statement = select(User).where(User.employee_id == employee_id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def count_users(self) -> int:
        """Count total users in system."""
        statement = select(func.count()).select_from(User)
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    async def count_system_admins(self) -> int:
        """Count SYSTEM_ADMIN accounts in the system.

        This is *not* the first-run setup gate -- ``count_users`` is. Zero
        system admins on a deployment that still holds accounts is a degraded
        state recovered by promotion (migration ``084``,
        ``ensure_super_admin``), not by re-running setup, so the count only
        tells operators through the log which state they are in.
        """
        statement = select(func.count()).select_from(User).where(User.role == UserRole.SYSTEM_ADMIN)
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    async def create_local_account(
        self,
        *,
        email: str,
        name: str,
        password_hash: str,
        role: UserRole,
        employee_id: UUID | None = None,
        must_change_password: bool = False,
    ) -> User:
        """Create local auth account."""
        user = User(
            email=email,
            name=name,
            password_hash=password_hash,
            role=role,
            employee_id=employee_id,
            must_change_password=must_change_password,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def update_password(
        self,
        user_id: UUID,
        password_hash: str,
        must_change_password: bool = False,
    ) -> User:
        """Update password hash and password-change flag."""
        user = await self.get_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        user.password_hash = password_hash
        user.must_change_password = must_change_password
        user.last_login = datetime.now(UTC)
        self.session.add(user)
        await self.session.flush()
        return user

    async def sync_profile(
        self,
        user_id: UUID,
        *,
        email: str | None = None,
        name: str | None = None,
        employee_id: UUID | None = None,
    ) -> User:
        """Sync auth profile fields from domain entities."""
        user = await self.get_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        if email is not None:
            user.email = email
        if name is not None:
            user.name = name
        if employee_id is not None:
            user.employee_id = employee_id
        self.session.add(user)
        await self.session.flush()
        return user

    async def delete_by_employee_id(self, employee_id: UUID) -> bool:
        """Delete a user linked to an employee. Idempotent: returns False if no user found.

        Args:
            employee_id: The UUID of the linked employee.

        Returns:
            True if a user was deleted, False if no user was linked.
        """
        user = await self.get_by_employee_id(employee_id)
        if user is None:
            return False
        await self.session.delete(user)
        await self.session.flush()
        return True
