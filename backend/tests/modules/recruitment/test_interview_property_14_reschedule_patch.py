"""Property 14: a successful reschedule patches the stored event and updates start.

Feature: interview-calendar-scheduling, Property 14.

Validates: Requirements 7.1, 7.3, 12.2.

For any Candidate whose Interview stores a ``calendar_event_id``, a successful
reschedule via :meth:`InterviewSchedulerService.reschedule_interview`:

* invokes the adapter's ``patch_event`` on that EXACT stored event id with an end
  equal to the new ``start`` plus ``duration_minutes`` (R7.1);
* never creates a new event (R7.1 - reschedule patches in place);
* updates the stored scheduled ``start`` to the new value while leaving the
  ``calendar_event_id`` unchanged (R7.3); and
* writes an ``interview_rescheduled`` audit entry recording the previous and new
  scheduled starts (R12.2).

The property is exercised end to end against the in-memory seams in
``_interview_support``: a fake ``CalendarPort`` records each adapter call (method,
target event id, and the :class:`CalendarEventSpec`) and, with no scripted
patch outcome, echoes the patched event id back as a successful
:class:`CalendarEvent`; an in-memory candidate repository/session backs
persistence; and the module-level ``log_audit`` is replaced by the spy sink (the
real helper would call ``session.add``/``flush``, which the fake session does
not implement).

``reschedule_interview`` expresses the event start/end tz-aware in the
Organization timezone (an aware ``start`` is converted with ``astimezone``,
preserving the instant), so the assertions are tz-robust: the duration is checked
as ``end - start`` and the stored start is compared as an absolute UTC instant.
New starts are drawn as tz-aware UTC datetimes far in the future so the
future-``start`` rule (R1.4) always passes while the requested instant stays
well-defined and strictly different from the seeded previous start.
"""

# Feature: interview-calendar-scheduling, Property 14

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.recruitment.application import interview_scheduler_service as candidate_service
from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_employee,
    make_interview,
)

# The event id stored on the Interview before the reschedule; the patch call MUST
# target this exact id and the stored reference MUST stay unchanged afterwards.
_EXISTING_EVENT_ID = "evt-existing-1"
# A fixed, tz-aware previous scheduled start seeded on the Interview. It sits well
# before the drawn new starts so the previous/new starts are always distinct.
_PREVIOUS_START = datetime(2080, 6, 1, 9, 0, 0, tzinfo=UTC)
# The etag stored alongside the event; the reschedule must send it as the
# conditional-write precondition.
_SEEDED_ETAG = '"etag-before-reschedule"'

# Valid, unambiguously-future new starts: tz-aware UTC datetimes far in the future
# (well beyond any execution-time clock skew), so the future-``start`` rule (R1.4)
# always passes while the requested instant stays well-defined.
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
    notes=st.none() | st.text(max_size=1000),
)
def test_successful_reschedule_patches_event_and_updates_start(
    start: datetime,
    duration_minutes: int,
    interviewer_count: int,
    notes: str | None,
) -> None:
    """A confirmed patch updates the stored start and audits previous/new starts.

    Validates: Requirements 7.1, 7.3, 12.2
    """

    async def _run() -> None:
        # Distinct interviewer emails so interviewer resolution never fails and
        # the reschedule reaches the patch call.
        employees = [
            make_employee(email=f"interviewer{i}@example.com") for i in range(interviewer_count)
        ]
        interviewer_ids: list[UUID] = [employee.id for employee in employees]

        # A Candidate already interview-scheduled, whose Interview carries the
        # stored event id and previous scheduled start (the reschedule must patch
        # this exact event).
        candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
        existing_interview = make_interview(
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
            calendar_etag=_SEEDED_ETAG,
            start_at=_PREVIOUS_START,
            timezone="Asia/Ho_Chi_Minh",
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
            interviews=[existing_interview],
            org_timezone="Asia/Ho_Chi_Minh",
        )

        # The real ``log_audit`` would hit ``session.add``/``flush`` (unsupported
        # by the fake session); the spy sink records entries without persisting.
        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            returned = await harness.service.reschedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=interviewer_ids,
                notes=notes,
            )

        # R7.1: exactly one patch call, on the EXACT stored event id, and NO new
        # event was created.
        assert len(harness.calendar.patch_calls) == 1
        assert len(harness.calendar.create_calls) == 0
        patch_call = harness.calendar.patch_calls[0]
        assert patch_call.event_id == _EXISTING_EVENT_ID
        assert patch_call.event_id == existing_interview.calendar_event_id

        # The patch is conditional on the etag we last stored for the event, so
        # Google answers 412 instead of clobbering an event edited elsewhere in
        # the meantime. Compared against the seeded etag rather than the live
        # entity's, because a successful patch overwrites the entity's etag with
        # the one the response carried. Asserted here because nothing else
        # forces the service to keep sending the precondition.
        assert patch_call.if_match == _SEEDED_ETAG

        # R7.1: the patched event window ends exactly ``duration_minutes`` after
        # its start (tz-robust: independent of the org timezone the service
        # applies), and the start is the requested instant.
        assert patch_call.spec is not None
        assert patch_call.spec.end - patch_call.spec.start == timedelta(minutes=duration_minutes)
        assert patch_call.spec.start.astimezone(UTC) == start.astimezone(UTC)

        # The Candidate itself stays interview_scheduled; the schedule state it
        # used to carry now lives on the Interview.
        assert returned.status == CandidateStatus.INTERVIEW_SCHEDULED

        # R7.3: the stored scheduled start is updated to the new instant while the
        # calendar_event_id is left unchanged. Checked on both the live Interview
        # and its committed (persisted) snapshot.
        persisted = await harness.scheduled_interview(candidate.id)
        assert persisted is not None
        assert persisted.calendar_event_id == _EXISTING_EVENT_ID
        assert persisted.start_at is not None
        assert persisted.start_at.astimezone(UTC) == start.astimezone(UTC)

        committed = harness.committed_interview_snapshot(persisted.id)
        assert committed is not None
        assert committed["calendar_event_id"] == _EXISTING_EVENT_ID
        assert committed["start_at"].astimezone(UTC) == start.astimezone(UTC)

        # R12.2: an ``interview_rescheduled`` audit entry records the previous and
        # new scheduled starts. The service records them the way the audit shape
        # means them -- the previous start under ``previous_value``, the new start
        # under ``new_value`` -- rather than both under ``new_value``.
        rescheduled_entries = harness.audit_sink.entries_for("interview_rescheduled")
        assert len(rescheduled_entries) == 1
        entry = rescheduled_entries[0]
        assert entry.user_id == harness.user_id
        assert entry.entity_id == candidate.id
        assert entry.success is True
        assert entry.previous_value is not None
        assert entry.new_value is not None
        assert "start" in entry.previous_value
        assert "start" in entry.new_value
        # The recorded previous start is the seeded start; the recorded new start
        # is the requested instant (both compared tz-robustly as UTC instants).
        recorded_previous = entry.previous_value["start"]
        recorded_new = entry.new_value["start"]
        assert recorded_previous is not None
        assert datetime.fromisoformat(recorded_previous).astimezone(UTC) == _PREVIOUS_START
        assert datetime.fromisoformat(recorded_new).astimezone(UTC) == start.astimezone(UTC)

    asyncio.run(_run())
