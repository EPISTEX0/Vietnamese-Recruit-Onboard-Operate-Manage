"""Property 12: atomic rollback on a Google Calendar create failure.

Feature: interview-calendar-scheduling, Property 12.

Validates: Requirements 3.1, 3.2, 3.3, 3.4 - if Google Calendar event creation
fails during a schedule-interview request, the Scheduling_System leaves the
Candidate status unchanged (R3.1), stores no ``calendar_event_id`` (R3.2),
returns an error describing the Calendar failure (R3.3), and rolls back all
database changes so the Candidate record matches its pre-request state (R3.4).

The property is exercised end to end against the in-memory seams in
``_interview_support``. The fake ``CalendarPort`` is scripted so its single
``create_event`` call raises ``CalendarEventCreateFailedError`` (the canonical
error the real adapter raises after retries + a token refresh). The in-memory
candidate repository/session model commit/rollback: the service calls
``session.rollback()`` on the failure path, and the committed snapshot
(``candidate_repo.committed_snapshot``) lets us assert the persisted Candidate
is byte-for-byte its pre-request self. ``log_audit`` is replaced by the spy sink
because the failure path writes a failure audit entry, and the real helper would
call ``session.add``/``flush`` (unsupported by the fake session).

For every pre-request Candidate snapshot (any permitting status, no prior
interview fields) and any valid request, a create failure must:

* raise ``CalendarEventCreateFailedError`` (R3.3);
* leave the persisted Candidate exactly as before - status unchanged (R3.1) -
  and leave no Interview row behind, so no ``calendar_event_id`` / scheduled
  ``start_at`` / applied ``timezone`` was stored anywhere (R3.2, R3.4);
* drive at least one ``session.rollback()`` (R3.4); and
* have made exactly one (failed) Calendar create attempt.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.recruitment.application import interview_scheduler_service as candidate_service
from src.modules.recruitment.application.candidate_validators import VALID_TRANSITIONS
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import CalendarEventCreateFailedError
from tests.modules.recruitment._interview_support import (
    FakeCalendarPort,
    build_calendar_harness,
    make_candidate,
    make_employee,
)

# Statuses from which a transition to ``interview_scheduled`` IS allowed, derived
# from the live state machine so the set tracks the source of truth.
# Resolves to: new, reviewing.
PERMITTING_STATUSES: list[str] = [
    status
    for status in CandidateStatus
    if CandidateStatus.INTERVIEW_SCHEDULED in VALID_TRANSITIONS.get(status, set())
]


# Feature: interview-calendar-scheduling, Property 12
@settings(max_examples=100, deadline=None)
@given(
    status=st.sampled_from(PERMITTING_STATUSES),
    duration_minutes=st.integers(min_value=15, max_value=180),
    interviewer_count=st.integers(min_value=1, max_value=10),
    start_offset_minutes=st.integers(min_value=1, max_value=60 * 24 * 365),
    notes=st.none() | st.text(max_size=1000),
)
def test_calendar_create_failure_rolls_back_atomically(
    status: str,
    duration_minutes: int,
    interviewer_count: int,
    start_offset_minutes: int,
    notes: str | None,
) -> None:
    """A Calendar create failure raises and leaves the Candidate untouched.

    For any Candidate in a permitting status with no prior interview fields and
    any valid request, when ``create_event`` fails, ``schedule_interview`` raises
    ``CalendarEventCreateFailedError`` and the persisted Candidate record equals
    its pre-request snapshot exactly: status unchanged, and no Interview row was
    left behind, so no ``calendar_event_id`` / scheduled start / applied
    timezone was stored.

    Validates: Requirements 3.1, 3.2, 3.3, 3.4
    """

    async def _run() -> None:
        # Distinct interviewer emails so interviewer resolution never fails and
        # the schedule reaches the (scripted-to-fail) Calendar create call.
        employees = [
            make_employee(email=f"interviewer{i}@example.com") for i in range(interviewer_count)
        ]
        interviewer_ids: list[UUID] = [employee.id for employee in employees]

        # A Candidate in a permitting status with NO prior interview, so there is
        # no stored calendar_event_id / start / timezone to begin with -- those
        # live on the Interview row a successful schedule would create.
        candidate = make_candidate(status=status)

        # Script the single create_event call to raise the canonical adapter
        # failure, so the create-before-commit step (R2.1) fails atomically.
        calendar = FakeCalendarPort(create_outcomes=[CalendarEventCreateFailedError()])
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
            calendar=calendar,
        )

        # Capture the committed (persisted) snapshot BEFORE the request so we can
        # assert the record is unchanged afterwards (R3.4).
        snapshot_before = harness.candidate_repo.committed_snapshot(candidate.id)
        assert snapshot_before is not None
        rollbacks_before = harness.session.rollback_count

        # A valid, future start so request-field validation (R1.4) passes and the
        # Calendar create call is the only thing that fails.
        start = datetime.now(UTC) + timedelta(minutes=start_offset_minutes)

        # The failure path writes a failure audit entry; the spy sink records it
        # without touching the fake session's unsupported add/flush.
        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            # R3.3: the service raises an error describing the Calendar failure.
            with pytest.raises(CalendarEventCreateFailedError):
                await harness.service.schedule_interview(
                    candidate.id,
                    start=start,
                    duration_minutes=duration_minutes,
                    interviewer_ids=interviewer_ids,
                    notes=notes,
                )

        # Exactly one (failed) Calendar create attempt was made; no event exists.
        assert len(harness.calendar.create_calls) == 1

        # R3.4: the failure path rolled back at least once.
        assert harness.session.rollback_count >= rollbacks_before + 1

        # R3.4: the persisted Candidate record equals its pre-request snapshot
        # exactly (status + all interview fields unchanged).
        snapshot_after = harness.candidate_repo.committed_snapshot(candidate.id)
        assert snapshot_after == snapshot_before

        # R3.1 spelled out against the live Candidate instance.
        persisted = await harness.candidate_repo.get_by_id(candidate.id)
        assert persisted is not None
        assert persisted.status == status

        # R3.2 / R3.4: no event reference, scheduled start, or applied timezone
        # was stored -- the rollback left no Interview row behind at all.
        assert await harness.interviews_for(candidate.id) == []

    asyncio.run(_run())
