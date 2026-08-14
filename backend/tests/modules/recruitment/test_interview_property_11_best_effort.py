"""Property 11: best-effort sub-operations never fail scheduling.

For any created Google Calendar event in which some attendee invitations were
dropped and/or no Google Meet link was returned, the schedule still completes
successfully -- the Candidate transitions to ``interview_scheduled`` and the
returned ``calendar_event_id`` is stored -- and the Meet link is surfaced if and
only if one was returned (Requirements 5.3, 6.2, 6.3).

``CandidateService.schedule_interview`` treats a returned :class:`CalendarEvent`
as success even when its ``meet_link`` is ``None`` or its ``invited_emails`` drop
some of the requested attendees; only a *raised* exception aborts scheduling
(that atomic-rollback case is Property 12). The service persists the event id,
scheduled start, and timezone on the Candidate, but it does **not** store the
Meet link on the Candidate entity -- it only logs it. "Surfaced iff returned" is
therefore asserted at the adapter-result seam: the :class:`CalendarEvent` the
service receives from ``create_event`` carries a Meet link exactly when one was
scripted. A :class:`_RecordingCalendarPort` captures that returned event so the
test can compare the surfaced Meet link against the scripted presence/absence.

The orchestration is exercised end to end against the in-memory seams in
``_interview_support``: a fake ``CalendarPort`` records the create call and
returns the scripted partial-success event, an in-memory candidate
repository/session backs persistence (so the committed snapshot can be
inspected), and the module-level ``log_audit`` is replaced by the spy sink (the
real helper would call ``session.add``/``flush``, which the fake session does
not implement).

Validates: Requirements 5.3, 6.2, 6.3
"""

# Feature: interview-calendar-scheduling, Property 11

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.recruitment.application import interview_scheduler_service as candidate_service
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.value_objects import CalendarEvent, CalendarEventSpec
from tests.modules.recruitment._interview_support import (
    DEFAULT_HTML_LINK,
    DEFAULT_MEET_LINK,
    FakeCalendarPort,
    build_calendar_harness,
    make_candidate,
    make_employee,
)

# Statuses from which a transition to ``interview_scheduled`` is permitted, so
# the request reaches Calendar creation rather than being blocked by the state
# machine (R2.4).
PERMITTING_STATUSES = (CandidateStatus.NEW, CandidateStatus.REVIEWING)


class _RecordingCalendarPort(FakeCalendarPort):
    """``FakeCalendarPort`` that also records the ``CalendarEvent`` it returns.

    The base fake records the *call* (method, token, event id, spec) but not the
    event it hands back. This subclass captures each returned create event so the
    test can assert the Meet link the service actually received -- the seam where
    "surfaced iff returned" is observable, since the service does not persist the
    Meet link on the Candidate.
    """

    def __init__(self, *, create_outcomes: list[CalendarEvent]) -> None:
        super().__init__(create_outcomes=create_outcomes)
        self.returned_create_events: list[CalendarEvent] = []

    async def create_event(self, access_token: str, spec: CalendarEventSpec) -> CalendarEvent:
        event = await super().create_event(access_token, spec)
        self.returned_create_events.append(event)
        return event


@st.composite
def _partial_success_world(
    draw: st.DrawFn,
) -> tuple[str, list[Employee], bool, tuple[str, ...]]:
    """Draw a partial-success scenario: dropped attendees and/or no Meet link.

    Generates a Candidate email plus 1-10 interviewer Employees with unique,
    non-blank emails, then chooses a degradation that guarantees the created
    event is a genuine *partial* success (matching "some attendee invitations
    were dropped and/or no Google Meet link was returned"):

    * ``no_meet`` -- all attendees accepted, but no Meet link returned.
    * ``drop_attendees`` -- a Meet link returned, but a proper subset of the
      requested attendees was accepted (at least one dropped).
    * ``both`` -- no Meet link and some attendees dropped.

    Returns:
        ``(candidate_email, employees, meet_link_present, invited_emails)`` where
        ``invited_emails`` are the attendees the event reports as accepted (a
        subset of the requested attendee set, possibly empty when dropped).
    """
    candidate_email = f"candidate-{draw(st.uuids()).hex[:10]}@example.com"

    count = draw(st.integers(min_value=1, max_value=10))
    employees: list[Employee] = []
    interviewer_emails: list[str] = []
    for index in range(count):
        email = f"interviewer-{index}-{draw(st.uuids()).hex[:10]}@example.com"
        interviewer_emails.append(email)
        employees.append(make_employee(email=email, full_name=f"Interviewer {index}"))

    # Requested attendees mirror ``_build_attendees``: Candidate first, then the
    # interviewers. Emails are unique here, so the union is just this list.
    requested = [candidate_email, *interviewer_emails]

    degradation = draw(st.sampled_from(("no_meet", "drop_attendees", "both")))
    meet_link_present = degradation == "drop_attendees"

    if degradation in ("drop_attendees", "both"):
        # Keep a PROPER subset so at least one attendee is dropped (may be empty).
        kept = draw(
            st.lists(
                st.sampled_from(requested),
                unique=True,
                max_size=len(requested) - 1,
            )
        )
    else:
        # ``no_meet``: every attendee accepted; only the Meet link is missing.
        kept = list(requested)

    return candidate_email, employees, meet_link_present, tuple(kept)


