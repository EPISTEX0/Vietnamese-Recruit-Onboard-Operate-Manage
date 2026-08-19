"""Drop whitelist_entries and users.google_sub -- dead code, owner-approved (#418).

Revision ID: 089
Revises: 088
Create Date: 2026-08-19 00:00:00.000000+07:00

Both are leftovers from an OAuth login flow removed in a prior commit.
``WhitelistManager.is_allowed``/``.is_allowed_async`` had zero production call
sites, and ``users.google_sub`` was written and read by nothing once that flow
went. Neither carries data any deployment of this codebase depends on -- there
has been no release. See #418.

Do not confuse ``users.google_sub`` with
``organization_google_connections.google_sub`` (045): that one is live and
untouched here.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "089"
down_revision: str | None = "088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_whitelist_entries_type", table_name="whitelist_entries")
    op.drop_index("ix_whitelist_entries_value", table_name="whitelist_entries")
    op.drop_table("whitelist_entries")

    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "google_sub")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("google_sub", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)

    op.create_table(
        "whitelist_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("entry_type", sa.String(length=20), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"]),
        sa.UniqueConstraint("value", name="uq_whitelist_value"),
    )
    op.create_index("ix_whitelist_entries_value", "whitelist_entries", ["value"])
    op.create_index("ix_whitelist_entries_type", "whitelist_entries", ["entry_type"])
