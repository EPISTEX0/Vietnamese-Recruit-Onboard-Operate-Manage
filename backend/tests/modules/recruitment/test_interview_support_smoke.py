"""Smoke tests for the interview-calendar property-test seams.

Verifies that the reusable seams in ``_interview_support`` import cleanly and
behave as the downstream property tests (tasks 5.2-5.13, 7.x, 8.x, 10.x) will
rely on: the fake ``CalendarPort`` records calls and honours scripted outcomes,
the in-memory candidate repository commits/rolls back, the fake session resolves
interviewers through the real ``select(Employee)`` query, the spy audit sink
captures and can fail, the identity seams drive grant + refresh, the clock is
deterministic, and the timezone strategy yields valid IANA zones.

This is test infrastructure validation only - it asserts the seams are usable,
not feature behaviour (the feature lands in tasks 5.1+).

Requirements: 11.1
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import available_timezones

import httpx
import pytest
from hypothesis import given, settings

from src.modules.recruitment.application import interview_scheduler_service
from src.modules.recruitment.application.interview_scheduler_service import CalendarPort
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import (
    CalendarEventCreateFailedError,
    CalendarGrantMissingError,
    InterviewerMissingEmailError,
    InterviewerNotFoundError,
)
from src.modules.recruitment.domain.value_objects import CalendarEvent, CalendarEventSpec
from tests.modules.recruitment._interview_support import (
    FakeCalendarPort,
    FakeCalendarSession,
    FakeCandidateRepository,
    FixedClock,
    SpyAuditSink,
    build_calendar_harness,
    iana_timezones,
    make_candidate,
    make_employee,
    make_http_status_error,
)


def _spec(
    *, attendees: tuple[str, ...] = ("a@example.com",), meet: bool = True
) -> CalendarEventSpec:
    """Build a minimal valid CalendarEventSpec for adapter calls."""
    start = datetime(2025, 6, 1, 9, 0, tzinfo=UTC)
    return CalendarEventSpec(
        summary="Interview",
        description=None,
        start=start,
        end=start.replace(hour=10),
        timezone="Asia/Ho_Chi_Minh",
        calendar_id="recruitment@company.vn",
        attendee_emails=attendees,
        request_meet_link=meet,
    )


class TestFakeCalendarPort:
    """The fake CalendarPort satisfies the protocol and records calls."""

    def test_satisfies_calendar_port_protocol(self) -> None:
        """The fake is a runtime-checkable CalendarPort."""
        assert isinstance(FakeCalendarPort(), CalendarPort)

    async def test_create_records_call_and_returns_event(self) -> None:
        """create_event records the token + spec and returns a Meet event."""
        port = FakeCalendarPort()
        spec = _spec()

        event = await port.create_event("token-1", spec)

        assert isinstance(event, CalendarEvent)
        assert event.meet_link is not None
        assert len(port.create_calls) == 1
        call = port.create_calls[0]
        assert call.method == "create_event"
        assert call.access_token == "token-1"
        assert call.event_id is None
        assert call.spec is spec

    async def test_no_meet_request_yields_no_meet_link(self) -> None:
        """A spec that does not request Meet gets no Meet link by default."""
        port = FakeCalendarPort()
        event = await port.create_event("t", _spec(meet=False))
        assert event.meet_link is None

    async def test_scripted_create_failure_raises(self) -> None:
        """A scripted exception outcome is raised on create."""
        port = FakeCalendarPort(create_outcomes=[CalendarEventCreateFailedError()])
        with pytest.raises(CalendarEventCreateFailedError):
            await port.create_event("t", _spec())
        # The failing call is still recorded.
        assert len(port.create_calls) == 1

    async def test_scripted_401_then_default_success(self) -> None:
        """A scripted 401 then default lets the second attempt succeed."""
        error = make_http_status_error(401)
        port = FakeCalendarPort(create_outcomes=[error])

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await port.create_event("stale", _spec())
        assert exc_info.value.response.status_code == 401

        event = await port.create_event("fresh", _spec())
        assert event.event_id
        assert [c.access_token for c in port.create_calls] == ["stale", "fresh"]

    async def test_patch_echoes_event_id_and_delete_records(self) -> None:
        """patch_event echoes the id; delete_event records the target id."""
        port = FakeCalendarPort()
        patched = await port.patch_event("t", "evt-99", _spec())
        assert patched.event_id == "evt-99"

        await port.delete_event("t", "evt-99", "recruitment@company.vn")
        assert port.delete_calls[0].event_id == "evt-99"


class TestCandidateRepoAndSession:
    """In-memory candidate repo + session model commit/rollback semantics."""

    async def test_commit_then_rollback_restores_committed_state(self) -> None:
        """Rollback restores the last committed field values on the live object."""
        candidate = make_candidate(status=CandidateStatus.NEW)
        session = FakeCalendarSession()
        repo = FakeCandidateRepository(session, [candidate])

        # Mutate + stage + commit -> committed snapshot advances.
        candidate.status = CandidateStatus.INTERVIEW_SCHEDULED
        await repo.update(candidate)
        await session.commit()
        assert repo.committed_snapshot(candidate.id)["status"] == (
            CandidateStatus.INTERVIEW_SCHEDULED
        )

        # Mutate again then rollback -> live object reverts to committed state.
        candidate.status = CandidateStatus.REJECTED
        await repo.update(candidate)
        await session.rollback()

        live = await repo.get_by_id(candidate.id)
        assert live is not None
        assert live.status == CandidateStatus.INTERVIEW_SCHEDULED

    async def test_session_resolves_interviewers_via_real_query(self) -> None:
        """The session executes the service's real select(Employee) lookup."""
        emp_ok = make_employee(email="ok@example.com")
        emp_blank = make_employee(email="")
        harness = build_calendar_harness(
            candidates=[make_candidate()],
            employees=[emp_ok, emp_blank],
        )

        resolved = await harness.service._resolve_interviewers([emp_ok.id])
        assert resolved == [(emp_ok, "ok@example.com")]

        # Blank email is rejected by the real helper.
        with pytest.raises(InterviewerMissingEmailError):
            await harness.service._resolve_interviewers([emp_blank.id])

        # Unknown id is reported as unmatched by the real helper.
        unknown = make_employee().id
        with pytest.raises(InterviewerNotFoundError):
            await harness.service._resolve_interviewers([unknown])


