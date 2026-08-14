"""Property 22: an audit-write failure never rolls back the action.

Feature: interview-calendar-scheduling, Property 22.

Validates: Requirements 12.5.

R12.5: If audit log writing fails during a schedule, reschedule, or cancellation
action, the Scheduling_System allows the action to succeed without rolling back
the action.

For any of the four audited interview actions — ``schedule_interview``,
``reschedule_interview``, and the reject/archive cancellation side-effects — when
the module-level ``log_audit`` fails to persist its entry, the action under test
still succeeds and its effect is committed:

* ``schedule`` transitions the Candidate to ``interview_scheduled`` and stores
  the returned ``calendar_event_id``;
* ``reschedule`` updates the stored scheduled ``start`` (event reference
  unchanged);
* ``cancel_reject`` commits the transition to ``rejected``; and
* ``cancel_archive`` commits the transition to ``archived``.

The real ``log_audit`` wraps its writes in ``try/except`` and returns ``None`` on
failure, so an audit-write failure cannot raise into — and therefore cannot roll
back — the calling action. :class:`SpyAuditSink` with ``fail=True`` faithfully
models exactly this swallowing behaviour: it *records the attempt* (so we can
assert an audit was attempted) but never appends a persisted entry and never
raises. Installing it via ``patch.object(candidate_service, "log_audit", sink)``
therefore reproduces "the audit write failed" without changing how the action
behaves.

The property asserts, for whichever action is drawn, that (a) an audit write was
attempted (``audit_sink.attempts`` is non-empty), (b) no audit entry was actually
persisted (``audit_sink.entries`` is empty — every write "failed"), and (c) the
action's effect nonetheless persisted on the committed Candidate.

Starts are drawn as tz-aware UTC datetimes far in the future so the
future-``start`` rule (R1.4) always passes while the requested instant stays
well-defined.
"""

# Feature: interview-calendar-scheduling, Property 22

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    SpyAuditSink,
    build_calendar_harness,
    iana_timezones,
    make_candidate,
    make_employee,
    make_interview,
)

# The four audited interview actions exercised by this property (R12.5).
_ACTIONS = st.sampled_from(["schedule", "reschedule", "cancel_reject", "cancel_archive"])

# Statuses from which a transition to ``interview_scheduled`` is permitted; used
# to seed the ``schedule`` case so the action reaches its success audit.
_PERMITTING_STATUSES = st.sampled_from([CandidateStatus.NEW, CandidateStatus.REVIEWING])

# Valid, unambiguously-future starts: tz-aware UTC datetimes far in the future
# (well beyond any execution-time clock skew), so R1.4 always passes while the
# requested instant stays well-defined.
_FUTURE_START = st.datetimes(
    min_value=datetime(2090, 1, 1, 0, 0, 0),
    max_value=datetime(2099, 12, 31, 23, 59, 59),
    timezones=st.just(UTC),
)
# Valid durations span the inclusive 15..180 minute range (R1.2).
_DURATIONS = st.integers(min_value=15, max_value=180)
# Valid interviewer counts span the inclusive 1..10 range (R1.3).
_INTERVIEWER_COUNTS = st.integers(min_value=1, max_value=10)

# A pre-existing Calendar event id seeded for reschedule/cancel actions (those
# require a stored event reference so the Calendar side-effect is exercised).
_EXISTING_EVENT_ID = "evt-existing-22"
# A fixed, tz-aware previous scheduled start seeded on the Candidate; it sits well
# before the drawn new starts so the previous/new starts stay distinct.
_PREVIOUS_START = datetime(2080, 6, 1, 9, 0, 0, tzinfo=UTC)


