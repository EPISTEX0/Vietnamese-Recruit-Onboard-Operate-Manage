"""Contracts the interview write paths must keep, each broken by statement order.

These are not logic bugs — every one of them is a correct-looking line in the
wrong place, or a default that drifted away from the vocabulary around it. That
makes them close to invisible in review and, without these tests, invisible in
CI too.

Covered here:

* An audit-write failure must never turn a committed action into a 500 (R12.5 /
  R17.5). ``log_audit`` swallows its own errors, but the failure happens inside
  ``session.flush()``, which leaves the session needing a rollback — so a
  ``commit()`` placed after it raises ``PendingRollbackError`` and the caller
  sees the whole action fail even though it was already committed.
* A reschedule must not strip the join link off the Google event. For
  ``custom_link`` interviews the URL lives in the event description, so
  rebuilding the patch description from ``notes`` alone silently removes the
  only way attendees have to join.
* A write response without an etag must not wipe the stored one. Overwriting it
  with ``None`` turns every later patch into an unconditional write, silently
  disabling the conflict detection the etag exists for.
* ``create_replacement_interview``'s default ``mode`` must be a real
  :class:`MeetingMode`. A default outside that vocabulary makes the no-mode call
  a guaranteed 422.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.modules.recruitment.application import interview_scheduler_service as scheduler_module
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.value_objects import CalendarEvent, MeetingMode
from tests.modules.recruitment._interview_support import (
    FakeCalendarPort,
    build_calendar_harness,
    make_candidate,
    make_employee,
    make_interview,
)

_EVENT_ID = "evt-write-contracts"
_CUSTOM_LINK = "https://zoom.us/j/9876543210"
_STORED_ETAG = '"etag-stored-v3"'
_FUTURE = datetime(2090, 6, 1, 9, 0, 0, tzinfo=UTC)


class _PoisoningAuditSink:
    """A ``log_audit`` stand-in that fails the way the real one fails.

    The real helper wraps ``session.add`` + ``session.flush`` in try/except and
    returns ``None`` on error. What that try/except cannot undo is the state the
    failed flush leaves behind: SQLAlchemy marks the transaction as needing a
    rollback, so the *next* ``commit()`` raises. Modelling only the swallow (as
    ``SpyAuditSink(fail=True)`` does) cannot catch a commit placed after the
    audit; modelling the poisoned session can.
    """

    def __init__(self, session: Any) -> None:
        self._session = session
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1
        self._session.poisoned = True
        return None


def _poisonable(session: Any) -> None:
    """Make ``session.commit()`` raise once the session has been poisoned."""
    session.poisoned = False
    original_commit = session.commit

    async def commit() -> None:
        if session.poisoned:
            raise RuntimeError(
                "PendingRollbackError: this session is in 'prepared' state; "
                "no further SQL can be emitted until rollback() is called"
            )
        await original_commit()

    session.commit = commit


async def test_audit_write_failure_does_not_fail_a_committed_reschedule() -> None:
    """A poisoned session from a failed audit must not surface as a failed action.

    The reschedule commits its own write *before* the audit, so by the time the
    audit fails the action is already durable. Letting the post-audit commit
    propagate would report a 500 for work that actually succeeded.

    Validates: Requirements 12.5, 17.5
    """
    candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
    booked = make_interview(candidate_id=candidate.id, calendar_event_id=_EVENT_ID)
    employee = make_employee(email="interviewer@example.com")
    harness = build_calendar_harness(
        candidates=[candidate], employees=[employee], interviews=[booked]
    )
    _poisonable(harness.session)
    sink = _PoisoningAuditSink(harness.session)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scheduler_module, "log_audit", sink)
        # Must not raise: the reschedule itself already committed.
        await harness.service.reschedule_interview(
            candidate.id,
            start=_FUTURE,
            duration_minutes=60,
            interviewer_ids=[employee.id],
            notes="rescheduled",
        )

    assert sink.calls >= 1, "the audit write must genuinely have been attempted"
    # The reschedule's own effect survived.
    live = await harness.scheduled_interview(candidate.id)
    assert live is not None
    assert live.start_at.astimezone(UTC) == _FUTURE.astimezone(UTC)


async def test_reschedule_keeps_the_custom_join_link_on_the_event() -> None:
    """Rescheduling a custom_link interview must not strip its join URL.

    ``create_interview`` puts the URL in the event description (CalendarEventSpec
    has no ``location`` field), so a patch description rebuilt from ``notes``
    alone deletes the only join route attendees have.
    """
    candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
    booked = make_interview(
        candidate_id=candidate.id,
        calendar_event_id=_EVENT_ID,
        meeting_mode=MeetingMode.CUSTOM_LINK,
    )
    booked.meeting_link = _CUSTOM_LINK
    employee = make_employee(email="interviewer@example.com")
    harness = build_calendar_harness(
        candidates=[candidate], employees=[employee], interviews=[booked]
    )

    with harness.audit_patch():
        await harness.service.reschedule_interview(
            candidate.id,
            start=_FUTURE,
            duration_minutes=60,
            interviewer_ids=[employee.id],
            notes="moved to next week",
        )

    assert len(harness.calendar.patch_calls) == 1
    spec = harness.calendar.patch_calls[0].spec
    assert spec is not None
    assert spec.description is not None
    assert _CUSTOM_LINK in spec.description, (
        "the custom join link was dropped from the patched event description"
    )
    # The new notes are still carried.
    assert "moved to next week" in spec.description


async def test_patch_without_an_etag_does_not_wipe_the_stored_one() -> None:
    """A response carrying no etag must leave the stored etag intact.

    Overwriting it with ``None`` makes every later patch unconditional, so the
    412-conflict detection quietly stops working. The neighbouring
    ``calendar_updated`` assignment is already guarded this way.
    """
    candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
    booked = make_interview(
        candidate_id=candidate.id,
        calendar_event_id=_EVENT_ID,
        calendar_etag=_STORED_ETAG,
    )
    employee = make_employee(email="interviewer@example.com")
    # A confirmed patch whose response omits the etag.
    calendar = FakeCalendarPort(
        patch_outcomes=[CalendarEvent(event_id=_EVENT_ID, html_link=None, meet_link=None)]
    )
    harness = build_calendar_harness(
        candidates=[candidate],
        employees=[employee],
        interviews=[booked],
        calendar=calendar,
    )

    with harness.audit_patch():
        await harness.service.reschedule_interview(
            candidate.id,
            start=_FUTURE,
            duration_minutes=60,
            interviewer_ids=[employee.id],
            notes=None,
        )

    live = await harness.scheduled_interview(candidate.id)
    assert live is not None
    assert live.calendar_etag == _STORED_ETAG, (
        "a response without an etag wiped the stored one, so the next patch "
        "would be an unconditional write"
    )


async def test_create_replacement_interview_default_mode_is_a_real_meeting_mode() -> None:
    """Calling create_replacement_interview without ``mode`` must not 422.

    Its default has to sit inside the :class:`MeetingMode` vocabulary the rest of
    the module validates against; a stale default outside that set makes the
    no-mode call fail every time.
    """
    candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
    cancelled = make_interview(
        candidate_id=candidate.id,
        calendar_event_id=_EVENT_ID,
        status="cancelled",
    )
    employee = make_employee(email="interviewer@example.com")
    harness = build_calendar_harness(
        candidates=[candidate], employees=[employee], interviews=[cancelled]
    )

    with harness.audit_patch():
        replacement = await harness.service.create_replacement_interview(
            cancelled.id,
            round_name="Replacement Round",
            start=_FUTURE,
            end=_FUTURE + timedelta(hours=1),
            timezone="Asia/Ho_Chi_Minh",
            interviewer_ids=[employee.id],
        )

    assert replacement.meeting_mode in {m.value for m in MeetingMode}
