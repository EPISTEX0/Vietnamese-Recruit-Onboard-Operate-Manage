"""Reusable property-test seams for interview-calendar scheduling.

This module provides the in-memory test doubles and Hypothesis strategies that
the interview-calendar property tests (tasks 5.2-5.13, 7.x, 8.x, 10.x) build
on, so the orchestration logic in :class:`InterviewSchedulerService` can be exercised
without a real Google Calendar API or a real database. It is imported directly
by the property-test modules (``from ._interview_support import ...``); it is
not itself a test module.

Seams provided
--------------
* :class:`FakeCalendarPort` - a ``CalendarPort`` implementation that records
  every call (method name, access token, event id, and the
  :class:`CalendarEventSpec`) and returns scripted :class:`CalendarEvent`
  results or raises scripted errors (e.g. ``CalendarEventCreateFailedError``,
  ``CalendarEventUpdateFailedError``, or an ``httpx.HTTPStatusError`` with
  status ``401`` to drive the token-refresh path). Scripting is configurable
  per instance via outcome queues.
* :class:`FakeCandidateRepository`, :class:`FakeInterviewRepository`, and
  :class:`FakeCalendarSession` - in-memory candidate/interview persistence with
  explicit staging + commit/rollback semantics (for the atomic-rollback
  property) and an employee lookup compatible with how
  :meth:`InterviewSchedulerService._resolve_interviewers` reads ``select(Employee)``.
  Calendar state (event id, scheduled start, applied timezone) lives on
  :class:`Interview`, so tests seed it with ``build_calendar_harness(interviews=...)``
  and read it back via ``harness.scheduled_interview(candidate_id)``.
* :class:`SpyAuditSink` - a stand-in for the module-level ``log_audit`` that
  records audit entries and can be configured to raise (to test R12.5 - audit
  failure must never roll back the action). Tests install it with
  ``monkeypatch.setattr(interview_scheduler_service, "log_audit", sink)``.
* :class:`FakeOAuthGrantRepository`, :class:`FakeCalendarGrantChecker`, and
  :class:`FakeTokenCipher` - the identity-side seams the calendar helpers
  (``_assert_calendar_grant`` / ``_with_calendar_token``) read, so grant-guard
  (R9) and 401-refresh behaviour are testable.
* :class:`FixedClock` - a deterministic ``now`` provider for the
  "start must be in the future" rule (R1.4). See the note on
  :func:`build_calendar_harness` for how task 5.1 should consume it.
* :func:`iana_timezones` - a Hypothesis strategy drawing IANA timezones from
  ``zoneinfo.available_timezones()`` (R11).

Requirements: 11.1
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
from zoneinfo import available_timezones

import httpx
from hypothesis import strategies as st

from src.modules.employee.domain.entities import Employee
from src.modules.identity.api.schemas import GoogleTokens, GrantStatus
from src.modules.identity.domain.entities import OAuthGrant
from src.modules.recruitment.application import (
    candidate_lifecycle_service as candidate_lifecycle_service_module,
)
from src.modules.recruitment.application import (
    interview_scheduler_service as interview_scheduler_service_module,
)
from src.modules.recruitment.application.candidate_lifecycle_service import (
    CandidateLifecycleService,
)
from src.modules.recruitment.application.interview_scheduler_service import (
    CalendarPort,
    InterviewSchedulerService,
)
from src.modules.recruitment.domain.entities import Candidate, Interview, InterviewParticipant
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.value_objects import (
    CalendarEvent,
    CalendarEventSpec,
    SyncEventChanges,
)

# The Calendar scope that makes ``calendar_grant_valid`` true (mirrors the
# identity ``OAuthService._CALENDAR_SCOPES`` set).
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DEFAULT_GRANTED_SCOPES: tuple[str, ...] = (CALENDAR_SCOPE,)

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_MEET_LINK = "https://meet.google.com/fake-abc-defg"
DEFAULT_HTML_LINK = "https://calendar.google.com/event/fake"


# ─── Recorded adapter calls + scripted outcomes ────────────────────────


@dataclass(frozen=True)
class RecordedCalendarCall:
    """A single call captured by :class:`FakeCalendarPort`.

    Attributes:
        method: The adapter method name (``"create_event"``, ``"patch_event"``,
            or ``"delete_event"``).
        access_token: The access token the service passed to the adapter.
        event_id: The target event id for patch/delete calls; ``None`` for
            create calls.
        spec: The :class:`CalendarEventSpec` for create/patch calls; ``None``
            for delete calls.
        if_match: The etag the service sent as the conditional-write
            precondition, or ``None`` for an unconditional call. Recorded so
            tests can assert that a reschedule really does condition its patch
            on the stored etag -- without it the restored conditional write
            could be deleted again and every test would stay green.
    """

    method: str
    access_token: str
    event_id: str | None
    spec: CalendarEventSpec | None
    if_match: str | None = None


# A scripted outcome for a create/patch call: an event to return, an exception
# to raise, or a callable computing the event from the recorded call.
EventOutcome = CalendarEvent | BaseException | Callable[[RecordedCalendarCall], CalendarEvent]
# A scripted outcome for a delete call: ``None`` (success), an exception to
# raise, or a callable side effect.
DeleteOutcome = BaseException | Callable[[RecordedCalendarCall], None] | None
# A scripted outcome for a sync (list_events) call.
SyncOutcome = SyncEventChanges | BaseException | Callable[[RecordedCalendarCall], SyncEventChanges]
# A scripted outcome for a get_event call.
GetOutcome = CalendarEvent | BaseException | Callable[[RecordedCalendarCall], CalendarEvent]


class _Default:
    """Sentinel marking "no scripted outcome; use the default behaviour"."""


_DEFAULT = _Default()


def make_http_status_error(
    status_code: int,
    *,
    method: str = "POST",
    url: str = "https://www.googleapis.com/calendar/v3/calendars/primary/events",
) -> httpx.HTTPStatusError:
    """Build an ``httpx.HTTPStatusError`` for a given status code.

    Useful for scripting the 401 token-refresh path on :class:`FakeCalendarPort`
    (``_with_calendar_token`` catches ``httpx.HTTPStatusError`` and inspects
    ``exc.response.status_code``).

    Args:
        status_code: The HTTP status code carried by the error's response.
        method: The HTTP method of the synthetic request.
        url: The URL of the synthetic request.

    Returns:
        An ``httpx.HTTPStatusError`` whose response reports ``status_code``.
    """
    request = httpx.Request(method, url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


# ─── Fake CalendarPort ─────────────────────────────────────────────────


class FakeCalendarPort:
    """In-memory ``CalendarPort`` that records calls and returns scripted I/O.

    Every ``create_event`` / ``patch_event`` / ``delete_event`` invocation is
    appended to :attr:`calls` with its method, access token, target event id,
    and the :class:`CalendarEventSpec`. Outcomes are scripted per instance via
    optional outcome queues consumed in order:

    * ``create_outcomes`` / ``patch_outcomes``: each item is a
      :class:`CalendarEvent` to return, a ``BaseException`` to raise, or a
      callable computing the event from the recorded call.
    * ``delete_outcomes``: each item is ``None`` (success), a ``BaseException``
      to raise, or a callable side effect.
    * ``sync_outcomes``: each item is a :class:`SyncEventChanges`, a
      ``BaseException``, or a callable computing the result.
    * ``get_outcomes``: each item is a :class:`CalendarEvent`, a
      ``BaseException``, or a callable computing the event.

    When a queue is omitted (``None``) or becomes exhausted, the default
    behaviour applies: create/patch synthesize a :class:`CalendarEvent` from the
    spec (create mints a fresh event id; patch echoes the patched id), and
    delete succeeds. This makes the common "happy path" require no scripting,
    while failure and 401-refresh paths stay fully controllable.
    """

    def __init__(
        self,
        *,
        create_outcomes: Sequence[EventOutcome] | None = None,
        patch_outcomes: Sequence[EventOutcome] | None = None,
        delete_outcomes: Sequence[DeleteOutcome] | None = None,
        sync_outcomes: Sequence[SyncOutcome] | None = None,
        get_outcomes: Sequence[GetOutcome] | None = None,
        meet_link: str | None = DEFAULT_MEET_LINK,
        html_link: str | None = DEFAULT_HTML_LINK,
    ) -> None:
        """Initialize the fake with optional per-method outcome queues.

        Args:
            create_outcomes: Scripted outcomes for ``create_event`` calls.
            patch_outcomes: Scripted outcomes for ``patch_event`` calls.
            delete_outcomes: Scripted outcomes for ``delete_event`` calls.
            sync_outcomes: Scripted outcomes for ``list_events`` calls.
            get_outcomes: Scripted outcomes for ``get_event`` calls.
            meet_link: Default Meet link returned when a spec requests one and
                no explicit outcome is scripted.
            html_link: Default ``htmlLink`` returned by default outcomes.
        """
        self.calls: list[RecordedCalendarCall] = []
        self._create_outcomes: list[EventOutcome] | None = (
            list(create_outcomes) if create_outcomes is not None else None
        )
        self._patch_outcomes: list[EventOutcome] | None = (
            list(patch_outcomes) if patch_outcomes is not None else None
        )
        self._delete_outcomes: list[DeleteOutcome] | None = (
            list(delete_outcomes) if delete_outcomes is not None else None
        )
        self._sync_outcomes: list[SyncOutcome] | None = (
            list(sync_outcomes) if sync_outcomes is not None else None
        )
        self._get_outcomes: list[GetOutcome] | None = (
            list(get_outcomes) if get_outcomes is not None else None
        )
        self._meet_link = meet_link
        self._html_link = html_link
        self._event_counter = 0

    # -- CalendarPort protocol ------------------------------------------

    async def create_event(self, access_token: str, spec: CalendarEventSpec) -> CalendarEvent:
        """Record a create call and return/raise its scripted outcome."""
        call = RecordedCalendarCall(
            method="create_event", access_token=access_token, event_id=None, spec=spec
        )
        self.calls.append(call)
        outcome = self._take(self._create_outcomes)
        if isinstance(outcome, _Default):
            return self._default_event(spec, event_id=self._mint_event_id())
        return self._resolve_event(outcome, call)

    async def patch_event(
        self,
        access_token: str,
        event_id: str,
        spec: CalendarEventSpec,
        if_match: str | None = None,
    ) -> CalendarEvent:
        """Record a patch call and return/raise its scripted outcome."""
        call = RecordedCalendarCall(
            method="patch_event",
            access_token=access_token,
            event_id=event_id,
            spec=spec,
            if_match=if_match,
        )
        self.calls.append(call)
        outcome = self._take(self._patch_outcomes)
        if isinstance(outcome, _Default):
            return self._default_event(spec, event_id=event_id)
        return self._resolve_event(outcome, call)

    async def delete_event(
        self,
        access_token: str,
        event_id: str,
        calendar_id: str,
        if_match: str | None = None,
    ) -> None:
        """Record a delete call and raise its scripted outcome, if any."""
        call = RecordedCalendarCall(
            method="delete_event",
            access_token=access_token,
            event_id=event_id,
            spec=None,
            if_match=if_match,
        )
        self.calls.append(call)
        outcome = self._take(self._delete_outcomes)
        if isinstance(outcome, _Default) or outcome is None:
            return
        if isinstance(outcome, BaseException):
            raise outcome
        outcome(call)

    async def get_event(
        self,
        access_token: str,
        event_id: str,
        calendar_id: str,
    ) -> CalendarEvent:
        """Record a get_event call and return a default or scripted outcome."""
        call = RecordedCalendarCall(
            method="get_event", access_token=access_token, event_id=event_id, spec=None
        )
        self.calls.append(call)
        outcome = self._take(self._get_outcomes)
        if isinstance(outcome, _Default):
            return CalendarEvent(event_id=event_id, html_link=None, meet_link=None)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, CalendarEvent):
            return outcome
        return outcome(call)

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        *,
        sync_token: str | None = None,
        page_token: str | None = None,
        max_results: int = 250,
    ) -> SyncEventChanges:
        """Record a list_events call and return/raise its scripted outcome."""
        call = RecordedCalendarCall(
            method="list_events",
            access_token=access_token,
            event_id=None,
            spec=None,
        )
        self.calls.append(call)
        outcome = self._take(self._sync_outcomes)
        if isinstance(outcome, _Default):
            return SyncEventChanges(
                events=(),
                next_sync_token="tok-default-sync",
            )
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, SyncEventChanges):
            return outcome
        return outcome(call)

    # -- Recording helpers ----------------------------------------------

    @property
    def create_calls(self) -> list[RecordedCalendarCall]:
        """All recorded ``create_event`` calls, in order."""
        return [c for c in self.calls if c.method == "create_event"]

    @property
    def patch_calls(self) -> list[RecordedCalendarCall]:
        """All recorded ``patch_event`` calls, in order."""
        return [c for c in self.calls if c.method == "patch_event"]

    @property
    def delete_calls(self) -> list[RecordedCalendarCall]:
        """All recorded ``delete_event`` calls, in order."""
        return [c for c in self.calls if c.method == "delete_event"]

    @property
    def sync_calls(self) -> list[RecordedCalendarCall]:
        """All recorded ``list_events`` calls, in order."""
        return [c for c in self.calls if c.method == "list_events"]

    @property
    def get_calls(self) -> list[RecordedCalendarCall]:
        """All recorded ``get_event`` calls, in order."""
        return [c for c in self.calls if c.method == "get_event"]

    @property
    def was_called(self) -> bool:
        """True if the adapter was invoked at least once."""
        return bool(self.calls)

    # -- Internals ------------------------------------------------------

    def _mint_event_id(self) -> str:
        """Return a fresh, deterministic-per-instance event id."""
        self._event_counter += 1
        return f"evt-{self._event_counter:04d}-{uuid4().hex[:8]}"

    def _default_event(self, spec: CalendarEventSpec, *, event_id: str) -> CalendarEvent:
        """Build a successful :class:`CalendarEvent` from a spec."""
        return CalendarEvent(
            event_id=event_id,
            html_link=self._html_link,
            meet_link=self._meet_link if spec.request_meet_link else None,
            invited_emails=tuple(spec.attendee_emails),
        )

    @staticmethod
    def _take(queue: list[Any] | None) -> Any:
        """Pop the next scripted outcome, or the default sentinel."""
        if queue is None or not queue:
            return _DEFAULT
        return queue.pop(0)

    @staticmethod
    def _resolve_event(outcome: EventOutcome, call: RecordedCalendarCall) -> CalendarEvent:
        """Resolve a scripted create/patch outcome into an event (or raise)."""
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, CalendarEvent):
            return outcome
        return outcome(call)


# ─── In-memory candidate repository + session ──────────────────────────


class FakeCandidateRepository:
    """In-memory ``CandidateRepository`` stand-in with staging semantics.

    Models the subset of :class:`CandidateRepository` that
    :class:`InterviewSchedulerService` uses for the interview flows: ``get_by_id`` and
    ``update``. Mutations made through ``update`` are staged on the shared
    :class:`FakeCalendarSession` and only become visible to a *fresh* read
    after ``commit``; ``rollback`` discards them. This lets the atomic-rollback
    property (R3 / Property 12) assert that a failed Calendar create leaves the
    persisted Candidate exactly as it was before the request.

    Because the service mutates the live :class:`Candidate` instance in place
    (``candidate.status = ...``) before calling ``update``, the repository keeps
    a committed *snapshot* (a copy) separate from the live object, so a rollback
    can restore the committed field values onto the live instance.
    """

    def __init__(self, session: FakeCalendarSession, candidates: Sequence[Candidate]) -> None:
        """Initialize the repository over a session and seed candidates.

        Args:
            session: The shared fake session coordinating commit/rollback.
            candidates: Candidates that already exist (committed state).
        """
        self._session = session
        self._live: dict[UUID, Candidate] = {c.id: c for c in candidates}
        self._committed: dict[UUID, dict[str, Any]] = {
            c.id: _snapshot_candidate(c) for c in candidates
        }
        session.bind_candidate_repo(self)

    async def get_by_id(self, candidate_id: UUID) -> Candidate | None:
        """Return the live Candidate for ``candidate_id``, or ``None``."""
        return self._live.get(candidate_id)

    async def update(self, candidate: Candidate) -> Candidate:
        """Stage a Candidate mutation (visible on commit, undone on rollback)."""
        self._live[candidate.id] = candidate
        self._session.stage_candidate(candidate.id)
        return candidate

    def committed_snapshot(self, candidate_id: UUID) -> dict[str, Any] | None:
        """Return the last *committed* field snapshot for a Candidate."""
        snap = self._committed.get(candidate_id)
        return dict(snap) if snap is not None else None

    # -- commit/rollback hooks (called by FakeCalendarSession) ----------

    def _commit_staged(self, staged_ids: set[UUID]) -> None:
        """Promote staged live values into the committed snapshot store."""
        for candidate_id in staged_ids:
            live = self._live.get(candidate_id)
            if live is not None:
                self._committed[candidate_id] = _snapshot_candidate(live)

    def _rollback_staged(self, staged_ids: set[UUID]) -> None:
        """Restore committed field values onto the live Candidate instances."""
        for candidate_id in staged_ids:
            snap = self._committed.get(candidate_id)
            live = self._live.get(candidate_id)
            if snap is not None and live is not None:
                for field_name, value in snap.items():
                    setattr(live, field_name, value)


# Candidate fields the snapshot tracks for commit/rollback fidelity.
_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "status",
    "rejection_reason",
    "rejected_at",
    "accepted_at",
    "archived_at",
)

# Interview fields the snapshot tracks. Since the calendar-scheduling refactor
# these -- not the Candidate's -- are the calendar state the interview flows
# read and write, so the atomic-rollback and reschedule-failure properties are
# asserted against them.
_INTERVIEW_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "status",
    "start_at",
    "end_at",
    "timezone",
    "calendar_event_id",
    "calendar_id",
    "calendar_etag",
    "calendar_updated",
    "meeting_mode",
    "meeting_link",
    "needs_relink",
)


def _snapshot_candidate(candidate: Candidate) -> dict[str, Any]:
    """Capture the mutable interview/status fields of a Candidate."""
    return {name: getattr(candidate, name) for name in _SNAPSHOT_FIELDS}


def _snapshot_interview(interview: Interview) -> dict[str, Any]:
    """Capture the mutable calendar/scheduling fields of an Interview."""
    return {name: getattr(interview, name) for name in _INTERVIEW_SNAPSHOT_FIELDS}


class FakeInterviewRepository:
    """In-memory ``InterviewRepository`` stand-in over the session's store.

    :class:`InterviewSchedulerService` reads interviews two ways: through
    ``select(Interview)`` on the session (``_get_interview_by_event_id``) and
    through ``self._interview_repo`` (``_get_scheduled_interview``,
    ``_get_interview_or_raise``). Both must see the same rows -- an Interview
    that ``schedule_interview`` staged with ``session.add`` has to be visible to
    the very next ``reschedule_interview`` -- so this repository holds no state
    of its own and reads straight out of :class:`FakeCalendarSession`.
    """

    def __init__(self, session: FakeCalendarSession) -> None:
        """Initialize the repository over the session holding the interviews."""
        self._session = session

    async def find_by_candidate_id(self, candidate_id: UUID) -> list[Interview]:
        """Return every live Interview belonging to a Candidate."""
        return [iv for iv in self._session.interviews.values() if iv.candidate_id == candidate_id]

    async def get_by_id(self, interview_id: UUID) -> Interview | None:
        """Return the live Interview for ``interview_id``, or ``None``."""
        return self._session.interviews.get(interview_id)

    async def update(self, interview: Interview) -> Interview:
        """Stage an Interview mutation (visible on commit, undone on rollback)."""
        self._session.add(interview)
        return interview

    async def create(self, interview: Interview) -> Interview:
        """Stage a new Interview the same way ``update`` stages a change."""
        self._session.add(interview)
        return interview

    async def add_participant(self, participant: InterviewParticipant) -> InterviewParticipant:
        """Record an InterviewParticipant so tests can assert on invitees."""
        self._session.participants.append(participant)
        return participant


class FakeCalendarSession:
    """Minimal async session modelling commit/rollback + employee lookup.

    Provides the two things :class:`InterviewSchedulerService` needs from its
    ``AsyncSession`` during the interview flows:

    1. ``commit`` / ``rollback`` that drive the staged Candidate mutations held
       by :class:`FakeCandidateRepository` (atomicity for R3 / Property 12).
    2. ``execute`` for the ``select(Employee).where(col(Employee.id).in_(...))``
       query used by :meth:`InterviewSchedulerService._resolve_interviewers`. The session
       returns a result whose ``.scalars().all()`` yields the seeded Employees
       whose ids appear in the query's ``IN`` clause.

    Employees are matched by inspecting the compiled statement's bind
    parameters, so the service's real ``select(Employee)`` expression is used
    unchanged - no monkeypatching of ``_resolve_interviewers`` is required.
    """

    def __init__(
        self,
        employees: Sequence[Employee] | None = None,
        interviews: Sequence[Interview] | None = None,
    ) -> None:
        """Initialize the session and seed the employee/interview lookups.

        Args:
            employees: Employees available to the interviewer lookup.
            interviews: Interviews that already exist (committed state), i.e.
                Candidates that are already scheduled on the calendar.
        """
        self.employees: dict[UUID, Employee] = {e.id: e for e in (employees or [])}
        self.interviews: dict[UUID, Interview] = {iv.id: iv for iv in (interviews or [])}
        self.participants: list[InterviewParticipant] = []
        self.commit_count = 0
        self.rollback_count = 0
        self._candidate_repo: FakeCandidateRepository | None = None
        self._staged_candidate_ids: set[UUID] = set()
        self._committed_interviews: dict[UUID, dict[str, Any]] = {
            iv.id: _snapshot_interview(iv) for iv in (interviews or [])
        }
        self._staged_interview_ids: set[UUID] = set()

    def add(self, instance: object) -> None:
        """Stage an Interview (visible on commit, undone on rollback).

        Other entity types (``InterviewParticipant``, audit rows) are ignored:
        no property under test reads them back through the session.
        """
        if isinstance(instance, Interview):
            self.interviews[instance.id] = instance
            self._staged_interview_ids.add(instance.id)

    def bind_candidate_repo(self, repo: FakeCandidateRepository) -> None:
        """Associate the candidate repository for commit/rollback coordination."""
        self._candidate_repo = repo

    def stage_candidate(self, candidate_id: UUID) -> None:
        """Mark a Candidate as having uncommitted staged mutations."""
        self._staged_candidate_ids.add(candidate_id)

    def committed_interview_snapshot(self, interview_id: UUID) -> dict[str, Any] | None:
        """Return the last *committed* field snapshot for an Interview."""
        snap = self._committed_interviews.get(interview_id)
        return dict(snap) if snap is not None else None

    async def commit(self) -> None:
        """Promote staged Candidate and Interview mutations into committed state."""
        self.commit_count += 1
        if self._candidate_repo is not None:
            self._candidate_repo._commit_staged(self._staged_candidate_ids)
        self._staged_candidate_ids.clear()
        for interview_id in self._staged_interview_ids:
            live = self.interviews.get(interview_id)
            if live is not None:
                self._committed_interviews[interview_id] = _snapshot_interview(live)
        self._staged_interview_ids.clear()

    async def rollback(self) -> None:
        """Discard staged Candidate and Interview mutations, restoring committed state."""
        self.rollback_count += 1
        if self._candidate_repo is not None:
            self._candidate_repo._rollback_staged(self._staged_candidate_ids)
        self._staged_candidate_ids.clear()
        for interview_id in self._staged_interview_ids:
            snap = self._committed_interviews.get(interview_id)
            live = self.interviews.get(interview_id)
            if snap is None:
                # Never committed: the row did not exist before this transaction,
                # so a rollback must make it vanish entirely.
                self.interviews.pop(interview_id, None)
            elif live is not None:
                for field_name, value in snap.items():
                    setattr(live, field_name, value)
        self._staged_interview_ids.clear()

    async def execute(self, statement: Any) -> _FakeEmployeeResult | _FakeInterviewResult:
        """Execute an interviewer ``select(Employee)`` or ``select(Interview)`` against seeded data.

        When the statement targets ``Employee``, extracts the requested ids from the
        statement's ``IN`` clause and returns the matching seeded Employees.
        When the statement targets ``Interview``, returns a result matching on
        ``candidate_id`` or ``calendar_event_id``.

        Args:
            statement: The SQLAlchemy/SQLModel select statement.

        Returns:
            A result object exposing ``.scalars().all()`` and ``.all()``.
        """
        # Detect if this is an Interview query by checking the compiled columns.
        try:
            compiled = statement.compile()
            raw_sql = str(compiled).lower()
        except Exception:
            raw_sql = ""

        if "from interviews" in raw_sql or "from interview" in raw_sql:
            return self._execute_interview_query(compiled, statement)

        # Default: Employee query
        requested_ids = _extract_in_clause_uuids(statement)
        if requested_ids is None:
            matched = list(self.employees.values())
        else:
            matched = [self.employees[id_] for id_ in requested_ids if id_ in self.employees]
        return _FakeEmployeeResult(matched)

    def _execute_interview_query(self, compiled: Any, statement: Any) -> _FakeInterviewResult:
        """Execute an Interview query against seeded interviews."""
        # Extract bind parameters
        params = {}
        try:
            params = compiled.params
        except Exception:
            pass

        matched = list(self.interviews.values())
        # Filter by candidate_id if present
        candidate_id = params.get("candidate_id_1") or params.get("candidate_id")
        if candidate_id is not None and isinstance(candidate_id, UUID):
            matched = [i for i in matched if i.candidate_id == candidate_id]
        # Filter by calendar_event_id if present
        event_id = params.get("calendar_event_id_1") or params.get("calendar_event_id")
        if event_id is not None:
            matched = [i for i in matched if i.calendar_event_id == event_id]
        return _FakeInterviewResult(matched)


class _FakeEmployeeResult:
    """Result stand-in exposing the SQLAlchemy access patterns used here."""

    def __init__(self, employees: list[Employee]) -> None:
        self._employees = employees

    def scalars(self) -> _FakeScalars:
        """Return a scalars accessor (``_resolve_interviewers`` path)."""
        return _FakeScalars(self._employees)

    def all(self) -> list[tuple[Any, ...]]:
        """Return ``(id,)`` rows (legacy ``_validate_interviewer_ids`` path)."""
        return [(e.id,) for e in self._employees]


class _FakeInterviewResult:
    """Result stand-in for ``select(Interview)`` queries."""

    def __init__(self, interviews: list[Interview]) -> None:
        self._interviews = interviews

    def scalars(self) -> _FakeScalars:
        """Return a scalars accessor wrapping Interview entities."""
        return _FakeScalars(self._interviews)


class _FakeScalars:
    """Scalars accessor returning the matched Employee entities."""

    def __init__(self, employees: list[Employee]) -> None:
        self._employees = employees

    def all(self) -> list[Employee]:
        """Return all matched Employee entities."""
        return list(self._employees)

    def first(self) -> Employee | None:
        """Return the first matched row, matching SQLAlchemy scalars."""
        return self._employees[0] if self._employees else None


def _extract_in_clause_uuids(statement: Any) -> list[UUID] | None:
    """Best-effort extraction of UUIDs from a statement's ``IN`` parameters.

    Compiles the statement and reads its bind parameter values, returning the
    UUID-valued ones. Returns ``None`` when extraction is not possible, so the
    caller falls back to returning all seeded employees.

    Args:
        statement: The SQLAlchemy/SQLModel select statement.

    Returns:
        The list of UUID bind values, or ``None`` if they cannot be read.
    """
    try:
        compiled = statement.compile()
        params = compiled.params
    except Exception:  # noqa: BLE001 - extraction is best-effort
        return None

    uuids = [value for value in params.values() if isinstance(value, UUID)]
    return uuids or None


# ─── Spy audit sink ────────────────────────────────────────────────────


@dataclass
class RecordedAuditEntry:
    """A captured ``log_audit`` invocation's keyword arguments."""

    operation_type: str
    entity_type: str
    entity_id: UUID | None
    user_id: UUID | None
    previous_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    change_summary: str | None
    success: bool
    extra: dict[str, Any] = field(default_factory=dict)


