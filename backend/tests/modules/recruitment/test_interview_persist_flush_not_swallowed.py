"""Regression tests for #385: ``_persist_interview_schedule`` no longer

swallows a ``flush()`` failure, and the test double it runs against in every
other interview test can no longer hide a skipped flush.

Before the fix, ``self._session.add(interview); if hasattr(self._session,
"flush"): try: await self._session.flush() except Exception: pass`` did two
things wrong at once: (1) the ``except Exception: pass`` discarded a real
flush-time failure (constraint violation, bad value, DB error) and let a
*later* statement on the same session die instead, with
``PendingRollbackError`` -- a symptom of the poisoned transaction, not the
root cause; and (2) ``hasattr`` is only ever false for a test double, so
production silently skipped ``flush()`` in every test that used
``FakeCalendarSession`` (30 files, via ``build_calendar_harness``), which
itself had no ``flush`` method to be found.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from src.modules.recruitment.application import interview_scheduler_service as candidate_service
from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_employee,
)

_FUTURE_START = datetime(2095, 6, 1, 10, 0, 0, tzinfo=UTC)


class _BrokenFlush(RuntimeError):
    """Stands in for a real flush-time failure (constraint violation, etc.)."""


def test_flush_failure_propagates_instead_of_becoming_pending_rollback() -> None:
    """A ``flush()`` failure raises as itself, not a later ``PendingRollbackError``.

    Regression for #385 part 1: the swallow-then-fail-elsewhere behaviour that
    replaced the real cause with a session-state exception is gone. The
    scheduling call must fail with exactly ``_BrokenFlush``, and must fail
    before ever reaching the calendar-conflict/audit/commit steps that follow
    persistence.
    """

    async def _run() -> None:
        employee = make_employee(email="interviewer@example.com")
        candidate = make_candidate(status=CandidateStatus.NEW)
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=[employee],
            fail_on_flush=_BrokenFlush("simulated constraint violation"),
        )

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            with pytest.raises(_BrokenFlush):
                await harness.service.schedule_interview(
                    candidate.id,
                    start=_FUTURE_START,
                    duration_minutes=30,
                    interviewer_ids=[employee.id],
                )

        # The flush was actually attempted (not skipped) before raising.
        assert harness.session.flush_count == 1
        # Nothing committed: the candidate never reached the persisted state,
        # and no audit entry for a successful schedule exists.
        assert candidate.status == CandidateStatus.NEW
        assert harness.audit_sink.entries_for("interview_scheduled") == []

    asyncio.run(_run())


def test_fake_calendar_session_flush_is_exercised_not_skipped() -> None:
    """The fake session's ``flush()`` is actually called by production code.

    Regression for #385 part 2: before the fix, ``hasattr(session, "flush")``
    was ``False`` for every test running through ``build_calendar_harness``
    (``FakeCalendarSession`` had no ``flush``), so production's ``flush()``
    call was skipped in all of them without any test noticing. Asserting the
    counter here is the tripwire: if the production code ever stops calling
    ``flush()`` on this seam, this goes to 0 and fails loudly instead of
    silently.
    """

    async def _run() -> None:
        employee = make_employee(email="interviewer@example.com")
        candidate = make_candidate(status=CandidateStatus.NEW)
        harness = build_calendar_harness(candidates=[candidate], employees=[employee])

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            await harness.service.schedule_interview(
                candidate.id,
                start=_FUTURE_START,
                duration_minutes=30,
                interviewer_ids=[employee.id],
            )

        assert harness.session.flush_count == 1

    asyncio.run(_run())
