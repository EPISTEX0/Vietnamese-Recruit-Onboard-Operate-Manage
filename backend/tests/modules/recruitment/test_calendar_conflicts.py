"""Tests for calendar conflict capture and resolution (GH #157).

Validates that when a conditional write (If-Match) to Google Calendar
fails with 412, a conflict is captured, listed, and resolvable.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from src.modules.recruitment.application import interview_scheduler_service as candidate_service
from src.modules.recruitment.domain.entities import (
    CalendarConflict,
    Interview,
)
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import (
    CalendarConflictNotFoundError,
    CalendarEventConflictError,
)
from src.modules.recruitment.domain.value_objects import CalendarEvent, CalendarEventSpec
from tests.modules.recruitment._interview_support import (
    DEFAULT_HTML_LINK,
    FakeCalendarPort,
    RecordedCalendarCall,
    build_calendar_harness,
    make_candidate,
    make_employee,
    make_http_status_error,
    make_interview,
)

_EXISTING_EVENT_ID = "evt-existing-1"
_FUTURE_START = datetime(2090, 6, 1, 9, 0, 0, tzinfo=UTC)


def _make_get_event_response(
    event_id: str = _EXISTING_EVENT_ID,
    etag: str = '"remote-etag-2"',
) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        html_link="https://calendar.google.com/remote",
        meet_link=None,
        etag=etag,
        updated=datetime(2090, 6, 1, 10, 0, 0, tzinfo=UTC),
        status="confirmed",
    )


# ─── Test: 412 from patch_event triggers conflict capture ─────────────


def test_412_during_patch_triggers_conflict_capture() -> None:
    """When patch_event raises 412, _capture_calendar_conflict is invoked."""

    async def _run() -> None:
        candidate = make_candidate(
            status=CandidateStatus.INTERVIEW_SCHEDULED,
        )
        employee = make_employee()
        interview = make_interview(
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=[employee],
            calendar=FakeCalendarPort(
                patch_outcomes=[make_http_status_error(412)],
                get_outcomes=[_make_get_event_response()],
            ),
            org_timezone="Asia/Ho_Chi_Minh",
        )
        # Seed the Interview so _get_interview_by_event_id can find it
        harness.session.interviews[interview.id] = interview

        captured_args: dict[str, object] = {}

        async def _capture_spy(
            user_id: UUID,
            candidate_id: UUID,
            event_id: str,
            operation: str,
        ) -> None:
            captured_args["user_id"] = user_id
            captured_args["candidate_id"] = candidate_id
            captured_args["event_id"] = event_id
            captured_args["operation"] = operation

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            with patch.object(harness.service, "_capture_calendar_conflict", _capture_spy):
                try:
                    spec = CalendarEventSpec(
                        summary="Test",
                        description=None,
                        start=_FUTURE_START,
                        end=_FUTURE_START + timedelta(minutes=30),
                        timezone="Asia/Ho_Chi_Minh",
                        calendar_id="recruitment@company.vn",
                        attendee_emails=(),
                    )
                    await harness.service._patch_calendar_event(
                        user_id=harness.user_id,
                        candidate_id=candidate.id,
                        event_id=_EXISTING_EVENT_ID,
                        spec=spec,
                        if_match='"local-etag-1"',
                    )
                    pytest.fail("Expected CalendarEventConflictError")
                except CalendarEventConflictError:
                    pass

        assert captured_args.get("candidate_id") == candidate.id
        assert captured_args.get("event_id") == _EXISTING_EVENT_ID
        assert captured_args.get("operation") == "patch_event"

    asyncio.run(_run())


# ─── Test: list unresolved conflicts with mock session ────────────────


def test_list_calendar_conflicts_with_mock() -> None:
    """list_calendar_conflicts returns conflicts from the calendar conflict repository."""

    async def _run() -> None:
        candidate = make_candidate()
        conflict_id = uuid4()
        conflict = CalendarConflict(
            id=conflict_id,
            interview_id=uuid4(),
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
            status="unresolved",
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            calendar_conflicts=[conflict],
        )

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            conflicts = await harness.service.list_calendar_conflicts()

        assert len(conflicts) == 1
        assert conflicts[0].id == conflict_id
        assert conflicts[0].status == "unresolved"

    asyncio.run(_run())


# ─── Test: resolve keep_google updates Interview etag ──────────────────


def test_resolve_keep_google_updates_interview_etag() -> None:
    """Resolving keep_google updates the Interview etag from the remote snapshot."""

    async def _run() -> None:
        candidate = make_candidate()

        conflict = CalendarConflict(
            interview_id=uuid4(),
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
            local_snapshot={"calendar_etag": '"local-etag-1"'},
            remote_snapshot={"etag": '"remote-etag-2"', "updated": "2090-06-01T10:00:00+00:00"},
            status="unresolved",
        )
        interview = Interview(
            id=conflict.interview_id,
            candidate_id=candidate.id,
            status="scheduled",
            round_name="Technical",
            start_at=_FUTURE_START,
            end_at=_FUTURE_START + timedelta(hours=1),
            timezone="Asia/Ho_Chi_Minh",
            calendar_event_id=_EXISTING_EVENT_ID,
            calendar_etag='"local-etag-1"',
            meeting_mode="google_meet",
        )

        harness = build_calendar_harness(
            candidates=[candidate],
            calendar_conflicts=[conflict],
        )

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            with patch.object(
                harness.service,
                "_get_interview_by_event_id",
                return_value=interview,
            ):
                resolved = await harness.service.resolve_calendar_conflict(
                    conflict_id=conflict.id,
                    choice="keep_google",
                    acting_user_id=harness.user_id,
                )

        assert resolved.status == "resolved_keep_google"
        assert interview.calendar_etag == '"remote-etag-2"'

    asyncio.run(_run())


# ─── Test: resolve overwrite_vroom pushes to Google ────────────────────


def test_resolve_overwrite_vroom_pushes_to_google() -> None:
    """Resolving overwrite_vroom pushes to Google and updates the etag."""

    async def _run() -> None:
        candidate = make_candidate()
        employee = make_employee()

        new_etag = '"new-etag-3"'
        updated_time = datetime(2090, 6, 1, 11, 0, 0, tzinfo=UTC)

        def _patch_response(call: RecordedCalendarCall) -> CalendarEvent:
            return CalendarEvent(
                event_id=_EXISTING_EVENT_ID,
                html_link=DEFAULT_HTML_LINK,
                meet_link=None,
                etag=new_etag,
                updated=updated_time,
                status="confirmed",
            )

        conflict = CalendarConflict(
            interview_id=uuid4(),
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
            local_snapshot={"calendar_etag": '"local-etag-1"'},
            remote_snapshot={"etag": '"remote-etag-2"'},
            status="unresolved",
        )
        interview = Interview(
            id=conflict.interview_id,
            candidate_id=candidate.id,
            status="scheduled",
            round_name="Technical",
            start_at=_FUTURE_START,
            end_at=_FUTURE_START + timedelta(hours=1),
            timezone="Asia/Ho_Chi_Minh",
            calendar_event_id=_EXISTING_EVENT_ID,
            calendar_etag='"local-etag-1"',
            meeting_mode="google_meet",
        )

        calendar = FakeCalendarPort(patch_outcomes=[_patch_response])
        harness = build_calendar_harness(
            candidates=[candidate],
            employees=[employee],
            calendar_conflicts=[conflict],
            calendar=calendar,
        )

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            with patch.object(
                harness.service,
                "_get_interview_by_event_id",
                return_value=interview,
            ):
                resolved = await harness.service.resolve_calendar_conflict(
                    conflict_id=conflict.id,
                    choice="overwrite_vroom",
                    acting_user_id=harness.user_id,
                )

        assert resolved.status == "resolved_overwrite_vroom"
        assert interview.calendar_etag == new_etag
        assert len(calendar.patch_calls) == 1
        assert calendar.patch_calls[0].event_id == _EXISTING_EVENT_ID

    asyncio.run(_run())


# ─── Test: resolve with invalid choice ─────────────────────────────────


def test_resolve_invalid_choice_raises_value_error() -> None:
    """An invalid resolution choice raises ValueError."""

    async def _run() -> None:
        candidate = make_candidate()
        conflict = CalendarConflict(
            interview_id=uuid4(),
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
            status="unresolved",
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            calendar_conflicts=[conflict],
        )

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            with pytest.raises(ValueError, match="Invalid resolution choice"):
                await harness.service.resolve_calendar_conflict(
                    conflict_id=conflict.id,
                    choice="invalid",
                    acting_user_id=harness.user_id,
                )

    asyncio.run(_run())


# ─── Test: resolve missing conflict ────────────────────────────────────


def test_resolve_missing_conflict_raises_not_found() -> None:
    """Resolving a non-existent conflict raises CalendarConflictNotFoundError."""

    async def _run() -> None:
        candidate = make_candidate()
        harness = build_calendar_harness(candidates=[candidate])

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            with pytest.raises(CalendarConflictNotFoundError):
                await harness.service.resolve_calendar_conflict(
                    conflict_id=uuid4(),
                    choice="keep_google",
                    acting_user_id=harness.user_id,
                )

    asyncio.run(_run())


# ─── Test: resolve already-resolved conflict ───────────────────────────


def test_resolve_already_resolved_raises_value_error() -> None:
    """Resolving an already-resolved conflict raises ValueError."""

    async def _run() -> None:
        candidate = make_candidate()
        conflict = CalendarConflict(
            interview_id=uuid4(),
            candidate_id=candidate.id,
            calendar_event_id=_EXISTING_EVENT_ID,
            status="resolved_keep_google",
        )
        harness = build_calendar_harness(
            candidates=[candidate],
            calendar_conflicts=[conflict],
        )

        with patch.object(candidate_service, "log_audit", harness.audit_sink):
            with pytest.raises(ValueError, match="already resolved"):
                await harness.service.resolve_calendar_conflict(
                    conflict_id=conflict.id,
                    choice="keep_google",
                    acting_user_id=harness.user_id,
                )

    asyncio.run(_run())
