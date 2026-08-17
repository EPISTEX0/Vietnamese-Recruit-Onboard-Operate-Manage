"""Interview Scheduler Service for the Recruitment module.

Manages interview scheduling, rescheduling, cancellation, completion,
replacement interviews, and calendar conflict capture and resolution.

Extracted from CandidateService. Uses the CalendarPort protocol for
Google Calendar operations and InterviewRepository for persistence.

Requirements: ADR-0008, 6.5, 7.1, 7.3, 7.4, 7.5, 8.1-8.6, 9.1, 9.3, 9.5,
10.1, 10.4, 10.7-10.8, 11.1, 11.3, 11.5, 12.1, 12.3, 12.5, 13.1, 13.5
"""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.employee.domain.entities import Employee
from src.modules.recruitment.application.candidate_validators import validate_transition
from src.modules.recruitment.domain.entities import (
    CalendarConflict,
    Candidate,
    Interview,
    InterviewParticipant,
)
from src.modules.recruitment.domain.enums import CalendarConflictStatus, CandidateStatus
from src.modules.recruitment.domain.exceptions import (
    CalendarConflictNotFoundError,
    CalendarEventConflictError,
    CalendarEventCreateFailedError,
    CalendarEventUpdateFailedError,
    CalendarGrantMissingError,
    CalendarRelinkRequiredError,
    CandidateNotFoundError,
    InterviewerMissingEmailError,
    InterviewerNotFoundError,
    NoInterviewToRescheduleError,
)
from src.modules.recruitment.domain.value_objects import (
    CalendarEvent,
    CalendarEventSpec,
    MeetingMode,
)
from src.modules.recruitment.infrastructure.audit_repository import log_audit
from src.modules.recruitment.infrastructure.repositories import (
    CandidateRepository,
    InterviewRepository,
)

if TYPE_CHECKING:
    from src.modules.identity.infrastructure.connection_state_repository import (
        OrganizationGoogleConnectionRepository,
    )
    from src.modules.recruitment.infrastructure.org_settings_repository import (
        OrganizationSettingsRepository,
    )

logger = logging.getLogger(__name__)

# Participant email shape: exactly one ``@`` with non-empty local and domain
# parts. Deliberately permissive -- Google is the real authority on
# deliverability; this only rejects input that could never be an address.
_PARTICIPANT_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")

# Upper bound on interviewers and on external participants for one interview,
# mirroring the ``max_length=20`` the API schema puts on both lists.
_MAX_INTERVIEW_PARTICIPANTS = 20

# Return type for adapter calls executed through ``_with_org_token``.
_CalendarResultT = TypeVar("_CalendarResultT")


@runtime_checkable
class CalendarPort(Protocol):
    """Protocol for Google Calendar event operations.

    Abstracts the recruitment CalendarAdapter so the service can be
    exercised against an in-memory fake. Each method takes the acting
    HR user's OAuth access_token (with the calendar.events scope) and
    operates on the specified calendar.
    """

    async def create_event(self, access_token: str, spec: CalendarEventSpec) -> CalendarEvent:
        """Create a Calendar event from the given specification."""
        ...

    async def patch_event(
        self,
        access_token: str,
        event_id: str,
        spec: CalendarEventSpec,
        if_match: str | None = None,
    ) -> CalendarEvent:
        """Conditionally patch an existing Calendar event."""
        ...

    async def delete_event(
        self,
        access_token: str,
        event_id: str,
        calendar_id: str,
        if_match: str | None = None,
    ) -> None:
        """Conditionally delete (cancel) an existing Calendar event."""
        ...

    async def get_event(
        self,
        access_token: str,
        event_id: str,
        calendar_id: str,
    ) -> CalendarEvent:
        """Fetch a single Calendar event by ID to get the remote snapshot."""
        ...

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        *,
        sync_token: str | None = None,
        page_token: str | None = None,
        max_results: int = 250,
    ) -> Any:
        """List events (sync) from a Calendar, with optional sync token."""
        ...


@runtime_checkable
class TokenCipher(Protocol):
    """Protocol for decrypting stored OAuth tokens.

    Abstracts the identity module's ``CryptoUtils`` (AES-256-GCM) so the
    recruitment service can decrypt the stored access token before calling
    the Calendar adapter.
    """

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a stored ciphertext into plaintext."""
        ...

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext into ciphertext."""
        ...


