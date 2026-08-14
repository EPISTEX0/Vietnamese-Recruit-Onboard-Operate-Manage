"""Property 7: Organization timezone is applied and stored (interview-calendar).

For any configured Organization timezone, every create *and* patch event
specification that :class:`CandidateService` hands to the Calendar adapter
carries that timezone, and on a successful schedule (or reschedule) the timezone
stored on the Candidate equals the Organization timezone.

The property is exercised end to end against the in-memory seams in
``_interview_support``: a fake ``CalendarPort`` records the spec the service
builds (``harness.calendar.create_calls[0].spec`` for schedule,
``harness.calendar.patch_calls[0].spec`` for reschedule), an in-memory candidate
repository/session backs persistence, and the module-level ``log_audit`` is
replaced by the spy sink (the real helper would call ``session.add``/``flush``,
which the fake session does not implement).

``schedule_interview``/``reschedule_interview`` express the event start/end in the
Organization timezone (an aware ``start`` is converted with ``astimezone``,
preserving the instant). The start assertions are therefore written in a
tz-robust way -- compared as an absolute UTC instant -- while the timezone field
on the spec and the persisted Candidate is asserted to equal the drawn IANA
timezone exactly. Starts are drawn as tz-aware UTC datetimes far in the future so
the future-``start`` rule (R1.4) always passes while the requested instant stays
well-defined.

Validates: Requirements 11.1, 11.2
"""

# Feature: interview-calendar-scheduling, Property 7

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
    make_interview,
)

# Statuses from which a transition to ``interview_scheduled`` is permitted.
_PERMITTING_STATUSES = st.sampled_from([CandidateStatus.NEW, CandidateStatus.REVIEWING])
# Valid, unambiguously-future starts: tz-aware UTC datetimes far in the future
# (well beyond any execution-time clock skew), so the future-``start`` rule
# (R1.4) always passes while the requested instant stays well-defined.
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
    status=_PERMITTING_STATUSES,
    start=_FUTURE_START,
    duration_minutes=_DURATIONS,
    interviewer_count=_INTERVIEWER_COUNTS,
    org_timezone=iana_timezones(),
)
def test_schedule_applies_and_stores_organization_timezone(
    status: str,
    start: datetime,
    duration_minutes: int,
    interviewer_count: int,
    org_timezone: str,
) -> None:
    """A successful schedule carries the org timezone on the spec and persists it.

    Validates: Requirements 11.1, 11.2
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
            await harness.service.schedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=interviewer_ids,
                notes=None,
            )

        # A successful schedule builds exactly one create-event spec.
        assert len(harness.calendar.create_calls) == 1
        spec = harness.calendar.create_calls[0].spec
        assert spec is not None

        # R11.2: the create event spec carries the Organization's timezone.
        assert spec.timezone == org_timezone
        # R11.1: the start is interpreted in the Organization timezone -- the
        # absolute UTC instant is preserved (tz-robust).
        assert spec.start.astimezone(UTC) == start.astimezone(UTC)

        # R11.2: on success the applied timezone is persisted on the Interview
        # the schedule creates (the Candidate no longer carries it).
        persisted = await harness.scheduled_interview(candidate.id)
        assert persisted is not None
        assert persisted.timezone == org_timezone

    asyncio.run(_run())


@settings(max_examples=100, deadline=None)
@given(
    start=_FUTURE_START,
    duration_minutes=_DURATIONS,
    interviewer_count=_INTERVIEWER_COUNTS,
    org_timezone=iana_timezones(),
)
def test_reschedule_applies_and_stores_organization_timezone(
    start: datetime,
    duration_minutes: int,
    interviewer_count: int,
    org_timezone: str,
) -> None:
    """A successful reschedule carries the org timezone on the patch spec + persists it.

    Validates: Requirements 11.1, 11.2
    """

    async def _run() -> None:
        employees = [
            make_employee(email=f"interviewer{i}@example.com") for i in range(interviewer_count)
        ]
        interviewer_ids: list[UUID] = [employee.id for employee in employees]

        # A Candidate already scheduled: its Interview carries a stored event id
        # + start so that ``reschedule_interview`` patches the existing event
        # (R7.1) rather than rejecting with ``NoInterviewToRescheduleError``
        # (R7.5).
        candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
        existing_interview = make_interview(
            candidate_id=candidate.id,
            calendar_event_id="evt-existing-0001",
            start_at=datetime(2089, 6, 1, 9, 0, 0, tzinfo=UTC),
            timezone="UTC",
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=employees,
            interviews=[existing_interview],
            org_timezone=org_timezone,
        )

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            await harness.service.reschedule_interview(
                candidate.id,
                start=start,
                duration_minutes=duration_minutes,
                interviewer_ids=interviewer_ids,
                notes=None,
            )

        # A successful reschedule patches the existing event exactly once and
        # never creates a new one.
        assert len(harness.calendar.patch_calls) == 1
        assert len(harness.calendar.create_calls) == 0
        spec = harness.calendar.patch_calls[0].spec
        assert spec is not None

        # R11.2: the patch event spec carries the Organization's timezone.
        assert spec.timezone == org_timezone
        # R11.1: the new start is interpreted in the Organization timezone -- the
        # absolute UTC instant is preserved (tz-robust).
        assert spec.start.astimezone(UTC) == start.astimezone(UTC)

        # R11.2: on success the applied timezone is persisted on the Interview
        # (the Candidate no longer carries it).
        persisted = await harness.scheduled_interview(candidate.id)
        assert persisted is not None
        assert persisted.timezone == org_timezone

    asyncio.run(_run())
