"""Property-based test for unmatched interviewer ids on schedule.

# Feature: interview-calendar-scheduling, Property 2

Property 2: Unmatched interviewer ids are reported.

For any set of existing Employees and any list of interviewer ids mixing
existing and non-existing ids, :meth:`CandidateService.schedule_interview` is
rejected with an :class:`InterviewerNotFoundError`, the error's listed unmatched
ids equal exactly the set of ids that do not correspond to an Employee, and no
Google Calendar event is created.

The interviewer-resolution check runs in ``schedule_interview`` *after* request
validation (duration 15-180, 1-10 interviewers, future ``start``), the
status-transition check, and the Calendar-grant check, but *before* the Calendar
adapter is ever invoked and before any audit write. The test therefore drives a
``NEW`` Candidate with a valid grant, a future ``start``, a 60-minute duration,
and an interviewer-id list of 1-10 unique ids so that the unmatched-id check is
the rule that fires. Because this rejection path never reaches ``log_audit``,
the module-level audit sink does not need to be patched.

Validates: Requirements 1.7
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
from src.modules.recruitment.domain.exceptions import InterviewerNotFoundError
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_employee,
)

# A 60-minute duration sits inside the valid 15-180 range, so request
# validation passes and the unmatched-interviewer check is what rejects.
_VALID_DURATION_MINUTES = 60


@st.composite
def _mixed_interviewer_ids(
    draw: st.DrawFn,
) -> tuple[list[Employee], list[UUID], set[UUID]]:
    """Draw a 1-10 id list mixing seeded Employees with non-existing ids.

    Generates ``total`` (1-10) unique UUIDs, designates the first ``n_seeded``
    of them as existing Employees and the remainder as non-existing ids, and
    shuffles the combined list so the unmatched ids appear in arbitrary
    positions. ``n_seeded`` is capped at ``total - 1`` so at least one id is
    always unmatched (otherwise scheduling would not be rejected).

    Returns:
        A tuple of ``(employees, interviewer_ids, expected_unmatched)`` where
        ``employees`` are the seeded interviewer Employees, ``interviewer_ids``
        is the shuffled request list, and ``expected_unmatched`` is the set of
        ids that do not correspond to an Employee.
    """
    total = draw(st.integers(min_value=1, max_value=10))
    ids = draw(st.lists(st.uuids(), min_size=total, max_size=total, unique=True))
    n_seeded = draw(st.integers(min_value=0, max_value=total - 1))

    seeded_ids = ids[:n_seeded]
    unmatched_ids = ids[n_seeded:]

    employees = [
        make_employee(employee_id=eid, email=f"emp-{eid.hex[:8]}@example.com") for eid in seeded_ids
    ]
    interviewer_ids = draw(st.permutations(ids))
    return employees, list(interviewer_ids), set(unmatched_ids)


async def _run_unmatched_interviewers(
    employees: list[Employee],
    interviewer_ids: list[UUID],
    expected_unmatched: set[UUID],
) -> None:
    """Assert scheduling is rejected and the unmatched ids are reported exactly."""
    candidate = make_candidate(status=CandidateStatus.NEW)
    harness = build_calendar_harness(candidates=[candidate], employees=employees)

    # A future start with a valid duration so request validation passes and the
    # unmatched-id check is the rule that rejects (R1.4 uses real "now").
    start = datetime.now(UTC) + timedelta(days=1)

    with pytest.raises(InterviewerNotFoundError) as exc_info:
        await harness.service.schedule_interview(
            candidate.id,
            start=start,
            duration_minutes=_VALID_DURATION_MINUTES,
            interviewer_ids=interviewer_ids,
            notes=None,
        )

    # The error lists exactly the ids that do not correspond to an Employee.
    assert set(exc_info.value.unmatched_ids) == expected_unmatched

    # No Calendar event was created (the adapter was never invoked) and the
    # Candidate did not transition. The event reference lives on the Interview
    # row a successful schedule creates, so "no event was stored" is asserted as
    # "no Interview exists for this Candidate".
    assert harness.calendar.was_called is False
    assert candidate.status == CandidateStatus.NEW
    assert await harness.interviews_for(candidate.id) == []


# Feature: interview-calendar-scheduling, Property 2
@settings(max_examples=100, deadline=None)
@given(case=_mixed_interviewer_ids())
def test_unmatched_interviewer_ids_are_reported(
    case: tuple[list[Employee], list[UUID], set[UUID]],
) -> None:
    """Unmatched interviewer ids are reported and no Calendar event is created.

    Validates: Requirements 1.7
    """
    employees, interviewer_ids, expected_unmatched = case
    asyncio.run(_run_unmatched_interviewers(employees, interviewer_ids, expected_unmatched))


def test_mixed_strategy_always_has_at_least_one_unmatched() -> None:
    """Guard: a concrete mix of one existing and one missing id is rejected.

    A worked example that anchors the property: with a single seeded Employee
    and a single non-existing id, scheduling is rejected reporting exactly the
    non-existing id and the Calendar adapter is never invoked.
    """
    existing = make_employee(email="present@example.com")
    missing_id = make_employee().id  # a fresh id that is NOT seeded

    asyncio.run(
        _run_unmatched_interviewers(
            employees=[existing],
            interviewer_ids=[existing.id, missing_id],
            expected_unmatched={missing_id},
        )
    )
