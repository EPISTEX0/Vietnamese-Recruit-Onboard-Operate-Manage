"""Property test for the Calendar grant guard (Property 3).

Feature: interview-calendar-scheduling, Property 3

Property 3 — Calendar grant guard:
    For any schedule or reschedule request, if the Organization's Google
    Calendar connection is not usable, the request is rejected with a
    re-consent error (``CalendarGrantMissingError``), the Candidate record and
    its stored interview are left unchanged, and the Calendar adapter is never
    invoked.

Interviews are booked on the Organization's *selected recruitment calendar*, so
the authority that governs a Calendar write is the Organization's Google
connection rather than the acting HR user's personal OAuth grant. The guard is
therefore driven through :class:`FakeOrgConnectionRepository`, made unusable two
ways, both exercised by Hypothesis:

* ``connected=False`` — no connection row at all (Google was never linked);
* ``status="revoked"`` — a connection row exists but the link is no longer live.

Both still raise the same re-consent ``CalendarGrantMissingError`` (403 /
``CALENDAR_GRANT_MISSING``) that the per-user grant guard used to raise, so the
rule being pinned down — *no authority to write, no Calendar call, no state
change* — is unchanged; only the authority it reads has moved.

``schedule_interview`` asserts the connection *after* request-field validation
and the status-transition check but *before* resolving interviewers or touching
the Calendar adapter, so every generated request uses valid fields and a
permitting Candidate status to ensure the guard is the rule under test. The
reschedule case seeds an Interview that already carries a stored
``calendar_event_id``/``start_at`` so the request reaches the guard rather than
the "no interview to reschedule" check.

Validates: Requirements 9.1, 9.2, 9.3
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.recruitment.application.interview_scheduler_service import (
    InterviewSchedulerService as CandidateService,
)
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import CalendarGrantMissingError
from tests.modules.recruitment._interview_support import (
    FakeOrgConnectionRepository,
    build_calendar_harness,
    make_candidate,
    make_employee,
    make_interview,
)

# Candidate statuses from which a transition to ``interview_scheduled`` is
# permitted (so the schedule request reaches the grant assertion, R9).
_PERMITTING_STATUSES = (CandidateStatus.NEW, CandidateStatus.REVIEWING)

# ``reschedule_interview`` is implemented in task 7.1; skip its property until
# the method exists so this file stays green on the schedule-only milestone.
_HAS_RESCHEDULE = hasattr(CandidateService, "reschedule_interview")


def _interviewers(count: int) -> list[Employee]:
    """Build ``count`` interviewer Employees with distinct, non-blank emails."""
    return [make_employee(email=f"interviewer{i}@example.com") for i in range(count)]


# ─── Property 3 (schedule path) — R9.1, R9.3 ───────────────────────────


@settings(max_examples=100, deadline=None)
@given(
    duration_minutes=st.integers(min_value=15, max_value=180),
    interviewer_count=st.integers(min_value=1, max_value=10),
    start_offset_minutes=st.integers(min_value=10, max_value=525_600),
    use_unlinked_connection=st.booleans(),
    status=st.sampled_from(_PERMITTING_STATUSES),
    notes=st.one_of(st.none(), st.text(max_size=1000)),
)
def test_schedule_blocked_when_calendar_grant_missing(
    duration_minutes: int,
    interviewer_count: int,
    start_offset_minutes: int,
    use_unlinked_connection: bool,
    status: str,
    notes: str | None,
) -> None:
    """A missing Calendar grant blocks scheduling without touching the adapter.

    For any valid schedule request and any permitting Candidate status, when the
    Organization's Google Calendar connection is not usable the request is
    rejected with a re-consent ``CalendarGrantMissingError`` (403 /
    ``CALENDAR_GRANT_MISSING``), the Calendar adapter is never invoked, and the
    Candidate record is left exactly as it was.

    Validates: Requirements 9.1, 9.3
    """

    async def _run() -> None:
        candidate = make_candidate(status=status)
        interviewers = _interviewers(interviewer_count)

        # Two ways to leave the Organization unable to write to Calendar: no
        # connection row at all, or a row whose link is no longer live.
        connection_repo = (
            FakeOrgConnectionRepository(connected=False)
            if use_unlinked_connection
            else FakeOrgConnectionRepository(status="revoked")
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=interviewers,
            connection_repo=connection_repo,
        )

        before = harness.candidate_repo.committed_snapshot(candidate.id)
        start = datetime.now(UTC) + timedelta(minutes=start_offset_minutes)

        with pytest.raises(CalendarGrantMissingError) as exc_info:
            await harness.service.schedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=[e.id for e in interviewers],
                notes=notes,
            )

        # The error directs the user to re-consent (R9.1).
        assert exc_info.value.error_code == "CALENDAR_GRANT_MISSING"
        assert exc_info.value.status_code == 403

        # The Calendar adapter was never invoked (R9.3).
        assert harness.calendar.was_called is False
        assert harness.calendar.calls == []

        # The Candidate record is left unchanged (R9.3): the committed snapshot
        # is identical and the live entity carries no interview references, and
        # no transaction was committed or rolled back.
        after = harness.candidate_repo.committed_snapshot(candidate.id)
        assert after == before
        live = await harness.candidate_repo.get_by_id(candidate.id)
        assert live is not None
        assert live.status == status
        # No interview references were stored: the event id, the scheduled
        # start, and the applied timezone all live on the Interview row a
        # successful schedule creates, and no such row exists.
        assert await harness.interviews_for(candidate.id) == []
        assert harness.session.commit_count == 0
        assert harness.session.rollback_count == 0

    asyncio.run(_run())


# ─── Property 3 (reschedule path) — R9.2, R9.3 ─────────────────────────


@pytest.mark.skipif(
    not _HAS_RESCHEDULE,
    reason="reschedule_interview is implemented in task 7.1; grant guard covered there",
)
@settings(max_examples=100, deadline=None)
@given(
    duration_minutes=st.integers(min_value=15, max_value=180),
    interviewer_count=st.integers(min_value=1, max_value=10),
    start_offset_minutes=st.integers(min_value=10, max_value=525_600),
    use_unlinked_connection=st.booleans(),
    notes=st.one_of(st.none(), st.text(max_size=1000)),
)
def test_reschedule_blocked_when_calendar_grant_missing(
    duration_minutes: int,
    interviewer_count: int,
    start_offset_minutes: int,
    use_unlinked_connection: bool,
    notes: str | None,
) -> None:
    """A missing Calendar grant blocks rescheduling without touching the adapter.

    For any reschedule request against a Candidate that already has a stored
    interview, when the Organization's Google Calendar connection is not usable
    the request is rejected with a re-consent ``CalendarGrantMissingError``, the
    Calendar adapter is never invoked, and the stored ``calendar_event_id`` and
    scheduled ``start_at`` on the Interview are left unchanged.

    Validates: Requirements 9.2, 9.3
    """

    async def _run() -> None:
        existing_event_id = "evt-existing-0001"
        existing_start = datetime(2025, 6, 1, 9, 0, tzinfo=UTC)
        candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
        # The stored interview the reschedule must leave untouched. Its calendar
        # references live on the Interview entity, not on the Candidate.
        existing_interview = make_interview(
            candidate_id=candidate.id,
            calendar_event_id=existing_event_id,
            start_at=existing_start,
            timezone="Asia/Ho_Chi_Minh",
        )
        interviewers = _interviewers(interviewer_count)

        connection_repo = (
            FakeOrgConnectionRepository(connected=False)
            if use_unlinked_connection
            else FakeOrgConnectionRepository(status="revoked")
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=interviewers,
            interviews=[existing_interview],
            connection_repo=connection_repo,
        )

        before = harness.candidate_repo.committed_snapshot(candidate.id)
        start = datetime.now(UTC) + timedelta(minutes=start_offset_minutes)

        with pytest.raises(CalendarGrantMissingError) as exc_info:
            await harness.service.reschedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=[e.id for e in interviewers],
                notes=notes,
            )

        assert exc_info.value.error_code == "CALENDAR_GRANT_MISSING"
        assert exc_info.value.status_code == 403

        # The Calendar adapter was never invoked (R9.3).
        assert harness.calendar.was_called is False
        assert harness.calendar.calls == []

        # The stored interview references are left unchanged (R9.3).
        after = harness.candidate_repo.committed_snapshot(candidate.id)
        assert after == before
        live = await harness.candidate_repo.get_by_id(candidate.id)
        assert live is not None
        assert live.status == CandidateStatus.INTERVIEW_SCHEDULED
        live_interview = await harness.scheduled_interview(candidate.id)
        assert live_interview is not None
        assert live_interview.calendar_event_id == existing_event_id
        assert live_interview.start_at == existing_start
        assert harness.session.commit_count == 0
        assert harness.session.rollback_count == 0

    asyncio.run(_run())