class SpyAuditSink:
    """Records ``log_audit`` calls and optionally simulates write failure.

    Drop-in replacement for the module-level
    ``recruitment.infrastructure.audit_repository.log_audit`` async function.
    Tests install it with::

        monkeypatch.setattr(interview_scheduler_service, "log_audit", spy)

    Each call is captured in :attr:`entries`. When ``fail`` is true the sink
    *swallows* the error exactly like the real ``log_audit`` (which wraps writes
    in try/except and returns ``None``), so the action under test still
    succeeds (R12.5 / Property 22). Set ``raises=True`` instead to model a sink
    that propagates - useful for asserting the service itself tolerates a raise.
    """

    def __init__(self, *, fail: bool = False, raises: bool = False) -> None:
        """Initialize the spy.

        Args:
            fail: When true, record an attempt but return ``None`` without
                persisting (mirrors ``log_audit`` swallowing an internal error).
            raises: When true, raise ``RuntimeError`` on each call (to verify the
                caller tolerates audit-write exceptions).
        """
        self.entries: list[RecordedAuditEntry] = []
        self.attempts: list[RecordedAuditEntry] = []
        self._fail = fail
        self._raises = raises

    async def __call__(
        self,
        session: Any,
        operation_type: str,
        entity_type: str,
        entity_id: UUID | None = None,
        user_id: UUID | None = None,
        previous_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        change_summary: str | None = None,
        success: bool = True,
        **extra: Any,
    ) -> RecordedAuditEntry | None:
        """Capture an audit call, honouring the configured failure mode."""
        entry = RecordedAuditEntry(
            operation_type=operation_type,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            previous_value=previous_value,
            new_value=new_value,
            change_summary=change_summary,
            success=success,
            extra=dict(extra),
        )
        self.attempts.append(entry)
        if self._raises:
            raise RuntimeError("simulated audit-write failure")
        if self._fail:
            return None
        self.entries.append(entry)
        return entry

    def entries_for(self, operation_type: str) -> list[RecordedAuditEntry]:
        """Return successfully recorded entries with the given operation type."""
        return [e for e in self.entries if e.operation_type == operation_type]


