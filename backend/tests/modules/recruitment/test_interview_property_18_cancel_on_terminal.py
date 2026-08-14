"""Property test for reject/archive cancelling the interview event (Property 18).

Feature: interview-calendar-scheduling, Property 18

Property 18 — Reject or archive cancels the event when present:
    For any Candidate that has a stored ``calendar_event_id``, rejecting OR
    archiving it invokes the adapter's delete on that EXACT event id, completes
    the status transition (``rejected``/``archived``), and writes an audit entry
    recording the cancellation action, the acting HR user, the Candidate id, and
    the cancelled ``calendar_event_id``.

A Candidate seeded in ``interview_scheduled`` both carries a stored interview
event and can transition to either ``rejected`` or ``archived`` (per the state
machine: ``interview_scheduled → accepted, rejected, archived``). The action is
drawn from ``{"reject", "archive"}`` so a single property covers both terminal
transitions (R8.1, R8.2). The acting HR user's Calendar grant is valid and the
fake adapter's default ``delete_event`` succeeds, so the happy-path cancellation
runs end to end.

The cancellation side-effect runs AFTER the terminal transition has committed
and writes the audit via the module-level ``log_audit``; the real helper hits
``session.add``/``flush`` (unsupported by the fake session), so it is replaced
with the spy sink that records entries without persisting.

Validates: Requirements 8.1, 8.2, 12.3
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_interview,
)

# The known stored event id the cancellation must target EXACTLY.
_STORED_EVENT_ID = "evt-existing-1"

# A pre-existing scheduled start for the seeded interview (realistic but
# irrelevant to reject/archive, which do not read it).
_INTERVIEW_START = datetime(2090, 6, 1, 9, 0, 0, tzinfo=UTC)

# The terminal actions that must cancel a stored event (R8.1, R8.2).
_ACTIONS = st.sampled_from(["reject", "archive"])

# Expected final status per action.
_FINAL_STATUS = {
    "reject": CandidateStatus.REJECTED,
    "archive": CandidateStatus.ARCHIVED,
}


@settings(max_examples=100, deadline=None)
@given(
    action=_ACTIONS,
    reason=st.one_of(st.none(), st.text(max_size=1000)),
)
def test_reject_or_archive_cancels_stored_event(action: str, reason: str | None) -> None:
    """Reject/archive on a stored event deletes it, transitions, and audits.

    For any Candidate carrying a stored ``calendar_event_id``, the chosen
    terminal action (reject or archive):

    * invokes the adapter's ``delete_event`` exactly once on the EXACT stored
      event id (R8.1, R8.2);
    * completes the status transition to ``rejected``/``archived``; and
    * writes an ``interview_event_cancelled`` audit entry recording the acting
      HR user, the Candidate id, the cancelled ``calendar_event_id``, and the
      triggering action (R12.3).

    Validates: Requirements 8.1, 8.2, 12.3
    """

    async def _run() -> None:
        candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
        booked = make_interview(
            candidate_id=candidate.id,
            calendar_event_id=_STORED_EVENT_ID,
            start_at=_INTERVIEW_START,
            timezone="Asia/Ho_Chi_Minh",
        )
        harness = build_calendar_harness(candidates=[candidate], interviews=[booked])

        # Precondition for this property: a stored interview event exists.
        scheduled = await harness.scheduled_interview(candidate.id)
        assert scheduled is not None
        assert scheduled.calendar_event_id == _STORED_EVENT_ID

        with harness.audit_patch():
            if action == "reject":
                returned = await harness.lifecycle.reject_candidate(candidate.id, reason)
            else:
                returned = await harness.lifecycle.archive_candidate(candidate.id)

        # R8.1 / R8.2: exactly one delete call, targeting the EXACT stored id;
        # no spurious create/patch calls.
        assert len(harness.calendar.delete_calls) == 1
        assert harness.calendar.delete_calls[0].event_id == _STORED_EVENT_ID
        assert harness.calendar.create_calls == []
        assert harness.calendar.patch_calls == []

        # The status transition completed (R8.1 / R8.2).
        assert returned.status == _FINAL_STATUS[action]
        persisted = await harness.candidate_repo.get_by_id(candidate.id)
        assert persisted is not None
        assert persisted.status == _FINAL_STATUS[action]

        # R12.3: an ``interview_event_cancelled`` audit entry records the
        # cancellation action, the acting HR user, the Candidate id, and the
        # cancelled ``calendar_event_id``.
        cancelled_entries = harness.audit_sink.entries_for("interview_event_cancelled")
        assert len(cancelled_entries) == 1
        entry = cancelled_entries[0]
        assert entry.user_id == harness.user_id
        assert entry.entity_id == candidate.id
        assert entry.success is True
        assert entry.new_value is not None
        assert entry.new_value["calendar_event_id"] == _STORED_EVENT_ID
        assert entry.new_value["trigger"] == action

    asyncio.run(_run())
