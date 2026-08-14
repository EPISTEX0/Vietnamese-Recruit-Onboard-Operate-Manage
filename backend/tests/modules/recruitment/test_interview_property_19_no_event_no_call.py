"""Property 19: reject or archive without a stored event makes no Calendar call.

Feature: interview-calendar-scheduling, Property 19.

Validates: Requirement 8.3 - if an HR user rejects or archives a Candidate that
has NO stored ``calendar_event_id``, the Scheduling_System completes the status
transition WITHOUT calling Google Calendar.

The terminal actions (``reject_candidate`` / ``archive_candidate``) commit the
status transition and then invoke the best-effort
``_cancel_interview_event`` side-effect. That helper short-circuits when
``candidate.calendar_event_id is None`` (R8.3), so the Calendar adapter is never
touched and no cancellation audit entry (success or failure) is written.

This property is exercised end to end against the in-memory seams in
``_interview_support``. ``log_audit`` is replaced by the spy sink because the
transition writes its normal ``candidate_rejected`` / ``candidate_archived``
audit entry, and the real helper would call ``session.add``/``flush``
(unsupported by the fake session). For any Candidate seeded with
``calendar_event_id=None`` in a status that permits the chosen action, the test
asserts:

* the Candidate transitioned to ``rejected`` / ``archived`` accordingly;
* the Calendar adapter was never invoked (no create/patch/delete);
* no ``interview_event_cancelled`` and no ``interview_cancel_failed`` audit
  entries were written - only the normal terminal-transition audit entry.
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.recruitment.application.candidate_validators import VALID_TRANSITIONS
from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
)

# Statuses from which a transition to ``rejected`` is allowed, derived from the
# live state machine so the set tracks the source of truth.
_REJECT_SOURCES: set[str] = {
    status
    for status in CandidateStatus
    if CandidateStatus.REJECTED in VALID_TRANSITIONS.get(status, set())
}
# Statuses from which a transition to ``archived`` is allowed.
_ARCHIVE_SOURCES: set[str] = {
    status
    for status in CandidateStatus
    if CandidateStatus.ARCHIVED in VALID_TRANSITIONS.get(status, set())
}
# Statuses valid for BOTH actions, so a single drawn status works whichever
# action Hypothesis picks. Resolves to: new, reviewing, interview_scheduled.
COMMON_SOURCE_STATUSES: list[str] = sorted(_REJECT_SOURCES & _ARCHIVE_SOURCES)

# The terminal status + audit operation produced by each action.
_TARGET_STATUS: dict[str, str] = {
    "reject": CandidateStatus.REJECTED,
    "archive": CandidateStatus.ARCHIVED,
}
_TRANSITION_AUDIT: dict[str, str] = {
    "reject": "candidate_rejected",
    "archive": "candidate_archived",
}


# Feature: interview-calendar-scheduling, Property 19
@settings(max_examples=100, deadline=None)
@given(
    action=st.sampled_from(["reject", "archive"]),
    status=st.sampled_from(COMMON_SOURCE_STATUSES),
    reason=st.none() | st.text(max_size=1000),
)
def test_reject_or_archive_without_event_makes_no_calendar_call(
    action: str,
    status: str,
    reason: str | None,
) -> None:
    """Rejecting/archiving a Candidate with no stored event skips Calendar.

    For any Candidate whose ``calendar_event_id`` is ``None`` and whose status
    permits the chosen action, the terminal transition completes without
    invoking the Calendar adapter and without writing any cancellation audit
    entry.

    Validates: Requirement 8.3
    """

    async def _run() -> None:
        # A Candidate with NO stored interview event, in a status that permits
        # the drawn action.
        candidate = make_candidate(status=status)
        harness = build_calendar_harness(candidates=[candidate])
        # Precondition: nothing is booked on Calendar for this Candidate.
        assert await harness.interviews_for(candidate.id) == []

        with harness.audit_patch():
            if action == "reject":
                result = await harness.lifecycle.reject_candidate(candidate.id, reason=reason)
            else:
                result = await harness.lifecycle.archive_candidate(candidate.id)

        # The status transition completed (R8.3).
        assert result.status == _TARGET_STATUS[action]
        persisted = await harness.candidate_repo.get_by_id(candidate.id)
        assert persisted is not None
        assert persisted.status == _TARGET_STATUS[action]
        # No interview was conjured up along the way.
        assert await harness.interviews_for(candidate.id) == []

        # R8.3: no Calendar call of any kind (create/patch/delete) was made.
        assert harness.calendar.was_called is False
        assert harness.calendar.delete_calls == []

        # No cancellation audit entries (neither success nor failure) were
        # written - only the normal terminal-transition audit entry.
        assert harness.audit_sink.entries_for("interview_event_cancelled") == []
        assert harness.audit_sink.entries_for("interview_cancel_failed") == []
        assert len(harness.audit_sink.entries_for(_TRANSITION_AUDIT[action])) == 1

    asyncio.run(_run())
