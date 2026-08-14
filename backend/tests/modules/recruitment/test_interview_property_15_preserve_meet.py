"""Property 15: rescheduling preserves the existing Google Meet link.

Feature: interview-calendar-scheduling, Property 15.

Validates: Requirements 7.2.

For any reschedule of a Candidate whose Interview already stores a
``calendar_event_id``, ``InterviewSchedulerService.reschedule_interview`` patches the
*existing* Google Calendar event in place rather than creating a new one. The
patch specification handed to the Calendar adapter must NOT request a new
conferencing link (``request_meet_link is False``), which is exactly what makes
Google Calendar leave the event's existing Google Meet link untouched (the
adapter omits ``conferenceData`` from the PATCH body for such specs). Requesting
a fresh Meet link on patch would mint a new conference and discard the link
attendees already hold, so the absence of that request is the testable surrogate
for "the existing Meet link is preserved" (R7.2).

The property is exercised end to end against the in-memory seams in
``_interview_support``: a fake ``CalendarPort`` records each adapter call
(method, access token, target event id, and the ``CalendarEventSpec``); an
in-memory candidate repository/session backs persistence; and the module-level
``log_audit`` is replaced by the spy sink (the real helper would call
``session.add``/``flush``, which the fake session does not implement).

Starts are drawn as tz-aware UTC datetimes far in the future so the
future-``start`` rule (R1.4) always passes while the requested instant stays
well-defined.
"""

# Feature: interview-calendar-scheduling, Property 15

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.recruitment.application import interview_scheduler_service as candidate_service
from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    iana_timezones,
    make_candidate,
    make_employee,
    make_interview,
)

# A pre-existing Calendar event the reschedule will patch in place.
_EXISTING_EVENT_ID = "evt-existing-0001"

# Valid, unambiguously-future starts: tz-aware UTC datetimes far in the future
# (well beyond any execution-time clock skew), so R1.4 always passes while the
# requested instant stays well-defined.
_FUTURE_START = st.datetimes(
    min_value=datetime(2090, 1, 1, 0, 0, 0),
    max_value=datetime(2099, 12, 31, 23, 59, 59),
    timezones=st.just(UTC),
)
# Valid durations span the inclusive 15..180 minute range (R1.2).
_DURATIONS = st.integers(min_value=15, max_value=180)
# Valid interviewer counts span the inclusive 1..10 range (R1.3).
_INTERVIEWER_COUNTS = st.integers(min_value=1, max_value=10)


@settings(max_examples=100, deadline=None)
@given(
    start=_FUTURE_START,
    duration_minutes=_DURATIONS,
    interviewer_count=_INTERVIEWER_COUNTS,
    org_timezone=iana_timezones(),
    notes=st.none() | st.text(max_size=1000),
)
def test_reschedule_patch_spec_never_requests_a_new_meet_link(
    start: datetime,
    duration_minutes: int,
    interviewer_count: int,
    org_timezone: str,
    notes: str | None,
) -> None:
    """A reschedule patch spec never requests a new Meet link (R7.2).

    Validates: Requirements 7.2
    """

    async def _run() -> None:
        employees = [
            make_employee(email=f"interviewer{i}@example.com") for i in range(interviewer_count)
        ]
        interviewer_ids: list[UUID] = [employee.id for employee in employees]

        # A Candidate that already has a scheduled interview (its Interview
        # carries the stored event id + start) — the precondition for a
        # reschedule (R7.1/R7.5).
        candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
        existing_interview = make_interview(
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
            start_at=datetime(2089, 6, 1, 9, 0, 0, tzinfo=UTC),
            timezone="UTC",
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
            interviews=[existing_interview],
            org_timezone=org_timezone,
        )

        # The real ``log_audit`` would hit ``session.add``/``flush`` (unsupported
        # by the fake session); the spy sink records entries without persisting.
        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            await harness.service.reschedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=interviewer_ids,
                notes=notes,
            )

        # R7.2: the reschedule patches the existing event exactly once and never
        # creates a new one.
        assert len(harness.calendar.patch_calls) == 1
        assert len(harness.calendar.create_calls) == 0

        # R7.2: the patch targets the exact stored event id and the patch spec
        # does NOT request a new conferencing link, so the existing Google Meet
        # link is preserved.
        patch_call = harness.calendar.patch_calls[0]
        assert patch_call.event_id == _EXISTING_EVENT_ID
        assert patch_call.spec is not None
        assert patch_call.spec.request_meet_link is False

    asyncio.run(_run())