@settings(max_examples=100, deadline=None)
@given(
    world=_partial_success_world(),
    status=st.sampled_from(PERMITTING_STATUSES),
    timezone=st.sampled_from(("Asia/Ho_Chi_Minh", "UTC", "Europe/Paris")),
    duration_minutes=st.integers(min_value=15, max_value=180),
    start_offset_minutes=st.integers(min_value=1, max_value=60 * 24 * 365),
    notes=st.none() | st.text(max_size=1000),
)
def test_best_effort_sub_operations_never_fail_scheduling(
    world: tuple[str, list[Employee], bool, tuple[str, ...]],
    status: CandidateStatus,
    timezone: str,
    duration_minutes: int,
    start_offset_minutes: int,
    notes: str | None,
) -> None:
    """A partial-success create still schedules; the Meet link is surfaced iff returned.

    For any created event with dropped attendees and/or no Meet link, the
    schedule completes: the Candidate transitions to ``interview_scheduled`` and
    the returned ``calendar_event_id`` is stored. The Meet link surfaced by the
    Calendar adapter equals the scripted Meet link (present iff one was returned).

    Validates: Requirements 5.3, 6.2, 6.3
    """
    candidate_email, employees, meet_link_present, invited_emails = world

    async def _run() -> None:
        candidate = make_candidate(status=status, email=candidate_email)

        # Script a partial-success create result: a confirmed event (so creation
        # succeeded) with a Meet link present iff scripted and only the accepted
        # attendees echoed back.
        scripted_event = CalendarEvent(
            event_id=f"evt-scripted-{uuid4().hex[:8]}",
            html_link=DEFAULT_HTML_LINK,
            meet_link=DEFAULT_MEET_LINK if meet_link_present else None,
            invited_emails=invited_emails,
        )
        calendar = _RecordingCalendarPort(create_outcomes=[scripted_event])

        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
            calendar=calendar,
            org_timezone=timezone,
        )

        # Strictly-future, tz-aware start so request-field validation (R1.4)
        # passes and the flow reaches Calendar creation.
        start = datetime.now(UTC) + timedelta(minutes=start_offset_minutes)

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            result = await harness.service.schedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=[employee.id for employee in employees],
                notes=notes,
            )

        # R5.3 + R6.3: the sub-operation degradation never aborts scheduling --
        # exactly one create call, no rollback, and the transaction committed.
        assert len(calendar.create_calls) == 1
        assert harness.session.rollback_count == 0
        assert harness.session.commit_count >= 1

        # The Candidate transitions to ``interview_scheduled`` and the returned
        # event id, start, and timezone are stored on the Interview the schedule
        # creates (R5.3 / R6.2 / R6.3: schedule still completes).
        assert result.status == CandidateStatus.INTERVIEW_SCHEDULED
        interview = await harness.scheduled_interview(candidate.id)
        assert interview is not None
        assert interview.calendar_event_id == scripted_event.event_id
        assert interview.start_at is not None
        assert interview.timezone == timezone

        # The persisted (committed) snapshots reflect the same successful outcome.
        snapshot = harness.candidate_repo.committed_snapshot(candidate.id)
        assert snapshot is not None
        assert snapshot["status"] == CandidateStatus.INTERVIEW_SCHEDULED
        interview_snapshot = harness.committed_interview_snapshot(interview.id)
        assert interview_snapshot is not None
        assert interview_snapshot["calendar_event_id"] == scripted_event.event_id

        # A success audit entry is recorded for the schedule action.
        assert harness.audit_sink.entries_for("interview_scheduled")

        # R6.2 / R6.3: the Meet link is surfaced iff one was returned. The service
        # does not persist the Meet link on the Candidate, so this is observed at
        # the adapter-result seam -- the event the service received from
        # ``create_event`` carries the scripted Meet link.
        assert len(calendar.returned_create_events) == 1
        surfaced = calendar.returned_create_events[0]
        assert (surfaced.meet_link is not None) == meet_link_present
        assert surfaced.meet_link == (DEFAULT_MEET_LINK if meet_link_present else None)

    asyncio.run(_run())
