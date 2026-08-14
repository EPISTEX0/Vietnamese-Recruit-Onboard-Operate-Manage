"""Property 13: Single ``calendar_event_id`` invariant (interview-calendar).

For any sequence of valid schedule-then-reschedule operations on a Candidate,
the stored ``calendar_event_id`` is always a single value equal to the most
recently created or patched event and never accumulates more than one reference.

Because ``calendar_event_id`` is a single scalar column on the ``Interview``
entity (not a collection, per R4.4 / R4.5), "single value" is *structural*:
there is exactly one slot. Since the calendar-scheduling refactor a Candidate
can in principle carry more than one Interview row, so the test also asserts
that scheduling-then-rescheduling leaves exactly *one* such row -- the reference
must not accumulate at the row level either. This property pins down that
behaviour across an operation sequence:

* After ``schedule_interview`` the stored ``calendar_event_id`` is a single
  non-``None`` ``str`` equal to the event the Calendar adapter created (the fake
  mints that id and the service stores the returned value).
* ``reschedule_interview`` *patches* the same event id and never creates a new
  event, so after one or more reschedules the stored ``calendar_event_id`` is
  still that same single original id -- it does not change and does not
  accumulate. Each reschedule issues exactly one patch against that id while the
  create-call total stays at exactly one.

The property is exercised end to end against the in-memory seams in
``_interview_support``: a fake ``CalendarPort`` records every adapter call
(``create_calls`` / ``patch_calls``), an in-memory candidate repository/session
backs persistence, and the module-level ``log_audit`` is replaced by the spy
sink (the real helper would call ``session.add``/``flush``, which the fake
session does not implement).

Starts are drawn as tz-aware UTC datetimes far in the future so the
future-``start`` rule (R1.4) always passes for both the initial schedule and
every reschedule, while the requested instants stay well-defined.

Validates: Requirements 4.4
"""

# Feature: interview-calendar-scheduling, Property 13

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
)

# Statuses from which a transition to ``interview_scheduled`` is permitted.
_PERMITTING_STATUSES = st.sampled_from([CandidateStatus.NEW, CandidateStatus.REVIEWING])
# Valid, unambiguously-future starts: tz-aware UTC datetimes far in the future
# (well beyond any execution-time clock skew), so R1.4 always passes for both the
# schedule and every reschedule while the requested instants stay well-defined.
_FUTURE_START = st.datetimes(
    min_value=datetime(2090, 1, 1, 0, 0, 0),
    max_value=datetime(2099, 12, 31, 23, 59, 59),
    timezones=st.just(UTC),
)
# Valid durations span the inclusive 15..180 minute range (R1.2).
_DURATIONS = st.integers(min_value=15, max_value=180)
# Valid interviewer counts span the inclusive 1..10 range (R1.3).
_INTERVIEWER_COUNTS = st.integers(min_value=1, max_value=10)
# Number of reschedules applied after the initial schedule (1..3).
_RESCHEDULE_COUNTS = st.integers(min_value=1, max_value=3)


@settings(max_examples=100, deadline=None)
@given(
    status=_PERMITTING_STATUSES,
    schedule_start=_FUTURE_START,
    reschedule_starts=st.lists(_FUTURE_START, min_size=1, max_size=3),
    duration_minutes=_DURATIONS,
    interviewer_count=_INTERVIEWER_COUNTS,
    org_timezone=iana_timezones(),
)
def test_single_calendar_event_id_invariant(
    status: str,
    schedule_start: datetime,
    reschedule_starts: list[datetime],
    duration_minutes: int,
    interviewer_count: int,
    org_timezone: str,
) -> None:
    """Schedule-then-reschedule keeps a single, unchanging ``calendar_event_id``.

    Validates: Requirements 4.4
    """

    async def _run() -> None:
        employees = [
            make_employee(email=f"interviewer{i}@example.com") for i in range(interviewer_count)
        ]
        interviewer_ids: list[UUID] = [employee.id for employee in employees]

        candidate = make_candidate(status=status)
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
            org_timezone=org_timezone,
        )

        # The real ``log_audit`` would hit ``session.add``/``flush`` (unsupported
        # by the fake session); the spy sink records entries without persisting.
        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            # (1) Schedule: the service creates one event and stores its id.
            await harness.service.schedule_interview(
                candidate.id,
                start=schedule_start,
                duration_minutes=duration_minutes,
                interviewer_ids=interviewer_ids,
                notes=None,
            )

            # Exactly one Interview row exists, holding exactly one reference.
            assert len(await harness.interviews_for(candidate.id)) == 1
            persisted = await harness.scheduled_interview(candidate.id)
            assert persisted is not None
            event_id_after_schedule = persisted.calendar_event_id
            # A single non-``None`` scalar value: exactly one stored reference.
            assert isinstance(event_id_after_schedule, str)
            assert event_id_after_schedule != ""
            # It equals the event the adapter created.
            assert len(harness.calendar.create_calls) == 1
            assert len(harness.calendar.patch_calls) == 0

            # (2) Reschedule N times: each patches the SAME event, never creates.
            for index, new_start in enumerate(reschedule_starts, start=1):
                await harness.service.reschedule_interview(
                    candidate.id,
                    start=new_start,
                    duration_minutes=duration_minutes,
                    interviewer_ids=interviewer_ids,
                    notes=None,
                )

                # Still exactly one Interview row: a reschedule patches in place
                # rather than accumulating a second reference.
                assert len(await harness.interviews_for(candidate.id)) == 1
                persisted = await harness.scheduled_interview(candidate.id)
                assert persisted is not None
                # The stored reference is unchanged -- still the single original id.
                assert persisted.calendar_event_id == event_id_after_schedule
                # No new event was created (create-call total stays exactly one).
                assert len(harness.calendar.create_calls) == 1
                # Exactly one patch was issued per reschedule, all against that id.
                assert len(harness.calendar.patch_calls) == index
                assert all(
                    call.event_id == event_id_after_schedule
                    for call in harness.calendar.patch_calls
                )

        # End state: exactly one create call total and the stored
        # ``calendar_event_id`` is still that single original id -- it never
        # accumulated more than one reference.
        assert len(await harness.interviews_for(candidate.id)) == 1
        final = await harness.scheduled_interview(candidate.id)
        assert final is not None
        assert final.calendar_event_id == event_id_after_schedule
        assert len(harness.calendar.create_calls) == 1
        assert len(harness.calendar.patch_calls) == len(reschedule_starts)

    asyncio.run(_run())
