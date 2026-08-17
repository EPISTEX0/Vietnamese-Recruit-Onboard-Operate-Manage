"""RoleService for managing user role assignments.

Moves users between the three roles of ADR-0009 (SYSTEM_ADMIN, HR, USER) and
bootstraps the super admin at application startup. Every transition runs
through :meth:`RoleService.change_role`, which refuses changes that would
lock the deployment out of its own system-admin namespace.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.domain.entities import User, UserRole
from src.modules.identity.domain.exceptions import AuthError
from src.modules.identity.infrastructure.user_repository import UserRepository

logger = logging.getLogger(__name__)


class LastAdminError(AuthError):
    """Cannot remove the SYSTEM_ADMIN role from the last system admin.

    Raised when a role change would leave the deployment with zero
    SYSTEM_ADMIN accounts. There is no in-app recovery from that state --
    creating a system admin itself requires a system admin -- so it is
    refused at the service boundary.
    """

    status_code = 400
    error_code = "ADMIN_LAST_ADMIN"
    message = "Cannot remove the last system administrator"


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

    Coordinates role changes while enforcing invariants:
    - An admin cannot change their own role.
    - The configured super admin's role cannot be changed.
    - The last remaining SYSTEM_ADMIN cannot lose that role.

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
        self._user_repo = UserRepository(session)
        self._super_admin_email = super_admin_email.lower() if super_admin_email else None

    async def change_role(
        self, target_user_id: UUID, new_role: UserRole, admin_user: User
    ) -> tuple[User, UserRole]:
        """Assign ``new_role`` to a user, enforcing every lockout guard.

        This is the single entry point for role transitions; the named
        ``promote_*``/``demote_*`` helpers delegate here so no transition can
        bypass a guard. Guards apply uniformly in both directions -- demoting
        the last system admin to HR strands a deployment exactly as demoting
        them to USER does.

        Args:
            target_user_id: The user whose role is changing.
            new_role: The role to assign.
            admin_user: The system admin performing the change.

        Returns:
            A tuple of (updated user, the role held before the change). The
            previous role is returned so callers can write an accurate audit
            entry without re-reading the row.

        Raises:
            UserNotFoundError: The target user does not exist.
            SelfDemotionError: The actor is the target.
            SuperAdminProtectedError: The target is the configured super admin.
            LastAdminError: The change would leave zero system admins.
        """
        user = await self._get_user_by_id(target_user_id)
        if user is None:
            raise UserNotFoundError(f"User with ID {target_user_id} not found")

        previous_role = user.role
        if previous_role == new_role:
            return user, previous_role

        # An admin changing their own role can lock themselves out of the
        # namespace they need in order to undo it.
        if user.id == admin_user.id:
            raise SelfDemotionError()

        if self._super_admin_email and user.email.lower() == self._super_admin_email:
            raise SuperAdminProtectedError()

        if previous_role == UserRole.SYSTEM_ADMIN and await self._count_system_admins() <= 1:
            raise LastAdminError()

        user = await self._user_repo.update_role(user, new_role)
        logger.info(
            "User %s role changed %s -> %s by %s",
            user.email,
            previous_role.value,
            new_role.value,
            admin_user.email,
        )
        return user, previous_role

    async def promote_to_system_admin(self, target_user_id: UUID, admin_user: User) -> User:
        """Assign the SYSTEM_ADMIN role to a user."""
        user, _ = await self.change_role(target_user_id, UserRole.SYSTEM_ADMIN, admin_user)
        return user

    async def promote_to_hr(self, target_user_id: UUID, admin_user: User) -> User:
        """Assign the HR role to a user."""
        user, _ = await self.change_role(target_user_id, UserRole.HR, admin_user)
        return user

    async def demote_to_user(self, target_user_id: UUID, admin_user: User) -> User:
        """Assign the self-service USER role to a user."""
        user, _ = await self.change_role(target_user_id, UserRole.USER, admin_user)
        return user

    async def _get_user_by_id(self, user_id: UUID) -> User | None:
        """Load a user by primary key, or None when absent."""
        return await self._user_repo.get_by_id(user_id)

    async def ensure_super_admin(self, email: str) -> None:
        """Ensure the super admin email has the SYSTEM_ADMIN role."""
        user = await self._user_repo.get_by_email(email)

        if user is None:
            logger.info("Super admin user '%s' not found in database.", email)
            return

        if user.role != UserRole.SYSTEM_ADMIN:
            await self._user_repo.update_role(user, UserRole.SYSTEM_ADMIN)
            logger.info("Super admin role assigned to existing user '%s'.", email)

    async def _count_system_admins(self) -> int:
        """Count the number of users with the SYSTEM_ADMIN role."""
        return await self._user_repo.count_system_admins()
