"""Property test for reschedule with no stored event (Property 17).

Feature: interview-calendar-scheduling, Property 17

Property 17 — Reschedule without a stored event is rejected:
    For any Candidate that has no scheduled Interview, a reschedule
    request is rejected with an error indicating no interview exists to
    reschedule (``NoInterviewToRescheduleError``), and the Calendar adapter is
    never invoked.

``reschedule_interview`` performs the "no interview to reschedule" check
(step 1) *before* any other check or Calendar call — in particular before the
Organization-connection assertion and before resolving interviewers. So for any
Candidate with no scheduled Interview the request must raise
``NoInterviewToRescheduleError`` regardless of the connection state or the
request fields, and the adapter must never be touched. To pin that ordering, the
property exercises both a live and an unlinked Organization connection and
asserts the no-event error wins either way.

Validates: Requirements 7.5
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import NoInterviewToRescheduleError
from tests.modules.recruitment._interview_support import (
    FakeOrgConnectionRepository,
    build_calendar_harness,
    make_candidate,
    make_employee,
)

# Candidate statuses exercised — none of them is seeded with an Interview, so
# there is no interview to reschedule. The no-event guard runs first, so the
# specific status is irrelevant to the outcome; a mix is used to make that
# explicit.
_STATUSES = (
    CandidateStatus.NEW,
    CandidateStatus.REVIEWING,
    CandidateStatus.INTERVIEW_SCHEDULED,
)


def _interviewers(count: int) -> list[Employee]:
    """Build ``count`` interviewer Employees with distinct, non-blank emails."""
    return [make_employee(email=f"interviewer{i}@example.com") for i in range(count)]


@settings(max_examples=100, deadline=None)
@given(
    duration_minutes=st.integers(min_value=15, max_value=180),
    interviewer_count=st.integers(min_value=1, max_value=10),
    start_offset_minutes=st.integers(min_value=10, max_value=525_600),
    status=st.sampled_from(_STATUSES),
    use_valid_grant=st.booleans(),
    notes=st.one_of(st.none(), st.text(max_size=1000)),
)
def test_reschedule_without_stored_event_is_rejected(
    duration_minutes: int,
    interviewer_count: int,
    start_offset_minutes: int,
    status: str,
    use_valid_grant: bool,
    notes: str | None,
) -> None:
    """A Candidate with no stored event cannot be rescheduled (R7.5).

    For any Candidate with no scheduled Interview, a reschedule request is
    rejected with ``NoInterviewToRescheduleError`` (409 /
    ``NO_INTERVIEW_TO_RESCHEDULE``), the Calendar adapter is never invoked, and
    the Candidate record is left exactly as it was. This holds whether or not
    the Organization Calendar connection is usable, because the no-event check
    runs before that guard.

    Validates: Requirements 7.5
    """

    async def _run() -> None:
        candidate = make_candidate(status=status)
        interviewers = _interviewers(interviewer_count)

        if use_valid_grant:
            harness = build_calendar_harness(candidates=[candidate], employees=interviewers)
        else:
            # An unusable Organization Calendar connection must not change the
            # outcome: the no-event check is first, so
            # ``NoInterviewToRescheduleError`` still wins over the
            # ``CalendarGrantMissingError`` the guard would otherwise raise.
            harness = build_calendar_harness(
                candidates=[candidate],
                employees=interviewers,
                connection_repo=FakeOrgConnectionRepository(connected=False),
            )

        # Precondition for this property: no stored interview event exists.
        assert await harness.interviews_for(candidate.id) == []

        before = harness.candidate_repo.committed_snapshot(candidate.id)
        start = datetime.now(UTC) + timedelta(minutes=start_offset_minutes)

        with pytest.raises(NoInterviewToRescheduleError) as exc_info:
            await harness.service.reschedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=[e.id for e in interviewers],
                notes=notes,
            )

        # The error indicates no interview exists to reschedule (R7.5).
        assert exc_info.value.error_code == "NO_INTERVIEW_TO_RESCHEDULE"
        assert exc_info.value.status_code == 409

        # The Calendar adapter was never invoked (R7.5).
        assert harness.calendar.was_called is False
        assert harness.calendar.calls == []

        # The Candidate record is left unchanged: the committed snapshot is
        # identical, still no Interview row was created (so no event reference,
        # scheduled start, or applied timezone was stored), and no transaction
        # was committed or rolled back.
        after = harness.candidate_repo.committed_snapshot(candidate.id)
        assert after == before
        live = await harness.candidate_repo.get_by_id(candidate.id)
        assert live is not None
        assert live.status == status
        assert await harness.interviews_for(candidate.id) == []
        assert harness.session.commit_count == 0
        assert harness.session.rollback_count == 0

    asyncio.run(_run())
