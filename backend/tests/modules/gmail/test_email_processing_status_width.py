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

import ast
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
from src.modules.recruitment.domain.entities import CVDocument
from src.modules.recruitment.domain.enums import ProcessingStatus

SRC_DIR = Path(__file__).resolve().parents[3] / "src"

ATTRIBUTE = "processing_status"

# Every value written to ``email_messages.processing_status``. Listed rather
# than derived so that a status disappearing from the code is a visible edit
# here too; ``test_every_status_literal_in_src_fits_the_column`` is the half
# that reads ``src/`` and catches one being added.
STATUS_VALUES = [
    "unprocessed",
    "classified",
    "needs_review",
    "cv_processing",
    "needs_classification",
    "classification_failed",
    # Written by ``ClassificationService`` when the provider is unavailable --
    # a ternary spanning three lines, which is precisely the form the first
    # version of the scan below (a regex) could not see.
    "ai_unavailable",
    "permanently_failed",
]


def _status_string_literals() -> dict[str, set[str]]:
    """Map every string literal written to a ``processing_status`` to its files.

    Walks the AST rather than matching text. The regex this replaced required a
    quote immediately after ``=`` and so was blind to::

        email.processing_status = (
            "ai_unavailable" if retry_count < _MAX else "permanently_failed"
        )

    which is a live assignment in ``gmail/application/classification_service.py``.
    A scan that silently misses forms is worse than no scan: it reports green
    over the values it happens to understand.

    Both assignment shapes count -- ``x.processing_status = ...`` and
    ``Model(processing_status=...)`` -- and every string constant anywhere in
    the assigned expression is collected, so ternaries and tuples are covered.
    """
    found: dict[str, set[str]] = {}

    for path in sorted(SRC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            values: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                if any(
                    isinstance(t, ast.Attribute | ast.Name)
                    and (t.attr if isinstance(t, ast.Attribute) else t.id) == ATTRIBUTE
                    for t in node.targets
                ):
                    values.append(node.value)
            elif isinstance(node, ast.Call):
                values += [kw.value for kw in node.keywords if kw.arg == ATTRIBUTE]

            for value in values:
                for inner in ast.walk(value):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        found.setdefault(inner.value, set()).add(str(path.relative_to(SRC_DIR)))

    return found


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
    """No string written to a ``processing_status`` in ``src/`` may exceed the column.

    Reads the source rather than the enumerated list above on purpose: the bug
    this file exists for was a *new* literal outgrowing an old column, which a
    test over the values already known cannot see.

    Two tables carry a ``processing_status``: ``email_messages`` and
    ``cv_documents``. The scan cannot tell which one a given literal is destined
    for without resolving types, so it checks every literal against the narrower
    of the two. That is conservative -- it can only ever over-report -- and
    over-reporting on a column-width check costs one line of thought, while
    under-reporting costs three months of silently unmarked emails.
    """
    widths = {
        "email_messages": EmailMessage.__table__.c.processing_status.type.length,
        "cv_documents": CVDocument.__table__.c.processing_status.type.length,
    }
    assert all(w is not None for w in widths.values()), (
        f"a processing_status column lost its length ({widths}); the check below is vacuous"
    )
    width = min(widths.values())

    found = _status_string_literals()
    assert found, "found no processing_status assignments in src/ -- the scan is broken"
    assert "classification_failed" in found, (
        "the scan no longer sees `classification_failed`, the literal this whole "
        "file exists for. It is assigned in intent_classifier.py; if that is still "
        "true, the AST walk is broken rather than the code."
    )

    too_long = {literal: sorted(files) for literal, files in found.items() if len(literal) > width}
    assert not too_long, (
        f"processing_status is VARCHAR({width}) ({widths}), and these literals do not "
        f"fit: { {k: len(k) for k in too_long} }. PostgreSQL raises rather than "
        f"truncates, so every write of them fails. Written at: {too_long}"
    )


def test_the_scan_sees_every_status_this_file_round_trips() -> None:
    """The listed values and the scanned ones must not drift apart.

    ``STATUS_VALUES`` above is hand-maintained, and a hand-maintained list of
    "every value the application writes" is exactly the thing that goes stale.
    The first version of this file listed six of the eight, because the two it
    missed are written by a ternary the scan could not see either -- one blind
    spot hiding another. Cross-checking the two halves is what stops that from
    being invisible.
    """
    scanned = set(_status_string_literals())
    missing = sorted(scanned - set(STATUS_VALUES) - {m.value for m in ProcessingStatus})
    assert not missing, (
        f"src/ writes {missing} to a processing_status, and STATUS_VALUES does not "
        "list them, so no round-trip covers them. Add them to STATUS_VALUES."
    )


def test_every_processing_status_enum_member_fits_cv_documents() -> None:
    """``ProcessingStatus`` is the value set for ``cv_documents.processing_status``.

    Those writes are ``ProcessingStatus.COMPLETED``, not string literals, so the
    scan above never sees them -- the enum is the only place they exist. Same
    failure mode as ``classification_failed``: a member added later with a long
    name would raise on flush and nothing else would notice.
    """
    width = CVDocument.__table__.c.processing_status.type.length
    too_long = {m.name: m.value for m in ProcessingStatus if len(m.value) > width}
    assert not too_long, (
        f"cv_documents.processing_status is VARCHAR({width}); these ProcessingStatus "
        f"members do not fit: {too_long}"
    )
