"""Property 16: a reschedule patch failure leaves stored references unchanged.

Feature: interview-calendar-scheduling, Property 16.

Validates: Requirements 7.4 - if the Google Calendar event update fails during a
reschedule, the Scheduling_System leaves the stored ``calendar_event_id`` and
scheduled ``start`` on the Candidate's Interview unchanged and returns a
``CALENDAR_UPDATE_FAILED`` error to the acting HR user.

The property is exercised end to end against the in-memory seams in
``_interview_support``. A Candidate is seeded in status ``interview_scheduled``
with an Interview carrying an existing ``calendar_event_id`` and a known
scheduled ``start`` / timezone, the Organization Calendar connection is live,
and the request
carries a fresh, future ``start`` that differs from the stored one. The fake
``CalendarPort`` is scripted so its single ``patch_event`` call raises
``CalendarEventUpdateFailedError`` (the canonical error the real adapter raises
after retries + a token refresh). ``log_audit`` is replaced by the spy sink
because the failure path writes a failure audit entry, and the real helper would
call ``session.add``/``flush`` (unsupported by the fake session).

For every Candidate with a stored event and any valid reschedule request, a
patch failure must:

* raise ``CalendarEventUpdateFailedError`` (``CALENDAR_UPDATE_FAILED`` / 502);
* leave the stored ``calendar_event_id`` equal to its original value;
* leave the Interview's stored scheduled ``start_at`` equal to its original
  value (i.e. NOT the new requested start);
* leave the persisted Candidate and Interview equal to their pre-request
  committed snapshots; and
* have made exactly one (failed) ``patch_event`` attempt and zero ``create``
  calls (rescheduling never creates a new event).
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
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import CalendarEventUpdateFailedError
from tests.modules.recruitment._interview_support import (
    FakeCalendarPort,
    build_calendar_harness,
    make_candidate,
    make_employee,
    make_interview,
)

# A stored start clearly in the past relative to any generated (future) start,
# so "unchanged" can be asserted as "NOT the new requested start" unambiguously.
_EXISTING_EVENT_ID = "evt-existing-1"
_EXISTING_START = datetime(2000, 1, 1, 9, 0, tzinfo=UTC)
_EXISTING_TIMEZONE = "Asia/Ho_Chi_Minh"


# Feature: interview-calendar-scheduling, Property 16
@settings(max_examples=100, deadline=None)
@given(
    duration_minutes=st.integers(min_value=15, max_value=180),
    interviewer_count=st.integers(min_value=1, max_value=10),
    start_offset_minutes=st.integers(min_value=10, max_value=525_600),
    notes=st.one_of(st.none(), st.text(max_size=1000)),
)
def test_reschedule_patch_failure_leaves_references_unchanged(
    duration_minutes: int,
    interviewer_count: int,
    start_offset_minutes: int,
    notes: str | None,
) -> None:
    """A patch failure raises and leaves stored interview references untouched.

    For any Candidate with a stored ``calendar_event_id`` and any valid
    reschedule request, when ``patch_event`` fails, ``reschedule_interview``
    raises ``CalendarEventUpdateFailedError`` and the persisted Interview keeps
    its original ``calendar_event_id`` and scheduled ``start_at`` (never the new
    requested start), with exactly one failed patch attempt and no create call.

    Validates: Requirements 7.4
    """

    async def _run() -> None:
        # Distinct interviewer emails so interviewer resolution never fails and
        # the reschedule reaches the (scripted-to-fail) Calendar patch call.
        employees = [
            make_employee(email=f"interviewer{i}@example.com") for i in range(interviewer_count)
        ]
        interviewer_ids: list[UUID] = [employee.id for employee in employees]

        # A Candidate whose Interview already stores an event (R7 precondition).
        candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
        existing_interview = make_interview(
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
            start_at=_EXISTING_START,
            timezone=_EXISTING_TIMEZONE,
        )

        # Script the single patch_event call to raise the canonical adapter
        # failure, so the patch-before-commit step (R7.1) fails (R7.4).
        calendar = FakeCalendarPort(patch_outcomes=[CalendarEventUpdateFailedError()])
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
            interviews=[existing_interview],
            calendar=calendar,
        )

        # Capture the committed (persisted) snapshots BEFORE the request so we
        # can assert the records are unchanged afterwards (R7.4).
        snapshot_before = harness.candidate_repo.committed_snapshot(candidate.id)
        assert snapshot_before is not None
        interview_snapshot_before = harness.committed_interview_snapshot(existing_interview.id)
        assert interview_snapshot_before is not None

        # A valid, future start that differs from the stored one, so request-field
        # validation (R1.4) passes and the Calendar patch is the only failure.
        start = datetime.now(UTC) + timedelta(minutes=start_offset_minutes)
        assert start != _EXISTING_START

        # The failure path writes a failure audit entry; the spy sink records it
        # without touching the fake session's unsupported add/flush.
        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            # R7.4: the service raises a CALENDAR_UPDATE_FAILED error.
            with pytest.raises(CalendarEventUpdateFailedError) as exc_info:
                await harness.service.reschedule_interview(
                    candidate.id,
                    start=start,
                    duration_minutes=duration_minutes,
                    interviewer_ids=interviewer_ids,
                    notes=notes,
                )

        # The returned error is the documented CALENDAR_UPDATE_FAILED / 502.
        assert exc_info.value.error_code == "CALENDAR_UPDATE_FAILED"
        assert exc_info.value.status_code == 502

        # Exactly one (failed) patch attempt was made, and no event was created:
        # rescheduling patches the existing event in place (R7.1).
        assert len(harness.calendar.patch_calls) == 1
        assert harness.calendar.create_calls == []
        # The patch targeted the EXACT stored event id.
        assert harness.calendar.patch_calls[0].event_id == _EXISTING_EVENT_ID

        # R7.4: the persisted Candidate and the persisted Interview each equal
        # their pre-request snapshot exactly (stored references, scheduled start
        # and status all unchanged).
        snapshot_after = harness.candidate_repo.committed_snapshot(candidate.id)
        assert snapshot_after == snapshot_before
        interview_snapshot_after = harness.committed_interview_snapshot(existing_interview.id)
        assert interview_snapshot_after == interview_snapshot_before

        # R7.4 spelled out against the live entities: the stored
        # calendar_event_id and scheduled start are left unchanged - and the
        # scheduled start is NOT the newly requested start.
        persisted_interview = await harness.scheduled_interview(candidate.id)
        assert persisted_interview is not None
        assert persisted_interview.calendar_event_id == _EXISTING_EVENT_ID
        assert persisted_interview.start_at == _EXISTING_START
        assert persisted_interview.start_at != start
        assert persisted_interview.timezone == _EXISTING_TIMEZONE
        persisted = await harness.candidate_repo.get_by_id(candidate.id)
        assert persisted is not None
        assert persisted.status == CandidateStatus.INTERVIEW_SCHEDULED

    asyncio.run(_run())
