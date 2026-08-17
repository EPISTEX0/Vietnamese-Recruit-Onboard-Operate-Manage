"""Pydantic v2 request/response schemas for the Recruitment CV Pipeline API.

Defines data transfer objects used by the recruitment router endpoints
for structured data validation and serialization.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.modules.recruitment.domain.enums import (
    ApplicationSource,
    CalendarConflictStatus,
    CandidateStatus,
    JobOpeningStatus,
    LinkProposalStatus,
    ProcessingStatus,
)
from src.modules.recruitment.domain.value_objects import EducationItem, ExperienceItem

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CandidateListParams(BaseModel):
    """Query parameters for listing candidates with pagination, search, and filters.

    Attributes:
        page: Page number (1-indexed).
        page_size: Number of items per page (1–100).
        search: Free-text search across name, email, phone, skills.
        status: Filter by one or more candidate statuses.
        from_date: Filter candidates created on or after this date.
        to_date: Filter candidates created on or before this date.
        min_confidence: Minimum confidence score filter (0.0–1.0).
        skills: Comma-separated skills filter (OR logic).
    """

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    search: str | None = Field(default=None, min_length=1, max_length=200)
    status: list[CandidateStatus] | None = None
    from_date: date | None = None
    to_date: date | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    skills: str | None = None  # comma-separated


class ScheduleInterviewRequest(BaseModel):
    """Request schema for scheduling an interview with a candidate.

    Carries the ADR-0008 contract: an exact ``start`` datetime plus a
    ``duration_minutes`` value (replacing the previous loose ``date``/``time``
    fields) so the Calendar event reflects a precise, unambiguous meeting time.

    Attributes:
        start: Interview start datetime. Future-validation happens in the
            service against the current time (not a Pydantic validator).
        duration_minutes: Duration in minutes (15–180).
        interviewer_ids: List of employee UUIDs conducting the interview (1–10).
        notes: Optional notes for the interview (max 1000 chars).
    """

    start: datetime
    duration_minutes: int = Field(ge=15, le=180)
    interviewer_ids: list[UUID] = Field(min_length=1, max_length=10)
    notes: str | None = Field(default=None, max_length=1000)


class CreateInterviewRequest(BaseModel):
    """Request schema for creating a new interview with calendar event.

    This is the GH #154 interview create command, separate from the legacy
    ``ScheduleInterviewRequest``. Supports round name, IANA timezone, meeting
    mode, employee and external participants.

    Attributes:
        round_name: Name/round of the interview (e.g. "Technical Round 1").
        start: Interview start datetime (timezone-aware preferred).
        end: Interview end datetime (timezone-aware).
        timezone: IANA timezone name (e.g. "Asia/Ho_Chi_Minh").
        mode: Meeting mode (google_meet, in_person, custom_link).
        meeting_link: Custom meeting link for custom_link mode.
        interviewer_ids: Employee UUIDs conducting the interview.
        external_participant_emails: External participant email addresses.
        notes: Optional notes for the interview.
    """

    round_name: str = Field(min_length=1, max_length=255)
    start: datetime
    end: datetime
    timezone: str = Field(default="Asia/Ho_Chi_Minh", max_length=64)
    mode: str = Field(default="google_meet", max_length=20)
    meeting_link: str | None = Field(default=None, max_length=1024)
    interviewer_ids: list[UUID] = Field(default_factory=list, max_length=20)
    external_participant_emails: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=1000)


class SendEmailRequest(BaseModel):
    """Request schema for sending an email to a candidate.

    Attributes:
        subject: Email subject line (1–500 characters).
        body_html: HTML body content (max 100,000 characters).
        template_name: Optional email template name to use instead of body_html.
    """

    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(max_length=100_000)
    template_name: str | None = Field(default=None, max_length=100)


class RejectRequest(BaseModel):
    """Request schema for rejecting a candidate.

    Attributes:
        reason: Optional rejection reason (max 1000 characters).
    """

    reason: str | None = Field(default=None, max_length=1000)


class ParsedCVInput(BaseModel):
    """Request schema for submitting corrected CV data during manual review.

    Attributes:
        name: Candidate name (1–200 characters).
        email: Candidate email (valid format, max 254 characters).
        phone: Phone number (optional, max 20 characters).
        skills: List of skill strings (max 50 items).
        experience: List of experience entries (max 20 items).
        education: List of education entries (max 10 items).
        summary: Brief candidate summary (max 500 characters).
    """

    name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=254)
    phone: str = Field(default="", max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=50)
    experience: list[ExperienceItem] = Field(default_factory=list, max_length=20)
    education: list[EducationItem] = Field(default_factory=list, max_length=10)
    summary: str = Field(default="", max_length=500)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CVDocumentResponse(BaseModel):
    """Response schema for a CV document linked to a candidate.

    Attributes:
        id: Unique document identifier.
        original_filename: Original uploaded filename.
        mime_type: MIME type of the file.
        size_bytes: File size in bytes.
        uploaded_at: When the file was uploaded.
        presigned_url: Pre-signed MinIO download URL (valid 15 min), or None.
        processing_status: Current processing status of the document.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    presigned_url: str | None = None
    processing_status: ProcessingStatus