# ─── Identity-side seams (grant + token refresh + cipher) ──────────────


class FakeOAuthGrantRepository:
    """In-memory ``OAuthGrantReader`` returning a single configured grant."""

    def __init__(self, grant: OAuthGrant | None) -> None:
        """Initialize with the grant returned for every user (or ``None``)."""
        self._grant = grant
        self.lookups: list[UUID] = []

    async def get_by_user_id(self, user_id: UUID) -> OAuthGrant | None:
        """Return the configured grant, recording the lookup."""
        self.lookups.append(user_id)
        return self._grant


class FakeCalendarGrantChecker:
    """``CalendarGrantChecker`` stand-in for grant status + token refresh.

    ``determine_grant_status`` reports ``calendar_grant_valid`` based on whether
    the configured Calendar scope is present in the supplied scopes (mirroring
    the real ``OAuthService``). ``refresh_google_token`` returns a scripted
    :class:`GoogleTokens` (or ``None`` to model a revoked grant) and records its
    invocations, so the 401-refresh path in ``_with_calendar_token`` is testable.
    """

    def __init__(
        self,
        *,
        refreshed_tokens: GoogleTokens | None = None,
        calendar_scope: str = CALENDAR_SCOPE,
    ) -> None:
        """Initialize the checker.

        Args:
            refreshed_tokens: Tokens returned by ``refresh_google_token``;
                ``None`` models a revoked grant (refresh fails).
            calendar_scope: The scope whose presence makes the grant valid.
        """
        self._refreshed_tokens = refreshed_tokens
        self._calendar_scope = calendar_scope
        self.refresh_calls: list[UUID] = []

    def determine_grant_status(self, scopes: list[str]) -> GrantStatus:
        """Compute grant status from granted scopes."""
        calendar_valid = self._calendar_scope in set(scopes)
        return GrantStatus(gmail_grant_valid=False, calendar_grant_valid=calendar_valid)

    async def refresh_google_token(self, user_id: UUID) -> GoogleTokens | None:
        """Return the scripted refreshed tokens, recording the call."""
        self.refresh_calls.append(user_id)
        return self._refreshed_tokens