class TestGrantAndTokenSeams:
    """Identity seams drive the grant guard and the 401-refresh path."""

    async def test_valid_grant_passes_and_missing_scope_blocks(self) -> None:
        """_ensure_org_connection_active passes with a valid org connection."""
        valid = build_calendar_harness(candidates=[make_candidate()])
        await valid.service._ensure_org_connection_active()  # no raise

        # When connection_repo is not set, it must refuse and name the
        # misconfiguration. The refusal is a domain CalendarGrantMissingError
        # (403 / CALENDAR_GRANT_MISSING) rather than a bare RuntimeError, so the
        # API layer renders a re-consent response instead of a 500.
        no_repo = build_calendar_harness(candidates=[make_candidate()], connection_repo=None)
        if hasattr(no_repo.service, "_connection_repo"):
            no_repo.service._connection_repo = None
        with pytest.raises(CalendarGrantMissingError, match="not configured"):
            await no_repo.service._ensure_org_connection_active()

    async def test_connection_repo_active_grant_passes(self) -> None:
        """A valid org connection with selected calendar passes the check."""
        valid = build_calendar_harness(candidates=[make_candidate()])
        await valid.service._ensure_org_connection_active()  # no raise

    async def test_with_org_token_decrypts_and_passes_token(self) -> None:
        """_with_org_token decrypts the org token before the adapter call."""
        harness = build_calendar_harness(candidates=[make_candidate()])
        seen: list[str] = []

        async def _call(token: str) -> str:
            seen.append(token)
            return token

        result = await harness.service._with_org_token(_call)
        assert result == "test-access-token"
        assert seen == ["test-access-token"]


class TestSpyAuditSink:
    """The spy audit sink records entries and supports a failure mode."""

    async def test_records_entry(self) -> None:
        """A normal call is captured in entries."""
        sink = SpyAuditSink()
        await sink(
            session=None,
            operation_type="schedule_interview",
            entity_type="candidate",
            success=True,
        )
        assert len(sink.entries) == 1
        assert sink.entries_for("schedule_interview")

    async def test_failure_mode_swallows_like_log_audit(self) -> None:
        """fail=True records the attempt but persists nothing and returns None."""
        sink = SpyAuditSink(fail=True)
        result = await sink(session=None, operation_type="x", entity_type="candidate")
        assert result is None
        assert sink.attempts and not sink.entries

    async def test_raises_mode_propagates(self) -> None:
        """raises=True propagates so the service's tolerance can be tested."""
        sink = SpyAuditSink(raises=True)
        with pytest.raises(RuntimeError):
            await sink(session=None, operation_type="x", entity_type="candidate")

    def test_installable_over_module_log_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The sink can replace the module-level log_audit symbol."""
        sink = SpyAuditSink()
        monkeypatch.setattr(interview_scheduler_service, "log_audit", sink)
        assert interview_scheduler_service.log_audit is sink


class TestFixedClock:
    """The injected clock is deterministic and derives future/past instants."""

    def test_now_is_fixed_and_future_past_bracket_it(self) -> None:
        """future() is after now and past() is before now."""
        clock = FixedClock(datetime(2025, 1, 1, 12, 0, tzinfo=UTC))
        assert clock() == clock.now
        assert clock.future(minutes=30) > clock.now
        assert clock.past(minutes=30) < clock.now


# Feature: interview-calendar-scheduling, timezone strategy (R11)
@settings(max_examples=50, deadline=None)
@given(tz=iana_timezones())
def test_timezone_strategy_yields_valid_iana_zones(tz: str) -> None:
    """Every drawn timezone is a real IANA zone recognized by zoneinfo.

    Validates: Requirements 11.1
    """
    assert tz in available_timezones()


def test_build_harness_smoke() -> None:
    """The harness wires a usable InterviewSchedulerService with all seams attached."""

    async def _run() -> None:
        harness = build_calendar_harness(
            candidates=[make_candidate()],
            employees=[make_employee()],
        )
        assert harness.service is not None
        assert harness.calendar.was_called is False
        # The org timezone stub is reachable through the service helper.
        assert await harness.service._get_org_timezone() == "Asia/Ho_Chi_Minh"

    asyncio.run(_run())
