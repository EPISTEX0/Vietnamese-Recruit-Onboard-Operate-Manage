"""Property 1: schedule-interview request field validation.

A schedule-interview request is accepted *if and only if* every request-field
rule holds: ``duration_minutes`` is within 15-180 inclusive, the interviewer-id
list length is within 1-10 inclusive, ``start`` is strictly after the current
time, and ``notes`` (when present) is at most 1000 characters. When any rule is
violated the request is rejected with a validation error (a plain ``ValueError``
that the API layer maps to 422) and no Google Calendar event is created.

**Validates: Requirements 1.2, 1.3, 1.4, 1.5**

The orchestration is exercised against the in-memory seams in
``_interview_support`` (a fake ``CalendarPort``, in-memory candidate/employee
repositories, and the identity grant seams), so no real Google Calendar API or
database is required. Request-field validation runs in step 1 of
``CandidateService.schedule_interview`` - before the Candidate is loaded, the
grant is checked, interviewers are resolved, or any Calendar call is made - so
every rejection happens with the Calendar adapter untouched. To make the
"accepted" half of the biconditional genuine, the other preconditions are held
valid: the Candidate is in a permitting status, the default grant is valid, and
every interviewer id (for in-range counts) matches a seeded Employee with a
usable email, so an all-valid request truly succeeds.

The rewritten ``schedule_interview`` reads ``datetime.now(UTC)`` directly for the
future-``start`` rule (no injected clock seam), so ``start`` is derived from the
real ``datetime.now(UTC)`` at call time with a generous (>= 1 minute) margin to
keep the boundary deterministic and the suite non-flaky.

Requirements: 1.2, 1.3, 1.4, 1.5
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.recruitment.application import interview_scheduler_service as candidate_service
from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_employee,
)

# Statuses from which a transition to ``interview_scheduled`` is permitted, so an
# all-valid request reaches and clears the status gate and actually schedules.
PERMITTING_STATUSES = (CandidateStatus.NEW, CandidateStatus.REVIEWING)

# Request-field bounds under test (mirrors ``_validate_schedule_request``).
MIN_DURATION, MAX_DURATION = 15, 180
MIN_INTERVIEWERS, MAX_INTERVIEWERS = 1, 10
MAX_NOTES = 1000
# Offsets range over a full year so far-future/far-past starts are exercised too.
ONE_YEAR_MINUTES = 60 * 24 * 365


def _durations() -> st.SearchStrategy[int]:
    """Draw ``duration_minutes`` across and beyond the valid 15-180 range."""
    return st.one_of(
        st.integers(min_value=MIN_DURATION, max_value=MAX_DURATION),  # valid
        st.integers(min_value=-60, max_value=MIN_DURATION - 1),  # below (incl 0/neg)
        st.integers(min_value=MAX_DURATION + 1, max_value=600),  # above
    )


def _notes() -> st.SearchStrategy[str | None]:
    """Draw ``notes`` as omitted, within 1000 chars, or beyond 1000 chars."""
    return st.one_of(
        st.none(),  # omitted (valid)
        st.text(max_size=MAX_NOTES),  # present, <= 1000 (valid)
        st.text(min_size=MAX_NOTES + 1, max_size=MAX_NOTES + 100),  # too long
    )


@st.composite
def _interviewer_sets(draw: st.DrawFn) -> tuple[list[Employee], list[UUID]]:
    """Draw an interviewer set whose length spans and exceeds the 1-10 range.

    Builds exactly ``count`` interviewer Employees (``count`` in 0-14), each with
    a unique, non-blank email, and returns them alongside their ids. Seeding a
    matching Employee for every id means that for in-range counts the not-found
    (R1.7) and missing-email (R10) checks pass, so the interviewer-count rule is
    the only interviewer-related thing that can fail - and an in-range count lets
    an otherwise-valid request schedule successfully. For out-of-range counts the
    count check rejects the request before interviewers are ever resolved, so the
    extra seeded Employees are simply never read.

    Returns:
        A tuple ``(employees, interviewer_ids)`` where ``employees`` are seeded
        into the harness and ``interviewer_ids`` are their ids in order.
    """
    count = draw(st.integers(min_value=0, max_value=MAX_INTERVIEWERS + 4))
    employees = [
        make_employee(email=f"interviewer{index}@example.com", full_name=f"Interviewer {index}")
        for index in range(count)
    ]
    interviewer_ids = [employee.id for employee in employees]
    return employees, interviewer_ids


# Feature: interview-calendar-scheduling, Property 1
@settings(max_examples=100, deadline=None)
@given(
    interviewers=_interviewer_sets(),
    duration_minutes=_durations(),
    notes=_notes(),
    status=st.sampled_from(PERMITTING_STATUSES),
    start_in_future=st.booleans(),
    start_offset_minutes=st.integers(min_value=1, max_value=ONE_YEAR_MINUTES),
)
def test_request_field_validation(
    interviewers: tuple[list[Employee], list[UUID]],
    duration_minutes: int,
    notes: str | None,
    status: CandidateStatus,
    start_in_future: bool,
    start_offset_minutes: int,
) -> None:
    """Acceptance holds exactly when all four request-field rules hold.

    For any schedule-interview request, the request is accepted iff
    ``duration_minutes`` is within 15-180, the interviewer-id list length is
    within 1-10, ``start`` is strictly after the current time, and ``notes``
    (when present) is at most 1000 characters; otherwise it is rejected with a
    ``ValueError`` and no Calendar event is created.

    Validates: Requirements 1.2, 1.3, 1.4, 1.5
    """
    employees, interviewer_ids = interviewers

    async def _run() -> None:
        candidate = make_candidate(status=status)
        harness = build_calendar_harness(candidates=[candidate], employees=employees)

        # ``start`` is derived from the real clock the service reads, with a
        # >= 1 minute margin on either side so the strict future/past boundary
        # is deterministic.
        now = datetime.now(UTC)
        offset = timedelta(minutes=start_offset_minutes)
        start = now + offset if start_in_future else now - offset

        # The expected acceptance is the conjunction of the four field rules.
        duration_valid = MIN_DURATION <= duration_minutes <= MAX_DURATION
        count_valid = MIN_INTERVIEWERS <= len(interviewer_ids) <= MAX_INTERVIEWERS
        notes_valid = notes is None or len(notes) <= MAX_NOTES
        start_valid = start_in_future
        expected_valid = duration_valid and count_valid and notes_valid and start_valid

        # Route audit writes to the spy so the success path does not touch a real
        # audit repository (the fake session has no ``add``/``flush``).
        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            if expected_valid:
                result = await harness.service.schedule_interview(
                    candidate.id,
                    start=start,
                    duration_minutes=duration_minutes,
                    interviewer_ids=interviewer_ids,
                    notes=notes,
                )

                # Accepted: the Candidate is scheduled and an event was created.
                # The event reference is persisted on the Interview row the
                # schedule creates, not on the Candidate.
                assert result.status == CandidateStatus.INTERVIEW_SCHEDULED
                interview = await harness.scheduled_interview(candidate.id)
                assert interview is not None
                assert interview.calendar_event_id is not None
                assert harness.calendar.was_called is True
                assert len(harness.calendar.create_calls) == 1
            else:
                with pytest.raises(ValueError):
                    await harness.service.schedule_interview(
                        candidate.id,
                        start=start,
                        duration_minutes=duration_minutes,
                        interviewer_ids=interviewer_ids,
                        notes=notes,
                    )

                # Rejected before any Calendar call: the adapter is untouched and
                # the Candidate is left exactly as it was.
                assert harness.calendar.was_called is False
                live = await harness.candidate_repo.get_by_id(candidate.id)
                assert live is not None
                assert live.status == status
                # No event reference, scheduled start, or applied timezone was
                # stored: all three live on the Interview row a successful
                # schedule creates, and no such row exists.
                assert await harness.interviews_for(candidate.id) == []

    asyncio.run(_run())
