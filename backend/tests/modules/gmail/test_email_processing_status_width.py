"""``email_messages.processing_status`` must hold every status the code writes.

The column was ``VARCHAR(20)`` from 008 (2026-05-20) until 086. On the same day
008 created it, the recruitment pipeline began writing
``"classification_failed"`` -- 21 characters. PostgreSQL does not truncate an
over-long ``varchar(n)``, it raises ``StringDataRightTruncation``, so every
attempt to mark an email as failed died on flush.

Nothing caught it for three months, and the reason is worth stating: the writer
is ``IntentClassifier._mark_classification_failed()``, whose whole body sits
inside ``except Exception: logger.error(...)``. The failure of the failure
handler was swallowed by the failure handler. The email stayed
``'unprocessed'`` and came back round the classification loop, so the symptom
was "some emails are reprocessed forever", nowhere near the cause.

``docs/schema-drift-audit.md`` had this on file as P4 "harmless widening,
worth checking separately" -- the direction of the drift was read correctly
(the model was ahead at ``max_length=30``) but the conclusion "harmless" was
about what applying the diff would do, not about the state it would fix.

Two assertions, because they fail on different mistakes:

* the round-trip dies if the *column* is narrowed again -- it runs against a
  database at ``alembic upgrade head``, the same shape as
  ``tests/modules/test_enum_column_db_roundtrip.py``;
* the static width check dies if a *new* status literal is added that does not
  fit, which is the mistake that actually happened and which no round-trip over
  today's known values could have caught.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

from src.modules.gmail.domain.entities import EmailMessage
from src.modules.identity.domain.entities import User, UserRole

SRC_DIR = Path(__file__).resolve().parents[3] / "src"

# Every value assigned to ``processing_status`` anywhere in the application.
# Listed rather than derived so that a status disappearing from the code is a
# visible edit here too; ``test_every_status_literal_in_src_fits_the_column``
# is the half that reads ``src/`` and would catch a new one.
STATUS_VALUES = [
    "unprocessed",
    "classified",
    "needs_review",
    "cv_processing",
    "needs_classification",
    "classification_failed",
]


@pytest_asyncio.fixture
async def session(postgres_async_url: str) -> AsyncIterator[AsyncSession]:
    """Provide a fresh async session per test, rolled back on teardown."""
    engine = create_async_engine(postgres_async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


async def _make_user(db_session: AsyncSession) -> User:
    """Insert one user; ``email_messages.user_id`` is a non-null FK to it."""
    suffix = uuid4().hex[:12]
    user = User(
        email=f"status-width-{suffix}@example.com",
        name="Status Width User",
        google_sub=f"google-sub-{suffix}",
        role=UserRole.HR,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.integration
@pytest.mark.parametrize("status", STATUS_VALUES)
async def test_every_status_the_code_writes_survives_a_round_trip(
    session: AsyncSession, status: str
) -> None:
    """Writing each status must reach the database and read back unchanged."""
    user = await _make_user(session)
    suffix = uuid4().hex[:12]
    row = EmailMessage(
        user_id=user.id,
        gmail_message_id=f"msg-{suffix}",
        gmail_thread_id=f"thread-{suffix}",
        processing_status=status,
        received_at=datetime.now(UTC),
    )
    session.add(row)

    await session.flush()

    session.expunge_all()
    result = await session.execute(select(EmailMessage).where(EmailMessage.id == row.id))
    loaded = result.scalars().one()
    assert loaded.processing_status == status, (
        f"{status!r} ({len(status)} chars) did not round-trip. PostgreSQL raises "
        "rather than truncates, so a narrower column fails the flush above -- see "
        "migration 086."
    )


def test_every_status_literal_in_src_fits_the_column() -> None:
    """No assignment to ``processing_status`` in ``src/`` may exceed the column.

    Reads the source rather than the enumerated list above on purpose: the bug
    this file exists for was a *new* literal outgrowing an old column, which a
    test over the values already known cannot see.
    """
    width = EmailMessage.__table__.c.processing_status.type.length
    assert width is not None, "processing_status lost its length; the check below is vacuous"

    pattern = re.compile(r"""processing_status\s*=\s*["']([a-z_]+)["']""")
    found: dict[str, list[str]] = {}
    for path in SRC_DIR.rglob("*.py"):
        for literal in pattern.findall(path.read_text(encoding="utf-8")):
            found.setdefault(literal, []).append(str(path.relative_to(SRC_DIR)))

    assert found, "found no processing_status assignments in src/ -- the scan is broken"

    too_long = {
        literal: sorted(set(files)) for literal, files in found.items() if len(literal) > width
    }
    assert not too_long, (
        f"processing_status is VARCHAR({width}), and these literals do not fit: "
        f"{ {k: len(k) for k in too_long} }. PostgreSQL raises rather than truncates, "
        f"so every write of them fails. Written at: {too_long}"
    )
