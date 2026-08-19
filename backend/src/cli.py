"""Rescue CLI for a deployment that lost every ``system_admin`` account.

Runs inside the backend container, where shell access already proves
ownership of the deployment -- see ``AGENTS.md`` "Khôi phục / reset database"::

    docker compose exec backend uv run python -m src.cli create-admin --email ... --name ...
    docker compose exec backend uv run python -m src.cli reset-password --email ...

``create-admin`` refuses while the deployment still holds an active
``system_admin``: it is a rescue tool for a locked-out deployment, not a
standing way to mint administrators (#419). ``reset-password`` covers the
gap that guard leaves -- the account survives but its password does not.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Unused directly, but required: ``User.employee_id`` carries a
# ``foreign_key="employees.id")`` and this process never imports the employee
# module through anything else, so SQLModel's shared metadata has no
# ``employees`` table to resolve that FK against without this import --
# flushing a ``User`` row raises ``NoReferencedTableError`` otherwise. The
# real server process never hits this because ``src.main`` imports every
# module's entities before the app starts serving.
import src.modules.employee.domain.entities  # noqa: F401
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.container import _get_async_session_maker
from src.modules.identity.domain.entities import AuditActionType, UserRole
from src.modules.identity.infrastructure.audit_log_repository import AuditLogRepository
from src.modules.identity.infrastructure.password_utils import (
    generate_temporary_password,
    hash_password,
)
from src.modules.identity.infrastructure.user_repository import UserRepository


class RescueCliError(Exception):
    """A rescue command was refused; the message is safe to print as-is."""


async def create_admin(session: AsyncSession, *, email: str, name: str) -> tuple[str, str]:
    """Create a new SYSTEM_ADMIN, refusing while one is already active.

    Args:
        session: The database session the caller commits/rolls back.
        email: Email for the new account.
        name: Display name for the new account.

    Returns:
        A tuple of (email, temporary password) -- the only place the
        password is available, so the caller must show it to the operator.

    Raises:
        RescueCliError: An active system_admin already exists, or ``email``
            is already taken by another account.
    """
    user_repo = UserRepository(session)
    if await user_repo.count_active_system_admins() > 0:
        raise RescueCliError(
            "Refused: an active system_admin already exists on this deployment. "
            "create-admin is a rescue command, not a standing way to mint admins. "
            "Use 'reset-password' to recover an existing account instead."
        )

    temp_password = generate_temporary_password()
    try:
        user = await user_repo.create_local_account(
            email=email.lower(),
            name=name,
            password_hash=hash_password(temp_password),
            role=UserRole.SYSTEM_ADMIN,
            must_change_password=True,
        )
        await AuditService(AuditLogRepository(session)).log_action(
            admin=user,
            action_type=AuditActionType.CLI_CREATE_ADMIN,
            details={"actor": "cli"},
        )
    except IntegrityError as exc:
        await session.rollback()
        raise RescueCliError(f"Refused: an account already exists for '{email}'.") from exc

    await session.commit()
    return user.email, temp_password


async def reset_password(session: AsyncSession, *, email: str) -> tuple[str, str]:
    """Reset an existing account's password to a fresh temporary one.

    Args:
        session: The database session the caller commits/rolls back.
        email: Email of the account to reset.

    Returns:
        A tuple of (email, temporary password).

    Raises:
        RescueCliError: No account matches ``email``.
    """
    user_repo = UserRepository(session)
    user = await user_repo.get_by_email(email)
    if user is None:
        raise RescueCliError(f"Refused: no account found for '{email}'.")

    temp_password = generate_temporary_password()
    user = await user_repo.update_password(
        user.id, hash_password(temp_password), must_change_password=True
    )
    await AuditService(AuditLogRepository(session)).log_action(
        admin=user,
        action_type=AuditActionType.CLI_RESET_PASSWORD,
        details={"actor": "cli"},
    )
    await session.commit()
    return user.email, temp_password


@asynccontextmanager
async def _session_scope() -> AsyncGenerator[AsyncSession]:
    """Open one database session for a single CLI invocation.

    Reuses ``container._get_async_session_maker()`` rather than building a
    second engine from ``AuthSettings`` -- the same private function
    ``src.main``'s own bootstrap functions call directly for the same reason:
    this process never enters FastAPI's ``Depends`` request cycle that
    ``get_db_session`` is wired for.
    """
    session_maker = _get_async_session_maker()
    async with session_maker() as session:
        yield session


async def _run(args: argparse.Namespace) -> int:
    async with _session_scope() as session:
        try:
            if args.command == "create-admin":
                email, temp_password = await create_admin(session, email=args.email, name=args.name)
            else:
                email, temp_password = await reset_password(session, email=args.email)
        except RescueCliError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    print(f"OK: {email}")
    print(f"Temporary password: {temp_password}")
    print("must_change_password is set -- the account must change it on next login.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Rescue CLI for a deployment that lost every system_admin account.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin_parser = subparsers.add_parser(
        "create-admin",
        help="Create a new system_admin (refused if one is already active)",
    )
    create_admin_parser.add_argument("--email", required=True)
    create_admin_parser.add_argument("--name", required=True)

    reset_password_parser = subparsers.add_parser(
        "reset-password",
        help="Reset an existing account's password to a new temporary one",
    )
    reset_password_parser.add_argument("--email", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m src.cli``."""
    args = _build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
