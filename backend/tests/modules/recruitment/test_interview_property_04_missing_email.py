"""Property 4: interviewer missing email blocks scheduling.

Validates that a schedule-interview request is rejected whenever at least one
matched interviewer Employee has a blank email, the offending interviewer is
identified in the error, the Candidate status is left unchanged, and no Calendar
event is created (Requirements 10.1, 10.2).

The orchestration is exercised against the in-memory seams in
``_interview_support`` (a fake ``CalendarPort``, in-memory candidate/employee
repositories, and the identity grant seams), so no real Google Calendar API or
database is required. All interviewer ids passed are seeded Employees so the
not-found check (R1.7) passes and the blank-email check (R10) is the rule under
test - mirroring the ordering in ``CandidateService._resolve_interviewers``
(not-found first, then blank-email).

Requirements: 10.1, 10.2
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import InterviewerMissingEmailError
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_employee,
)

# Statuses from which a transition to ``interview_scheduled`` is permitted, so
# the request reaches the interviewer-resolution step rather than being blocked
# earlier by the state machine (R2.4).
PERMITTING_STATUSES = (CandidateStatus.NEW, CandidateStatus.REVIEWING)

# Variants of a "blank" email: empty, whitespace-only, or mixed whitespace.
# ``_resolve_interviewers`` strips the email before checking, so each of these
# is treated as missing (R10).
BLANK_EMAILS = ("", " ", "   ", "\t", "\n", "  \t \n ")


@st.composite
def _interviewers_with_blank(
    draw: st.DrawFn,
) -> tuple[list[Employee], list[UUID], set[UUID]]:
    """Generate an interviewer set with at least one blank-email Employee.

    Builds between 1 and 10 interviewer Employees, each with either a valid
    unique email or a blank email, guaranteeing at least one blank. Every
    Employee is intended to be seeded into the harness so all ids match an
    existing Employee (the not-found check passes) and the blank-email rule is
    the one exercised. The returned interviewer-id list is a permutation, so the
    "first blank in request order" can be any of the blank interviewers.

    Returns:
        A tuple ``(employees, interviewer_ids, blank_ids)`` where ``employees``
        are all seeded Employees, ``interviewer_ids`` is a permutation of their
        ids to pass to the service, and ``blank_ids`` is the set of ids whose
        Employee has a blank email.
    """
    blank_flags = draw(st.lists(st.booleans(), min_size=1, max_size=10).filter(any))

    employees: list[Employee] = []
    blank_ids: set[UUID] = set()
    for index, is_blank in enumerate(blank_flags):
        if is_blank:
            email = draw(st.sampled_from(BLANK_EMAILS))
            employee = make_employee(email=email, full_name=f"Blank Interviewer {index}")
            blank_ids.add(employee.id)
        else:
            employee = make_employee(
                email=f"valid{index}@example.com", full_name=f"Valid Interviewer {index}"
            )
        employees.append(employee)

    interviewer_ids = draw(st.permutations([employee.id for employee in employees]))
    return employees, list(interviewer_ids), blank_ids


# Feature: interview-calendar-scheduling, Property 4
@settings(max_examples=100, deadline=None)
@given(
    data=_interviewers_with_blank(),
    status=st.sampled_from(PERMITTING_STATUSES),
    duration_minutes=st.integers(min_value=15, max_value=180),
    start_offset_minutes=st.integers(min_value=1, max_value=60 * 24 * 365),
    notes=st.none() | st.text(max_size=1000),
)
def test_interviewer_missing_email_blocks_scheduling(
    data: tuple[list[Employee], list[UUID], set[UUID]],
    status: CandidateStatus,
    duration_minutes: int,
    start_offset_minutes: int,
    notes: str | None,
) -> None:
    """A blank interviewer email blocks scheduling without side effects.

    For any interviewer set containing at least one blank-email Employee, the
    schedule request is rejected with an ``InterviewerMissingEmailError`` that
    identifies one of the blank interviewers, the Candidate status is unchanged,
    and the Calendar adapter is never invoked.

    Validates: Requirements 10.1, 10.2
    """
    employees, interviewer_ids, blank_ids = data

    async def _run() -> None:
        candidate = make_candidate(status=status)
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
        )
        # Strictly-future, tz-aware start so request-field validation (R1.4)
        # passes and the flow reaches interviewer resolution.
        start = datetime.now(UTC) + timedelta(minutes=start_offset_minutes)

        with pytest.raises(InterviewerMissingEmailError) as exc_info:
            await harness.service.schedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=interviewer_ids,
                notes=notes,
            )

        error = exc_info.value
        # R10.1: the error identifies one of the blank-email interviewers and
        # carries it in the structured details payload.
        assert error.interviewer_id in blank_ids
        assert error.details == {"interviewer_id": str(error.interviewer_id)}

        # R10.2: no Calendar event was created.
        assert harness.calendar.was_called is False

        # R10.2: the Candidate status is left unchanged and no event reference,
        # scheduled start, or applied timezone was persisted -- all three live on
        # the Interview row a successful schedule creates, and none exists.
        live = await harness.candidate_repo.get_by_id(candidate.id)
        assert live is not None
        assert live.status == status
        assert await harness.interviews_for(candidate.id) == []

    asyncio.run(_run())