class CandidateListItemResponse(BaseModel):
    """Response schema for a single candidate in the list view.

    Attributes:
        id: Candidate UUID.
        name: Candidate full name.
        email: Candidate email address.
        phone: Candidate phone number.
        skills: Up to the first 5 skills.
        status: Current lifecycle status.
        confidence_score: Parse confidence score (0.0–1.0).
        created_at: When the candidate was created.
        has_cv: Whether the candidate has at least one CV document.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    phone: str = ""
    skills: list[str] = Field(default_factory=list)
    status: CandidateStatus
    confidence_score: float = 0.0
    created_at: datetime
    has_cv: bool = False
    job_opening_id: UUID | None = None
    job_opening_title: str = ""


class CandidateListResponse(BaseModel):
    """Paginated response for the candidate list endpoint.

    Attributes:
        candidates: List of candidate records for the current page.
        total_count: Total number of matching candidates.
        page: Current page number (1-indexed).
        page_size: Number of items per page.
    """

    candidates: list[CandidateListItemResponse]
    total_count: int
    page: int
    page_size: int


class InterviewParticipantResponse(BaseModel):
    """Response schema for an interview participant."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    interview_id: UUID
    type: str
    email: str
    name: str | None = None
    employee_id: UUID | None = None


class InterviewResponse(BaseModel):
    """Response schema for an interview."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    status: str
    round_name: str
    start_at: datetime
    end_at: datetime
    timezone: str
    calendar_event_id: str | None = None
    needs_relink: bool = False
    participants: list[InterviewParticipantResponse] = Field(default_factory=list)


class CandidateDetailResponse(BaseModel):
    """Full candidate detail response including CV documents.

    Attributes:
        id: Candidate UUID.
        name: Candidate full name.
        email: Candidate email address.
        phone: Candidate phone number.
        skills: Full list of skills.
        experience: List of experience objects.
        education: List of education objects.
        summary: Candidate summary.
        status: Current lifecycle status.
        confidence_score: Confidence score (0.0–1.0).
        source_email_message_id: UUID of the source email, if available.
        rejection_reason: Rejection reason if rejected.
        rejected_at: Rejection timestamp.
        accepted_at: Acceptance timestamp.
        archived_at: Archive timestamp.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        cv_documents: List of linked CV documents with presigned URLs.
        interviews: List of interviews for the candidate.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    phone: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[dict[str, Any]] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    status: CandidateStatus
    confidence_score: float = 0.0
    source_email_message_id: UUID | None = None
    rejection_reason: str | None = None
    rejected_at: datetime | None = None
    accepted_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    job_opening_id: UUID | None = None
    job_opening_title: str = ""
    cv_documents: list[CVDocumentResponse] = Field(default_factory=list)
    interviews: list[InterviewResponse] = Field(default_factory=list)


