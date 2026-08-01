"""RoleService for managing user role assignments.

Provides methods to promote users to admin, demote admins to regular users,
and bootstrap the super admin at application startup. Includes protection
against demoting the last admin or the super admin.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.identity.domain.entities import User, UserRole
from src.modules.identity.domain.exceptions import AuthError

logger = logging.getLogger(__name__)


class LastAdminError(AuthError):
    """Cannot demote the last remaining administrator.

    Raised when an attempt is made to remove admin role from the only
    user with admin privileges, which would leave the system without
    any administrator.
    """

    status_code = 400
    error_code = "ADMIN_LAST_ADMIN"
    message = "Cannot remove the last administrator"


class SuperAdminProtectedError(AuthError):
    """Super admin role cannot be changed.

    Raised when an attempt is made to demote the super admin user
    whose email is configured via AUTH_SUPER_ADMIN_EMAIL.
    """

    status_code = 400
    error_code = "ADMIN_SUPER_ADMIN_PROTECTED"
    message = "Super admin role cannot be changed"


class SelfDemotionError(AuthError):
    """Admin cannot change their own role.

    Raised when an admin attempts to demote or promote themselves.
    This prevents accidental lockout from administrative functions.
    """

    status_code = 400
    error_code = "ADMIN_SELF_DEMOTION"
    message = "Cannot change your own role. Ask another admin to change it."


class UserNotFoundError(AuthError):
    """Target user does not exist.

    Raised when a role change is attempted on a user ID that does
    not correspond to any user in the database.
    """

    status_code = 404
    error_code = "USER_NOT_FOUND"
    message = "User not found"


class RoleService:
    """Manages user role assignments with safety protections.

    Coordinates role changes (promote/demote) while enforcing invariants:
    - The super admin cannot be demoted.
    - The last remaining admin cannot be demoted.

    Args:
        session: Async database session for user queries and updates.
        super_admin_email: The configured super admin email address, or None
            if not configured.
    """

    def __init__(self, session: AsyncSession, super_admin_email: str | None = None) -> None:
        """Initialize RoleService with database session and super admin config.

        Args:
            session: An SQLAlchemy AsyncSession for database operations.
            super_admin_email: The email of the super admin (from AUTH_SUPER_ADMIN_EMAIL
                env var), or None if not configured.
        """
        self._session = session
        self._super_admin_email = super_admin_email.lower() if super_admin_email else None

    async def promote_to_system_admin(self, target_user_id: UUID, admin_user: User) -> User:
        """Promote a user to the system_admin role.

        Changes the target user's role to SYSTEM_ADMIN.
        """
        user = await self._user_repository.get_by_id(target_user_id)
        if user is None:
            raise UserNotFoundError(f"User with ID {target_user_id} not found")

        if user.role == UserRole.SYSTEM_ADMIN:
            return user

        user.role = UserRole.SYSTEM_ADMIN
        self._session.add(user)
        await self._session.flush()
        return user

    async def promote_to_hr(self, target_user_id: UUID, admin_user: User) -> User:
        """Promote a user to the hr role.

        Changes the target user's role to HR.
        """
        user = await self._user_repository.get_by_id(target_user_id)
        if user is None:
            raise UserNotFoundError(f"User with ID {target_user_id} not found")

        if user.role == UserRole.HR:
            return user

        user.role = UserRole.HR
        self._session.add(user)
        await self._session.flush()
        return user

    async def demote_to_user(self, target_user_id: UUID, admin_user: User) -> User:
        """Demote a user to regular user role.

        Changes the target user's role to USER. Enforces protection against
        demoting the super admin or the last remaining system admin.
        """
        user = await self._user_repository.get_by_id(target_user_id)
        if user is None:
            raise UserNotFoundError(f"User with ID {target_user_id} not found")

        if user.role == UserRole.USER:
            return user

        # Prevent self-demotion
        if user.id == admin_user.id:
            raise SelfDemotionError()

        # Prevent demoting super admin
        if self._super_admin_email and user.email.lower() == self._super_admin_email.lower():
            raise SuperAdminProtectedError()

        # Prevent demoting the last system_admin
        if user.role == UserRole.SYSTEM_ADMIN:
            admin_count = await self._count_system_admins()
            if admin_count <= 1:
                raise LastAdminError()

        user.role = UserRole.USER
        self._session.add(user)
        await self._session.flush()
        logger.info("User %s demoted to user by %s", user.email, admin_user.email)
        return user

    async def ensure_super_admin(self, email: str) -> None:
        """Ensure the super admin email has the SYSTEM_ADMIN role."""
        statement = select(User).where(func.lower(User.email) == email.lower())
        result = await self._session.execute(statement)
        user = result.scalars().first()

        if user is None:
            logger.info("Super admin user '%s' not found in database.", email)
            return

        if user.role != UserRole.SYSTEM_ADMIN:
            user.role = UserRole.SYSTEM_ADMIN
            self._session.add(user)
            await self._session.flush()
            logger.info("Super admin role assigned to existing user '%s'.", email)

    async def _count_system_admins(self) -> int:
        """Count the number of users with the SYSTEM_ADMIN role."""
        statement = select(func.count()).select_from(User).where(User.role == UserRole.SYSTEM_ADMIN)
        result = await self._session.execute(statement)
        return result.scalar_one()
