"""FastAPI router for the Recruitment Candidate Pool endpoints.

Defines the /api/recruitment/candidates/* endpoints for candidate
list, detail, CV view, and action operations (schedule interview,
send email, reject, accept, archive).

Requirements: 6.1, 6.7, 7.1, 7.3-7.5, 8.1-8.5, 9.1, 9.3, 9.5,
10.1, 10.4, 10.7-10.8, 11.1, 11.3, 11.5, 12.1, 12.3, 12.5,
13.1, 13.5
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from src.modules.gmail.api.schemas import OutboundEmailResponse as OutboundEmailResponseSchema
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole
from src.modules.recruitment.api.schemas import (
    AssignCandidateRequest,
    CandidateDetailResponse,
    CandidateListItemResponse,
    CandidateListResponse,
    CandidateResponse,
    CreateInterviewRequest,
    CVDocumentResponse,
    CVPresignedUrlResponse,
    InterviewParticipantResponse,
    InterviewResponse,
    ReassignCandidateRequest,
    RejectRequest,
    ScheduleInterviewRequest,
    SendEmailRequest,
)
from src.modules.recruitment.application.candidate_lifecycle_service import (
    CandidateLifecycleService,
    PaginatedCandidates,
)
from src.modules.recruitment.application.candidate_notification_service import (
    CandidateNotificationService,
)
from src.modules.recruitment.application.interview_scheduler_service import (
    InterviewSchedulerService,
)
from src.modules.recruitment.container import (
    get_candidate_lifecycle_service,
    get_candidate_notification_service,
    get_interview_scheduler_service,
)
from src.modules.recruitment.domain.entities import Interview, InterviewParticipant, JobOpening
from src.modules.recruitment.domain.enums import CandidateStatus, ProcessingStatus
from src.modules.recruitment.domain.exceptions import (
    CandidateNotFoundError,
    CVDocumentNotFoundError,
    CVFileNotFoundError,
    StorageServiceUnavailableError,
)
from src.modules.recruitment.infrastructure.minio_client import RecruitmentMinIOClient
from src.modules.recruitment.infrastructure.repositories import (
    CandidateRepository,
    CVDocumentRepository,
)

# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def _get_minio_client() -> RecruitmentMinIOClient:
    """Provide the RecruitmentMinIOClient singleton.

    Returns:
        A RecruitmentMinIOClient configured with recruitment settings.
    """
    from src.modules.recruitment.container import get_recruitment_settings

    settings = get_recruitment_settings()
    return RecruitmentMinIOClient(settings)


# ---------------------------------------------------------------------------
# Type aliases for injected dependencies
# ---------------------------------------------------------------------------

CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def require_hr(current_user: CurrentUserDep) -> User:
    from fastapi import HTTPException

    if current_user.role != UserRole.HR:
        raise HTTPException(status_code=403, detail="HR access required")
    return current_user


HRUserDep = Annotated[User, Depends(require_hr)]
CandidateLifecycleServiceDep = Annotated[
    CandidateLifecycleService, Depends(get_candidate_lifecycle_service)
]
InterviewSchedulerServiceDep = Annotated[
    InterviewSchedulerService, Depends(get_interview_scheduler_service)
]
CandidateNotificationServiceDep = Annotated[
    CandidateNotificationService, Depends(get_candidate_notification_service)
]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

candidate_router = APIRouter(
    prefix="/api/recruitment/candidates",
    tags=["recruitment-candidates"],
)


# ---------------------------------------------------------------------------
# List candidates
# ---------------------------------------------------------------------------


@candidate_router.get("", response_model=CandidateListResponse)
async def list_candidates(
    current_user: CurrentUserDep,
    candidate_service: CandidateLifecycleServiceDep,
    session: AsyncSession = Depends(get_db_session),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    search: str | None = Query(
        default=None, min_length=1, max_length=200, description="Search query"
    ),
    status: list[CandidateStatus] | None = Query(default=None, description="Filter by status"),
    from_date: date | None = Query(default=None, description="Filter from date"),
    to_date: date | None = Query(default=None, description="Filter to date"),
    min_confidence: float | None = Query(
        default=None, ge=0.0, le=1.0, description="Minimum confidence score"
    ),
    skills: str | None = Query(default=None, description="Comma-separated skills filter"),
) -> CandidateListResponse:
    """List candidates with pagination, search, and filters.

    Returns a paginated list of candidates sorted by created_at descending.
    Archived candidates are excluded unless explicitly filtered by status.

    Args:
        current_user: The authenticated user.
        candidate_service: The candidate service.
        page: Page number (1-indexed).
        page_size: Number of items per page (1–100).
        search: Optional search query for name/email/phone/skills.
        status: Optional status filter (one or more values).
        from_date: Optional start date for created_at range.
        to_date: Optional end date for created_at range.
        min_confidence: Optional minimum confidence score (0.0–1.0).
        skills: Optional comma-separated skills filter.

    Returns:
        Paginated list of candidates with total count.
    """
    # Parse skills filter from comma-separated string
    skills_list: list[str] | None = None
    if skills:
        skills_list = [s.strip() for s in skills.split(",") if s.strip()]

    # Convert status enum list to string list for the service
    status_list: list[str] | None = None
    if status:
        status_list = [s.value for s in status]

    # Convert dates to datetime for the service
    date_from: datetime | None = None
    date_to: datetime | None = None
    if from_date:
        date_from = datetime(from_date.year, from_date.month, from_date.day)
    if to_date:
        date_to = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59)

    result: PaginatedCandidates = await candidate_service.list_candidates(
        status=status_list,
        date_from=date_from,
        date_to=date_to,
        min_confidence=min_confidence,
        skills=skills_list,
        search=search,
        page=page,
        page_size=page_size,
    )

    # Build response items with skills truncated to first 5
    # Resolve Job Opening titles for list items
    jo_ids = list({c.job_opening_id for c in result.candidates if c.job_opening_id})
    jo_titles: dict[UUID, str] = {}
    if jo_ids:
        stmt = select(JobOpening).where(col(JobOpening.id).in_(jo_ids))
        jo_result = await session.execute(stmt)
        for jo in jo_result.scalars().all():
            jo_titles[jo.id] = jo.title

    items = [
        CandidateListItemResponse(
            id=c.id,
            name=c.name,
            email=c.email,
            phone=c.phone or "",
            skills=(c.skills or [])[:5],
            status=CandidateStatus(c.status),
            confidence_score=c.confidence_score,
            created_at=c.created_at,
            has_cv=True,  # Candidates are created from CVs
            job_opening_id=c.job_opening_id,
            job_opening_title=jo_titles.get(c.job_opening_id, "") if c.job_opening_id else "",
        )
        for c in result.candidates
    ]

    return CandidateListResponse(
        candidates=items,
        total_count=result.total_count,
        page=result.page,
        page_size=result.page_size,
    )


# ---------------------------------------------------------------------------
# Get candidate detail
# ---------------------------------------------------------------------------


@candidate_router.get("/{candidate_id}", response_model=CandidateDetailResponse)
async def get_candidate(
    candidate_id: UUID,
    current_user: CurrentUserDep,
    candidate_service: CandidateLifecycleServiceDep,
    session: AsyncSession = Depends(get_db_session),
) -> CandidateDetailResponse:
    """Get full candidate detail with linked CV documents.

    Returns the complete candidate record including all CV documents
    with presigned download URLs.

    Args:
        candidate_id: UUID of the candidate.
        current_user: The authenticated user.
        candidate_service: The candidate service.

    Returns:
        Full candidate detail with CV documents.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
    """
    detail = await candidate_service.get_candidate(candidate_id)

    cv_docs = [
        CVDocumentResponse(
            id=doc.id,
            original_filename=doc.original_filename,
            mime_type=doc.mime_type,
            size_bytes=doc.size_bytes,
            uploaded_at=doc.uploaded_at,
            presigned_url=doc.presigned_url,
            processing_status=ProcessingStatus.COMPLETED,
        )
        for doc in detail.cv_documents
    ]

    candidate = detail.candidate

    # Resolve Job Opening title
    job_opening_title = ""
    if candidate.job_opening_id:
        jo_stmt = select(JobOpening).where(JobOpening.id == candidate.job_opening_id)
        jo_result = await session.execute(jo_stmt)
        jo = jo_result.scalars().first()
        if jo:
            job_opening_title = jo.title

    # Fetch interviews for this candidate
    interviews_stmt = select(Interview).where(Interview.candidate_id == candidate.id)
    interviews_res = await session.execute(interviews_stmt)
    interviews_list = interviews_res.scalars().all()

    interview_responses = []
    for iv in interviews_list:
        parts_stmt = select(InterviewParticipant).where(InterviewParticipant.interview_id == iv.id)
        parts_res = await session.execute(parts_stmt)
        parts_list = parts_res.scalars().all()

        part_responses = [
            InterviewParticipantResponse(
                id=p.id,
                interview_id=p.interview_id,
                type=p.type,
                email=p.email,
                name=p.name,
                employee_id=p.employee_id,
            )
            for p in parts_list
        ]

        interview_responses.append(
            InterviewResponse(
                id=iv.id,
                candidate_id=iv.candidate_id,
                status=iv.status,
                round_name=iv.round_name,
                start_at=iv.start_at,
                end_at=iv.end_at,
                timezone=iv.timezone,
                calendar_event_id=iv.calendar_event_id,
                needs_relink=iv.needs_relink,
                participants=part_responses,
            )
        )

    return CandidateDetailResponse(
        id=candidate.id,
        name=candidate.name,
        email=candidate.email,
        phone=candidate.phone or "",
        skills=candidate.skills or [],
        experience=candidate.experience or [],
        education=candidate.education or [],
        summary=candidate.summary or "",
        status=CandidateStatus(candidate.status),
        confidence_score=candidate.confidence_score,
        source_email_message_id=candidate.source_email_message_id,
        rejection_reason=candidate.rejection_reason,
        rejected_at=candidate.rejected_at,
        accepted_at=candidate.accepted_at,
        archived_at=candidate.archived_at,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        job_opening_id=candidate.job_opening_id,
        job_opening_title=job_opening_title,
        cv_documents=cv_docs,
        interviews=interview_responses,
    )


# ---------------------------------------------------------------------------
# View CV presigned URL
# ---------------------------------------------------------------------------


@candidate_router.get(
    "/{candidate_id}/cv/{document_id}",
    response_model=CVPresignedUrlResponse,
)
async def get_cv_presigned_url(
    candidate_id: UUID,
    document_id: UUID,
    current_user: CurrentUserDep,
    session: AsyncSession = Depends(get_db_session),
) -> CVPresignedUrlResponse:
    """Get a presigned URL for downloading a candidate's CV document.

    Returns a presigned MinIO URL valid for 15 minutes along with
    file metadata.

    Args:
        candidate_id: UUID of the candidate.
        document_id: UUID of the CV document.
        current_user: The authenticated user.
        session: The async database session.

    Returns:
        Presigned URL and file metadata.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
        CVDocumentNotFoundError: If the document doesn't exist or
            doesn't belong to the candidate.
        CVFileNotFoundError: If the file is missing from MinIO.
        StorageServiceUnavailableError: If MinIO is unreachable.
    """
    # Verify candidate exists
    candidate_repo = CandidateRepository(session)
    candidate = await candidate_repo.get_by_id(candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate not found: {candidate_id}")

    # Verify document exists and belongs to candidate
    cv_doc_repo = CVDocumentRepository(session)
    cv_doc = await cv_doc_repo.get_by_id(document_id)
    if cv_doc is None or cv_doc.candidate_id != candidate_id:
        raise CVDocumentNotFoundError(
            f"CV document not found or does not belong to candidate: {document_id}"
        )

    # Generate presigned URL
    minio_client = _get_minio_client()
    try:
        presigned_url = await minio_client.generate_presigned_url(cv_doc.file_path)
    except CVFileNotFoundError:
        raise
    except StorageServiceUnavailableError:
        raise
    except Exception as exc:
        raise StorageServiceUnavailableError(f"Failed to generate presigned URL: {exc}")

    return CVPresignedUrlResponse(
        presigned_url=presigned_url,
        filename=cv_doc.original_filename,
        mime_type=cv_doc.mime_type,
        size_bytes=cv_doc.size_bytes,
    )


# ---------------------------------------------------------------------------
# Schedule interview
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/schedule-interview",
    response_model=CandidateResponse,
)
async def schedule_interview(
    candidate_id: UUID,
    body: ScheduleInterviewRequest,
    current_user: CurrentUserDep,
    candidate_service: InterviewSchedulerServiceDep,
) -> CandidateResponse:
    """Schedule an interview for a candidate.

    Validates the status transition and interviewer IDs, then updates
    the candidate status to interview_scheduled.

    Args:
        candidate_id: UUID of the candidate.
        body: Interview scheduling parameters.
        current_user: The authenticated user.
        candidate_service: The candidate service.

    Returns:
        The updated candidate record.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
        InvalidStatusTransitionError: If the transition is not allowed.
        ValueError: If interviewer IDs are invalid.
    """
    candidate = await candidate_service.schedule_interview(
        candidate_id,
        start=body.start,
        duration_minutes=body.duration_minutes,
        interviewer_ids=body.interviewer_ids,
        notes=body.notes,
    )

    return CandidateResponse.model_validate(candidate)


# ---------------------------------------------------------------------------
# Reschedule interview
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/reschedule-interview",
    response_model=CandidateResponse,
)
async def reschedule_interview(
    candidate_id: UUID,
    body: ScheduleInterviewRequest,
    current_user: CurrentUserDep,
    candidate_service: InterviewSchedulerServiceDep,
) -> CandidateResponse:
    """Reschedule an existing interview for a candidate.

    Patches the existing Google Calendar event in place with the new time
    window, preserving the Meet link. Requires the candidate to already have
    a scheduled interview.

    Args:
        candidate_id: UUID of the candidate.
        body: Interview scheduling parameters (new start, duration, etc.).
        current_user: The authenticated user.
        candidate_service: The candidate service.

    Returns:
        The updated candidate record.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
        NoInterviewToRescheduleError: If no interview exists to reschedule.
        ValueError: If interviewer IDs are invalid.
    """
    candidate = await candidate_service.reschedule_interview(
        candidate_id,
        start=body.start,
        duration_minutes=body.duration_minutes,
        interviewer_ids=body.interviewer_ids,
        notes=body.notes,
    )

    return CandidateResponse.model_validate(candidate)

    # ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Create interview (GH #154)
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/create-interview",
    status_code=201,
    response_model=InterviewResponse,
)
async def create_interview(
    candidate_id: UUID,
    body: CreateInterviewRequest,
    current_user: HRUserDep,
    candidate_service: InterviewSchedulerServiceDep,
) -> InterviewResponse:
    """Create a new interview with a Calendar event on the selected calendar.

    This is the GH #154 interview creation command. It creates an Interview
    record with the specified round name, timezone, meeting mode, participants,
    and a Calendar event. The Candidate status is NOT changed by this command --
    the HR explicitly transitions the candidate separately.

    Args:
        candidate_id: UUID of the candidate.
        body: Interview creation parameters.
        current_user: The authenticated admin user.
        candidate_service: The candidate service.

    Returns:
        The created Interview record.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
        CalendarEventCreateFailedError: If Calendar event creation fails.
    """
    interview = await candidate_service.create_interview(
        candidate_id,
        round_name=body.round_name,
        start=body.start,
        end=body.end,
        timezone=body.timezone,
        mode=body.mode,
        meeting_link=body.meeting_link,
        interviewer_ids=body.interviewer_ids,
        external_participant_emails=body.external_participant_emails,
        notes=body.notes,
    )

    # Fetch participants for the response
    from sqlmodel import select

    parts_stmt = select(InterviewParticipant).where(
        InterviewParticipant.interview_id == interview.id
    )
    parts_res = await candidate_service._session.execute(parts_stmt)
    parts_list = parts_res.scalars().all()
    part_responses = [
        InterviewParticipantResponse(
            id=p.id,
            interview_id=p.interview_id,
            type=p.type,
            email=p.email,
            name=p.name,
            employee_id=p.employee_id,
        )
        for p in parts_list
    ]

    return InterviewResponse(
        id=interview.id,
        candidate_id=interview.candidate_id,
        status=interview.status,
        round_name=interview.round_name,
        start_at=interview.start_at,
        end_at=interview.end_at,
        timezone=interview.timezone,
        calendar_event_id=interview.calendar_event_id,
        needs_relink=interview.needs_relink,
        participants=part_responses,
    )


# ---------------------------------------------------------------------------
# Complete interview (GH #155)
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/interviews/{interview_id}/complete",
    response_model=InterviewResponse,
)
async def complete_interview(
    candidate_id: UUID,
    interview_id: UUID,
    current_user: HRUserDep,
    candidate_service: InterviewSchedulerServiceDep,
) -> InterviewResponse:
    """Mark an Interview as completed.

    Transitions the Interview from 'scheduled' to 'completed'.
    Does NOT change the Candidate status.

    Args:
        candidate_id: UUID of the candidate.
        interview_id: UUID of the Interview to complete.
        current_user: The authenticated admin user.
        candidate_service: The candidate service.

    Returns:
        The updated Interview record.

    Raises:
        InterviewNotFoundError: If the Interview doesn't exist.
        InterviewStatusTransitionError: If the Interview is not scheduled.
    """
    interview = await candidate_service.complete_interview(interview_id)

    # Fetch participants
    parts_stmt = select(InterviewParticipant).where(
        InterviewParticipant.interview_id == interview.id
    )
    parts_res = await candidate_service._session.execute(parts_stmt)
    parts_list = parts_res.scalars().all()
    part_responses = [
        InterviewParticipantResponse(
            id=p.id,
            interview_id=p.interview_id,
            type=p.type,
            email=p.email,
            name=p.name,
            employee_id=p.employee_id,
        )
        for p in parts_list
    ]

    return InterviewResponse(
        id=interview.id,
        candidate_id=interview.candidate_id,
        status=interview.status,
        round_name=interview.round_name,
        start_at=interview.start_at,
        end_at=interview.end_at,
        timezone=interview.timezone,
        calendar_event_id=interview.calendar_event_id,
        needs_relink=interview.needs_relink,
        participants=part_responses,
    )


# ---------------------------------------------------------------------------
# Cancel interview (GH #155)
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/interviews/{interview_id}/cancel",
    response_model=InterviewResponse,
)
async def cancel_interview(
    candidate_id: UUID,
    interview_id: UUID,
    current_user: HRUserDep,
    candidate_service: InterviewSchedulerServiceDep,
) -> InterviewResponse:
    """Cancel an Interview and send Calendar cancellation.

    Transitions the Interview from 'scheduled' to 'cancelled'.
    Sends sendUpdates=all Calendar cancellation. Does NOT change
    the Candidate status.

    Args:
        candidate_id: UUID of the candidate.
        interview_id: UUID of the Interview to cancel.
        current_user: The authenticated admin user.
        candidate_service: The candidate service.

    Returns:
        The updated Interview record.

    Raises:
        InterviewNotFoundError: If the Interview doesn't exist.
        InterviewStatusTransitionError: If the Interview is not scheduled.
    """
    interview = await candidate_service.cancel_interview(interview_id)

    # Fetch participants
    parts_stmt = select(InterviewParticipant).where(
        InterviewParticipant.interview_id == interview.id
    )
    parts_res = await candidate_service._session.execute(parts_stmt)
    parts_list = parts_res.scalars().all()
    part_responses = [
        InterviewParticipantResponse(
            id=p.id,
            interview_id=p.interview_id,
            type=p.type,
            email=p.email,
            name=p.name,
            employee_id=p.employee_id,
        )
        for p in parts_list
    ]

    return InterviewResponse(
        id=interview.id,
        candidate_id=interview.candidate_id,
        status=interview.status,
        round_name=interview.round_name,
        start_at=interview.start_at,
        end_at=interview.end_at,
        timezone=interview.timezone,
        calendar_event_id=interview.calendar_event_id,
        needs_relink=interview.needs_relink,
        participants=part_responses,
    )


# ---------------------------------------------------------------------------
# Create replacement interview (GH #155)
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/interviews/{interview_id}/replacement",
    status_code=201,
    response_model=InterviewResponse,
)
async def create_replacement_interview(
    candidate_id: UUID,
    interview_id: UUID,
    body: CreateInterviewRequest,
    current_user: HRUserDep,
    candidate_service: InterviewSchedulerServiceDep,
) -> InterviewResponse:
    """Create a replacement Interview after cancellation.

    Creates a new Interview record with a new Calendar event,
    keeping the old (cancelled) Interview in history.

    Args:
        candidate_id: UUID of the candidate.
        interview_id: UUID of the cancelled Interview.
        body: Interview creation parameters.
        current_user: The authenticated admin user.
        candidate_service: The candidate service.

    Returns:
        The new Interview record.

    Raises:
        InterviewNotFoundError: If the cancelled Interview doesn't exist.
        InterviewStatusTransitionError: If the source is not cancelled.
    """
    interview = await candidate_service.create_replacement_interview(
        interview_id,
        round_name=body.round_name,
        start=body.start,
        end=body.end,
        timezone=body.timezone,
        mode=body.mode,
        meeting_link=body.meeting_link,
        interviewer_ids=body.interviewer_ids,
        external_participant_emails=body.external_participant_emails,
        notes=body.notes,
    )

    # Fetch participants
    parts_stmt = select(InterviewParticipant).where(
        InterviewParticipant.interview_id == interview.id
    )
    parts_res = await candidate_service._session.execute(parts_stmt)
    parts_list = parts_res.scalars().all()
    part_responses = [
        InterviewParticipantResponse(
            id=p.id,
            interview_id=p.interview_id,
            type=p.type,
            email=p.email,
            name=p.name,
            employee_id=p.employee_id,
        )
        for p in parts_list
    ]

    return InterviewResponse(
        id=interview.id,
        candidate_id=interview.candidate_id,
        status=interview.status,
        round_name=interview.round_name,
        start_at=interview.start_at,
        end_at=interview.end_at,
        timezone=interview.timezone,
        calendar_event_id=interview.calendar_event_id,
        needs_relink=interview.needs_relink,
        participants=part_responses,
    )


# Send email
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/send-email",
    status_code=201,
    response_model=OutboundEmailResponseSchema,
)
async def send_email(
    candidate_id: UUID,
    body: SendEmailRequest,
    current_user: CurrentUserDep,
    candidate_service: CandidateNotificationServiceDep,
    session: AsyncSession = Depends(get_db_session),
) -> OutboundEmailResponseSchema:
    """Create an outbound email command for a candidate.

    Instead of sending directly, creates an OutboundEmail record
    with pending status. A background process sends it using the
    Organization Google Connection.

    Args:
        candidate_id: UUID of the candidate.
        body: Email parameters (subject, body_html).
        current_user: The authenticated user.
        candidate_service: The candidate service.
        session: The async database session.

    Returns:
        The created OutboundEmail record.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
        ValueError: If the candidate's email is invalid.
    """

    from src.modules.gmail.container import build_outbound_email_service
    from src.modules.recruitment.infrastructure.repositories import CandidateRepository

    candidate_repo = CandidateRepository(session)
    candidate = await candidate_repo.get_by_id(candidate_id)
    if candidate is None:
        raise CandidateNotFoundError(f"Candidate not found: {candidate_id}")

    # Validate candidate email
    if not candidate.email or not candidate.email.strip():
        raise ValueError(f"Candidate email is empty or invalid: '{candidate.email}'")
    email = candidate.email.strip()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError(f"Candidate email is invalid: '{candidate.email}'")

    outbound_service = await build_outbound_email_service(session)
    outbound = await outbound_service.create_outbound(
        candidate_id=candidate_id,
        recipient_email=email,
        subject=body.subject,
        body_html=body.body_html,
        created_by_user_id=current_user.id,
        hr_user=current_user,
    )

    await session.commit()

    return OutboundEmailResponseSchema.model_validate(outbound)


@candidate_router.post(
    "/{candidate_id}/reject",
    response_model=CandidateResponse,
)
async def reject_candidate(
    candidate_id: UUID,
    current_user: CurrentUserDep,
    candidate_service: CandidateLifecycleServiceDep,
    body: RejectRequest | None = None,
) -> CandidateResponse:
    """Reject a candidate.

    Updates the candidate status to rejected and stores the optional
    rejection reason.

    Args:
        candidate_id: UUID of the candidate.
        current_user: The authenticated user.
        candidate_service: The candidate service.
        body: Optional rejection reason.

    Returns:
        The updated candidate record.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
        InvalidStatusTransitionError: If the transition is not allowed.
    """
    reason = body.reason if body else None
    candidate = await candidate_service.reject_candidate(
        candidate_id=candidate_id,
        reason=reason,
    )

    return CandidateResponse.model_validate(candidate)


# ---------------------------------------------------------------------------
# Accept candidate
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/accept",
    response_model=CandidateResponse,
)
async def accept_candidate(
    candidate_id: UUID,
    current_user: CurrentUserDep,
    candidate_service: CandidateLifecycleServiceDep,
) -> CandidateResponse:
    """Accept a candidate (pass interview).

    Updates the candidate status to accepted. Only allowed from
    interview_scheduled or reviewing status.

    Args:
        candidate_id: UUID of the candidate.
        current_user: The authenticated user.
        candidate_service: The candidate service.

    Returns:
        The updated candidate record.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
        InvalidStatusTransitionError: If the transition is not allowed.
    """
    candidate = await candidate_service.accept_candidate(candidate_id)
    return CandidateResponse.model_validate(candidate)


# ---------------------------------------------------------------------------
# Archive candidate
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/archive",
    response_model=CandidateResponse,
)
async def archive_candidate(
    candidate_id: UUID,
    current_user: CurrentUserDep,
    candidate_service: CandidateLifecycleServiceDep,
) -> CandidateResponse:
    """Archive a candidate.

    Updates the candidate status to archived. Idempotent for
    already-archived candidates. Not allowed from accepted status.

    Args:
        candidate_id: UUID of the candidate.
        current_user: The authenticated user.
        candidate_service: The candidate service.

    Returns:
        The updated candidate record.

    Raises:
        CandidateNotFoundError: If the candidate doesn't exist.
        InvalidStatusTransitionError: If the transition is not allowed.
    """
    candidate = await candidate_service.archive_candidate(candidate_id)
    return CandidateResponse.model_validate(candidate)


# ---------------------------------------------------------------------------
# Assign candidate to Job Opening
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/assign",
    response_model=CandidateResponse,
)
async def assign_candidate(
    candidate_id: UUID,
    body: AssignCandidateRequest,
    current_user: HRUserDep,
    candidate_service: CandidateLifecycleServiceDep,
) -> CandidateResponse:
    """Assign an unassigned Candidate to an open Job Opening.

    Candidate must not already be assigned. Candidate status must be
    new, reviewing, or interview_scheduled. Job Opening must be open.

    Args:
        candidate_id: UUID of the candidate.
        body: AssignCandidateRequest with the target job_opening_id.
        current_user: The authenticated user.
        candidate_service: The candidate service.

    Returns:
        The updated candidate record.
    """
    candidate = await candidate_service.assign_candidate(
        candidate_id=candidate_id,
        job_opening_id=body.job_opening_id,
    )
    return CandidateResponse.model_validate(candidate)


# ---------------------------------------------------------------------------
# Reassign candidate to different Job Opening
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/reassign",
    response_model=CandidateResponse,
)
async def reassign_candidate(
    candidate_id: UUID,
    body: ReassignCandidateRequest,
    current_user: HRUserDep,
    candidate_service: CandidateLifecycleServiceDep,
) -> CandidateResponse:
    """Reassign a Candidate to a different open Job Opening.

    Candidate must already be assigned. Candidate status must be
    new, reviewing, or interview_scheduled. New Job Opening must be open.

    Args:
        candidate_id: UUID of the candidate.
        body: ReassignCandidateRequest with the new job_opening_id.
        current_user: The authenticated user.
        candidate_service: The candidate service.

    Returns:
        The updated candidate record.
    """
    candidate = await candidate_service.reassign_candidate(
        candidate_id=candidate_id,
        new_job_opening_id=body.job_opening_id,
    )
    return CandidateResponse.model_validate(candidate)


# ---------------------------------------------------------------------------
# Unassign candidate from Job Opening
# ---------------------------------------------------------------------------


@candidate_router.post(
    "/{candidate_id}/unassign",
    response_model=CandidateResponse,
)
async def unassign_candidate(
    candidate_id: UUID,
    current_user: HRUserDep,
    candidate_service: CandidateLifecycleServiceDep,
) -> CandidateResponse:
    """Remove a Candidate's assignment to a Job Opening.

    Candidate must currently be assigned. Candidate status must be
    new, reviewing, or interview_scheduled.

    Args:
        candidate_id: UUID of the candidate.
        current_user: The authenticated user.
        candidate_service: The candidate service.

    Returns:
        The updated candidate record.
    """
    candidate = await candidate_service.unassign_candidate(candidate_id)
    return CandidateResponse.model_validate(candidate)
