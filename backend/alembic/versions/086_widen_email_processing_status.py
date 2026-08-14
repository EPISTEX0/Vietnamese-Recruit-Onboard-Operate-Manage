"""widen_email_processing_status

``email_messages.processing_status`` is ``VARCHAR(20)`` on the database and
``max_length=30`` in the model. ``docs/schema-drift-audit.md`` filed this under
P4 as a harmless widening and flagged it "worth checking separately". Checked:
it is not harmless, and it is not merely drift. It is the ``users.role`` failure
of migration 082 a second time, on a column that has been broken since the day
it was created.

The application writes six values into it. One of them does not fit::

    classification_failed   21 chars
    needs_classification    20
    cv_processing           13
    needs_review            12
    unprocessed             11
    classified              10

PostgreSQL does not truncate an over-long ``varchar(n)``; it raises. Verified
against a database at head::

    INSERT INTO email_messages (..., processing_status, ...)
    VALUES (..., 'classification_failed', ...);
    ERROR:  value too long for type character varying(20)

The writer is ``IntentClassifier._mark_classification_failed()``, the handler
that runs when PII redaction fails or the LLM gives up after retries. It wraps
the flush in ``except Exception`` and logs, so the failure is silent twice over:
the email is never marked, and the operator sees a log line rather than an
error. Affected emails keep ``processing_status = 'unprocessed'`` and are picked
up by classification again on the next pass -- the dead-letter path never
catches them because nothing can write the state that means "dead".

Introduced 2026-05-20: 008 created the column ``sa.String(length=20)`` and the
same day's recruitment commit began writing ``"classification_failed"``. The
model has said 30 throughout, so the model is the side that was right.

Widening a ``varchar`` is metadata-only in PostgreSQL -- no table rewrite, no
lock beyond the brief ``ACCESS EXCLUSIVE`` of the catalogue update -- and no
data can be lost, because every value that fits in 20 fits in 30. There is no
backfill to do: the rows that should have been marked were never written at all,
and this migration cannot tell which they were. Reprocessing them is an
operational follow-up, not something a schema migration can do correctly.

``downgrade()`` narrows back to 20, which is the state that carries the bug. It
would fail on any row already holding a 21-character value, and that is the
right behaviour: refusing is better than truncating audit-relevant state.

Revision ID: 086
Revises: 085
Create Date: 2026-08-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "086"
down_revision: str | None = "085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "email_messages",
        "processing_status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=30),
        existing_nullable=False,
        existing_server_default="unprocessed",
    )


def downgrade() -> None:
    op.alter_column(
        "email_messages",
        "processing_status",
        existing_type=sa.String(length=30),
        type_=sa.String(length=20),
        existing_nullable=False,
        existing_server_default="unprocessed",
    )
