"""Candidate Lifecycle Service for the Recruitment module.

Manages Candidate CRUD, status transitions, Job Opening assignment,
list/search, and detail retrieval with linked CV documents and presigned URLs.

Extracted from CandidateService. Implements the CandidateCreator protocol
used by cv_processor.py and review_service.py.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9,
6.1, 6.2, 6.3, 6.4, 6.6, 6.7, 6.8, 7.1, 7.2, 7.3, 7.4, 7.5, 13.2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.recruitment.application.candidate_validators import (
    CandidateValidationError,
    validate_candidate_fields,
    validate_transition,
)
from src.modules.recruitment.domain.entities import Candidate, CVDocument
from src.modules.recruitment.domain.enums import CandidateStatus, JobOpeningStatus
from src.modules.recruitment.domain.exceptions import (
    CandidateAssignmentBlockedError,
    CandidateNotFoundError,
    InvalidStatusTransitionError,
    JobOpeningNotFoundError,
    JobOpeningNotOpenError,
)
from src.modules.recruitment.domain.value_objects import ParsedCV
from src.modules.recruitment.infrastructure.audit_repository import log_audit
from src.modules.recruitment.infrastructure.minio_client import RecruitmentMinIOClient
from src.modules.recruitment.infrastructure.repositories import (
    CandidateRepository,
    CVDocumentRepository,
    JobOpeningRepository,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@runtime_checkable
class DomainEventPublisher(Protocol):
    """Protocol for publishing domain events."""

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish a domain event."""
        ...


@runtime_checkable
class InterviewCanceller(Protocol):
    """Protocol for cancelling a Candidate's booked interview (R8).

    Rejecting or archiving a Candidate must also call off the interview that was
    booked for them, otherwise the interviewers keep a meeting on their calendar
    for someone who is out of the process. That cancellation is Calendar work,
    owned by ``InterviewSchedulerService``; this protocol is the one-method
    surface the lifecycle needs, so nothing about Google leaks in here.

    Implementations are best-effort and must not raise: the terminal transition
    has already committed by the time they are called (R8.4, R8.5).
    """

    async def cancel_interview_for_candidate(self, candidate_id: UUID, *, trigger: str) -> None:
        """Cancel the Candidate's booked interview event, if there is one."""
        ...


@dataclass
class CVDocumentDetail:
    """CV document metadata with an optional presigned download URL.

    Attributes:
        id: UUID of the CV document.
        original_filename: Original filename of the uploaded CV.
        mime_type: MIME type of the file.
        size_bytes: File size in bytes.
        uploaded_at: Timestamp when the file was uploaded.
        presigned_url: Presigned MinIO URL for direct download, or None
            if URL generation failed.
        url_error: Error message if presigned URL generation failed.
    """

    id: UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    presigned_url: str | None = None
    url_error: str | None = None


@dataclass
class CandidateDetail:
    """Full candidate detail including linked CV documents with presigned URLs.

    Attributes:
        candidate: The Candidate entity with all fields.
        cv_documents: List of CV documents with presigned download URLs.
    """

    candidate: Candidate
    cv_documents: list[CVDocumentDetail] = field(default_factory=list)


@dataclass
class PaginatedCandidates:
    """Paginated list of candidates with total count.

    Attributes:
        candidates: List of Candidate entities for the current page.
        total_count: Total number of candidates matching the query filters.
        page: Current page number (1-indexed).
        page_size: Number of items per page.
    """

    candidates: list[Candidate]
    total_count: int
    page: int
    page_size: int


