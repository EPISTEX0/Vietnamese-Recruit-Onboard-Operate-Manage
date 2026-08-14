"""Property 21: every action failure is audited.

Feature: interview-calendar-scheduling, Property 21

Property 21 — Every action failure is audited:
    If a schedule, reschedule, or cancellation action fails, the
    Scheduling_System writes an audit log entry recording the attempted action
    and a failure indicator.

Validates: Requirements 12.4

The property is exercised end to end against the in-memory seams in
``_interview_support`` and spans all three failure paths via a single sampled
scenario:

* ``schedule`` — a permitting-status Candidate with no stored event; the fake
  ``CalendarPort`` is scripted so ``create_event`` raises
  ``CalendarEventCreateFailedError``. ``schedule_interview`` must raise and write
  an ``interview_schedule_failed`` audit entry with ``success is False``.
* ``reschedule`` — a Candidate with a stored event; ``patch_event`` raises
  ``CalendarEventUpdateFailedError``. ``reschedule_interview`` must raise and
  write an ``interview_reschedule_failed`` audit entry with ``success is False``.
* ``cancel`` — a Candidate with a stored event; ``delete_event`` raises. The
  terminal transition (``reject`` or ``archive``) must NOT raise (cancellation is
  best-effort, R8.4/R8.5) but must write an ``interview_cancel_failed`` audit
  entry with ``success is False``.

In every scenario the recorded failure audit entry must carry the documented
``operation_type``, a failure indicator (``success is False``), and the attempted
action (an ``attempted_action`` field in ``new_value``). ``log_audit`` is
replaced by the default spy sink (which records entries without persisting),
because the real helper would call ``session.add``/``flush`` — unsupported by the
fake session.
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
from src.modules.recruitment.domain.exceptions import (
    CalendarEventCreateFailedError,
    CalendarEventUpdateFailedError,
)
from tests.modules.recruitment._interview_support import (
    FakeCalendarPort,
    SpyAuditSink,
    build_calendar_harness,
    make_candidate,
    make_employee,
    make_interview,
)

# Statuses from which a transition to ``interview_scheduled`` IS allowed, derived
# from the live state machine so the set tracks the source of truth (new,
# reviewing). Used to seed the schedule-failure scenario.
PERMITTING_STATUSES: list[str] = [
    status
    for status in CandidateStatus
    if CandidateStatus.INTERVIEW_SCHEDULED in VALID_TRANSITIONS.get(status, set())
]

# A stored interview event for the reschedule/cancel scenarios.
_STORED_EVENT_ID = "evt-existing-1"
_STORED_START = datetime(2000, 1, 1, 9, 0, tzinfo=UTC)
_STORED_TIMEZONE = "Asia/Ho_Chi_Minh"

# The documented failure ``operation_type`` per scenario (R12.4).
_FAILURE_OPERATION = {
    "schedule": "interview_schedule_failed",
    "reschedule": "interview_reschedule_failed",
    "cancel": "interview_cancel_failed",
}


# Feature: interview-calendar-scheduling, Property 21
@settings(max_examples=100, deadline=None)
@given(
    scenario=st.sampled_from(["schedule", "reschedule", "cancel"]),
    cancel_action=st.sampled_from(["reject", "archive"]),
    permitting_status=st.sampled_from(PERMITTING_STATUSES),
    duration_minutes=st.integers(min_value=15, max_value=180),
    interviewer_count=st.integers(min_value=1, max_value=10),
    start_offset_minutes=st.integers(min_value=10, max_value=525_600),
    notes=st.one_of(st.none(), st.text(max_size=1000)),
)
def test_every_action_failure_is_audited(
    scenario: str,
    cancel_action: str,
    permitting_status: str,
    duration_minutes: int,
    interviewer_count: int,
    start_offset_minutes: int,
    notes: str | None,
) -> None:
    """A failed schedule, reschedule, or cancellation always writes a failure audit.

    For each of the three failure paths, the Scheduling_System records an audit
    entry whose ``operation_type`` is the documented failure type, whose
    ``success`` flag is ``False`` (the failure indicator), and whose ``new_value``
    names the attempted action.

    Validates: Requirements 12.4
    """

    async def _run() -> None:
        # Distinct interviewer emails so interviewer resolution never fails and
        # each action reaches its (scripted-to-fail) Calendar call.
        employees = [
            make_employee(email=f"interviewer{i}@example.com") for i in range(interviewer_count)
        ]
        interviewer_ids: list[UUID] = [employee.id for employee in employees]

        # A valid, future start so request-field validation (R1.4) passes and the
        # Calendar call is the only thing that fails on the schedule/reschedule
        # paths.
        start = datetime.now(UTC) + timedelta(minutes=start_offset_minutes)

        # Each scenario records its failure audit via the module-level
        # ``log_audit``; the default spy sink records entries without touching the
        # fake session's unsupported add/flush.
        audit_sink = SpyAuditSink()

        if scenario == "schedule":
            # A permitting-status Candidate with no prior interview fields, so the
            # transition is valid and the create call is reached, then fails.
            candidate = make_candidate(status=permitting_status)
            calendar = FakeCalendarPort(create_outcomes=[CalendarEventCreateFailedError()])
            harness = build_calendar_harness(
                candidates=[candidate],
                employees=employees,
                calendar=calendar,
                audit_sink=audit_sink,
            )
            with patch.object(candidate_service, "log_audit", audit_sink):
                with pytest.raises(CalendarEventCreateFailedError):
                    await harness.service.schedule_interview(
                        candidate.id,
                        start=start,
                        duration_minutes=duration_minutes,
                        interviewer_ids=interviewer_ids,
                        notes=notes,
                    )
            # Exactly one (failed) create attempt; no event was created.
            assert len(harness.calendar.create_calls) == 1

        elif scenario == "reschedule":
            # A Candidate that already has a stored interview event (R7
            # precondition); the patch call is reached, then fails.
            candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
            booked = make_interview(
                candidate_id=candidate.id,
                calendar_event_id=_STORED_EVENT_ID,
                start_at=_STORED_START,
                timezone=_STORED_TIMEZONE,
            )
            calendar = FakeCalendarPort(patch_outcomes=[CalendarEventUpdateFailedError()])
            harness = build_calendar_harness(
                candidates=[candidate],
                employees=employees,
                interviews=[booked],
                calendar=calendar,
                audit_sink=audit_sink,
            )
            with patch.object(candidate_service, "log_audit", audit_sink):
                with pytest.raises(CalendarEventUpdateFailedError):
                    await harness.service.reschedule_interview(
                        candidate.id,
                        start=start,
                        duration_minutes=duration_minutes,
                        interviewer_ids=interviewer_ids,
                        notes=notes,
                    )
            # Exactly one (failed) patch attempt; rescheduling never creates.
            assert len(harness.calendar.patch_calls) == 1
            assert harness.calendar.create_calls == []

        else:  # scenario == "cancel"
            # A Candidate with a stored event that can transition to
            # rejected/archived; the cancellation delete is scripted to fail.
            candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
            booked = make_interview(
                candidate_id=candidate.id,
                calendar_event_id=_STORED_EVENT_ID,
                start_at=_STORED_START,
                timezone=_STORED_TIMEZONE,
            )
            calendar = FakeCalendarPort(
                delete_outcomes=[RuntimeError("simulated calendar delete failure")]
            )
            harness = build_calendar_harness(
                candidates=[candidate],
                employees=employees,
                interviews=[booked],
                calendar=calendar,
                audit_sink=audit_sink,
            )
            with harness.audit_patch():
                # Cancellation failure is best-effort: the terminal transition
                # must NOT raise (R8.4 / R8.5).
                if cancel_action == "reject":
                    returned = await harness.lifecycle.reject_candidate(candidate.id, notes)
                else:
                    returned = await harness.lifecycle.archive_candidate(candidate.id)
            # The terminal transition still completed despite the failed delete.
            expected_status = (
                CandidateStatus.REJECTED if cancel_action == "reject" else CandidateStatus.ARCHIVED
            )
            assert returned.status == expected_status
            # Exactly one (failed) delete attempt on the EXACT stored event id.
            assert len(harness.calendar.delete_calls) == 1
            assert harness.calendar.delete_calls[0].event_id == _STORED_EVENT_ID

        # R12.4: a failure audit entry exists for this action with the documented
        # operation_type, a failure indicator (success is False), and a record of
        # the attempted action.
        operation_type = _FAILURE_OPERATION[scenario]
        failure_entries = audit_sink.entries_for(operation_type)
        assert len(failure_entries) == 1
        entry = failure_entries[0]
        assert entry.success is False
        assert entry.entity_id == candidate.id
        assert entry.new_value is not None
        # The attempted action is recorded (R12.4).
        assert "attempted_action" in entry.new_value
        assert entry.new_value["attempted_action"]
        assert entry.new_value["candidate_id"] == str(candidate.id)

    asyncio.run(_run())