class FakeOrgConnectionRepository:
    """In-memory ``OrganizationGoogleConnectionRepository`` stand-in.

    Since interviews are booked on the Organization's *selected recruitment
    calendar*, this connection -- not the acting HR user's personal OAuth grant
    -- is what authorizes a Calendar write. It is therefore the seam the guard in
    ``_ensure_org_connection_active`` / ``_resolve_org_calendar_id`` reads, and
    the one a test drives to model "the Organization cannot write to Calendar".

    Two independent ways to make the connection unusable:

    * ``connected=False`` -- no connection row at all (``get_singleton`` returns
      ``None``), i.e. Google was never linked;
    * ``status="revoked"`` (anything other than ``"connected"``) -- a row exists
      but the link is no longer live.
    """

    def __init__(
        self,
        *,
        connected: bool = True,
        status: str = "connected",
        selected_calendar_id: str | None = "recruitment@company.vn",
        access_token_enc: str = "enc:test-access-token",
        refresh_token_enc: str = "enc:test-refresh-token",
        client_secret_enc: str = "enc:test-secret",
        token_expires_at: datetime | None = None,
    ) -> None:
        """Initialize the connection seam.

        Args:
            connected: When false, ``get_singleton`` returns ``None`` (no
                connection row exists at all).
            status: The connection status; anything but ``"connected"`` makes
                the guard reject.
            selected_calendar_id: The recruitment calendar id; ``None`` models
                "connected but no calendar selected yet".
            access_token_enc: Encrypted access token.
            refresh_token_enc: Encrypted refresh token.
            client_secret_enc: Encrypted OAuth client secret.
            token_expires_at: Access-token expiry, if any.
        """
        self._connected = connected
        self.connection = SimpleNamespace(
            status=status,
            selected_calendar_id=selected_calendar_id,
            access_token_enc=access_token_enc,
            refresh_token_enc=refresh_token_enc,
            client_secret_enc=client_secret_enc,
            token_expires_at=token_expires_at,
        )
        self.upserts: list[Any] = []

    async def get_singleton(self) -> Any | None:
        """Return the Organization's Google connection, or ``None`` if unlinked."""
        return self.connection if self._connected else None

    async def upsert_singleton(self, connection: Any) -> None:
        """Record a connection write (e.g. a refreshed access token)."""
        self.upserts.append(connection)


