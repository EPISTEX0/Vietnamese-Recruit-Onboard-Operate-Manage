"""recover_system_admin_role

Repairs the two defects 082 left behind.

1. 082 promised to "update length of role column if needed" but never did.
   ``users.role`` is ``VARCHAR(10)`` while ``'system_admin'`` is 12 characters,
   so the role is physically unwritable: first-run setup, the super-admin
   bootstrap, and every promotion fail with "value too long".

2. 082 rewrote every ``admin`` row to ``hr`` and created no ``system_admin``,
   leaving deployments with zero system admins. There is no in-app recovery
   from that -- creating a system admin requires being one -- so it has to
   happen here.

Idempotent: re-running is a no-op once a system admin exists.

Revision ID: 084
Revises: 083
Create Date: 2026-08-13 00:00:00.000000

"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '084'
down_revision: Union[str, None] = '083'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 'system_admin' is 12 chars; leave room for future role names.
ROLE_COLUMN_LENGTH = 20


def upgrade() -> None:
    # --- 1. Make the system_admin value storable at all. -------------------
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=10),
        type_=sa.String(length=ROLE_COLUMN_LENGTH),
        existing_nullable=False,
        existing_server_default=sa.text("'user'::character varying"),
    )

    connection = op.get_bind()

    # --- 2. Ensure the deployment has at least one system admin. -----------
    existing = connection.execute(
        sa.text("SELECT count(*) FROM users WHERE role = 'system_admin'")
    ).scalar_one()
    if existing > 0:
        return

    total_users = connection.execute(sa.text("SELECT count(*) FROM users")).scalar_one()
    if total_users == 0:
        # Fresh install: first-run setup creates the system admin itself.
        return

    # Preferred: the operator named an account via AUTH_SUPER_ADMIN_EMAIL.
    super_admin_email = (os.environ.get("AUTH_SUPER_ADMIN_EMAIL") or "").strip()
    if super_admin_email:
        promoted = connection.execute(
            sa.text(
                "UPDATE users SET role = 'system_admin' "
                "WHERE lower(email) = lower(:email) RETURNING email"
            ),
            {"email": super_admin_email},
        ).fetchone()
        if promoted is not None:
            print(f"[084] Promoted '{promoted[0]}' to system_admin (AUTH_SUPER_ADMIN_EMAIL).")
            return
        print(
            f"[084] AUTH_SUPER_ADMIN_EMAIL='{super_admin_email}' matches no user; "
            "falling back to the oldest administrative account."
        )

    # Fallback: the longest-standing administrative account. 082 rewrote every
    # pre-split 'admin' to 'hr', so the oldest 'hr' row is the original admin.
    promoted = connection.execute(
        sa.text(
            "UPDATE users SET role = 'system_admin' WHERE id = ("
            "  SELECT id FROM users WHERE role = 'hr' ORDER BY created_at, email LIMIT 1"
            ") RETURNING email"
        )
    ).fetchone()
    if promoted is not None:
        print(f"[084] Promoted oldest HR account '{promoted[0]}' to system_admin.")
        return

    # Users exist but none can be identified as an administrator. Guessing here
    # would hand infrastructure credentials to an arbitrary account, so stop.
    raise RuntimeError(
        "Migration 084 cannot determine a system admin: the database has "
        f"{total_users} user(s) but no 'hr' account to promote and "
        "AUTH_SUPER_ADMIN_EMAIL is unset or matches no user. Set "
        "AUTH_SUPER_ADMIN_EMAIL to an existing user's email and re-run."
    )


def downgrade() -> None:
    # The column width is deliberately left at VARCHAR(20). Narrowing it back
    # would fail while any 'system_admin' row survives (082's downgrade, which
    # runs after this one, is what maps those rows away), and a wider column is
    # harmless. Role assignments are not reverted: which account holds
    # system_admin is an operational decision, not schema state.
    pass
