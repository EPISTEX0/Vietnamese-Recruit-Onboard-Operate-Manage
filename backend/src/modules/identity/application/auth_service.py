"""AuthService orchestrator for local authentication and sessions."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from src.modules.identity.domain.entities import UserRole
from src.modules.identity.domain.exceptions import (
    AccessDeniedError,
    AuthError,
    InvalidCredentialsError,
    InvalidTokenError,
    SetupAlreadyCompletedError,
)
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.password_utils import (
    generate_temporary_password,
    hash_password,
    verify_password,
)

if TYPE_CHECKING:
    from src.modules.identity.application.token_service import (
        RefreshTokenRepository,
        TokenService,
    )
    from src.modules.identity.infrastructure.user_repository import UserRepository
    from src.modules.recruitment.infrastructure.org_settings_repository import (
        OrganizationSettingsRepository,
    )


logger = logging.getLogger(__name__)


class AccountAlreadyExistsError(AuthError):
    """An account already exists for the requested email address."""

    status_code = 409
    error_code = "ACCOUNT_ALREADY_EXISTS"
    message = "An account already exists for this email"


@dataclass
class LocalAuthResult:
    """Result of a successful local auth action."""

    access_token: str
    refresh_token: str
    user: Any
    must_change_password: bool


class AuthService:
    """Orchestrates local authentication and session management."""

    def __init__(
        self,
        settings: AuthSettings,
        token_service: TokenService,
        user_repository: UserRepository,
        refresh_token_repository: RefreshTokenRepository,
        organization_repository: OrganizationSettingsRepository | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize AuthService with local auth dependencies."""
        self._settings = settings
        self._token_service = token_service
        self._user_repository = user_repository
        self._refresh_token_repository = refresh_token_repository
        self._organization_repository = organization_repository
        self._session = session

    async def get_setup_status(self) -> bool:
        """Return True once the deployment holds any account at all.

        First-run setup mints a ``SYSTEM_ADMIN`` with no authentication, so what
        gates it is a security decision. The gate is the ``users`` table and
        nothing else: setup is the bootstrap for a deployment that has no way in
        because it has no accounts.

        It is deliberately *not* keyed on the ``organization_settings`` row.
        That row is created by ordinary settings reads and survives the accounts
        it was created alongside, so keying on it failed in both directions --
        it refused setup on a deployment whose ``users`` table had been emptied,
        and it allowed setup on a deployment with a live ``SYSTEM_ADMIN`` whose
        settings row happened to be missing.

        Nor is it keyed on ``count_system_admins() == 0``: a deployment holding
        real accounts and real employee data has already been bootstrapped, and
        losing its administrators is repaired by *promoting* a named existing
        account -- what migration ``084`` and ``ensure_super_admin`` do through
        ``AUTH_SUPER_ADMIN_EMAIL`` -- not by letting an anonymous caller mint a
        fresh administrator over that data.
        """
        return (await self._user_repository.count_users()) > 0

    async def setup_first_run(
        self, organization_name: str, name: str, email: str, password: str
    ) -> LocalAuthResult:
        """Create the Organization and the deployment's first SYSTEM_ADMIN."""
        if self._organization_repository is None:
            raise RuntimeError("Organization repository is not configured")
        if await self.get_setup_status():
            await self._log_setup_refusal()
            raise SetupAlreadyCompletedError()

        try:
            # Claiming the Organization singleton is what serializes concurrent
            # bootstraps: a fresh INSERT collides on the unique singleton key,
            # and an existing row is claimed under a row lock.
            await self._organization_repository.create_for_setup(organization_name)
            # Re-read the gate now that the claim is held. A request that waited
            # on the row lock may have been overtaken while it waited, and on
            # that path there is no INSERT left to collide with. A losing
            # request must not leak the partial Organization row or a session.
            already_bootstrapped = await self.get_setup_status()
        except (IntegrityError, ValueError) as exc:
            await self._rollback_setup()
            raise SetupAlreadyCompletedError() from exc
        if already_bootstrapped:
            await self._rollback_setup()
            raise SetupAlreadyCompletedError()

        try:
            user = await self._user_repository.create_local_account(
                email=email,
                name=name,
                password_hash=hash_password(password),
                role=UserRole.SYSTEM_ADMIN,
                must_change_password=False,
            )
        except Exception:
            await self._rollback_setup()
            raise

        # Commit the two setup records before issuing any authenticated session.
        if self._session is not None:
            await self._session.commit()
        result = await self._issue_session(user)
        if self._session is not None:
            await self._session.commit()
        return result

    async def _rollback_setup(self) -> None:
        """Discard the in-flight setup transaction, when a session is wired."""
        if self._session is not None:
            await self._session.rollback()

    async def _log_setup_refusal(self) -> None:
        """Record *why* setup was refused without telling the caller.

        Every refusal answers ``409 AUTH_SETUP_ALREADY_COMPLETED`` on purpose.
        ``/setup`` is unauthenticated, so an answer that distinguished "an admin
        exists" from "accounts exist but no admin" would tell an anonymous
        caller exactly when a deployment's administrative namespace is empty.

        An operator locked out of a populated deployment still needs to know the
        way back, so the distinction -- and the promotion path that resolves it
        -- goes to the log instead.
        """
        if (await self._user_repository.count_system_admins()) > 0:
            return
        logger.warning(
            "First-run setup refused: this deployment holds accounts but no "
            "system_admin. Recover by promoting an existing account -- set "
            "AUTH_SUPER_ADMIN_EMAIL to its address and restart -- rather than "
            "by creating a new administrator through setup."
        )

    async def login(self, email: str, password: str) -> LocalAuthResult:
        """Authenticate local account with email/password."""
        user = await self._user_repository.get_by_email(email)
        if (
            user is None
            or not user.password_hash
            or not verify_password(password, user.password_hash)
        ):
            raise InvalidCredentialsError()
        if not user.is_active:
            raise AccessDeniedError("Account is inactive")
        user.last_login = datetime.now(UTC)
        self._user_repository.session.add(user)
        await self._user_repository.session.flush()
        return await self._issue_session(user)

    async def change_password(
        self,
        user: Any,
        current_password: str,
        new_password: str,
    ) -> LocalAuthResult:
        """Update password for current user and issue fresh session."""
        current = user  # runtime type is User
        if not verify_password(current_password, current.password_hash):
            raise InvalidTokenError()
        updated = await self._user_repository.update_password(
            current.id,
            hash_password(new_password),
            must_change_password=False,
        )
        await self._token_service.revoke_user_tokens(updated.id)
        return await self._issue_session(updated)

    async def create_staff_account(
        self,
        *,
        email: str,
        name: str,
        role: UserRole,
    ) -> tuple[Any, str]:
        """Create an HR or SYSTEM_ADMIN account with a temporary password.

        This is the system admin's provisioning path (ADR-0009 section 3).
        First-run setup mints a single SYSTEM_ADMIN and every other
        account-creation route sits behind ``require_hr``, so without this the
        deployment can never produce its first HR account.

        Args:
            email: The new account's email address.
            name: The new account's display name.
            role: HR or SYSTEM_ADMIN. Self-service USER accounts are created by
                HR against an Employee record via ``create_employee_account``.

        Returns:
            A tuple of (created user, the generated temporary password).

        Raises:
            AccountAlreadyExistsError: An account already uses this email.
            ValueError: ``role`` is not a staff role.
        """
        if role not in (UserRole.HR, UserRole.SYSTEM_ADMIN):
            raise ValueError(f"create_staff_account does not provision {role.value} accounts")

        normalized_email = email.lower()
        if await self._user_repository.get_by_email(normalized_email) is not None:
            raise AccountAlreadyExistsError(f"Account already exists for {normalized_email}")

        temp_password = generate_temporary_password()
        user = await self._user_repository.create_local_account(
            email=normalized_email,
            name=name,
            password_hash=hash_password(temp_password),
            role=role,
            must_change_password=True,
        )
        if self._session is not None:
            await self._session.commit()
        return user, temp_password

    async def create_employee_account(
        self,
        employee: Any,
    ) -> tuple[Any, str]:
        """Create Employee Account with temp password."""
        existing = await self._user_repository.get_by_employee_id(employee.id)
        if existing is not None:
            raise AccessDeniedError("Employee account already exists")
        temp_password = generate_temporary_password()
        user = await self._user_repository.create_local_account(
            email=employee.email.lower(),
            name=employee.full_name,
            password_hash=hash_password(temp_password),
            role=UserRole.USER,
            employee_id=employee.id,
            must_change_password=True,
        )
        return user, temp_password

    async def delete_employee_account(
        self,
        employee: Any,
    ) -> bool:
        """Delete Employee Account. Idempotent: returns False if no account exists.

        Args:
            employee: The Employee domain entity with .id and .email fields.

        Returns:
            True if a user was deleted, False if no user was linked.
        """
        return await self._user_repository.delete_by_employee_id(employee.id)

    async def _issue_session(self, user: Any) -> LocalAuthResult:
        """Build JWT + refresh token pair for local auth."""
        employee_id = getattr(user, "employee_id", None)
        await self._token_service.revoke_user_tokens(user.id)
        access_token = self._token_service.create_access_token(
            user.id,
            user.email,
            employee_id=employee_id,
            must_change_password=bool(getattr(user, "must_change_password", False)),
        )
        raw_refresh_token, token_hash = self._token_service.create_refresh_token(user.id)
        expires_at = datetime.now(UTC) + timedelta(days=self._settings.refresh_token_expire_days)
        await self._refresh_token_repository.store(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        return LocalAuthResult(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            user=user,
            must_change_password=bool(getattr(user, "must_change_password", False)),
        )

    async def logout(self, refresh_token: str) -> None:
        """Revoke a refresh token to end the user's session.

        Hashes the provided raw refresh token and marks it as revoked
        in the database.

        Args:
            refresh_token: The raw refresh token string from the client.
        """
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        record = await self._refresh_token_repository.find_by_token_hash(token_hash)
        if record is not None:
            await self._refresh_token_repository.revoke(token_hash)