class FakeTokenCipher:
    """``TokenCipher`` stand-in that maps ciphertext to plaintext.

    By default it strips a ``"enc:"`` prefix so a stored ``"enc:tok-123"``
    decrypts to ``"tok-123"``; an explicit mapping can be supplied for finer
    control. Unknown ciphertext decrypts to itself.
    """

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        """Initialize with an optional explicit ciphertext->plaintext map."""
        self._mapping = dict(mapping or {})

    def decrypt(self, ciphertext: str) -> str:
        """Return the plaintext for a stored ciphertext."""
        if ciphertext in self._mapping:
            return self._mapping[ciphertext]
        if ciphertext.startswith("enc:"):
            return ciphertext[len("enc:") :]
        return ciphertext


# ─── Injected clock ────────────────────────────────────────────────────


class FixedClock:
    """Deterministic ``now`` provider for the future-``start`` rule (R1.4).

    Callable so it can be passed wherever a ``Callable[[], datetime]`` "now"
    provider is expected. ``schedule_interview`` today reads
    ``datetime.now(UTC)`` directly (only inside ``reject``/``accept``/``archive``
    for timestamps); task 5.1 rewrites ``schedule_interview`` and should accept
    an injected ``now`` provider (e.g. a ``clock: Callable[[], datetime]``
    constructor argument defaulting to ``lambda: datetime.now(UTC)``) so this
    fixed clock controls "now". Until then, tests can use :meth:`future` /
    :meth:`past` to derive deterministic start datetimes relative to this clock.
    """

    def __init__(self, now: datetime | None = None) -> None:
        """Initialize the clock at a fixed instant (defaults to a constant)."""
        self._now = now or datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        """Return the fixed current time."""
        return self._now

    @property
    def now(self) -> datetime:
        """The fixed instant this clock reports."""
        return self._now

    def future(self, *, minutes: int = 60) -> datetime:
        """Return an instant strictly after ``now`` by ``minutes``."""
        return self._now + timedelta(minutes=minutes)

    def past(self, *, minutes: int = 60) -> datetime:
        """Return an instant strictly before ``now`` by ``minutes``."""
        return self._now - timedelta(minutes=minutes)


