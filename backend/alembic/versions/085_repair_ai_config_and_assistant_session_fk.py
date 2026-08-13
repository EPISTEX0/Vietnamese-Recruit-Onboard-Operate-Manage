"""repair_ai_config_and_assistant_session_fk

Two places where the database, not the model, is the side that drifted. Both
were found by ``compare_metadata`` and classified P2 in
``docs/schema-drift-audit.md`` -- autogenerate would emit them and they would
fail on apply, loudly, against data that is already inconsistent.

1. ``organization_ai_configurations.api_key_enc`` is nullable. 056 created it
   NOT NULL; 057 relaxed it while adding ``server_default ''`` -- the intent
   there was "default to empty", and dropping NOT NULL came along for the ride.
   Nothing writes NULL: the service stores ``''`` for a config that has no key
   yet (org still on the deployment key), and every reader tests falsiness, so
   ``''`` and NULL already mean the same thing to the application. NOT NULL
   removes the second way to spell "no key". 057's own ``downgrade()`` restores
   NOT NULL, so it never regarded nullable as the correct end state.

2. ``assistant_chat_sessions.employee_id`` has no foreign key. 075 created the
   table with an FK on ``user_id`` and an index -- but no FK -- on
   ``employee_id``, while the model has declared ``foreign_key="employees.id"``
   since day one. A forgotten constraint, not a decision: nothing else in the
   schema references employees without one.

Idempotent in both directions -- each step checks the catalog first, and the
NULL backfill runs before the NOT NULL so a database that does hold NULLs is
repaired rather than rejected.

Revision ID: 085
Revises: 084
Create Date: 2026-08-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "085"
down_revision: str | None = "084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "assistant_chat_sessions_employee_id_fkey"


def _foreign_key_exists(connection: sa.Connection, name: str) -> bool:
    return bool(
        connection.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint WHERE conname = :name AND contype = 'f'"
            ),
            {"name": name},
        ).scalar()
    )


def upgrade() -> None:
    connection = op.get_bind()

    # --- 1. api_key_enc: backfill, then forbid NULL. -----------------------
    # "" is the sentinel the application already uses for "no org key stored".
    connection.execute(
        sa.text(
            "UPDATE organization_ai_configurations "
            "SET api_key_enc = '' WHERE api_key_enc IS NULL"
        )
    )
    op.alter_column(
        "organization_ai_configurations",
        "api_key_enc",
        existing_type=sa.Text(),
        nullable=False,
        existing_server_default=sa.text("''::text"),
    )

    # --- 2. assistant_chat_sessions.employee_id: the FK 075 forgot. --------
    # Orphans cannot be deleted -- the session rows are real conversation
    # history -- so detach them instead. The column is nullable by design.
    connection.execute(
        sa.text(
            "UPDATE assistant_chat_sessions SET employee_id = NULL "
            "WHERE employee_id IS NOT NULL "
            "AND employee_id NOT IN (SELECT id FROM employees)"
        )
    )
    if not _foreign_key_exists(connection, FK_NAME):
        op.create_foreign_key(
            FK_NAME,
            "assistant_chat_sessions",
            "employees",
            ["employee_id"],
            ["id"],
        )


def downgrade() -> None:
    connection = op.get_bind()

    if _foreign_key_exists(connection, FK_NAME):
        op.drop_constraint(FK_NAME, "assistant_chat_sessions", type_="foreignkey")

    op.alter_column(
        "organization_ai_configurations",
        "api_key_enc",
        existing_type=sa.Text(),
        nullable=True,
        existing_server_default=sa.text("''::text"),
    )