class InterviewSchedulerService:
    """Manages interview scheduling and calendar operations.

    Provides methods for scheduling, rescheduling, cancelling, completing
    interviews, creating replacement interviews, listing interviews, and
    managing calendar conflicts.

    Args:
        candidate_repo: Repository for candidate persistence.
        interview_repo: Repository for interview persistence.
        calendar_port: Calendar adapter (protocol) for event operations.
        org_settings_repo: Organization settings repository (timezone).
        connection_repo: Organization Google Connection repository for
            selected calendar and organization-owned OAuth tokens.
        crypto: AES-256-GCM utilities for decrypting the access token.
        session: Async database session.
        user_id: Acting user UUID for audit attribution.
    """

    def __init__(
        self,
        candidate_repo: CandidateRepository,
        interview_repo: InterviewRepository,
        calendar_port: CalendarPort | None = None,
        org_settings_repo: OrganizationSettingsRepository | None = None,
        connection_repo: OrganizationGoogleConnectionRepository | None = None,
        crypto: TokenCipher | None = None,
        session: AsyncSession | None = None,
        user_id: UUID | None = None,
    ) -> None:
        self._candidate_repo = candidate_repo
        self._interview_repo = interview_repo
        self._calendar_port = calendar_port
        self._org_settings_repo = org_settings_repo
        self._connection_repo = connection_repo
        self._crypto = crypto
        self._session = session
        self._user_id = user_id

    # ─── Schedule interview ───────────────────────────────────────────

    async def schedule_interview(
        self,
        candidate_id: UUID,
        *,
        start: datetime,
        duration_minutes: int,
        interviewer_ids: list[UUID],
        notes: str | None = None,
    ) -> Candidate:
        """Schedule an interview by creating a Google Calendar event atomically.

        Implements the synchronous, atomic scheduling contract from ADR-0008.
        The Calendar event is created on the Organization's calendar **before**
        the database transaction commits; only on Calendar success does the
        Candidate transition to ``interview_scheduled`` and persist the event
        reference, the scheduled start, and the applied timezone. A Calendar
        failure rolls back all database changes and leaves the Candidate
        untouched.

        Args:
            candidate_id: UUID of the Candidate.
            start: Interview start datetime (tz-aware preferred).
            duration_minutes: Interview duration in minutes (15-180 inclusive).
            interviewer_ids: Interviewer Employee identifiers (1-10).
            notes: Optional interview notes (<= 1000 characters).

        Returns:
            The updated Candidate entity.

        Raises:
            ValueError: If a request field violates its bounds.
            CandidateNotFoundError: If the candidate doesn't exist.
            InvalidStatusTransitionError: If the transition is not allowed.
            CalendarGrantMissingError: If the Organization Google Connection is
                missing or invalid.
            InterviewerNotFoundError: If any interviewer id has no Employee.
            InterviewerMissingEmailError: If a matched interviewer has no email.
            CalendarEventCreateFailedError: If the Calendar event creation fails.
        """
        if self._calendar_port is None:
            raise RuntimeError("Calendar port is not configured")
        if self._user_id is None:
            raise RuntimeError("Acting HR user id is not configured")
        calendar_port = self._calendar_port
        user_id = self._user_id

        # Step 1: validate the request fields.
        self._validate_schedule_request(
            start=start,
            duration_minutes=duration_minutes,
            interviewer_ids=interviewer_ids,
            notes=notes,
        )

        # Step 2: load the candidate and validate the transition (R2.4).
        candidate = await self._get_candidate_or_raise(candidate_id)
        previous_status = candidate.status
        validate_transition(
            current_status=candidate.status,
            target_status=CandidateStatus.INTERVIEW_SCHEDULED,
            action="schedule_interview",
        )

        # Step 3: ensure org connection is active before any Calendar call (R9).
        await self._ensure_org_connection_active()
        calendar_id = await self._resolve_org_calendar_id()

        # Step 4: resolve interviewer Employees and their emails (R1.7, R10).
        resolved = await self._resolve_interviewers(interviewer_ids)
        interviewer_emails = [email for _, email in resolved]

        # Step 5: timezone, end, attendees, and the tz-aware event spec.
        timezone = await self._get_org_timezone()
        tz = ZoneInfo(timezone)
        start_resolved = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
        end_resolved = start_resolved + timedelta(minutes=duration_minutes)
        attendee_emails = self._build_attendees(candidate, interviewer_emails)
        spec = CalendarEventSpec(
            summary=f"Interview with {candidate.name}",
            description=notes,
            start=start_resolved,
            end=end_resolved,
            timezone=timezone,
            calendar_id=calendar_id,
            attendee_emails=tuple(attendee_emails),
            request_meet_link=True,
        )

        # Step 6: create the Calendar event BEFORE committing (R2.1).
        event = await self._create_calendar_event(user_id, candidate_id, calendar_port, spec)

        # Step 7: persist the event reference, start, timezone, and status, then
        # commit (R2.3, R4.1-R4.3).
        persisted_candidate = await self._persist_interview_schedule(
            candidate,
            event.event_id,
            start_resolved,
            timezone,
            duration_minutes,
            interviewer_ids,
            calendar_id=calendar_id,
            notes=notes,
        )
        if persisted_candidate is not None:
            candidate = persisted_candidate

        # Step 9: success audit (R12.1).
        await self._audit_interview_schedule(
            user_id,
            candidate,
            event.event_id,
            start_resolved,
            timezone,
            interviewer_ids,
            previous_status,
        )

        if event.meet_link is not None:
            logger.info("Interview scheduled for candidate %s with Meet link", candidate.id)

        return candidate

    # ─── Reschedule interview ─────────────────────────────────────────

    async def reschedule_interview(
        self,
        candidate_id: UUID,
        *,
        start: datetime,
        duration_minutes: int,
        interviewer_ids: list[UUID],
        notes: str | None = None,
        force: bool = False,
    ) -> Candidate:
        """Reschedule an interview by patching the existing Calendar event.

        Implements the reschedule contract from ADR-0008 (R7). The existing
        Google Calendar event is patched in place with the new time window.

        Args:
            candidate_id: UUID of the Candidate.
            start: New interview start datetime.
            duration_minutes: Interview duration in minutes (15-180 inclusive).
            interviewer_ids: Interviewer Employee identifiers (1-10).
            notes: Optional interview notes.
            force: When True, skip the existing interview check for
                rescheduling (used by forced reschedules from conflict resolution).

        Returns:
            The updated Candidate entity.

        Raises:
            ValueError: If a request field violates its bounds.
            CandidateNotFoundError: If the candidate doesn't exist.
            NoInterviewToRescheduleError: If no interview exists.
            CalendarEventUpdateFailedError: If the Calendar patch fails.
        """
        if self._calendar_port is None:
            raise RuntimeError("Calendar port is not configured")
        if self._user_id is None:
            raise RuntimeError("Acting HR user id is not configured")

        # Step 1: load the candidate and find the existing interview.
        candidate = await self._get_candidate_or_raise(candidate_id)
        interview = await self._get_scheduled_interview(candidate_id)
        if interview is None:
            raise NoInterviewToRescheduleError(
                f"Candidate {candidate_id} has no interview to reschedule"
            )
        event_id = interview.calendar_event_id

        # Step 2: ensure org connection is active.
        await self._ensure_org_connection_active()

        # Step 3: validate the request fields.
        self._validate_schedule_request(
            start=start,
            duration_minutes=duration_minutes,
            interviewer_ids=interviewer_ids,
            notes=notes,
        )

        # Step 4: resolve timezone, end, interviewers, attendees.
        timezone = await self._get_org_timezone()
        tz = ZoneInfo(timezone)
        start_resolved = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
        end_resolved = start_resolved + timedelta(minutes=duration_minutes)
        resolved = await self._resolve_interviewers(interviewer_ids)
        interviewer_emails = [email for _, email in resolved]
        attendee_emails = self._build_attendees(candidate, interviewer_emails)

        calendar_id = await self._resolve_org_calendar_id()
        spec = CalendarEventSpec(
            summary=f"Interview with {candidate.name}",
            # Rebuilt from the interview's own mode/link, not from ``notes``
            # alone: for a custom_link interview the join URL lives in here.
            description=self._build_event_description(
                notes,
                mode=interview.meeting_mode,
                meeting_link=interview.meeting_link,
            ),
            start=start_resolved,
            end=end_resolved,
            timezone=timezone,
            calendar_id=calendar_id,
            attendee_emails=tuple(attendee_emails),
            request_meet_link=False,  # Preserve existing Meet link (R11.1-R11.2)
        )

        # Step 5: patch the EXACT existing event (R7.1). The patch is conditional
        # on the etag we last stored, so an event somebody edited on Google in
        # the meantime surfaces as a 412 conflict instead of being overwritten.
        previous_start = interview.start_at
        result_event = await self._patch_calendar_event(
            user_id=self._user_id,
            candidate_id=candidate_id,
            event_id=event_id,
            spec=spec,
            if_match=interview.calendar_etag,
        )

        # Step 6: on success, update the Interview record.
        interview.start_at = start_resolved
        interview.end_at = end_resolved
        interview.timezone = timezone
        # Guarded like ``calendar_updated`` beside it: a response without an
        # etag means we no longer know the remote version, but blanking the
        # stored one would make every later patch unconditional and switch off
        # 412 conflict detection without a sound. Keeping the last known etag
        # keeps the next write conditional -- at worst it loses a race loudly,
        # which is what the conflict-capture path is for.
        if result_event.etag:
            interview.calendar_etag = result_event.etag
        if result_event.updated:
            interview.calendar_updated = result_event.updated
        self._session.add(interview)

        # Update candidate status if needed (should already be interview_scheduled).
        if candidate.status != CandidateStatus.INTERVIEW_SCHEDULED:
            candidate.status = CandidateStatus.INTERVIEW_SCHEDULED
        candidate = await self._candidate_repo.update(candidate)
        if self._session is not None:
            await self._session.commit()

        # Step 8: audit (R12.2).
        await log_audit(
            session=self._session,
            operation_type="interview_rescheduled",
            entity_type="candidate",
            entity_id=candidate.id,
            user_id=self._user_id,
            previous_value={
                "start": previous_start.isoformat() if previous_start else None,
                "event_id": event_id,
            },
            new_value={
                "start": start_resolved.isoformat(),
                "event_id": event_id,
                "duration_minutes": duration_minutes,
            },
            change_summary=(
                f"Interview rescheduled for candidate {candidate.id}: "
                f"was {previous_start.isoformat() if previous_start else 'N/A'}, "
                f"now {start_resolved.isoformat()}"
            ),
            success=True,
        )
        await self._commit_audit()

        return candidate

    # ─── Cancel interview ─────────────────────────────────────────────

    async def cancel_interview(
        self,
        interview_id: UUID,
        reason: str | None = None,
    ) -> Interview:
        """Cancel an Interview and delete the Calendar event.

        Transitions the Interview from 'scheduled' to 'cancelled'.
        Deletes the Google Calendar event with sendUpdates=all.
        Does NOT change the Candidate status.

        Args:
            interview_id: UUID of the Interview to cancel.
            reason: Optional cancellation reason.

        Returns:
            The updated Interview record.

        Raises:
            InterviewNotFoundError: If the Interview doesn't exist.
            InterviewStatusTransitionError: If the Interview is not scheduled.
            CalendarEventUpdateFailedError: If the Calendar deletion fails.
        """
        interview = await self._get_interview_or_raise(interview_id)
        self._assert_interview_is_scheduled(interview, "cancel")

        if self._calendar_port is not None and interview.calendar_event_id:
            try:
                await self._delete_calendar_event(
                    interview.calendar_event_id,
                    interview.calendar_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to cancel Calendar event for interview %s: %s",
                    interview_id,
                    exc,
                )
                # Capture conflict if 412
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 412:
                    await self._capture_calendar_conflict(
                        user_id=self._user_id or UUID(int=0),
                        candidate_id=interview.candidate_id,
                        event_id=interview.calendar_event_id,
                        operation="cancel_interview",
                    )
                raise CalendarEventUpdateFailedError(
                    details={
                        "interview_id": str(interview_id),
                        "calendar_event_id": interview.calendar_event_id,
                        "error": str(exc),
                    }
                ) from exc

        previous_status = interview.status
        interview.status = "cancelled"
        interview = await self._interview_repo.update(interview)
        if self._session is not None:
            await self._session.commit()

        await log_audit(
            session=self._session,
            operation_type="interview_cancelled",
            entity_type="interview",
            entity_id=interview.id,
            user_id=self._user_id,
            previous_value={"status": previous_status},
            new_value={"status": "cancelled", "reason": reason},
            change_summary=(
                f"Interview cancelled{' (reason: ' + reason[:200] + ')' if reason else ''}"
            ),
            success=True,
        )
        await self._commit_audit()

        return interview

    # ─── Cancel on terminal candidate transition (R8) ──────────────────

    async def cancel_interview_for_candidate(self, candidate_id: UUID, *, trigger: str) -> None:
        """Best-effort cancellation of a Candidate's interview Calendar event.

        Invoked by :class:`CandidateLifecycleService` **after** a terminal
        transition (reject/archive) has already committed, so this method never
        raises: a Calendar failure must not undo a committed transition
        (R8.4, R8.5). Behaviour:

        * With no Calendar wiring (``calendar_port`` or the acting ``user_id``
          absent), do nothing -- callers constructed without Calendar
          dependencies keep their transition-only behaviour.
        * With no scheduled Interview, or none that holds a
          ``calendar_event_id``, make no Calendar call at all (R8.3).
        * Otherwise, for EVERY scheduled Interview the Candidate holds, delete
          the EXACT stored event, mark that Interview ``cancelled``, and write an
          ``interview_event_cancelled`` audit entry naming the acting HR user,
          the Candidate, the cancelled ``calendar_event_id``, and the trigger
          (R12.3). On any failure, swallow the error and write an
          ``interview_cancel_failed`` entry (``success=False``) carrying the same
          identifiers (R8.6). A Candidate can hold more than one scheduled round;
          cancelling only the most recent would leave the earlier one live.

        Each delete targets the calendar the event was actually booked on
        (``interview.calendar_id``), not whichever calendar the Organization has
        selected today -- re-selecting a calendar must not strand old events,
        and the adapter treats the resulting 404 as an idempotent success, so
        the mistake would be silent.

        The delete is deliberately unconditional (no ``If-Match``): the point is
        that the meeting stops existing. A conditional delete would answer 412
        for an event somebody merely edited, leaving a rejected candidate
        holding a live invitation.

        Args:
            candidate_id: The Candidate whose interview events should be cancelled.
            trigger: The terminal action that triggered the cancellation
                (``"reject"`` or ``"archive"``).
        """
        if self._calendar_port is None or self._user_id is None:
            return

        interviews = await self._interview_repo.find_by_candidate_id(candidate_id)
        for interview in [iv for iv in interviews if iv.status == "scheduled"]:
            event_id = interview.calendar_event_id
            if event_id is None:
                # Nothing booked on Calendar for this round (R8.3).
                continue
            await self._cancel_one_interview_event(
                interview, event_id=event_id, candidate_id=candidate_id, trigger=trigger
            )

    async def _cancel_one_interview_event(
        self,
        interview: Interview,
        *,
        event_id: str,
        candidate_id: UUID,
        trigger: str,
    ) -> None:
        """Delete one booked event and audit the outcome; never raises."""
        try:
            calendar_id = interview.calendar_id or await self._resolve_org_calendar_id()
            await self._delete_calendar_event(event_id, calendar_id)
        except Exception as exc:  # noqa: BLE001 - cancellation is best-effort
            logger.warning(
                "Calendar event cancellation failed for candidate %s (event %s) on %s: %s",
                candidate_id,
                event_id,
                trigger,
                exc,
            )
            await self._mark_interview_cancelled(interview)
            await log_audit(
                session=self._session,
                operation_type="interview_cancel_failed",
                entity_type="candidate",
                entity_id=candidate_id,
                user_id=self._user_id,
                new_value={
                    "attempted_action": f"{trigger}_cancel_interview",
                    "candidate_id": str(candidate_id),
                    "calendar_event_id": event_id,
                    "trigger": trigger,
                    "success": False,
                    "error": str(exc),
                },
                change_summary=(f"Interview cancellation failed on {trigger}; event {event_id}"),
                success=False,
            )
            await self._commit_audit()
            return

        await self._mark_interview_cancelled(interview)
        await log_audit(
            session=self._session,
            operation_type="interview_event_cancelled",
            entity_type="candidate",
            entity_id=candidate_id,
            user_id=self._user_id,
            new_value={
                "candidate_id": str(candidate_id),
                "calendar_event_id": event_id,
                "trigger": trigger,
            },
            change_summary=(f"Interview event {event_id} cancelled on {trigger}"),
            success=True,
        )
        await self._commit_audit()

    async def _commit_audit(self) -> None:
        """Commit the audit row just written, without letting it fail the action.

        ``log_audit`` promises swallow-and-continue (R17.5): an audit failure is
        logged and ignored. What its try/except cannot undo is the state a failed
        ``session.flush()`` leaves behind -- SQLAlchemy marks the transaction as
        needing a rollback, so the very next ``commit()`` raises
        ``PendingRollbackError``. Committing bare here would therefore convert a
        swallowed audit failure into a 500 for an action that already committed
        its own write further up, breaking the same promise from the outside.

        Every caller commits its real work *before* auditing, so discarding just
        the audit is the correct recovery.
        """
        if self._session is None:
            return
        try:
            await self._session.commit()
        except Exception:  # noqa: BLE001 - an audit write must never fail the action
            logger.warning(
                "Audit commit failed; the action itself already committed and stands",
                exc_info=True,
            )
            with suppress(Exception):
                await self._session.rollback()

    async def _mark_interview_cancelled(self, interview: Interview) -> None:
        """Record the Interview as cancelled on our side and commit.

        Runs on both the success and failure paths of
        :meth:`cancel_interview_for_candidate`: once the Candidate is rejected or
        archived the interview is over in our system regardless of whether Google
        accepted the delete, and leaving it ``scheduled`` would keep it in the
        upcoming-interviews view.
        """
        interview.status = "cancelled"
        if self._session is not None:
            self._session.add(interview)
            await self._session.commit()

    # ─── Complete interview ───────────────────────────────────────────

    async def complete_interview(self, interview_id: UUID) -> Interview:
        """Mark an Interview as completed.

        Transitions the Interview from 'scheduled' to 'completed'.
        Does NOT change the Candidate status.

        Args:
            interview_id: UUID of the Interview to complete.

        Returns:
            The updated Interview record.

        Raises:
            InterviewNotFoundError: If the Interview doesn't exist.
            InterviewStatusTransitionError: If the Interview is not scheduled.
        """
        interview = await self._get_interview_or_raise(interview_id)
        self._assert_interview_is_scheduled(interview, "complete")

        previous_status = interview.status
        interview.status = "completed"
        interview = await self._interview_repo.update(interview)
        if self._session is not None:
            await self._session.commit()

        await log_audit(
            session=self._session,
            operation_type="interview_completed",
            entity_type="interview",
            entity_id=interview.id,
            user_id=self._user_id,
            previous_value={"status": previous_status},
            new_value={"status": "completed"},
            change_summary="Interview completed",
            success=True,
        )
        await self._commit_audit()

        return interview

    # ─── Create interview (GH #154) ───────────────────────────────────

    async def create_interview(
        self,
        candidate_id: UUID,
        *,
        round_name: str,
        start: datetime,
        end: datetime,
        timezone: str,
        mode: str = MeetingMode.GOOGLE_MEET,
        meeting_link: str | None = None,
        interviewer_ids: list[UUID] | None = None,
        external_participant_emails: list[str] | None = None,
        notes: str | None = None,
    ) -> Interview:
        """Create a new interview with a Calendar event.

        This is the GH #154 interview creation command. Creates an Interview
        record with the specified round name, timezone, meeting mode, participants,
        and a Calendar event. The Candidate status is NOT changed.

        Args:
            candidate_id: UUID of the candidate.
            round_name: Name/round of the interview (e.g. "Technical Round 1").
            start: Interview start datetime (must be timezone-aware).
            end: Interview end datetime (must be timezone-aware, after ``start``).
            timezone: IANA timezone string.
            mode: Meeting mode -- one of :class:`MeetingMode`
                (``google_meet``, ``in_person``, ``custom_link``).
            meeting_link: External meeting link; required when ``mode`` is
                ``custom_link``.
            interviewer_ids: UUIDs of interviewer Employees (defaults to none).
            external_participant_emails: Optional external participant emails.
            notes: Optional notes.

        Returns:
            The created Interview record.

        Raises:
            ValueError: If a request field violates its bounds.
            CandidateNotFoundError: If the candidate doesn't exist.
            InterviewerNotFoundError: If any interviewer id has no Employee.
            InterviewerMissingEmailError: If a matched interviewer has no email.
            CalendarGrantMissingError: If the Organization Google Connection is
                missing or invalid.
            CalendarEventCreateFailedError: If Calendar event creation fails.
        """
        if self._calendar_port is None:
            raise RuntimeError("Calendar port is not configured")
        if self._user_id is None:
            raise RuntimeError("Acting HR user id is not configured")
        calendar_port = self._calendar_port

        # Step 1: validate the request fields. These run before the Candidate is
        # loaded and long before any Calendar call, so a bad request never
        # reaches Google. The API layer validates the same bounds on
        # ``CreateInterviewRequest``, but this method is also called directly
        # (assistant tooling, tests), so the service enforces them itself.
        round_name = round_name.strip()
        if not round_name:
            raise ValueError("round_name must not be empty")
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("end must be timezone-aware")
        if end <= start:
            raise ValueError("end must be strictly after start")
        timezone = timezone.strip()
        if not timezone:
            raise ValueError("timezone must not be empty")
        try:
            tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            # ZoneInfoNotFoundError is a KeyError, which the API layer's
            # ValueError -> 422 mapping does not catch; left unhandled a typo
            # like "GMT+7" would surface as a 500.
            raise ValueError(f"timezone is not a known IANA timezone: {timezone!r}") from exc
        # The mode vocabulary is owned by MeetingMode; reading it from the enum
        # keeps this check from drifting away from the API contract the way a
        # hard-coded set literal previously did.
        valid_modes = {m.value for m in MeetingMode}
        if mode not in valid_modes:
            raise ValueError(f"mode must be one of {sorted(valid_modes)}, got {mode!r}")
        if mode == MeetingMode.CUSTOM_LINK and not meeting_link:
            raise ValueError("meeting_link is required when mode is custom_link")
        if notes is not None and len(notes) > 1000:
            raise ValueError("notes must be at most 1000 characters")

        resolved_interviewer_ids = list(interviewer_ids or [])
        resolved_external_emails = [e.strip() for e in (external_participant_emails or [])]
        if len(resolved_interviewer_ids) > _MAX_INTERVIEW_PARTICIPANTS:
            raise ValueError(f"interviewer_ids must not exceed {_MAX_INTERVIEW_PARTICIPANTS}")
        if len(resolved_external_emails) > _MAX_INTERVIEW_PARTICIPANTS:
            raise ValueError(
                f"external_participant_emails must not exceed {_MAX_INTERVIEW_PARTICIPANTS}"
            )
        for ext_email in resolved_external_emails:
            if not _PARTICIPANT_EMAIL_PATTERN.match(ext_email):
                raise ValueError(f"Invalid external participant email: {ext_email!r}")

        candidate = await self._get_candidate_or_raise(candidate_id)

        # Resolve interviewers
        resolved = await self._resolve_interviewers(resolved_interviewer_ids)
        interviewer_emails = [email for _, email in resolved]

        # Ensure org connection
        await self._ensure_org_connection_active()
        calendar_id = await self._resolve_org_calendar_id()

        # Build attendee list
        attendee_emails: list[str] = [candidate.email] if candidate.email else []
        attendee_emails.extend(interviewer_emails)
        attendee_emails.extend(resolved_external_emails)

        start_resolved = start.astimezone(tz)
        end_resolved = end.astimezone(tz)

        # A Meet link is requested exactly for the google_meet mode. The other
        # two modes carry their own venue: in_person has none, custom_link
        # supplies its own URL, and minting a Google conference for either would
        # hand attendees a second, wrong way to join.
        request_meet = mode == MeetingMode.GOOGLE_MEET
        description = self._build_event_description(notes, mode=mode, meeting_link=meeting_link)
        spec = CalendarEventSpec(
            summary=f"Interview: {round_name} - {candidate.name}",
            description=description,
            start=start_resolved,
            end=end_resolved,
            timezone=timezone,
            calendar_id=calendar_id,
            attendee_emails=tuple(attendee_emails),
            request_meet_link=request_meet,
        )

        event = await self._create_calendar_event(self._user_id, candidate_id, calendar_port, spec)

        # Create Interview record
        interview = Interview(
            candidate_id=candidate.id,
            status="scheduled",
            round_name=round_name,
            start_at=start_resolved,
            end_at=end_resolved,
            timezone=timezone,
            calendar_event_id=event.event_id,
            calendar_id=calendar_id,
            calendar_etag=event.etag,
            calendar_updated=event.updated,
            meeting_mode=mode,
            meeting_link=event.meet_link or meeting_link,
            needs_relink=False,
        )
        interview = await self._interview_repo.create(interview)

        # Add participants. Every one starts at ``needsAction``: that is the
        # RSVP state Google reports for a freshly invited attendee, and the sync
        # job overwrites it from the event's attendee list. Leaving it NULL makes
        # a brand-new invitation indistinguishable from one Google has no
        # response for.
        cand_part = InterviewParticipant(
            interview_id=interview.id,
            type="candidate",
            email=candidate.email,
            name=candidate.name,
            response_status="needsAction",
        )
        await self._interview_repo.add_participant(cand_part)

        # Interviewer participants. ``_resolve_interviewers`` already matched
        # every id to an Employee with a usable (stripped) email, so reuse its
        # result rather than re-querying and re-reading the unstripped column.
        for employee, employee_email in resolved:
            emp_part = InterviewParticipant(
                interview_id=interview.id,
                type="employee",
                email=employee_email,
                name=employee.full_name,
                employee_id=employee.id,
                response_status="needsAction",
            )
            await self._interview_repo.add_participant(emp_part)

        # External participants
        for ext_email in resolved_external_emails:
            ext_part = InterviewParticipant(
                interview_id=interview.id,
                type="external",
                email=ext_email,
                name=ext_email.split("@")[0],
                response_status="needsAction",
            )
            await self._interview_repo.add_participant(ext_part)

        if self._session is not None:
            await self._session.commit()

        await log_audit(
            session=self._session,
            operation_type="interview_created",
            entity_type="interview",
            entity_id=interview.id,
            user_id=self._user_id,
            new_value={
                "candidate_id": str(candidate.id),
                "round_name": round_name,
                "start": start_resolved.isoformat(),
                "end": end_resolved.isoformat(),
                "calendar_event_id": event.event_id,
                "mode": mode,
            },
            change_summary=(f"Interview created: {round_name} for candidate {candidate.name}"),
            success=True,
        )
        await self._commit_audit()

        return interview

    # ─── Replacement interview ────────────────────────────────────────

    async def create_replacement_interview(
        self,
        cancelled_interview_id: UUID,
        *,
        round_name: str,
        start: datetime,
        end: datetime,
        timezone: str,
        mode: str = MeetingMode.GOOGLE_MEET,
        meeting_link: str | None = None,
        interviewer_ids: list[UUID] | None = None,
        external_participant_emails: list[str] | None = None,
        notes: str | None = None,
    ) -> Interview:
        """Create a replacement interview after cancellation.

        Creates a new Interview record with a new Calendar event,
        keeping the old (cancelled) Interview in history.

        Args:
            cancelled_interview_id: UUID of the cancelled Interview.
            round_name: Name/round of the interview.
            start: Interview start datetime.
            end: Interview end datetime.
            timezone: IANA timezone string.
            mode: Meeting mode.
            meeting_link: Optional external meeting link.
            interviewer_ids: UUIDs of interviewer Employees.
            external_participant_emails: Optional external participant emails.
            notes: Optional notes.

        Returns:
            The new Interview record.

        Raises:
            InterviewNotFoundError: If the cancelled Interview doesn't exist.
            InterviewStatusTransitionError: If the source is not cancelled.
        """
        cancelled = await self._get_interview_or_raise(cancelled_interview_id)
        if cancelled.status != "cancelled":
            from src.modules.recruitment.domain.exceptions import InterviewStatusTransitionError

            raise InterviewStatusTransitionError(
                f"Cannot create replacement for interview {cancelled_interview_id} "
                f"with status '{cancelled.status}'; expected 'cancelled'"
            )

        new_interview = await self.create_interview(
            cancelled.candidate_id,
            round_name=round_name,
            start=start,
            end=end,
            timezone=timezone,
            mode=mode,
            meeting_link=meeting_link,
            interviewer_ids=interviewer_ids,
            external_participant_emails=external_participant_emails,
            notes=notes,
        )

        # Audit the replacement
        await log_audit(
            session=self._session,
            operation_type="interview_replacement_created",
            entity_type="interview",
            entity_id=new_interview.id,
            user_id=self._user_id,
            new_value={
                "interview_id": str(new_interview.id),
                "calendar_event_id": new_interview.calendar_event_id,
                "replaces": str(cancelled_interview_id),
            },
            change_summary=(
                f"Replacement interview created for cancelled interview {cancelled_interview_id}"
            ),
            success=True,
        )
        await self._commit_audit()

        return new_interview

    # ─── List interviews ──────────────────────────────────────────────

    async def list_interviews_for_candidate(
        self,
        candidate_id: UUID,
    ) -> list[dict[str, object]]:
        """List all interviews for a candidate.

        Args:
            candidate_id: UUID of the candidate.

        Returns:
            List of interview dicts with participants.
        """
        interviews = await self._interview_repo.find_by_candidate_id(candidate_id)
        result: list[dict[str, object]] = []
        for iv in interviews:
            participants = await self._interview_repo.get_participants(iv.id)
            result.append(
                {
                    "id": iv.id,
                    "candidate_id": iv.candidate_id,
                    "status": iv.status,
                    "round_name": iv.round_name,
                    "start_at": iv.start_at,
                    "end_at": iv.end_at,
                    "timezone": iv.timezone,
                    "calendar_event_id": iv.calendar_event_id,
                    "needs_relink": iv.needs_relink,
                    "participants": [
                        {
                            "id": p.id,
                            "interview_id": p.interview_id,
                            "type": p.type,
                            "email": p.email,
                            "name": p.name,
                            "employee_id": p.employee_id,
                        }
                        for p in participants
                    ],
                }
            )
        return result

    # ─── Get participants ─────────────────────────────────────────────

    async def get_participants(self, interview_id: UUID) -> list[InterviewParticipant]:
        """Get participants for an interview.

        Replaces the ``_session.execute()`` leak from router code.

        Args:
            interview_id: UUID of the interview.

        Returns:
            List of InterviewParticipant entities.
        """
        return await self._interview_repo.get_participants(interview_id)

    # ─── Calendar conflicts ───────────────────────────────────────────

    async def list_calendar_conflicts(
        self,
        status: str | None = None,
        candidate_id: UUID | None = None,
    ) -> list[CalendarConflict]:
        """List calendar conflicts, optionally filtered by status or candidate.

        Args:
            status: Optional status filter ("unresolved", "resolved_keep_google",
                "resolved_overwrite_vroom"). Defaults to "unresolved".
            candidate_id: Optional candidate UUID to filter by.

        Returns:
            List of CalendarConflict entities matching the filters.
        """
        stmt = select(CalendarConflict).order_by(CalendarConflict.created_at.desc())

        if status is not None:
            stmt = stmt.where(CalendarConflict.status == status)
        else:
            stmt = stmt.where(CalendarConflict.status == CalendarConflictStatus.UNRESOLVED)

        if candidate_id is not None:
            stmt = stmt.where(CalendarConflict.candidate_id == candidate_id)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def resolve_calendar_conflict(
        self,
        conflict_id: UUID,
        choice: str,
        acting_user_id: UUID,
    ) -> CalendarConflict:
        """Resolve a calendar conflict by keeping Google or overwriting Vroom.

        ``keep_google``: Update the local Interview record to match the remote
        Google Calendar snapshot and update the stored calendar_etag.

        ``overwrite_vroom``: Push Vroom's current state to Google Calendar
        using the remote event's ETag (the local etag is stale), then update
        the stored calendar_etag to the new value returned by Google.

        Args:
            conflict_id: UUID of the CalendarConflict to resolve.
            choice: "keep_google" or "overwrite_vroom".
            acting_user_id: UUID of the HR user resolving the conflict.

        Returns:
            The updated CalendarConflict entity.

        Raises:
            CalendarConflictNotFoundError: If the conflict doesn't exist.
            ValueError: If the choice is invalid or the conflict is already resolved.
            CalendarEventConflictError: If overwrite_vroom also gets a 412.
        """
        if choice not in ("keep_google", "overwrite_vroom"):
            raise ValueError(
                f"Invalid resolution choice: {choice!r}; expected "
                "'keep_google' or 'overwrite_vroom'"
            )

        stmt = select(CalendarConflict).where(CalendarConflict.id == conflict_id)
        result = await self._session.execute(stmt)
        conflict = result.scalars().first()

        if conflict is None:
            raise CalendarConflictNotFoundError(f"Calendar conflict not found: {conflict_id}")

        if conflict.status != CalendarConflictStatus.UNRESOLVED:
            raise ValueError(
                f"Conflict {conflict_id} is already resolved (status: {conflict.status})"
            )

        if self._calendar_port is None:
            raise RuntimeError("Calendar port is not configured")
        calendar_port = self._calendar_port

        interview = await self._get_interview_by_event_id(
            conflict.candidate_id, conflict.calendar_event_id
        )
        remote_etag = conflict.remote_snapshot.get("etag")

        applied_fields: list[str] = []

        if choice == "keep_google":
            # Apply Google's version to the local Interview.
            if interview is not None:
                if remote_event_etag := conflict.remote_snapshot.get("etag"):
                    interview.calendar_etag = remote_event_etag
                    applied_fields.append("calendar_etag")
                if remote_updated := conflict.remote_snapshot.get("updated"):
                    try:
                        interview.calendar_updated = datetime.fromisoformat(remote_updated)
                        applied_fields.append("calendar_updated")
                    except (ValueError, TypeError):
                        pass

                if remote_start := conflict.remote_snapshot.get("start_at"):
                    try:
                        interview.start_at = datetime.fromisoformat(remote_start)
                        applied_fields.append("start_at")
                    except (ValueError, TypeError):
                        pass
                if remote_end := conflict.remote_snapshot.get("end_at"):
                    try:
                        interview.end_at = datetime.fromisoformat(remote_end)
                        applied_fields.append("end_at")
                    except (ValueError, TypeError):
                        pass
                if remote_tz := conflict.remote_snapshot.get("timezone"):
                    interview.timezone = remote_tz
                    applied_fields.append("timezone")

                if remote_location := conflict.remote_snapshot.get("location"):
                    interview.remote_location = remote_location
                    applied_fields.append("remote_location")
                if remote_meet := conflict.remote_snapshot.get("meet_link"):
                    interview.meeting_link = remote_meet
                    applied_fields.append("meeting_link")

                # Only cancel the Interview when Google explicitly reports cancelled.
                if conflict.remote_snapshot.get("status") == "cancelled":
                    if interview.status != "cancelled":
                        interview.status = "cancelled"
                        applied_fields.append("status")

                self._session.add(interview)

            conflict.status = CalendarConflictStatus.RESOLVED_KEEP_GOOGLE
            conflict.resolved_by = acting_user_id
            conflict.resolved_at = datetime.now(UTC)
            self._session.add(conflict)
            if self._session is not None:
                await self._session.commit()

        elif choice == "overwrite_vroom":
            if interview is not None:
                timezone_val = interview.timezone or "Asia/Ho_Chi_Minh"
                calendar_id = await self._resolve_org_calendar_id()
                spec = CalendarEventSpec(
                    summary=f"Interview with {interview.candidate_id}",
                    description=None,
                    start=interview.start_at,
                    end=interview.end_at,
                    timezone=timezone_val,
                    calendar_id=calendar_id,
                    attendee_emails=(),
                    request_meet_link=False,
                )
                try:
                    result_event = await self._with_org_token(
                        lambda token: calendar_port.patch_event(
                            token,
                            conflict.calendar_event_id,
                            spec,
                            if_match=remote_etag,
                        ),
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 412:
                        # Another conflict occurred during resolution.
                        await self._capture_calendar_conflict(
                            user_id=acting_user_id,
                            candidate_id=conflict.candidate_id,
                            event_id=conflict.calendar_event_id,
                            operation="resolve_overwrite_vroom",
                        )
                        raise CalendarEventConflictError(
                            details={
                                "calendar_event_id": conflict.calendar_event_id,
                                "remote_status": 412,
                                "message": (
                                    "Another conflict occurred while overwriting; "
                                    "a new conflict record has been created"
                                ),
                            }
                        ) from exc
                    raise
                except Exception:
                    if self._session is not None:
                        await self._session.rollback()
                    raise

                interview.calendar_etag = result_event.etag
                if result_event.updated:
                    interview.calendar_updated = result_event.updated
                self._session.add(interview)

            conflict.status = CalendarConflictStatus.RESOLVED_OVERWRITE_VROOM
            conflict.resolved_by = acting_user_id
            conflict.resolved_at = datetime.now(UTC)
            self._session.add(conflict)
            if self._session is not None:
                await self._session.commit()

        # Audit the resolution.
        resolution_summary: dict[str, Any] = {
            "conflict_id": str(conflict.id),
            "choice": choice,
            "status": conflict.status,
            "calendar_event_id": conflict.calendar_event_id,
            "candidate_id": str(conflict.candidate_id),
            "interview_id": str(conflict.interview_id),
        }
        if choice == "keep_google" and applied_fields:
            resolution_summary["applied_fields"] = applied_fields

        change_text = (
            f"Calendar conflict {conflict.id} resolved by {choice}:"
            f" event {conflict.calendar_event_id}"
        )
        if choice == "keep_google" and applied_fields:
            change_text += f"; applied: {', '.join(applied_fields)}"

        await log_audit(
            session=self._session,
            operation_type="calendar_conflict_resolved",
            entity_type="calendar_conflict",
            entity_id=conflict.id,
            user_id=acting_user_id,
            previous_value={"status": CalendarConflictStatus.UNRESOLVED},
            new_value=resolution_summary,
            change_summary=change_text,
            success=True,
        )
        await self._commit_audit()

        return conflict

    # ─── Private: Calendar event operations ───────────────────────────

    async def _create_calendar_event(
        self,
        user_id: UUID,
        candidate_id: UUID,
        calendar_port: Any,
        spec: CalendarEventSpec,
    ) -> CalendarEvent:
        """Create a Calendar event, rolling back on failure.

        Args:
            user_id: Acting user ID.
            candidate_id: Candidate ID for audit.
            calendar_port: Calendar port instance.
            spec: Calendar event specification.

        Returns:
            The created CalendarEvent.

        Raises:
            CalendarEventCreateFailedError: If creation fails.
        """
        try:
            return await self._with_org_token(
                lambda token: calendar_port.create_event(token, spec),
            )

        except Exception as exc:
            if self._session is not None:
                await self._session.rollback()
            await log_audit(
                session=self._session,
                operation_type="interview_schedule_failed",
                entity_type="candidate",
                entity_id=candidate_id,
                user_id=user_id,
                new_value={
                    "attempted_action": "schedule_interview",
                    "candidate_id": str(candidate_id),
                    "error": str(exc),
                },
                change_summary="Interview schedule failed: Calendar event creation error",
                success=False,
            )
            await self._commit_audit()
            raise CalendarEventCreateFailedError() from exc

    async def _patch_calendar_event(
        self,
        user_id: UUID,
        candidate_id: UUID,
        event_id: str,
        spec: CalendarEventSpec,
        if_match: str | None = None,
    ) -> CalendarEvent:
        """Patch an existing Calendar event, rolling back on failure.

        Args:
            user_id: Acting user ID.
            candidate_id: Candidate ID for audit.
            event_id: Google Calendar event ID to patch.
            spec: New event specification.
            if_match: The etag the caller last saw for this event. Sending it
                makes the write conditional, so Google answers 412 instead of
                silently clobbering an event somebody else edited meanwhile.

        Returns:
            The patched CalendarEvent.

        Raises:
            CalendarEventConflictError: If the conditional write lost a race (412).
            CalendarEventUpdateFailedError: If the patch fails.
            CalendarRelinkRequiredError: If the event was deleted externally (410).
        """
        if self._calendar_port is None:
            raise RuntimeError("Calendar port is not configured")
        calendar_port = self._calendar_port

        try:
            return await self._with_org_token(
                lambda token: calendar_port.patch_event(token, event_id, spec, if_match),
            )

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 410:
                raise CalendarRelinkRequiredError(
                    details={
                        "calendar_event_id": event_id,
                        "message": "Calendar event was deleted externally; "
                        "a new event must be created",
                    }
                ) from exc
            if status == 412:
                # The conditional write lost a race: capture the conflict for HR
                # to resolve, then report it as a conflict (412 /
                # CALENDAR_CONFLICT) rather than as an upstream failure -- the
                # conflict router keys off this type to offer a resolution, and
                # "Google is broken" would be the wrong story to tell the user.
                await self._capture_calendar_conflict(
                    user_id=user_id,
                    candidate_id=candidate_id,
                    event_id=event_id,
                    operation="patch_event",
                )
                if self._session is not None:
                    await self._session.rollback()
                raise CalendarEventConflictError(
                    details={
                        "calendar_event_id": event_id,
                        "remote_status": 412,
                        "conflict_captured": True,
                        "message": (
                            "The Google Calendar event changed; "
                            "a conflict record has been created for resolution"
                        ),
                    }
                ) from exc
            raise

        except Exception as exc:
            if self._session is not None:
                await self._session.rollback()
            await log_audit(
                session=self._session,
                operation_type="interview_reschedule_failed",
                entity_type="candidate",
                entity_id=candidate_id,
                user_id=user_id,
                new_value={
                    "attempted_action": "reschedule_interview",
                    "candidate_id": str(candidate_id),
                    "calendar_event_id": event_id,
                    "error": str(exc),
                },
                change_summary="Interview reschedule failed: Calendar event patch error",
                success=False,
            )
            await self._commit_audit()
            raise CalendarEventUpdateFailedError(
                details={
                    "calendar_event_id": event_id,
                    "error": str(exc),
                }
            ) from exc

    async def _delete_calendar_event(
        self,
        event_id: str,
        calendar_id: str,
    ) -> None:
        """Delete a Calendar event (send cancellation).

        Args:
            event_id: Google Calendar event ID.
            calendar_id: Google Calendar ID.
        """
        await self._with_org_token(
            lambda token: self._calendar_port.delete_event(token, event_id, calendar_id),
        )

    async def _get_calendar_event(
        self,
        token: str,
        event_id: str,
        calendar_id: str,
    ) -> CalendarEvent:
        """Fetch a single Calendar event by ID."""
        return await self._calendar_port.get_event(token, event_id, calendar_id)

    # ─── Private: Token and connection management ─────────────────────

    async def _ensure_org_connection_active(self) -> None:
        """Ensure the Organization Google Connection is active.

        Raises:
            CalendarGrantMissingError: If no connection or not connected.
        """
        if self._connection_repo is None:
            raise CalendarGrantMissingError(
                message="Organization Google Connection repository is not configured"
            )
        connection = await self._connection_repo.get_singleton()
        if connection is None or connection.status != "connected":
            raise CalendarGrantMissingError(message="Organization Google Connection is not active")

    async def _resolve_org_calendar_id(self) -> str:
        """Resolve the Organization's selected calendar ID.

        Returns:
            The selected calendar ID string.

        Raises:
            CalendarGrantMissingError: If no calendar is selected.
        """
        if self._connection_repo is None:
            raise CalendarGrantMissingError(
                message="Organization Google Connection repository is not configured"
            )
        connection = await self._connection_repo.get_singleton()
        if connection is None or connection.status != "connected":
            raise CalendarGrantMissingError(message="Organization Google Connection is not active")
        calendar_id = connection.selected_calendar_id
        if not calendar_id:
            raise CalendarGrantMissingError(
                message="No recruitment calendar selected; "
                "select a calendar in Organization settings first"
            )
        return calendar_id

    async def _with_org_token(
        self,
        fn: Any,
    ) -> Any:
        """Execute a Calendar adapter call with the Organization's access token.

        Decrypts the stored access token, calls the provided async function,
        and on 401 triggers a token refresh before retrying once.

        Args:
            fn: Async callable that takes an access token string.

        Returns:
            The result of the callable.

        Raises:
            CalendarGrantMissingError: If no connection or token is available.
        """
        if self._connection_repo is None:
            raise CalendarGrantMissingError(
                message="Organization Google Connection repository is not configured"
            )
        if self._crypto is None:
            raise RuntimeError("Crypto utilities are not configured")

        connection = await self._connection_repo.get_singleton()
        if connection is None or connection.status != "connected":
            raise CalendarGrantMissingError(message="Organization Google Connection is not active")
        if not connection.access_token_enc:
            raise CalendarGrantMissingError(
                message="Organization Google Connection has no stored access token"
            )

        access_token = self._crypto.decrypt(connection.access_token_enc)

        try:
            return await fn(access_token)

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                # Token expired — refresh and retry once.
                new_token = await self._refresh_org_token(connection)
                return await fn(new_token)
            raise

    async def _refresh_org_token(self, connection: Any) -> str:
        """Refresh the Organization's Google OAuth access token.

        Args:
            connection: The Organization Google Connection entity.

        Returns:
            The new access token string.

        Raises:
            CalendarGrantMissingError: If refresh fails.
        """
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.credentials import Credentials

        if self._crypto is None:
            raise RuntimeError("Crypto utilities are not configured")

        refresh_token_plain = ""
        if connection.refresh_token_enc:
            refresh_token_plain = self._crypto.decrypt(connection.refresh_token_enc)

        if not refresh_token_plain:
            raise CalendarGrantMissingError(
                message="Organization Google Connection has no refresh token"
            )

        creds = Credentials(
            token=None,
            refresh_token=refresh_token_plain,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=connection.google_client_id or "",
            client_secret=connection.google_client_secret or "",
        )

        creds.refresh(GoogleRequest())

        if not creds.token:
            raise CalendarGrantMissingError(
                message="Failed to refresh Organization Google access token"
            )

        new_access_token = creds.token
        connection.access_token_enc = self._crypto.encrypt(new_access_token)
        connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=3600)
        self._session.add(connection)
        if self._session is not None:
            await self._session.flush()

        return new_access_token

    # ─── Private: Interviewer resolution ──────────────────────────────

    async def _resolve_interviewers(
        self,
        interviewer_ids: list[UUID],
    ) -> list[tuple[Any, str]]:
        """Resolve interviewer Employee entities and their emails.

        Args:
            interviewer_ids: List of Employee UUIDs.

        Returns:
            List of (Employee, email) tuples.

        Raises:
            InterviewerNotFoundError: If an interviewer ID has no Employee.
            InterviewerMissingEmailError: If a matched interviewer has no email.
        """
        # Every unmatched id is collected before raising: R1.7 asks the client
        # for the full list, and InterviewerNotFoundError takes a Sequence[UUID]
        # -- handing it a formatted string makes list() shred it into characters.
        # Blank emails are collected the same way rather than raised inline, so
        # the not-found report is never pre-empted by a blank-email report from a
        # later id: the client would fix one problem only to hit the other.
        resolved: list[tuple[Any, str]] = []
        unmatched: list[UUID] = []
        blank_email: list[UUID] = []
        for emp_id in interviewer_ids:
            emp = await self._get_employee(emp_id)
            if emp is None:
                unmatched.append(emp_id)
                continue
            # A whitespace-only address is as unusable as an empty one: Calendar
            # rejects it as an attendee, so treat it as missing (R10).
            email = (emp.email or "").strip()
            if not email:
                blank_email.append(emp_id)
                continue
            resolved.append((emp, email))

        if unmatched:
            raise InterviewerNotFoundError(unmatched)
        if blank_email:
            raise InterviewerMissingEmailError(blank_email[0])
        return resolved

    async def _get_employee(self, employee_id: UUID) -> Any | None:
        """Fetch an Employee by ID from the session.

        Args:
            employee_id: UUID of the Employee.

        Returns:
            The Employee entity or None.
        """
        stmt = select(Employee).where(Employee.id == employee_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    # ─── Private: Timezone helpers ────────────────────────────────────

    async def _get_org_timezone(self) -> str:
        """Get the Organization's timezone from settings.

        Returns:
            IANA timezone string (defaults to "Asia/Ho_Chi_Minh").
        """
        if self._org_settings_repo is None:
            return "Asia/Ho_Chi_Minh"
        return await self._org_settings_repo.get_timezone()

    def _build_attendees(self, candidate: Candidate, interviewer_emails: list[str]) -> list[str]:
        """Build the attendee email list from candidate and interviewers.

        Args:
            candidate: The Candidate entity.
            interviewer_emails: List of interviewer email addresses.

        Returns:
            List of attendee email addresses (candidate first, then
            interviewers), each address appearing once.
        """
        # An interviewer may also be the candidate, or be recorded with
        # different casing; Calendar should not be asked to invite the same
        # mailbox twice (R5.1, R5.2). First spelling seen wins.
        attendees: list[str] = []
        seen: set[str] = set()
        for email in [candidate.email, *interviewer_emails]:
            if not email:
                continue
            key = email.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            attendees.append(email)
        return attendees

    # ─── Private: Request validation ──────────────────────────────────

    @staticmethod
    def _build_event_description(
        notes: str | None,
        *,
        mode: str,
        meeting_link: str | None,
    ) -> str | None:
        """Compose the event description, carrying a custom join link with it.

        :class:`CalendarEventSpec` has no ``location`` field, so a
        ``custom_link`` interview's join URL travels inside the description.
        That makes the description load-bearing: every write that rebuilds it
        has to put the link back, or a plain reschedule silently deletes the
        only way attendees have to join. ``google_meet`` needs no such care --
        its conference lives in ``conferenceData``, which a patch preserves.

        Args:
            notes: The interview notes, if any.
            mode: The interview's meeting mode.
            meeting_link: The custom join URL, when the mode has one.

        Returns:
            The description to send to Calendar, or ``None`` when there is
            nothing to say.
        """
        if mode == MeetingMode.CUSTOM_LINK and meeting_link:
            return f"{notes or ''}\nMeeting link: {meeting_link}".strip()
        return notes

    def _validate_schedule_request(
        self,
        *,
        start: datetime,
        duration_minutes: int,
        interviewer_ids: list[UUID],
        notes: str | None = None,
    ) -> None:
        """Validate interview scheduling request fields.

        Args:
            start: Interview start datetime.
            duration_minutes: Duration in minutes.
            interviewer_ids: List of interviewer IDs.
            notes: Optional notes.

        Raises:
            ValueError: If any field is invalid.
        """
        if duration_minutes < 15 or duration_minutes > 180:
            raise ValueError(f"Duration must be between 15 and 180 minutes, got {duration_minutes}")

        if not interviewer_ids or len(interviewer_ids) > 10:
            raise ValueError(
                f"Number of interviewers must be between 1 and 10, got {len(interviewer_ids)}"
            )

        # R1.4: an interview cannot be booked in the past. A naive ``start``
        # is only compared once it has been given the organization timezone,
        # which happens later, so treat it as UTC for this bound -- the caller
        # is rejected either way if the instant has already gone by.
        reference = start if start.tzinfo else start.replace(tzinfo=UTC)
        if reference <= datetime.now(UTC):
            raise ValueError(f"Interview start must be in the future, got {start.isoformat()}")

        if notes and len(notes) > 1000:
            raise ValueError(f"Notes must be 1000 characters or fewer, got {len(notes)}")

    # ─── Private: Interview persistence ───────────────────────────────

    async def _persist_interview_schedule(
        self,
        candidate: Candidate,
        event_id: str,
        start_resolved: datetime,
        timezone: str,
        duration_minutes: int,
        interviewer_ids: list[UUID],
        calendar_id: str,
        notes: str | None = None,
    ) -> Candidate:
        """Persist the interview schedule: create Interview and participants.

        Creates the Interview record with the Calendar event reference and
        adds the candidate and interviewer participants.

        Args:
            candidate: The Candidate entity.
            event_id: Google Calendar event ID.
            start_resolved: Resolved start datetime.
            timezone: IANA timezone string.
            duration_minutes: Duration in minutes.
            interviewer_ids: List of interviewer UUIDs.
            calendar_id: Google Calendar ID.
            notes: Optional notes.

        Returns:
            The updated Candidate entity.
        """
        existing_interview = await self._get_interview_by_event_id(candidate.id, event_id)

        if not existing_interview:
            interview = Interview(
                candidate_id=candidate.id,
                status="scheduled",
                round_name="Interview",
                start_at=start_resolved,
                end_at=start_resolved + timedelta(minutes=duration_minutes),
                timezone=timezone,
                calendar_event_id=event_id,
                calendar_id=calendar_id,
                needs_relink=False,
            )
            self._session.add(interview)
            if hasattr(self._session, "flush"):
                try:
                    await self._session.flush()
                except Exception:
                    pass

            cand_part = InterviewParticipant(
                interview_id=interview.id,
                type="candidate",
                email=candidate.email,
                name=candidate.name,
            )
            self._session.add(cand_part)

            for emp_id in interviewer_ids:
                emp = await self._get_employee(emp_id)
                if emp:
                    emp_part = InterviewParticipant(
                        interview_id=interview.id,
                        type="employee",
                        email=emp.email,
                        name=emp.full_name,
                        employee_id=emp_id,
                    )
                    self._session.add(emp_part)

        candidate.status = CandidateStatus.INTERVIEW_SCHEDULED
        candidate = await self._candidate_repo.update(candidate)
        if self._session is not None:
            await self._session.commit()
        return candidate

    # ─── Private: Interview retrieval ─────────────────────────────────

    async def _get_interview_by_event_id(
        self,
        candidate_id: UUID,
        event_id: str,
    ) -> Interview | None:
        """Find an Interview by candidate ID and Calendar event ID.

        Args:
            candidate_id: UUID of the candidate.
            event_id: Google Calendar event ID.

        Returns:
            The Interview entity or None.
        """
        stmt = select(Interview).where(
            Interview.candidate_id == candidate_id,
            Interview.calendar_event_id == event_id,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def _get_scheduled_interview(self, candidate_id: UUID) -> Interview | None:
        """Get a scheduled interview for a candidate.

        Args:
            candidate_id: UUID of the candidate.

        Returns:
            A scheduled Interview or None.
        """
        interviews = await self._interview_repo.find_by_candidate_id(candidate_id)
        for iv in interviews:
            if iv.status == "scheduled":
                return iv
        return None

    async def _get_interview_or_raise(self, interview_id: UUID) -> Interview:
        """Get an Interview by ID or raise InterviewNotFoundError.

        Args:
            interview_id: UUID of the Interview.

        Returns:
            The Interview entity.

        Raises:
            InterviewNotFoundError: If not found.
        """
        interview = await self._interview_repo.get_by_id(interview_id)
        if interview is None:
            from src.modules.recruitment.domain.exceptions import InterviewNotFoundError

            raise InterviewNotFoundError(f"Interview not found: {interview_id}")
        return interview

    def _assert_interview_is_scheduled(self, interview: Interview, action: str) -> None:
        """Assert the Interview is in scheduled status.

        Args:
            interview: The Interview entity.
            action: Action name for the error message.

        Raises:
            InterviewStatusTransitionError: If not scheduled.
        """
        if interview.status != "scheduled":
            from src.modules.recruitment.domain.exceptions import InterviewStatusTransitionError

            raise InterviewStatusTransitionError(
                f"Cannot {action} interview {interview.id} with status "
                f"'{interview.status}'; expected 'scheduled'"
            )

    # ─── Private: Audit helpers ───────────────────────────────────────

    async def _audit_interview_schedule(
        self,
        user_id: UUID,
        candidate: Candidate,
        event_id: str,
        start_resolved: datetime,
        timezone: str,
        interviewer_ids: list[UUID],
        previous_status: str,
    ) -> None:
        """Write a success audit entry for an interview schedule.

        Args:
            user_id: Acting user ID.
            candidate: The Candidate entity.
            event_id: Google Calendar event ID.
            start_resolved: Resolved start datetime.
            timezone: IANA timezone string.
            interviewer_ids: List of interviewer UUIDs.
            previous_status: Previous candidate status.
        """
        await log_audit(
            session=self._session,
            operation_type="interview_scheduled",
            entity_type="candidate",
            entity_id=candidate.id,
            user_id=user_id,
            previous_value={"status": previous_status},
            new_value={
                "status": CandidateStatus.INTERVIEW_SCHEDULED,
                "calendar_event_id": event_id,
                "start": start_resolved.isoformat(),
                "timezone": timezone,
                "interviewer_ids": [str(i) for i in interviewer_ids],
            },
            change_summary=(
                f"Interview scheduled for candidate {candidate.id}: "
                f"start={start_resolved.isoformat()}, "
                f"event={event_id}"
            ),
            success=True,
        )
        await self._commit_audit()

    # ─── Private: Calendar conflict capture ───────────────────────────

    async def _capture_calendar_conflict(
        self,
        user_id: UUID,
        candidate_id: UUID,
        event_id: str,
        operation: str,
    ) -> None:
        """Capture a calendar conflict by fetching the remote event snapshot.

        When a conditional write (If-Match) fails with 412, this method:
        1. Fetches the remote latest event state from Google Calendar.
        2. Builds a local snapshot from the stored Interview record.
        3. Persists a CalendarConflict with status "unresolved".
        4. Does NOT mutate the Interview or Candidate.

        Args:
            user_id: The acting HR user's identifier.
            candidate_id: The candidate whose event conflicted.
            event_id: The Google Calendar event ID.
            operation: The operation that failed (e.g. "patch_event").
        """
        if self._calendar_port is None:
            logger.warning("Cannot capture conflict: Calendar port not configured")
            return

        remote_event: CalendarEvent | None = None
        try:
            calendar_id = await self._resolve_org_calendar_id()
            remote_event = await self._with_org_token(
                lambda token: self._get_calendar_event(token, event_id, calendar_id),
            )
        except Exception as exc:
            logger.warning(
                "Failed to fetch remote event for conflict capture (event %s): %s",
                event_id,
                exc,
            )

        # Build local snapshot from the Interview record.
        interview = await self._get_interview_by_event_id(candidate_id, event_id)
        local_snapshot: dict[str, Any] = {
            "interview_id": str(interview.id) if interview else None,
            "status": interview.status if interview else None,
            "start_at": (
                interview.start_at.isoformat() if interview and interview.start_at else None
            ),
            "end_at": interview.end_at.isoformat() if interview and interview.end_at else None,
            "timezone": interview.timezone if interview else None,
            "calendar_etag": interview.calendar_etag if interview else None,
            "calendar_updated": (
                interview.calendar_updated.isoformat()
                if interview and interview.calendar_updated
                else None
            ),
            "meeting_mode": interview.meeting_mode if interview else None,
            "meeting_link": interview.meeting_link if interview else None,
        }

        remote_snapshot: dict[str, Any] = {}
        if remote_event is not None:
            remote_snapshot = {
                "event_id": remote_event.event_id,
                "etag": remote_event.etag,
                "updated": remote_event.updated.isoformat() if remote_event.updated else None,
                "status": remote_event.status,
                "html_link": remote_event.html_link,
                "meet_link": remote_event.meet_link,
                "location": remote_event.location,
                "start_at": remote_event.start_at.isoformat() if remote_event.start_at else None,
                "end_at": remote_event.end_at.isoformat() if remote_event.end_at else None,
                "timezone": remote_event.timezone,
            }

        conflict_details: dict[str, Any] = {
            "operation": operation,
            "calendar_event_id": event_id,
            "reason": "If-Match conditional write failed with 412",
        }

        conflict = CalendarConflict(
            interview_id=interview.id if interview else UUID(int=0),
            candidate_id=candidate_id,
            calendar_event_id=event_id,
            local_snapshot=local_snapshot,
            remote_snapshot=remote_snapshot,
            conflict_details=conflict_details,
            status=CalendarConflictStatus.UNRESOLVED,
        )
        self._session.add(conflict)
        if self._session is not None:
            await self._session.commit()

        logger.info(
            "Calendar conflict captured: event=%s, candidate=%s, conflict_id=%s",
            event_id,
            candidate_id,
            conflict.id,
        )

    # ─── Public: Candidate retrieval (shared) ────────────────────────

    async def _get_candidate_or_raise(self, candidate_id: UUID) -> Candidate:
        """Retrieve a candidate by ID or raise CandidateNotFoundError.

        Args:
            candidate_id: The UUID of the candidate.

        Returns:
            The Candidate entity.

        Raises:
            CandidateNotFoundError: If the candidate doesn't exist.
        """
        candidate = await self._candidate_repo.get_by_id(candidate_id)
        if candidate is None:
            raise CandidateNotFoundError(f"Candidate not found: {candidate_id}")
        return candidate