# ─── Hypothesis strategies ─────────────────────────────────────────────

# Snapshot of the IANA timezone database once at import time. Building the set
# is moderately expensive, so caching it keeps example generation cheap across
# the 100+ Hypothesis iterations each property test runs.
_AVAILABLE_TIMEZONES: tuple[str, ...] = tuple(sorted(available_timezones()))


def iana_timezones() -> st.SearchStrategy[str]:
    """Hypothesis strategy drawing IANA timezones from ``zoneinfo`` (R11).

    Returns:
        A strategy sampling valid IANA timezone names recognized by
        ``zoneinfo.available_timezones()`` (e.g. ``"Asia/Ho_Chi_Minh"``,
        ``"Europe/Paris"``, ``"UTC"``).
    """
    return st.sampled_from(_AVAILABLE_TIMEZONES)


# ─── Entity factories ──────────────────────────────────────────────────


def make_candidate(
    *,
    status: str = CandidateStatus.NEW,
    email: str = "candidate@example.com",
    candidate_id: UUID | None = None,
) -> Candidate:
    """Build a Candidate for interview-scheduling tests.

    Calendar event fields live on the ``Interview`` entity (not on ``Candidate``).
    Tests that need a scheduled interview should create an ``Interview`` via
    the fake session instead.

    Args:
        status: Lifecycle status (defaults to ``new``).
        email: Candidate email address (used as the first attendee).
        candidate_id: Explicit id (defaults to a fresh UUID).

    Returns:
        A populated :class:`Candidate` entity (not persisted).
    """
    return Candidate(
        id=candidate_id or uuid4(),
        name="Nguyen Van A",
        email=email,
        phone="0901234567",
        skills=["Python"],
        experience=[],
        education=[],
        summary="Test candidate",
        status=status,
        confidence_score=0.9,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def make_employee(
    *,
    email: str = "interviewer@example.com",
    employee_id: UUID | None = None,
    full_name: str = "Interviewer Person",
) -> Employee:
    """Build an interviewer Employee for the lookup seam.

    Args:
        email: The interviewer email (use ``""`` to model a missing email).
        employee_id: Explicit id (defaults to a fresh UUID).
        full_name: Display name.

    Returns:
        A populated :class:`Employee` entity (not persisted).
    """
    return Employee(
        id=employee_id or uuid4(),
        employee_code=f"NV-{uuid4().hex[:6]}",
        full_name=full_name,
        email=email,
        is_active=True,
    )


def make_oauth_grant(
    *,
    user_id: UUID,
    scopes: Sequence[str] = DEFAULT_GRANTED_SCOPES,
    access_token_enc: str = "enc:tok-access",
    refresh_token_enc: str = "enc:tok-refresh",
) -> OAuthGrant:
    """Build an OAuthGrant for the grant/refresh seams.

    Args:
        user_id: The owning HR user id.
        scopes: Granted scopes; include :data:`CALENDAR_SCOPE` for a valid grant
            or omit it to model a missing Calendar grant (R9).
        access_token_enc: Encrypted access token (decrypted by
            :class:`FakeTokenCipher`).
        refresh_token_enc: Encrypted refresh token.

    Returns:
        A populated :class:`OAuthGrant` entity (not persisted).
    """
    return OAuthGrant(
        user_id=user_id,
        provider="google",
        access_token_enc=access_token_enc,
        refresh_token_enc=refresh_token_enc,
        scopes=list(scopes),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        is_valid=True,
    )


def make_interview(
    *,
    candidate_id: UUID,
    status: str = "scheduled",
    round_name: str = "Technical",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    timezone: str = "Asia/Ho_Chi_Minh",
    calendar_event_id: str | None = "evt-default",
    calendar_id: str | None = "recruitment@company.vn",
    calendar_etag: str | None = '"etag-v1"',
    meeting_mode: str = "google_meet",
    interview_id: UUID | None = None,
) -> Interview:
    """Build an Interview for calendar-related tests.

    This is how a test gives a Candidate an already-scheduled calendar event:
    since the scheduling refactor the event reference, the scheduled start, and
    the applied timezone live here rather than on :class:`Candidate`. Pass the
    result to ``build_calendar_harness(interviews=[...])``.

    Args:
        candidate_id: The owning Candidate id.
        status: Interview status (scheduled, completed, cancelled).
        round_name: Interview round name.
        start_at: Interview start (defaults to a future UTC datetime).
        end_at: Interview end (defaults to 1 hour after start).
        timezone: IANA timezone.
        calendar_event_id: The Google Calendar event id.
        calendar_id: The Google Calendar the event lives on.
        calendar_etag: The etag for conditional writes.
        meeting_mode: Meeting mode (google_meet, in_person, custom_link).
        interview_id: Explicit id (defaults to a fresh UUID).

    Returns:
        A populated :class:`Interview` entity (not persisted).
    """
    start = start_at or datetime(2090, 6, 1, 9, 0, 0, tzinfo=UTC)
    end = end_at or start + timedelta(hours=1)
    return Interview(
        id=interview_id or uuid4(),
        candidate_id=candidate_id,
        status=status,
        round_name=round_name,
        start_at=start,
        end_at=end,
        timezone=timezone,
        calendar_event_id=calendar_event_id,
        calendar_id=calendar_id,
        calendar_etag=calendar_etag,
        meeting_mode=meeting_mode,
    )


# ─── Service builder ───────────────────────────────────────────────────


@contextmanager
def _patch_audit_sinks(sink: SpyAuditSink) -> Iterator[SpyAuditSink]:
    """Replace the module-level ``log_audit`` in both services with ``sink``."""
    with (
        patch.object(interview_scheduler_service_module, "log_audit", sink),
        patch.object(candidate_lifecycle_service_module, "log_audit", sink),
    ):
        yield sink


@dataclass
class CalendarServiceHarness:
    """A wired :class:`InterviewSchedulerService` plus the seams it was built from.

    Bundles the service under test with every seam so property tests can drive
    the service and then assert against the recorded calls, audit entries, and
    persisted Candidate state.

    Attributes:
        service: The :class:`InterviewSchedulerService` under test.
        calendar: The :class:`FakeCalendarPort` recording adapter calls.
        candidate_repo: The in-memory candidate repository.
        interview_repo: The in-memory interview repository (shares the session's
            interview store).
        session: The fake session coordinating commit/rollback + employee lookup.
        audit_sink: The :class:`SpyAuditSink` capturing audit entries.
        grant_repo: The fake OAuth grant repository.
        oauth_service: The fake grant checker / token refresher.
        crypto: The fake token cipher.
        clock: The injected :class:`FixedClock`.
        user_id: The acting HR user id.
    """

    service: InterviewSchedulerService
    lifecycle: CandidateLifecycleService
    calendar: FakeCalendarPort
    candidate_repo: FakeCandidateRepository
    interview_repo: FakeInterviewRepository
    session: FakeCalendarSession
    audit_sink: SpyAuditSink
    grant_repo: FakeOAuthGrantRepository
    oauth_service: FakeCalendarGrantChecker
    crypto: FakeTokenCipher
    clock: FixedClock
    user_id: UUID

    def audit_patch(self) -> AbstractContextManager[SpyAuditSink]:
        """Route every audit write in both services to :attr:`audit_sink`.

        A terminal transition writes its ``candidate_rejected`` /
        ``candidate_archived`` entry through ``candidate_lifecycle_service``'s
        ``log_audit`` while the interview cancellation writes
        ``interview_event_cancelled`` / ``interview_cancel_failed`` through
        ``interview_scheduler_service``'s, so both module-level names have to be
        replaced for one sink to see the whole story. The real helper would call
        ``session.add``/``flush``, which the fake session does not implement.
        """
        return _patch_audit_sinks(self.audit_sink)

    async def scheduled_interview(self, candidate_id: UUID) -> Interview | None:
        """Return the Candidate's live scheduled Interview, or ``None``.

        The calendar state a Candidate used to carry inline (event id, start,
        timezone) now lives here, so the properties that used to read
        ``candidate.calendar_event_id`` read this instead.
        """
        for interview in await self.interview_repo.find_by_candidate_id(candidate_id):
            if interview.status == "scheduled":
                return interview
        return None

    async def interviews_for(self, candidate_id: UUID) -> list[Interview]:
        """Return every live Interview belonging to a Candidate, any status."""
        return await self.interview_repo.find_by_candidate_id(candidate_id)

    def committed_interview_snapshot(self, interview_id: UUID) -> dict[str, Any] | None:
        """Return the last *committed* field snapshot for an Interview."""
        return self.session.committed_interview_snapshot(interview_id)


def build_calendar_harness(
    *,
    candidates: Sequence[Candidate],
    employees: Sequence[Employee] = (),
    interviews: Sequence[Interview] = (),
    calendar: FakeCalendarPort | None = None,
    audit_sink: SpyAuditSink | None = None,
    grant: OAuthGrant | None | _Default = _DEFAULT,
    granted_scopes: Sequence[str] = DEFAULT_GRANTED_SCOPES,
    refreshed_tokens: GoogleTokens | None = None,
    org_timezone: str = DEFAULT_TIMEZONE,
    clock: FixedClock | None = None,
    user_id: UUID | None = None,
    connection_repo: object | None | _Default = _DEFAULT,
) -> CalendarServiceHarness:
    """Builds the in-memory candidate repository/session (seeded with
    ``candidates`` and ``employees``), the :class:`FakeCalendarPort`, the
    identity-side grant/refresh/cipher seams, and an organization-timezone
    stub, then constructs the service with all of them. The returned
    :class:`CalendarServiceHarness` exposes both the service and every seam.

    The :class:`SpyAuditSink` is returned on the harness, but installing it in
    place of the module-level ``log_audit`` is left to the test (via
    ``monkeypatch.setattr(interview_scheduler_service, "log_audit", harness.audit_sink)``)
    so each property test controls the patch lifetime.

    Args:
        candidates: Candidates seeded into the repository (committed state).
        employees: Employees available to the interviewer lookup.
        interviews: Interviews seeded into the session (committed state) -- this
            is how a Candidate is given an already-scheduled calendar event, now
            that the event reference lives on ``Interview`` rather than
            ``Candidate``. Build them with :func:`make_interview`.
        calendar: A pre-configured :class:`FakeCalendarPort`; a default one is
            created when omitted.
        audit_sink: A pre-configured :class:`SpyAuditSink`; a default one is
            created when omitted.
        grant: The OAuth grant the grant repo returns. Defaults to a valid grant
            for ``user_id`` with ``granted_scopes``; pass ``None`` to model a
            missing grant.
        granted_scopes: Scopes for the default grant (ignored if ``grant`` is
            given explicitly).
        refreshed_tokens: Tokens returned by the 401-refresh path.
        org_timezone: The organization timezone returned by the settings stub.
        clock: The injected :class:`FixedClock` (a default is created when
            omitted).
        user_id: The acting HR user id (a fresh UUID when omitted).

    Returns:
        A :class:`CalendarServiceHarness` bundling the service and its seams.
    """
    acting_user_id = user_id or uuid4()
    clock = clock or FixedClock()
    calendar = calendar or FakeCalendarPort()
    audit_sink = audit_sink or SpyAuditSink()

    session = FakeCalendarSession(employees=employees, interviews=interviews)
    candidate_repo = FakeCandidateRepository(session, candidates)
    interview_repo = FakeInterviewRepository(session)

    if isinstance(grant, _Default):
        resolved_grant: OAuthGrant | None = make_oauth_grant(
            user_id=acting_user_id, scopes=granted_scopes
        )
    else:
        resolved_grant = grant

    grant_repo = FakeOAuthGrantRepository(resolved_grant)
    oauth_service = FakeCalendarGrantChecker(refreshed_tokens=refreshed_tokens)
    crypto = FakeTokenCipher()

    if isinstance(connection_repo, _Default):
        resolved_conn_repo: Any = FakeOrgConnectionRepository()
    else:
        resolved_conn_repo = connection_repo

    org_settings_repo = AsyncMock()
    org_settings_repo.get_timezone = AsyncMock(return_value=org_timezone)

    lifecycle = CandidateLifecycleService(
        candidate_repo=candidate_repo,  # type: ignore[arg-type]
        cv_document_repo=AsyncMock(),
        job_opening_repo=AsyncMock(),
        minio_client=AsyncMock(),
        session=session,  # type: ignore[arg-type]
        user_id=acting_user_id,
    )

    service = InterviewSchedulerService(
        candidate_repo=candidate_repo,  # type: ignore[arg-type]
        interview_repo=interview_repo,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
        user_id=acting_user_id,
        calendar_port=calendar,
        org_settings_repo=org_settings_repo,
        connection_repo=resolved_conn_repo,  # type: ignore[arg-type]
        crypto=crypto,
    )

    # The lifecycle delegates interview cancellation back to the scheduler, the
    # same way the container wires them, so a reject/archive driven through the
    # harness exercises the real R8 path rather than a stub.
    lifecycle._interview_canceller = service

    return CalendarServiceHarness(
        service=service,
        lifecycle=lifecycle,
        calendar=calendar,
        candidate_repo=candidate_repo,
        interview_repo=interview_repo,
        session=session,
        audit_sink=audit_sink,
        grant_repo=grant_repo,
        oauth_service=oauth_service,
        crypto=crypto,
        clock=clock,
        user_id=acting_user_id,
    )


# ``CalendarPort`` is a runtime-checkable protocol; assert the fake satisfies it
# at import time so a drift in the protocol surfaces immediately in collection.
assert isinstance(FakeCalendarPort(), CalendarPort)