class CVReviewItemResponse(BaseModel):
    """Response schema for a CV document in the review queue.

    Attributes:
        id: CV document identifier.
        original_filename: Original uploaded filename.
        mime_type: MIME type of the file.
        size_bytes: File size in bytes.
        uploaded_at: When the file was uploaded.
        processing_status: Current processing status.
        processing_error: Error message if processing failed.
        validation_errors: List of validation error details.
        ocr_output: Extracted OCR text (if available).
        parsed_cv_data: Partial parsed CV data (if available).
        presigned_url: Pre-signed MinIO download URL, or None.
        candidate_id: Linked candidate ID, if any.
        gmail_message_id: Source Gmail message ID.
        created_at: When the document was created.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    processing_status: ProcessingStatus
    processing_error: str | None = None
    validation_errors: list[dict[str, Any]] | None = None
    ocr_output: str | None = None
    parsed_cv_data: dict[str, Any] | None = None
    presigned_url: str | None = None
    candidate_id: UUID | None = None
    gmail_message_id: str
    created_at: datetime


class MetricsResponse(BaseModel):
    """Response schema for CV processing pipeline metrics.

    Attributes:
        average_processing_time_ms: Average time to process a CV in milliseconds.
        success_rate: Ratio of successfully processed CVs (0.0–1.0).
        failure_rate: Ratio of failed CV processing attempts (0.0–1.0).
        queue_depth: Number of CVs currently pending processing.
    """

    average_processing_time_ms: float
    success_rate: float
    failure_rate: float
    queue_depth: int


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper.

    Attributes:
        items: List of items for the current page.
        total_count: Total number of matching items.
        page: Current page number (1-indexed).
        page_size: Number of items per page.
    """

    items: list[dict[str, Any]]
    total_count: int
    page: int
    page_size: int


class CandidateResponse(BaseModel):
    """Response schema for a single candidate (used by action endpoints).

    Attributes:
        id: Candidate UUID.
        name: Candidate full name.
        email: Candidate email address.
        phone: Candidate phone number.
        skills: List of skills.
        status: Current lifecycle status.
        confidence_score: Confidence score (0.0-1.0).
        rejection_reason: Rejection reason if rejected.
        rejected_at: Rejection timestamp.
        accepted_at: Acceptance timestamp.
        archived_at: Archive timestamp.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    phone: str = ""
    skills: list[str] = Field(default_factory=list)
    status: CandidateStatus
    confidence_score: float = 0.0
    rejection_reason: str | None = None
    rejected_at: datetime | None = None
    accepted_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    job_opening_id: UUID | None = None


class CVPresignedUrlResponse(BaseModel):
    """Response schema for a CV presigned URL request.

    Attributes:
        presigned_url: Presigned MinIO download URL (valid 15 minutes).
        filename: Original filename.
        mime_type: MIME type of the file.
        size_bytes: File size in bytes.
    """

    presigned_url: str
    filename: str
    mime_type: str
    size_bytes: int


# ---------------------------------------------------------------------------
# Candidate Job Opening Assignment schemas
# ---------------------------------------------------------------------------


class AssignCandidateRequest(BaseModel):
    """Request schema for assigning a Candidate to a Job Opening.

    Attributes:
        job_opening_id: UUID of the target Job Opening (required).
    """

    job_opening_id: UUID


class ReassignCandidateRequest(BaseModel):
    """Request schema for reassigning a Candidate to a different Job Opening.

    Attributes:
        job_opening_id: UUID of the new target Job Opening (required).
    """

    job_opening_id: UUID


# ---------------------------------------------------------------------------
# Job Opening schemas
# ---------------------------------------------------------------------------


class JobOpeningCreate(BaseModel):
    """Request schema for creating a Job Opening.

    Attributes:
        title: Title for the Job Opening (required, 1-255 chars).
        position_id: UUID of the Position (required).
        target_headcount: Number of people to hire (required, >= 1).
        description: Optional description (max 5000 chars).
        status: Initial status (default: draft).
    """

    title: str = Field(min_length=1, max_length=255)
    position_id: UUID
    target_headcount: int = Field(ge=1)
    description: str = Field(default="", max_length=5000)
    status: JobOpeningStatus = Field(default=JobOpeningStatus.DRAFT, description="Initial status")


class JobOpeningUpdate(BaseModel):
    """Request schema for updating a Job Opening's editable fields.

    Attributes:
        title: New title (optional, 1-255 chars).
        description: New description (optional, max 5000 chars).
        target_headcount: New target headcount (optional, >= 1).
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    target_headcount: int | None = Field(default=None, ge=1)