@settings(max_examples=100, deadline=None)
@given(
    action=_ACTIONS,
    permitting_status=_PERMITTING_STATUSES,
    start=_FUTURE_START,
    duration_minutes=_DURATIONS,
    interviewer_count=_INTERVIEWER_COUNTS,
    org_timezone=iana_timezones(),
    notes=st.none() | st.text(max_size=1000),
)
def test_audit_write_failure_never_rolls_back_the_action(
    action: str,
    permitting_status: str,
    start: datetime,
    duration_minutes: int,
    interviewer_count: int,
    org_timezone: str,
    notes: str | None,
) -> None:
    """A failed audit write leaves the action committed (R12.5).

    Validates: Requirements 12.5
    """

    async def _run() -> None:
        # Distinct interviewer emails so interviewer resolution never fails and
        # the schedule/reschedule actions reach their Calendar call + audit.
        employees = [
            make_employee(email=f"interviewer{i}@example.com") for i in range(interviewer_count)
        ]
        interviewer_ids: list[UUID] = [employee.id for employee in employees]

        # Seed the Candidate for the drawn action. Reschedule/cancel actions need
        # a stored event reference (and a prior start) so the Calendar
        # side-effect — and its audit — is exercised.
        booked: list = []
        if action == "schedule":
            candidate = make_candidate(status=permitting_status)
        else:
            candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
            booked = [
                make_interview(
                    candidate_id=candidate.id,
                    calendar_event_id=_EXISTING_EVENT_ID,
                    start_at=_PREVIOUS_START,
                    timezone="Asia/Ho_Chi_Minh",
                )
            ]

        # ``fail=True``: the sink swallows like the real ``log_audit`` — it
        # records every attempt but persists no entry and never raises, modelling
        # "the audit write failed" without altering the action's behaviour.
        failing_sink = SpyAuditSink(fail=True)
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
            interviews=booked,
            audit_sink=failing_sink,
            org_timezone=org_timezone,
        )

        with harness.audit_patch():
            if action == "schedule":
                returned = await harness.service.schedule_interview(
                    candidate.id,
                    start=start,
                    duration_minutes=duration_minutes,
                    interviewer_ids=interviewer_ids,
                    notes=notes,
                )
            elif action == "reschedule":
                returned = await harness.service.reschedule_interview(
                    candidate.id,
                    start=start,
                    duration_minutes=duration_minutes,
                    interviewer_ids=interviewer_ids,
                    notes=notes,
                )
            elif action == "cancel_reject":
                returned = await harness.lifecycle.reject_candidate(
                    candidate.id, reason="position filled"
                )
            else:  # cancel_archive
                returned = await harness.lifecycle.archive_candidate(candidate.id)

        # R12.5 (audit attempted): the action reached at least one ``log_audit``
        # call — the audit write was genuinely attempted, then "failed".
        assert harness.audit_sink.attempts, "expected at least one audit-write attempt"
        # R12.5 (write failed): no audit entry was persisted — every attempted
        # write was swallowed, exactly as the real ``log_audit`` does on failure.
        assert harness.audit_sink.entries == []

        # R12.5 (action not rolled back): the action's effect is committed on the
        # persisted Candidate despite the audit-write failure.
        persisted = await harness.candidate_repo.get_by_id(candidate.id)
        assert persisted is not None

        if action == "schedule":
            assert returned.status == CandidateStatus.INTERVIEW_SCHEDULED
            assert persisted.status == CandidateStatus.INTERVIEW_SCHEDULED
            # The schedule's effect -- the event reference and the scheduled
            # start -- is committed on the Interview it created.
            interview = await harness.scheduled_interview(candidate.id)
            assert interview is not None
            assert interview.calendar_event_id is not None
            assert interview.start_at.astimezone(UTC) == start.astimezone(UTC)
        elif action == "reschedule":
            # The stored start advanced to the new instant; the event reference
            # is left unchanged (reschedule patches in place).
            interview = await harness.scheduled_interview(candidate.id)
            assert interview is not None
            assert interview.calendar_event_id == _EXISTING_EVENT_ID
            assert interview.start_at.astimezone(UTC) == start.astimezone(UTC)
        elif action == "cancel_reject":
            assert returned.status == CandidateStatus.REJECTED
            assert persisted.status == CandidateStatus.REJECTED
        else:  # cancel_archive
            assert returned.status == CandidateStatus.ARCHIVED
            assert persisted.status == CandidateStatus.ARCHIVED

    asyncio.run(_run())
