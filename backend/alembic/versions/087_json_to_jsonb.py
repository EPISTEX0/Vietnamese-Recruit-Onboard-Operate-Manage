"""json_to_jsonb

Six columns are ``json`` on the database while the model has declared
``JSONB`` since the day each was created. ``docs/schema-drift-audit.md``
section 5.4 filed them as P3: no data loss, but a real table rewrite, so the
cost is operational rather than risky.

The direction is not a judgement call. 061 created
``recruitment_inbox_items`` writing ``sa.JSON()`` in the migration while the
model in the *same commit* (c9db11a, PR #184) wrote ``Column(JSONB)``; 069 did
the same for the two ``cv_documents`` columns. Three revisions after 061, 064
created ``correction_records.evidence`` and ``correction_records.source_hints``
-- the same two concepts, same module -- with ``JSONB()`` spelled out. The
generic ``sa.JSON()`` was a slip in the migration, not a decision about
storage.

The rest of the schema agrees: of the 33 json-ish columns at head, these six
are the only ``json``. ``cv_documents`` holds both kinds side by side --
``parsed_cv_data`` and ``validation_errors`` are ``jsonb``, ``field_provenance``
and ``confirmed_fields`` are ``json`` -- which no design would choose on
purpose.

Why it matters beyond tidiness: ``json`` stores the document as reparsed text.
It has no equality operator, so ``GROUP BY``/``DISTINCT`` on the column fails
outright; it supports none of the containment operators (``@>``, ``?``) and
cannot take a GIN index; and it re-parses on every access. ``jsonb`` is what
every query anyone would want to write against ``evidence`` or
``field_provenance`` needs.

``USING`` clause: PostgreSQL casts ``json -> jsonb`` directly, so no explicit
cast is needed and no value can fail -- anything that parsed as ``json`` is
valid ``jsonb``. The one difference to know about is that ``jsonb`` normalises:
key order is not preserved and duplicate keys collapse to the last one. Neither
matters here. These columns hold machine-written objects read back by key, and
duplicate keys cannot survive ``json.dumps`` of a Python dict in the first
place.

Cost: this is a full table rewrite of ``cv_documents`` and
``recruitment_inbox_items``, taking ``ACCESS EXCLUSIVE`` for the duration. Both
tables are empty on dev. Deployments holding real recruitment history should
expect the lock to scale with row count and schedule accordingly -- there is no
online path for a type change of this kind in PostgreSQL 15.

``downgrade()`` casts back to ``json``, which round-trips the values but not
the original key order, since that information is gone once ``jsonb`` has
stored them. Recorded here rather than left for someone to discover.

Revision ID: 087
Revises: 086
Create Date: 2026-08-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "087"
down_revision: str | None = "086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, nullable) for each column still typed ``json``.
COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("cv_documents", "confirmed_fields", False),
    ("cv_documents", "field_provenance", True),
    ("recruitment_inbox_items", "attachments_metadata", True),
    ("recruitment_inbox_items", "correction_history", True),
    ("recruitment_inbox_items", "evidence", True),
    ("recruitment_inbox_items", "source_hints", True),
)


def upgrade() -> None:
    for table, column, nullable in COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSON(astext_type=sa.Text()),
            type_=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=nullable,
            postgresql_using=f"{column}::jsonb",
        )


def downgrade() -> None:
    for table, column, nullable in COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            type_=postgresql.JSON(astext_type=sa.Text()),
            existing_nullable=nullable,
            postgresql_using=f"{column}::json",
        )