class JobOpeningStatusTransition(BaseModel):
    """Request schema for Job Opening status transitions.

    No fields needed - the action is determined by the HTTP method/endpoint.
    """

    pass


class JobOpeningResponse(BaseModel):
    """Response schema for a Job Opening with headcount reporting.

    Attributes:
        id: Job Opening UUID.
        title: Job Opening title.
        description: Job Opening description.
        position_id: UUID of the Position.
        target_headcount: Target number of hires.
        status: Current lifecycle status.
        opened_at: Timestamp when opened (None if not open).
        closed_at: Timestamp when closed (None if not closed).
        cancelled_at: Timestamp when cancelled (None if not cancelled).
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        candidate_counts: Per-status candidate counts for this Job Opening.
        position_name: Display name of the Position.
        accepted_count: Number of accepted candidates.
        filled: True if accepted_count >= target_headcount.
        overfilled: True if accepted_count > target_headcount.
        remaining_headcount: target_headcount - accepted_count (negative if overfilled).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str
    position_id: UUID
    target_headcount: int
    status: JobOpeningStatus
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    position_name: str = ""
    accepted_count: int = 0
    filled: bool = False
    overfilled: bool = False
    remaining_headcount: int = 0


class JobOpeningListItemResponse(BaseModel):
    """Response schema for a Job Opening in list view.

    Attributes:
        id: Job Opening UUID.
        title: Job Opening title.
        position_id: UUID of the Position.
        target_headcount: Target number of hires.
        status: Current lifecycle status.
        created_at: Creation timestamp.
        total_candidates: Total candidates assigned to this Job Opening.
        accepted_count: Number of accepted candidates.
        filled: True if accepted_count >= target_headcount.
        overfilled: True if accepted_count > target_headcount.
        remaining_headcount: target_headcount - accepted_count (negative if overfilled).
        position_name: Display name of the Position.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    position_id: UUID
    target_headcount: int
    status: JobOpeningStatus
    created_at: datetime
    total_candidates: int = 0
    accepted_count: int = 0
    filled: bool = False
    overfilled: bool = False
    remaining_headcount: int = 0
    position_name: str = ""


class JobOpeningListResponse(BaseModel):
    """Paginated response for the Job Opening list endpoint.

    Attributes:
        job_openings: List of Job Opening records for the current page.
        total_count: Total number of matching Job Openings.
        page: Current page number (1-indexed).
        page_size: Number of items per page.
    """

    job_openings: list[JobOpeningListItemResponse]
    total_count: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Job Opening metrics schemas
# ---------------------------------------------------------------------------


