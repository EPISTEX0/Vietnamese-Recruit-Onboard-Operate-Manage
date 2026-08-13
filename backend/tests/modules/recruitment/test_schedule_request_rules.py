"""Scheduling rules that the service reaches before any Calendar call.

These three rules are each asserted by the interview-calendar property suite,
but every one of those files also reads ``Candidate.calendar_event_id`` -- a
column migration 0xx deliberately dropped when the interview reference moved
to the ``interviews`` table -- so they error out before the rule itself is
ever checked. The rules are exercised directly here instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import InterviewerNotFoundError
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_employee,
)

_VALID_DURATION = 60


def _future_start() -> datetime:
    """A start comfortably clear of any execution-time clock skew."""
    return datetime.now(UTC) + timedelta(days=1)


class TestUnmatchedInterviewersAreReportedAsIds:
    """R1.7: the error names every interviewer id with no Employee."""

    async def test_reports_all_unmatched_ids_not_only_the_first(self) -> None:
        candidate = make_candidate(status=CandidateStatus.NEW)
        known = make_employee(email="known@example.com")
        missing_a, missing_b = uuid4(), uuid4()
        harness = build_calendar_harness(candidates=[candidate], employees=[known])

        with pytest.raises(InterviewerNotFoundError) as exc_info:
            await harness.service.schedule_interview(
                candidate.id,
                start=_future_start(),
                duration_minutes=_VALID_DURATION,
                interviewer_ids=[missing_a, known.id, missing_b],
            )

        assert set(exc_info.value.unmatched_ids) == {missing_a, missing_b}

    async def test_details_payload_carries_whole_uuids(self) -> None:
        """A stringified message passed to ``Sequence[UUID]`` shreds into chars."""
        candidate = make_candidate(status=CandidateStatus.NEW)
        missing = uuid4()
        harness = build_calendar_harness(candidates=[candidate], employees=[])

        with pytest.raises(InterviewerNotFoundError) as exc_info:
            await harness.service.schedule_interview(
                candidate.id,
                start=_future_start(),
                duration_minutes=_VALID_DURATION,
                interviewer_ids=[missing],
            )

        details = exc_info.value.details or {}
        assert details["unmatched_ids"] == [str(missing)]

    async def test_no_calendar_event_is_created(self) -> None:
        candidate = make_candidate(status=CandidateStatus.NEW)
        harness = build_calendar_harness(candidates=[candidate], employees=[])

        with pytest.raises(InterviewerNotFoundError):
            await harness.service.schedule_interview(
                candidate.id,
                start=_future_start(),
                duration_minutes=_VALID_DURATION,
                interviewer_ids=[uuid4()],
            )

        assert harness.calendar.was_called is False


class TestAttendeeDeduplication:
    """R5.1/R5.2: attendees are the candidate plus interviewers, each once."""

    async def test_interviewer_sharing_the_candidate_email_appears_once(self) -> None:
        candidate = make_candidate(status=CandidateStatus.NEW, email="dup@example.com")
        interviewer = make_employee(email="dup@example.com")
        harness = build_calendar_harness(candidates=[candidate], employees=[interviewer])

        await harness.service.schedule_interview(
            candidate.id,
            start=_future_start(),
            duration_minutes=_VALID_DURATION,
            interviewer_ids=[interviewer.id],
        )

        emails = harness.calendar.create_calls[0].spec.attendee_emails
        assert list(emails) == ["dup@example.com"]

    async def test_deduplication_is_case_insensitive(self) -> None:
        candidate = make_candidate(status=CandidateStatus.NEW, email="Dup@Example.com")
        interviewer = make_employee(email="dup@example.com")
        harness = build_calendar_harness(candidates=[candidate], employees=[interviewer])

        await harness.service.schedule_interview(
            candidate.id,
            start=_future_start(),
            duration_minutes=_VALID_DURATION,
            interviewer_ids=[interviewer.id],
        )

        emails = harness.calendar.create_calls[0].spec.attendee_emails
        assert len(emails) == 1

    async def test_distinct_interviewers_are_all_invited(self) -> None:
        candidate = make_candidate(status=CandidateStatus.NEW, email="cand@example.com")
        first = make_employee(email="a@example.com")
        second = make_employee(email="b@example.com")
        harness = build_calendar_harness(candidates=[candidate], employees=[first, second])

        await harness.service.schedule_interview(
            candidate.id,
            start=_future_start(),
            duration_minutes=_VALID_DURATION,
            interviewer_ids=[first.id, second.id],
        )

        emails = harness.calendar.create_calls[0].spec.attendee_emails
        assert set(emails) == {"cand@example.com", "a@example.com", "b@example.com"}


class TestStartMustBeInTheFuture:
    """R1.4: a past ``start`` is rejected before any Calendar call."""

    async def test_past_start_is_rejected(self) -> None:
        candidate = make_candidate(status=CandidateStatus.NEW)
        interviewer = make_employee(email="interviewer@example.com")
        harness = build_calendar_harness(candidates=[candidate], employees=[interviewer])

        with pytest.raises(ValueError, match="future"):
            await harness.service.schedule_interview(
                candidate.id,
                start=datetime.now(UTC) - timedelta(hours=1),
                duration_minutes=_VALID_DURATION,
                interviewer_ids=[interviewer.id],
            )

        assert harness.calendar.was_called is False

    async def test_future_start_is_accepted(self) -> None:
        candidate = make_candidate(status=CandidateStatus.NEW)
        interviewer = make_employee(email="interviewer@example.com")
        harness = build_calendar_harness(candidates=[candidate], employees=[interviewer])

        await harness.service.schedule_interview(
            candidate.id,
            start=_future_start(),
            duration_minutes=_VALID_DURATION,
            interviewer_ids=[interviewer.id],
        )

        assert harness.calendar.was_called is True
