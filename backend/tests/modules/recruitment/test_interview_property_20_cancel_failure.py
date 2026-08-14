"""Property 20: a cancellation failure never blocks the terminal transition.

Feature: interview-calendar-scheduling, Property 20.

Validates: Requirements 8.4, 8.5, 8.6 - if Google Calendar cancellation (the
``events.delete`` call) fails during a reject or archive of a Candidate that has
a stored ``calendar_event_id``, the Scheduling_System STILL transitions the
Candidate to ``rejected`` (R8.4) / ``archived`` (R8.5) - the terminal transition
commits regardless - AND writes a failed-cancellation audit entry that records
the cancelled ``calendar_event_id`` (R8.6).

The property is exercised end to end against the in-memory seams in
``_interview_support``. The fake ``CalendarPort`` is scripted so its single
``delete_event`` call raises (either an ``httpx.HTTPStatusError`` carrying a
500 - the canonical server-side failure that ``_with_calendar_token`` re-raises
because it is not a 401 - or a plain ``RuntimeError``). The service cancels the
event AFTER the terminal transition has already committed, so the swallowed
failure can never undo the reject/archive. ``log_audit`` is replaced by the spy
sink because the success path writes the ``candidate_rejected`` /
``candidate_archived`` audit and the failure path additionally writes the
``interview_cancel_failed`` audit; the real helper would call
``session.add``/``flush`` (unsupported by the fake session).

For every Candidate seeded in ``interview_scheduled`` with a stored
``calendar_event_id``, when the Calendar delete fails, the chosen terminal
action must:

* NOT raise (the action completes despite the cancel failure);
* transition the Candidate to the terminal status and commit it (the returned
  Candidate carries the terminal status and the committed snapshot reflects it);
* have made exactly one delete attempt against the stored event id; and
* write exactly one ``interview_cancel_failed`` audit entry (``success=False``)
  recording the ``calendar_event_id`` and the trigger.
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    FakeCalendarPort,
    build_calendar_harness,
    make_candidate,
    make_http_status_error,
    make_interview,
)

# The event id stored on the Candidate before the terminal action; the failed
# delete must target this exact id (R8.6).
STORED_EVENT_ID = "evt-existing-1"

# The terminal action under test and the status it must reach despite the
# cancellation failure (R8.4 / R8.5).
_TERMINAL_STATUS: dict[str, str] = {
    "reject": CandidateStatus.REJECTED,
    "archive": CandidateStatus.ARCHIVED,
}


# Feature: interview-calendar-scheduling, Property 20
@settings(max_examples=100, deadline=None)
@given(
    action=st.sampled_from(["reject", "archive"]),
    failure_kind=st.sampled_from(["http_500", "runtime"]),
)
def test_cancellation_failure_never_blocks_terminal_transition(
    action: str,
    failure_kind: str,
) -> None:
    """A failed Calendar delete still commits the reject/archive and audits it.

    For any Candidate in ``interview_scheduled`` with a stored
    ``calendar_event_id``, when ``delete_event`` fails (a 500 the adapter
    re-raises, or a generic error), the chosen terminal action completes without
    raising, transitions and commits the Candidate to ``rejected`` / ``archived``,
    makes exactly one delete attempt on the stored event id, and writes an
    ``interview_cancel_failed`` audit entry (``success=False``) recording the
    ``calendar_event_id`` and the trigger.

    Validates: Requirements 8.4, 8.5, 8.6
    """

    async def _run() -> None:
        # A fresh scripted failure per example: a 500 is re-raised by
        # ``_with_calendar_token`` (it is not a 401), and a RuntimeError
        # propagates directly; both reach ``_cancel_interview_event``'s
        # try/except, which swallows the error and audits it (R8.6).
        failure: BaseException = (
            make_http_status_error(500, method="DELETE")
            if failure_kind == "http_500"
            else RuntimeError("calendar delete blew up")
        )

        # A Candidate already scheduled, whose Interview carries the stored event
        # id the failed cancellation must target.
        candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
        booked = make_interview(
            candidate_id=candidate.id,
            calendar_event_id=STORED_EVENT_ID,
        )

        # Script the single delete_event call to fail.
        calendar = FakeCalendarPort(delete_outcomes=[failure])
        harness = build_calendar_harness(
            candidates=[candidate], interviews=[booked], calendar=calendar
        )

        commits_before = harness.session.commit_count

        with harness.audit_patch():
            # The action must NOT raise despite the cancel failure (R8.4/R8.5).
            if action == "reject":
                returned = await harness.lifecycle.reject_candidate(candidate.id)
            else:
                returned = await harness.lifecycle.archive_candidate(candidate.id)

        terminal_status = _TERMINAL_STATUS[action]

        # R8.4 / R8.5: the Candidate transitioned to the terminal status and the
        # transition committed (the returned Candidate carries the terminal
        # status, the live record reflects it, and the committed snapshot does).
        assert returned.status == terminal_status
        persisted = await harness.candidate_repo.get_by_id(candidate.id)
        assert persisted is not None
        assert persisted.status == terminal_status
        snapshot = harness.candidate_repo.committed_snapshot(candidate.id)
        assert snapshot is not None
        assert snapshot["status"] == terminal_status
        # The terminal transition itself committed (before the cancel attempt).
        assert harness.session.commit_count > commits_before

        # Exactly one delete attempt was made, against the stored event id - the
        # 500 path does not retry (only a 401 would trigger a refresh-and-retry).
        assert len(harness.calendar.delete_calls) == 1
        assert harness.calendar.delete_calls[0].event_id == STORED_EVENT_ID

        # R8.6: a failed-cancellation audit entry exists, flagged success=False,
        # recording the calendar_event_id and the trigger.
        cancel_failed = harness.audit_sink.entries_for("interview_cancel_failed")
        assert len(cancel_failed) == 1
        entry = cancel_failed[0]
        assert entry.success is False
        assert entry.entity_id == candidate.id
        assert entry.new_value is not None
        assert entry.new_value["calendar_event_id"] == STORED_EVENT_ID
        assert entry.new_value["trigger"] == action
        assert entry.new_value["success"] is False

    asyncio.run(_run())