class JobOpeningMetricsResponse(BaseModel):
    """Summary metrics for Job Opening lifecycle states.

    Aggregates total Job Openings and counts by each lifecycle status
    (draft, open, closed, cancelled) for HR visibility into recruitment
    pipeline health.

    Attributes:
        total_job_openings: Total count of all Job Openings.
        draft_count: Count of Job Openings in draft status.
        open_count: Count of Job Openings in open status.
        closed_count: Count of Job Openings in closed status.
        cancelled_count: Count of Job Openings in cancelled status.
    """

    total_job_openings: int
    draft_count: int
    open_count: int
    closed_count: int
    cancelled_count: int


class CalendarConflictResponse(BaseModel):
    """Response schema for a calendar conflict."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    interview_id: UUID
    candidate_id: UUID
    calendar_event_id: str
    local_snapshot: dict[str, Any] = Field(default_factory=dict)
    remote_snapshot: dict[str, Any] = Field(default_factory=dict)
    conflict_details: dict[str, Any] = Field(default_factory=dict)
    status: CalendarConflictStatus
    resolved_by: UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CalendarConflictListResponse(BaseModel):
    """Paginated response for the calendar conflict list."""

    conflicts: list[CalendarConflictResponse]
    total_count: int


class ResolveConflictRequest(BaseModel):
    """Request schema for resolving a calendar conflict.

    Attributes:
        choice: "keep_google" or "overwrite_vroom".
    """

    choice: str = Field(pattern=r"^(keep_google|overwrite_vroom)$")


# ---------------------------------------------------------------------------
# Recruitment Inbox schemas (GH #184)
# ---------------------------------------------------------------------------


class InboxItemResponse(BaseModel):
    """Response schema for a single Recruitment Inbox item.

    Attributes:
        id: UUID of the inbox item.
        gmail_message_id: Gmail message ID.
        gmail_thread_id: Gmail thread ID.
        subject: Email subject line.
        sender_name: Sender's display name.
        sender_email: Sender's email address.
        snippet: Email snippet.
        has_attachments: Whether the email has attachments.
        attachments_metadata: Safe attachment metadata (count, names, types, sizes).
        inbox_status: Current inbox status (needs_classification, etc.).
        prediction_intent: The AI prediction for routing intent.
        confidence_raw: Raw confidence score from provider.
        confidence_calibrated: Calibrated confidence score.
        evidence: List of evidence items from classification.
        source_hints: Source hints from classification.
        corrected_intent: HR-corrected intent, if any.
        corrected_by_user_id: UUID of the correcting HR user.
        corrected_at: Timestamp of correction.
        correction_history: Chronological list of corrections.
        dismissed: Whether the item is dismissed.
        dismissed_at: Dismissal timestamp.
        dismissed_by_user_id: UUID of the dismissing HR user.
        processing_error: Error description for failed items.
        retry_count: Number of retry attempts.
        is_retry_exhausted: Whether retries are exhausted.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    gmail_message_id: str
    gmail_thread_id: str
    sender_name: str = ""
    sender_email: str = ""
    subject: str = ""
    snippet: str = ""
    has_attachments: bool = False
    attachments_metadata: list[dict[str, Any]] | None = None
    inbox_status: str
    prediction_intent: str | None = None
    confidence_raw: float | None = None
    confidence_calibrated: float | None = None
    evidence: list[dict[str, Any]] | None = None
    source_hints: list[dict[str, Any]] | None = None
    corrected_intent: str | None = None
    corrected_by_user_id: UUID | None = None
    corrected_at: datetime | None = None
    correction_history: list[dict[str, Any]] | None = None
    dismissed: bool = False
    dismissed_at: datetime | None = None
    dismissed_by_user_id: UUID | None = None
    processing_error: str | None = None
    retry_count: int = 0
    is_retry_exhausted: bool = False
    created_at: datetime
    updated_at: datetime


class InboxListResponse(BaseModel):
    """Paginated response for the Recruitment Inbox list.

    Attributes:
        items: List of inbox items.
        total: Total number of matching items.
        page: Current page number.
        page_size: Items per page.
    """

    items: list[InboxItemResponse]
    total: int
    page: int
    page_size: int