class CandidateLifecycleService:
    """Manages Candidate lifecycle, list/search, and detail retrieval.

    Provides methods for creating/updating candidates from parsed CVs,
    listing candidates with filters and search, retrieving full candidate
    details with linked CV documents and presigned URLs, and managing
    candidate status transitions and Job Opening assignments.

    Implements the CandidateCreator protocol from cv_processor.py and
    the CandidateCreatorProtocol from review_service.py, providing
    the ``create_or_update_candidate`` method that the CV processing
    pipeline calls after successful parsing.

    Args:
        candidate_repo: Repository for candidate persistence.
        cv_document_repo: Repository for CV document persistence.
        job_opening_repo: Repository for job opening persistence.
        minio_client: MinIO client for generating presigned URLs.
        event_publisher: Optional domain event publisher.
        session: Async database session.
        user_id: Optional acting user UUID for audit attribution.
        interview_canceller: Optional seam that calls off a Candidate's booked
            interview when they are rejected or archived (R8). When omitted the
            transitions still work, but no Calendar event is cancelled.
    """

    def __init__(
        self,
        candidate_repo: CandidateRepository,
        cv_document_repo: CVDocumentRepository,
        job_opening_repo: JobOpeningRepository,
        minio_client: RecruitmentMinIOClient,
        event_publisher: DomainEventPublisher | None = None,
        session: AsyncSession | None = None,
        user_id: UUID | None = None,
        interview_canceller: InterviewCanceller | None = None,
    ) -> None:
        self._candidate_repo = candidate_repo
        self._cv_document_repo = cv_document_repo
        self._job_opening_repo = job_opening_repo
        self._minio_client = minio_client
        self._event_publisher = event_publisher
        self._session = session
        self._user_id = user_id
        self._interview_canceller = interview_canceller

    # ─── Create / Update (CandidateCreator protocol) ─────────────────────

    async def create_or_update_candidate(
        self,
        parsed_cv: ParsedCV,
        cv_document_id: UUID,
        source_email_id: UUID | None,
        confidence_score: float,
    ) -> Candidate:
        """Create a new candidate or update an existing one by email match.

        Implements the CandidateCreator protocol. This method:
        1. Validates name and email fields
        2. Checks for existing candidate with same email (deduplication)
        3. If exists: updates data fields but preserves existing status
        4. If new: creates with status="new"
        5. Links the CV document to the candidate
        6. Stores confidence_score and parsed_cv_json
        7. Logs audit entry

        Args:
            parsed_cv: Structured CV data from LLM parsing.
            cv_document_id: UUID of the associated CV document.
            source_email_id: UUID of the source email message (or None).
            confidence_score: Confidence score from LLM parsing (0.0-1.0).

        Returns:
            The created or updated Candidate entity.

        Raises:
            CandidateValidationError: If name or email validation fails.
        """
        # Step 1: Validate required fields
        validation_errors = validate_candidate_fields(parsed_cv)
        if validation_errors:
            # Store validation errors on the CV document
            cv_doc = await self._cv_document_repo.get_by_id(cv_document_id)
            if cv_doc is not None:
                cv_doc.validation_errors = validation_errors
                await self._cv_document_repo.update(cv_doc)
                if self._session is not None:
                    await self._session.commit()

            logger.warning(
                "Candidate validation failed for CV document %s: %s",
                cv_document_id,
                validation_errors,
            )
            raise CandidateValidationError(validation_errors)

        # Step 2: Check for existing candidate by email (deduplication)
        email = parsed_cv.email.strip().lower()
        existing_candidate = await self._candidate_repo.find_by_email(email)

        if existing_candidate is not None:
            # Step 3: Update existing candidate — preserve status
            candidate = await self._update_existing_candidate(
                existing_candidate, parsed_cv, confidence_score
            )
            operation = "candidate_updated"
        else:
            # Step 4: Create new candidate with status "new"
            candidate = await self._create_new_candidate(
                parsed_cv, source_email_id, confidence_score
            )
            operation = "candidate_created"

        # Step 5: Link CV document to candidate
        await self._link_cv_document(cv_document_id, candidate.id)

        # Commit all changes
        if self._session is not None:
            await self._session.commit()

        # Step 6: Log audit entry
        await log_audit(
            session=self._session,
            operation_type=operation,
            entity_type="candidate",
            entity_id=candidate.id,
            new_value={
                "name": candidate.name,
                "email": candidate.email,
                "status": candidate.status,
                "confidence_score": confidence_score,
                "cv_document_id": str(cv_document_id),
            },
            change_summary=(
                f"Candidate {operation.replace('candidate_', '')}: "
                f"{candidate.name} ({candidate.email}), "
                f"confidence={confidence_score:.2f}"
            ),
            success=True,
        )
        if self._session is not None:
            await self._session.commit()

        logger.info(
            "Candidate %s: id=%s, email=%s, confidence=%.2f",
            operation,
            candidate.id,
            candidate.email,
            confidence_score,
        )

        return candidate

    # ─── List / Detail ─────────────────────────────────────────────────

    async def list_candidates(
        self,
        *,
        status: list[str] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        min_confidence: float | None = None,
        skills: list[str] | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedCandidates:
        """Retrieve a paginated list of candidates with optional filters.

        Validates pagination parameters before delegating to the repository.
        Archived candidates are excluded by default unless the status filter
        explicitly includes "archived".

        Args:
            status: Optional list of status values to filter by.
            date_from: Optional start date for created_at range filter.
            date_to: Optional end date for created_at range filter.
            min_confidence: Optional minimum confidence score filter (0.0–1.0).
            skills: Optional list of skills to filter by (OR logic, case-insensitive).
            search: Optional text to search in name, email, phone, skills
                (case-insensitive partial match).
            page: Page number (1-indexed). Must be >= 1.
            page_size: Number of items per page. Must be between 1 and 100.

        Returns:
            PaginatedCandidates with the list of candidates and total count.

        Raises:
            ValueError: If page < 1 or page_size not in range 1–100.
        """
        if page < 1:
            raise ValueError("page must be >= 1")
        if page_size < 1 or page_size > 100:
            raise ValueError("page_size must be between 1 and 100")

        candidates, total_count = await self._candidate_repo.list_candidates(
            status=status,
            date_from=date_from,
            date_to=date_to,
            min_confidence=min_confidence,
            skills=skills,
            search=search,
            page=page,
            page_size=page_size,
        )

        return PaginatedCandidates(
            candidates=candidates,
            total_count=total_count,
            page=page,
            page_size=page_size,
        )

    async def get_candidate(self, candidate_id: UUID) -> CandidateDetail:
        """Retrieve full candidate detail with linked CV documents and presigned URLs.

        Fetches the candidate by ID, retrieves all linked CV documents,
        and generates a presigned download URL for each document. If URL
        generation fails for a document (e.g., MinIO unavailable or file
        missing), the document is still returned with url set to None and
        an error indicator.

        Args:
            candidate_id: UUID of the candidate to retrieve.

        Returns:
            CandidateDetail with the candidate and CV documents (each with
            a presigned URL or error indicator).

        Raises:
            CandidateNotFoundError: If no candidate exists with the given ID.
        """
        candidate = await self._candidate_repo.get_by_id(candidate_id)
        if candidate is None:
            raise CandidateNotFoundError(f"Candidate not found: {candidate_id}")

        # Fetch all linked CV documents
        cv_documents = await self._cv_document_repo.find_by_candidate_id(candidate_id)

        # Generate presigned URLs for each document
        cv_document_details: list[CVDocumentDetail] = []
        for doc in cv_documents:
            detail = await self._build_cv_document_detail(doc)
            cv_document_details.append(detail)

        return CandidateDetail(
            candidate=candidate,
            cv_documents=cv_document_details,
        )

    async def ensure_candidate_exists(self, candidate_id: UUID) -> None:
        """Raise CandidateNotFoundError if no Candidate has this id.

        A cheap existence check for callers that only need to know "does this
        candidate exist" -- unlike ``get_candidate``, this does not touch CV
        documents or generate presigned MinIO URLs.

        Args:
            candidate_id: UUID of the candidate to check.

        Raises:
            CandidateNotFoundError: If no candidate exists with the given ID.
        """
        await self._get_candidate_or_raise(candidate_id)

    # ─── Status transition actions ─────────────────────────────────────

    async def reject_candidate(self, candidate_id: UUID, reason: str | None = None) -> Candidate:
        """Transition candidate to rejected status.

        Validates the transition, stores the rejection reason and
        rejected_at timestamp, and logs an audit entry.

        When the Candidate has a booked interview, its Google Calendar event is
        cancelled as a best-effort side-effect AFTER the transition has
        committed, so a cancellation failure can never undo the rejection
        (R8.1, R8.4, R8.6, R12.3). With no booked interview, no Calendar call is
        made (R8.3).

        Args:
            candidate_id: UUID of the candidate to reject.
            reason: Optional rejection reason (max 1000 characters).

        Returns:
            The updated Candidate entity.

        Raises:
            CandidateNotFoundError: If the candidate doesn't exist.
            InvalidStatusTransitionError: If the transition is not allowed.
        """
        candidate = await self._get_candidate_or_raise(candidate_id)
        previous_status = candidate.status

        validate_transition(
            current_status=candidate.status,
            target_status=CandidateStatus.REJECTED,
            action="reject",
        )

        candidate.status = CandidateStatus.REJECTED
        candidate.rejection_reason = reason
        candidate.rejected_at = datetime.now(UTC)
        candidate = await self._candidate_repo.update(candidate)
        if self._session is not None:
            await self._session.commit()

        await log_audit(
            session=self._session,
            operation_type="candidate_rejected",
            entity_type="candidate",
            entity_id=candidate.id,
            user_id=self._user_id,
            previous_value={"status": previous_status},
            new_value={"status": CandidateStatus.REJECTED},
            change_summary=(f"Candidate rejected: {reason[:200] if reason else 'no reason'}"),
        )

        # Best-effort, and only after the rejection is committed (R8.1, R8.4).
        await self._cancel_booked_interview(candidate.id, trigger="reject")

        return candidate

    async def accept_candidate(self, candidate_id: UUID) -> Candidate:
        """Transition candidate to accepted status.

        Only allowed from interview_scheduled or reviewing status.
        Stores accepted_at timestamp, emits domain event, and logs audit.

        When accepted and assigned to a Job Opening, touches the Job Opening's
        updated_at timestamp.

        Args:
            candidate_id: UUID of the candidate to accept.

        Returns:
            The updated Candidate entity.

        Raises:
            CandidateNotFoundError: If the candidate doesn't exist.
            InvalidStatusTransitionError: If the transition is not allowed.
        """
        candidate = await self._get_candidate_or_raise(candidate_id)
        previous_status = candidate.status

        validate_transition(
            current_status=candidate.status,
            target_status=CandidateStatus.ACCEPTED,
            action="accept",
        )

        candidate.status = CandidateStatus.ACCEPTED
        candidate.accepted_at = datetime.now(UTC)
        candidate = await self._candidate_repo.update(candidate)

        # Touch the Job Opening updated_at timestamp when candidate is accepted
        if candidate.job_opening_id is not None:
            job_opening = await self._job_opening_repo.get_by_id(candidate.job_opening_id)
            if job_opening is not None:
                job_opening.updated_at = datetime.now(UTC)
                await self._job_opening_repo.update(job_opening)

        if self._session is not None:
            await self._session.commit()

        # Emit domain event for downstream modules (onboarding)
        if self._event_publisher:
            await self._event_publisher.publish(
                event_type="candidate_accepted",
                payload={
                    "candidate_id": str(candidate.id),
                    "name": candidate.name,
                    "email": candidate.email,
                },
            )

        await log_audit(
            session=self._session,
            operation_type="candidate_accepted",
            entity_type="candidate",
            entity_id=candidate.id,
            user_id=self._user_id,
            previous_value={"status": previous_status},
            new_value={
                "status": CandidateStatus.ACCEPTED,
                "job_opening_id": (
                    str(candidate.job_opening_id) if candidate.job_opening_id else None
                ),
            },
            change_summary="Candidate accepted",
        )

        return candidate

    async def archive_candidate(self, candidate_id: UUID) -> Candidate:
        """Transition candidate to archived status.

        Not allowed from accepted status. Idempotent for already-archived
        candidates (returns existing record without modification).

        When the Candidate has a booked interview, its Google Calendar event is
        cancelled as a best-effort side-effect AFTER the transition has
        committed, so a cancellation failure can never undo the archive
        (R8.2, R8.5, R8.6, R12.3). With no booked interview, no Calendar call is
        made (R8.3). The idempotent already-archived path cancels nothing -- it
        changed no state, and the first archive already cancelled the event.

        Args:
            candidate_id: UUID of the candidate to archive.

        Returns:
            The updated Candidate entity.

        Raises:
            CandidateNotFoundError: If the candidate doesn't exist.
            InvalidStatusTransitionError: If the transition is not allowed
                (e.g., from accepted status).
        """
        candidate = await self._get_candidate_or_raise(candidate_id)
        previous_status = candidate.status

        # Idempotent: already archived is a no-op
        if candidate.status == CandidateStatus.ARCHIVED:
            return candidate

        validate_transition(
            current_status=candidate.status,
            target_status=CandidateStatus.ARCHIVED,
            action="archive",
        )

        candidate.status = CandidateStatus.ARCHIVED
        candidate.archived_at = datetime.now(UTC)
        candidate = await self._candidate_repo.update(candidate)
        if self._session is not None:
            await self._session.commit()

        await log_audit(
            session=self._session,
            operation_type="candidate_archived",
            entity_type="candidate",
            entity_id=candidate.id,
            user_id=self._user_id,
            previous_value={"status": previous_status},
            new_value={"status": CandidateStatus.ARCHIVED},
            change_summary=f"Candidate archived from status '{previous_status}'",
        )

        # Best-effort, and only after the archive is committed (R8.2, R8.5).
        await self._cancel_booked_interview(candidate.id, trigger="archive")

        return candidate

    async def _cancel_booked_interview(self, candidate_id: UUID, *, trigger: str) -> None:
        """Call off the Candidate's booked interview after a terminal transition.

        Delegates to the :class:`InterviewCanceller` seam when one is wired.
        Swallows anything it raises: the reject/archive has already committed, so
        a Calendar problem must not surface as a failed transition (R8.4, R8.5).

        Args:
            candidate_id: The Candidate that just reached a terminal status.
            trigger: ``"reject"`` or ``"archive"``.
        """
        if self._interview_canceller is None:
            return
        try:
            await self._interview_canceller.cancel_interview_for_candidate(
                candidate_id, trigger=trigger
            )
        except Exception:  # noqa: BLE001 - cancellation must never undo the transition
            logger.warning(
                "Interview cancellation raised on %s of candidate %s; "
                "the terminal transition stands",
                trigger,
                candidate_id,
                exc_info=True,
            )

    # ─── Job Opening assignment ─────────────────────────────────────────

    _ASSIGNABLE_STATUSES: frozenset[str] = frozenset(
        {
            CandidateStatus.NEW,
            CandidateStatus.REVIEWING,
            CandidateStatus.INTERVIEW_SCHEDULED,
        }
    )

    async def assign_candidate(self, candidate_id: UUID, job_opening_id: UUID) -> Candidate:
        """Assign an unassigned Candidate to an open Job Opening.

        Rules:
        - Candidate must not already be assigned to a Job Opening.
        - Candidate status must be new, reviewing, or interview_scheduled.
        - Job Opening must have status 'open'.

        Args:
            candidate_id: UUID of the Candidate to assign.
            job_opening_id: UUID of the target Job Opening.

        Returns:
            The updated Candidate entity.

        Raises:
            CandidateNotFoundError: If the Candidate does not exist.
            JobOpeningNotFoundError: If the Job Opening does not exist.
            JobOpeningNotOpenError: If the Job Opening is not in 'open' status.
            CandidateAssignmentBlockedError: If the Candidate is in a terminal status.
            InvalidStatusTransitionError: If the Candidate is already assigned.
        """
        candidate = await self._get_candidate_locked_or_raise(candidate_id)

        if candidate.job_opening_id is not None:
            raise InvalidStatusTransitionError(
                current_status=candidate.status,
                attempted_action="assign",
            )

        if candidate.status not in self._ASSIGNABLE_STATUSES:
            raise CandidateAssignmentBlockedError(
                message=(
                    f"Cannot assign candidate in status '{candidate.status}'; "
                    f"allowed: {', '.join(sorted(self._ASSIGNABLE_STATUSES))}"
                ),
            )

        job_opening = await self._job_opening_repo.get_by_id(job_opening_id)
        if job_opening is None:
            raise JobOpeningNotFoundError(f"Job Opening not found: {job_opening_id}")
        if job_opening.status != JobOpeningStatus.OPEN:
            raise JobOpeningNotOpenError(
                job_opening_id=job_opening.id,
                current_status=job_opening.status,
            )
        candidate.job_opening_id = job_opening_id
        candidate = await self._candidate_repo.update(candidate)
        if self._session is not None:
            await self._session.commit()

        await log_audit(
            session=self._session,
            operation_type="candidate_assigned",
            entity_type="candidate",
            entity_id=candidate.id,
            user_id=self._user_id,
            previous_value={"job_opening_id": None},
            new_value={"job_opening_id": str(job_opening_id)},
            change_summary=f"Candidate assigned to Job Opening '{job_opening.title}'",
        )

        return candidate

    async def reassign_candidate(self, candidate_id: UUID, new_job_opening_id: UUID) -> Candidate:
        """Reassign a Candidate to a different open Job Opening.

        Rules:
        - Candidate must already be assigned.
        - Candidate status must be new, reviewing, or interview_scheduled.
        - New Job Opening must be open.

        Args:
            candidate_id: UUID of the Candidate to reassign.
            new_job_opening_id: UUID of the new target Job Opening.

        Returns:
            The updated Candidate entity.

        Raises:
            CandidateNotFoundError: If the Candidate does not exist.
            JobOpeningNotFoundError: If the new Job Opening does not exist.
            JobOpeningNotOpenError: If the new Job Opening is not in 'open' status.
            CandidateAssignmentBlockedError: If the Candidate is in a terminal status.
            InvalidStatusTransitionError: If the Candidate is not currently assigned.
        """
        candidate = await self._get_candidate_locked_or_raise(candidate_id)
        previous_job_opening_id = candidate.job_opening_id

        if candidate.job_opening_id is None:
            raise InvalidStatusTransitionError(
                current_status=candidate.status,
                attempted_action="reassign",
            )

        if candidate.status not in self._ASSIGNABLE_STATUSES:
            raise CandidateAssignmentBlockedError(
                message=(
                    f"Cannot reassign candidate in status '{candidate.status}'; "
                    f"allowed: {', '.join(sorted(self._ASSIGNABLE_STATUSES))}"
                ),
            )
        if candidate.job_opening_id == new_job_opening_id:
            return candidate

        new_job_opening = await self._job_opening_repo.get_by_id(new_job_opening_id)
        if new_job_opening is None:
            raise JobOpeningNotFoundError(f"Job Opening not found: {new_job_opening_id}")
        if new_job_opening.status != JobOpeningStatus.OPEN:
            raise JobOpeningNotOpenError(
                job_opening_id=new_job_opening.id,
                current_status=new_job_opening.status,
            )
        candidate.job_opening_id = new_job_opening_id
        candidate = await self._candidate_repo.update(candidate)
        if self._session is not None:
            await self._session.commit()

        await log_audit(
            session=self._session,
            operation_type="candidate_reassigned",
            entity_type="candidate",
            entity_id=candidate.id,
            user_id=self._user_id,
            previous_value={"job_opening_id": str(previous_job_opening_id)},
            new_value={"job_opening_id": str(new_job_opening_id)},
            change_summary=(
                f"Candidate reassigned from Job Opening {previous_job_opening_id} "
                f"to '{new_job_opening.title}'"
            ),
        )

        return candidate

    async def unassign_candidate(self, candidate_id: UUID) -> Candidate:
        """Remove a Candidate's assignment to a Job Opening.

        Candidate must currently be assigned. Candidate status must be
        new, reviewing, or interview_scheduled.

        Args:
            candidate_id: UUID of the Candidate to unassign.

        Returns:
            The updated Candidate entity.

        Raises:
            CandidateNotFoundError: If the Candidate does not exist.
            InvalidStatusTransitionError: If the Candidate is not currently assigned.
            CandidateAssignmentBlockedError: If the Candidate is in a terminal status.
        """
        candidate = await self._get_candidate_locked_or_raise(candidate_id)
        previous_job_opening_id = candidate.job_opening_id

        if candidate.job_opening_id is None:
            raise InvalidStatusTransitionError(
                current_status=candidate.status,
                attempted_action="unassign",
            )

        if candidate.status not in self._ASSIGNABLE_STATUSES:
            raise CandidateAssignmentBlockedError(
                message=(
                    f"Cannot unassign candidate in status '{candidate.status}'; "
                    f"allowed: {', '.join(sorted(self._ASSIGNABLE_STATUSES))}"
                ),
            )

        candidate.job_opening_id = None
        candidate = await self._candidate_repo.update(candidate)
        if self._session is not None:
            await self._session.commit()

        await log_audit(
            session=self._session,
            operation_type="candidate_unassigned",
            entity_type="candidate",
            entity_id=candidate.id,
            user_id=self._user_id,
            previous_value={"job_opening_id": str(previous_job_opening_id)},
            new_value={"job_opening_id": None},
            change_summary=f"Candidate unassigned from Job Opening {previous_job_opening_id}",
        )

        return candidate

    # ─── Private helpers ─────────────────────────────────────────────

    async def _build_cv_document_detail(self, doc: CVDocument) -> CVDocumentDetail:
        """Build a CVDocumentDetail with presigned URL for a single document.

        Attempts to generate a presigned URL. If generation fails (MinIO
        unavailable or file missing), returns the document metadata with
        url=None and an error indicator.

        Args:
            doc: The CVDocument entity to build detail for.

        Returns:
            CVDocumentDetail with presigned URL or error information.
        """
        presigned_url: str | None = None
        url_error: str | None = None

        if doc.file_path:
            try:
                presigned_url = await self._minio_client.generate_presigned_url(doc.file_path)
            except Exception as exc:
                logger.warning(
                    "Failed to generate presigned URL for CV document %s (path: %s): %s",
                    doc.id,
                    doc.file_path,
                    exc,
                )
                url_error = str(exc)

        return CVDocumentDetail(
            id=doc.id,
            original_filename=doc.original_filename,
            mime_type=doc.mime_type,
            size_bytes=doc.size_bytes,
            uploaded_at=doc.uploaded_at,
            presigned_url=presigned_url,
            url_error=url_error,
        )

    async def _create_new_candidate(
        self,
        parsed_cv: ParsedCV,
        source_email_id: UUID | None,
        confidence_score: float,
    ) -> Candidate:
        """Create a new Candidate entity from parsed CV data.

        Sets status to "new" and stores the complete parsed_cv_json.

        Args:
            parsed_cv: Structured CV data.
            source_email_id: UUID of the source email message.
            confidence_score: Confidence score from parsing.

        Returns:
            The newly created Candidate entity.
        """
        candidate = Candidate(
            name=parsed_cv.name.strip(),
            email=parsed_cv.email.strip().lower(),
            phone=parsed_cv.phone or "",
            skills=parsed_cv.skills or [],
            experience=[exp.model_dump() for exp in parsed_cv.experience]
            if parsed_cv.experience
            else [],
            education=[edu.model_dump() for edu in parsed_cv.education]
            if parsed_cv.education
            else [],
            summary=parsed_cv.summary or "",
            parsed_cv_json=parsed_cv.model_dump(),
            status=CandidateStatus.NEW,
            confidence_score=confidence_score,
            source_email_message_id=source_email_id,
        )

        return await self._candidate_repo.create(candidate)

    async def _update_existing_candidate(
        self,
        existing: Candidate,
        parsed_cv: ParsedCV,
        confidence_score: float,
    ) -> Candidate:
        """Update an existing Candidate with new parsed CV data.

        Preserves the existing candidate's status while updating
        all data fields with the latest parsed CV information.

        Args:
            existing: The existing Candidate entity to update.
            parsed_cv: New structured CV data.
            confidence_score: New confidence score.

        Returns:
            The updated Candidate entity.
        """
        existing.name = parsed_cv.name.strip()
        existing.phone = parsed_cv.phone or ""
        existing.skills = parsed_cv.skills or []
        existing.experience = (
            [exp.model_dump() for exp in parsed_cv.experience] if parsed_cv.experience else []
        )
        existing.education = (
            [edu.model_dump() for edu in parsed_cv.education] if parsed_cv.education else []
        )
        existing.summary = parsed_cv.summary or ""
        existing.parsed_cv_json = parsed_cv.model_dump()
        existing.confidence_score = confidence_score
        # Status is intentionally NOT updated (Requirement 5.5)

        return await self._candidate_repo.update(existing)

    async def _link_cv_document(self, cv_document_id: UUID, candidate_id: UUID) -> None:
        """Link a CV document to a candidate by setting candidate_id.

        Args:
            cv_document_id: UUID of the CV document to link.
            candidate_id: UUID of the candidate to link to.
        """
        cv_doc = await self._cv_document_repo.get_by_id(cv_document_id)
        if cv_doc is not None:
            cv_doc.candidate_id = candidate_id
            await self._cv_document_repo.update(cv_doc)

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

    async def _get_candidate_locked_or_raise(self, candidate_id: UUID) -> Candidate:
        """Retrieve a candidate by ID with row-level lock or raise.

        Args:
            candidate_id: The UUID of the candidate.

        Returns:
            The Candidate entity with a lock.

        Raises:
            CandidateNotFoundError: If the candidate doesn't exist.
        """
        candidate = await self._candidate_repo.get_by_id_for_update(candidate_id)
        if candidate is None:
            raise CandidateNotFoundError(f"Candidate not found: {candidate_id}")
        return candidate