class CorrectIntentRequest(BaseModel):
    """Request schema for correcting inbox item routing intent.

    Attributes:
        corrected_intent: The corrected routing intent value.
    """

    corrected_intent: str = Field(..., min_length=1, max_length=50)


class SplitApplicantRequest(BaseModel):
    """One applicant extracted by HR from a source message."""

    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr | None = None
    job_opening_id: UUID | None = None


class SplitInboxItemRequest(BaseModel):
    """Split one source message into one Job Application per applicant."""

    source: ApplicationSource
    applicants: list[SplitApplicantRequest] = Field(..., min_length=1)


class JobApplicationResponse(BaseModel):
    """Job Application created or linked from Recruitment Inbox work."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_email_message_id: UUID
    gmail_message_id: str
    gmail_thread_id: str
    intent: str
    has_cv: bool
    source: str
    applicant_name: str | None = None
    applicant_email: str | None = None
    sender_name: str
    sender_email: str
    evidence: list[dict[str, Any]]
    source_hints: list[dict[str, Any]]
    message_references: list[dict[str, Any]]
    audit_history: list[dict[str, Any]]
    job_opening_id: UUID | None = None
    candidate_id: UUID | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class SplitInboxItemResponse(BaseModel):
    """Applications created from a split action."""

    applications: list[JobApplicationResponse]


class ProposeLinkRequest(BaseModel):
    """Target for a cross-thread link proposal."""

    target_job_application_id: UUID


class ResolveLinkProposalRequest(BaseModel):
    """HR decision for a pending cross-thread link proposal."""

    decision: LinkProposalStatus


class LinkProposalResponse(BaseModel):
    """Auditable state of a cross-thread link proposal."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    recruitment_inbox_item_id: UUID
    target_job_application_id: UUID
    status: str
    proposed_by_user_id: UUID
    resolved_by_user_id: UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Job Application Assignment and Promotion schemas (GH #186)
# ---------------------------------------------------------------------------


class CorrectJobApplicationSourceRequest(BaseModel):
    """HR correction of the application source classification."""

    source: ApplicationSource


class JobApplicationSourceResponse(BaseModel):
    """Source and lifecycle state after an auditable source correction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: ApplicationSource
    status: str


class AssignJobApplicationRequest(BaseModel):
    """Request schema for assigning a Job Application to a Job Opening.

    Attributes:
        job_opening_id: UUID of the target Job Opening, or null to unassign.
    """

    job_opening_id: UUID | None = None


class PromoteJobApplicationRequest(BaseModel):
    """Request schema for promoting a Job Application to a Candidate.

    Attributes:
        applicant_name: Applicant's full name (required for promotion).
        applicant_email: Applicant's email address (required for promotion).
        job_opening_id: Optional UUID of the Job Opening to assign after promotion.
    """

    applicant_name: str = Field(min_length=1, max_length=255)
    applicant_email: str = Field(min_length=1, max_length=255)
    job_opening_id: UUID | None = None


class JobApplicationAssignmentResponse(BaseModel):
    """Response after assigning/unassigning a Job Application to a Job Opening.

    Attributes:
        id: Job Application UUID.
        job_opening_id: Assigned Job Opening UUID, or null if unassigned.
        candidate_id: Candidate UUID if promoted, or null.
        status: Current Job Application status.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_opening_id: UUID | None = None
    candidate_id: UUID | None = None
    status: str


class JobApplicationPromoteResponse(BaseModel):
    """Response after promoting a Job Application to a Candidate.

    Attributes:
        id: Job Application UUID.
        candidate_id: UUID of the newly created Candidate.
        candidate_name: Name of the created Candidate.
        candidate_email: Email of the created Candidate.
        job_opening_id: Assigned Job Opening UUID, or null.
        status: Updated Job Application status.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    candidate_name: str
    candidate_email: str
    job_opening_id: UUID | None = None
    status: str
