"""Domain entities for the Recruitment CV Pipeline module.

Defines the SQLModel table classes for Candidate, CVDocument, and
RecruitmentAuditLog that map to PostgreSQL tables used for recruitment
pipeline management.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from src.modules.recruitment.domain.enums import (
    ApplicationSource,
    CorrectionEvaluationStatus,
    InboxStatus,
    JobApplicationStatus,
    LinkProposalStatus,
)

# ``sa_type=Text`` below is load-bearing: a bare ``str`` renders SQLModel's
# ``AutoString``, i.e. ``VARCHAR`` with no length. That behaves exactly like
# ``TEXT`` but is a different type name, so autogenerate proposes an ALTER for
# every such column. The migrations wrote ``sa.Text()`` and the database has
# ``TEXT`` -- the model is the imprecise side. docs/schema-drift-audit.md 5.5.


class Candidate(SQLModel, table=True):
    """Represents a recruitment candidate created from CV processing.

    Candidates are created automatically when a CV is parsed with
    sufficient confidence, or manually by HR during review. Each
    candidate has a lifecycle status tracked by CandidateStatus enum.
    Deduplication is performed by email address.
    """

    __tablename__ = "candidates"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=255, nullable=False)
    email: str = Field(max_length=255, nullable=False, index=True)
    phone: str = Field(default="", max_length=20)
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    experience: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    education: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    summary: str = Field(default="", max_length=500)
    parsed_cv_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    status: str = Field(default="new", max_length=30, nullable=False, index=True)
    confidence_score: float = Field(default=0.0, nullable=False)
    source_email_message_id: UUID | None = Field(default=None, foreign_key="email_messages.id")
    rejection_reason: str | None = Field(default=None, max_length=1000)
    rejected_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    accepted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    archived_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    job_opening_id: UUID | None = Field(default=None, foreign_key="job_openings.id", index=True)


class RecruitmentInboxItem(SQLModel, table=True):
    """Recruitment Inbox item for emails requiring HR attention.

    Created when an email is classified as recruitment but confidence
    is below the policy threshold (needs_classification), or when
    provider retries are exhausted (permanently_failed → manual_review).

    This entity does NOT duplicate the JobApplication; it represents
    inbox-specific state for emails that did NOT reach the threshold
    for automatic Job Application creation.

    Dismissed items retain their record and are excluded from retry.
    """

    __tablename__ = "recruitment_inbox_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    source_email_message_id: UUID = Field(
        foreign_key="email_messages.id", nullable=False, index=True
    )
    gmail_message_id: str = Field(max_length=255, nullable=False, unique=True, index=True)
    gmail_thread_id: str = Field(max_length=255, nullable=False)
    sender_name: str = Field(default="", max_length=255, nullable=False)
    sender_email: str = Field(default="", max_length=255, nullable=False)
    subject: str = Field(default="", max_length=500)
    snippet: str = Field(default="", max_length=2000)
    has_attachments: bool = Field(default=False)

    # Safe attachment metadata (count, names, types, sizes) - never raw content
    attachments_metadata: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )

    # Classification result
    inbox_status: str = Field(
        default=InboxStatus.NEEDS_CLASSIFICATION, max_length=30, nullable=False, index=True
    )
    prediction_intent: str | None = Field(default=None, max_length=50)
    confidence_raw: float | None = Field(default=None)
    confidence_calibrated: float | None = Field(default=None)
    evidence: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    source_hints: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )

    # Correction tracking
    corrected_intent: str | None = Field(default=None, max_length=50)
    corrected_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    corrected_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    correction_history: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )

    # Dismissal
    dismissed: bool = Field(default=False, nullable=False, index=True)
    dismissed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    dismissed_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")

    # Retry tracking
    retry_count: int = Field(default=0, nullable=False)
    is_retry_exhausted: bool = Field(default=False, nullable=False)
    processing_error: str | None = Field(default=None, max_length=500)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CVDocument(SQLModel, table=True):
    """Represents a CV file stored in MinIO object storage.

    Tracks the lifecycle of a CV document from upload through OCR
    extraction and LLM parsing. Each document is linked to a Gmail
    message and optionally to a Candidate record once processing
    completes successfully.
    """

    __tablename__ = "cv_documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    candidate_id: UUID | None = Field(default=None, foreign_key="candidates.id", index=True)
    # Deliberately not unique, unlike ``RecruitmentInboxItem.gmail_message_id``
    # above: one email can carry several CV attachments, and
    # ``process_cv_from_email`` writes one row per attachment under the same
    # message id. ``CVDocumentRepository.get_by_gmail_message_id`` returns a
    # list for exactly that reason.
    gmail_message_id: str = Field(max_length=255, nullable=False, index=True)
    original_filename: str = Field(max_length=255, nullable=False)
    mime_type: str = Field(max_length=100, nullable=False)
    size_bytes: int = Field(nullable=False)
    file_path: str = Field(max_length=500, nullable=False)
    checksum: str | None = Field(default=None, max_length=64, index=True)
    ocr_output: str | None = Field(default=None, sa_type=Text)
    parsed_cv_data: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    field_provenance: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    confirmed_fields: list[str] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    confidence_score: float | None = Field(default=None)
    processing_status: str = Field(default="pending", max_length=30, nullable=False, index=True)
    processing_error: str | None = Field(default=None, max_length=500)
    validation_errors: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSONB))
    retry_count: int = Field(default=0, nullable=False)
    next_retry_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    last_retry_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    uploaded_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class RecruitmentAuditLog(SQLModel, table=True):
    """Audit log entry for recruitment module operations.

    Records all significant actions performed within the recruitment
    pipeline including intent classification, CV parsing, candidate
    status changes, and data retention operations. Ensures PII is
    never stored in audit entries.
    """

    __tablename__ = "recruitment_audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID | None = Field(default=None, foreign_key="users.id", index=True)
    operation_type: str = Field(max_length=50, nullable=False, index=True)
    entity_type: str = Field(max_length=50, nullable=False)
    entity_id: UUID | None = Field(default=None, index=True)
    previous_value: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    new_value: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    change_summary: str | None = Field(default=None, max_length=500)
    model_name: str | None = Field(default=None, max_length=100)
    token_usage: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    latency_ms: int | None = Field(default=None)
    success: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class CalendarSyncCursor(SQLModel, table=True):
    """Tracks the sync token for incremental calendar synchronization.

    One cursor per (Organization, selected_calendar_id) pair. The
    sync_token is used with Google Calendar's events.list API to fetch
    only events newer than the last successful sync. A 410 GONE from
    Google clears the token and triggers a bounded full sync.
    """

    __tablename__ = "calendar_sync_cursors"
    __table_args__ = (
        UniqueConstraint(
            "organization_singleton_key",
            "calendar_id",
            name="uq_org_calendar_sync_cursor",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_singleton_key: str = Field(default="default", max_length=32, nullable=False)
    calendar_id: str = Field(max_length=255, nullable=False, index=True)
    sync_token: str | None = Field(default=None, max_length=1024)
    page_token: str | None = Field(default=None, max_length=1024)
    last_sync_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class OrganizationSettings(SQLModel, table=True):
    """Single-row settings for the Organization (the company deployment).

    Holds the canonical IANA timezone, allowed email domains, and
    attendance network configuration. The repository enforces single-row
    semantics, seeding the configured default on first access.
    """

    __tablename__ = "organization_settings"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    singleton_key: str = Field(default="default", max_length=20, unique=True, nullable=False)
    name: str = Field(default="", max_length=255, nullable=False)
    timezone: str = Field(default="Asia/Ho_Chi_Minh", max_length=64, nullable=False)
    allowed_domains: list[str] = Field(
        sa_column=Column(ARRAY(String), nullable=False, server_default="{}"),
    )
    attendance_allowed_networks: list[str] = Field(
        sa_column=Column(ARRAY(String), nullable=False, server_default="{}"),
    )
    guide_progress: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class JobOpening(SQLModel, table=True):
    """Represents a Job Opening in recruitment planning.

    A Job Opening is tied to exactly one Position and tracks target headcount
    separately from the Candidate pipeline. It has its own lifecycle:
    draft → open → closed/cancelled, and closed → open (reopen).
    Cancelled is terminal. This is an optional concept that does not become
    a required parent of Candidate.
    """

    __tablename__ = "job_openings"

    # ``ix_job_openings_id`` duplicates the primary key's own index and buys
    # nothing, but 035 created it and the database has it. Declaring it keeps
    # autogenerate quiet; dropping it is a separate, deliberate decision with a
    # migration behind it, not something a model edit should do by omission.
    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    title: str = Field(max_length=255, nullable=False, index=True)
    description: str = Field(default="", max_length=5000)
    position_id: UUID = Field(foreign_key="positions.id", nullable=False, index=True)
    target_headcount: int = Field(nullable=False, ge=1)
    status: str = Field(default="draft", max_length=20, nullable=False, index=True)
    opened_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    closed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    cancelled_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    # On the ``Column``, not the ``Field`` — ``index=True`` is ignored whenever
    # ``sa_column`` is given. 035 created this for the default "newest openings
    # first" ordering.
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class Interview(SQLModel, table=True):
    """Represents a specific interview for a candidate."""

    __tablename__ = "interviews"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    candidate_id: UUID = Field(
        foreign_key="candidates.id", ondelete="CASCADE", index=True, nullable=False
    )
    # status: scheduled, completed, cancelled.
    #
    # Deliberately *not* indexed, and neither is ``calendar_id`` below. 046 and
    # 072 wrote this table by hand and did index ``candidate_id`` and
    # ``calendar_event_id`` -- so which columns get an index was a decision that
    # was actually made, and these were left out of it. Nothing in the code
    # queries either one: the only ``status`` predicate is inside the correlated
    # EXISTS in retention_job.py, already anchored by ``ix_interviews_candidate_id``
    # and filtering the handful of rows that index returns, and ``calendar_id`` is
    # never a WHERE clause anywhere (``CalendarSyncCursor.calendar_id``, a
    # different table, is the one that gets looked up and 073 indexed it).
    # ``index=True`` here never created an index on any deployed database; it only
    # made autogenerate offer to create one.
    status: str = Field(default="scheduled", max_length=30, nullable=False)
    round_name: str = Field(max_length=255, nullable=False)
    start_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    end_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    timezone: str = Field(max_length=64, nullable=False)
    calendar_event_id: str | None = Field(default=None, max_length=1024, index=True)
    calendar_id: str | None = Field(default=None, max_length=1024)
    remote_location: str | None = Field(default=None, max_length=1024)
    needs_relink: bool = Field(default=False, nullable=False)
    calendar_etag: str | None = Field(default=None, max_length=255)
    calendar_updated: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
    )
    meeting_mode: str = Field(default="google_meet", max_length=20, nullable=False)
    meeting_link: str | None = Field(default=None, max_length=1024)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class InterviewParticipant(SQLModel, table=True):
    """Represents a participant in an interview."""

    __tablename__ = "interview_participants"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    interview_id: UUID = Field(
        foreign_key="interviews.id", ondelete="CASCADE", index=True, nullable=False
    )
    # candidate, employee, external. Not indexed, and neither is ``employee_id``:
    # 046 indexed ``interview_id`` (the one column this table is ever queried by,
    # in ``InterviewRepository.get_participants``) and left these two alone.
    # Both are write-only from the database's point of view -- ``type`` is set on
    # insert and read back off a fetched row, never used as a predicate.
    type: str = Field(max_length=20, nullable=False)
    email: str = Field(max_length=255, nullable=False)
    name: str | None = Field(
        default=None,
        max_length=255,
    )
    employee_id: UUID | None = Field(default=None, foreign_key="employees.id")
    response_status: str | None = Field(
        default=None, max_length=30
    )  # needsAction, accepted, declined, tentative
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CalendarConflict(SQLModel, table=True):
    """Records a calendar event write conflict for resolution.

    When a conditional write (If-Match) to Google Calendar fails with 412,
    the current Vroom-side Interview state and the remote Calendar event
    state are captured without mutating the Interview or Candidate.
    """

    __tablename__ = "calendar_conflicts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Only ``candidate_id`` cascades on the real table; ``interview_id`` is
    # NO ACTION (pg_constraint.confdeltype 'a'). Keep the pair asymmetric.
    interview_id: UUID = Field(foreign_key="interviews.id", index=True, nullable=False)
    candidate_id: UUID = Field(
        foreign_key="candidates.id", ondelete="CASCADE", index=True, nullable=False
    )
    calendar_event_id: str = Field(max_length=1024, nullable=False)
    # Vroom-side snapshot at conflict time
    local_snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    # Remote Google Calendar snapshot at conflict time
    remote_snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    # conflict_details: what fields differed
    conflict_details: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    # status: unresolved, resolved_keep_google, resolved_overwrite_vroom
    status: str = Field(default="unresolved", max_length=30, nullable=False, index=True)
    resolved_by: UUID | None = Field(default=None, foreign_key="users.id")
    resolved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class JobApplication(SQLModel, table=True):
    """Represents a Job Application created from a confident recruitment email.

    A Job Application records the intent to apply to the Organization,
    regardless of whether CV attachments exist. It is created by the
    ingestion pipeline after a confident provider (AI) classification
    determines the email is a job application.

    Source is distinguished from applicant identity: the sender may be
    the applicant (direct), a referring employee (employee_referral), or
    an agency (agency). Applicant fields are populated from structured
    source hints where available, and remain nullable otherwise.

    This entity precedes Candidate; only HR action may promote a
    Job Application to a Candidate.
    """

    __tablename__ = "job_applications"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    source_email_message_id: UUID = Field(
        foreign_key="email_messages.id", nullable=False, index=True
    )
    gmail_message_id: str = Field(max_length=255, nullable=False, index=True)
    gmail_thread_id: str = Field(max_length=255, nullable=False)
    # Stable contract fields remain independent of the legacy Gmail category.
    intent: str = Field(default="job_application", max_length=50, nullable=False)
    has_cv: bool = Field(default=False, nullable=False)
    source: str = Field(default=ApplicationSource.DIRECT, max_length=30, nullable=False)
    applicant_name: str | None = Field(default=None, max_length=255)
    applicant_email: str | None = Field(default=None, max_length=255)
    sender_name: str = Field(default="", max_length=255, nullable=False)
    sender_email: str = Field(default="", max_length=255, nullable=False)
    evidence: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    source_hints: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    message_references: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    audit_history: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    job_opening_id: UUID | None = Field(
        default=None, foreign_key="job_openings.id", nullable=True, index=True
    )
    candidate_id: UUID | None = Field(
        default=None, foreign_key="candidates.id", nullable=True, unique=True, index=True
    )
    status: str = Field(default=JobApplicationStatus.NEW, max_length=20, nullable=False, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class JobApplicationLinkProposal(SQLModel, table=True):
    """A cross-thread link that has no effect until HR resolves it."""

    __tablename__ = "job_application_link_proposals"
    __table_args__ = (
        # Declared here, not as ``index=True`` on the fields: 063 named all three
        # after the short form ``link_proposals`` while the table is
        # ``job_application_link_proposals``, and one of them after
        # ``target_application_id`` while the column is
        # ``target_job_application_id``. ``index=True`` derives the name from the
        # table and column, so it renders three differently-named twins and
        # autogenerate proposes dropping the three real indexes to create them.
        # Same shape as the ``ix_kb_*`` group in knowledge_base/domain/entities.py.
        Index("ix_link_proposals_inbox_item_id", "recruitment_inbox_item_id"),
        Index("ix_link_proposals_target_application_id", "target_job_application_id"),
        Index("ix_link_proposals_status", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    recruitment_inbox_item_id: UUID = Field(
        foreign_key="recruitment_inbox_items.id",
        ondelete="CASCADE",
        nullable=False,
    )
    target_job_application_id: UUID = Field(
        foreign_key="job_applications.id",
        ondelete="CASCADE",
        nullable=False,
    )
    status: str = Field(default=LinkProposalStatus.PENDING, max_length=20, nullable=False)
    proposed_by_user_id: UUID = Field(foreign_key="users.id", nullable=False)
    resolved_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    resolved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CorrectionRecord(SQLModel, table=True):
    """Records an HR correction for safe evaluation feedback.

    Stores the prediction, HR decision, model/prompt/policy versions,
    and minimal metadata. Raw body, thread content, attachment content,
    and chain-of-thought are NEVER stored here.

    This record serves as the basis for opt-in evaluation samples.
    Corrections never trigger online learning or automatic prompt/model changes.
    """

    __tablename__ = "correction_records"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    source_type: str = Field(max_length=20, nullable=False)  # "inbox_item" or "job_application"
    source_id: UUID = Field(nullable=False, index=True)

    # Prediction (what the system said)
    prediction_intent: str | None = Field(default=None, max_length=50)
    confidence_raw: float | None = Field(default=None)
    confidence_calibrated: float | None = Field(default=None)

    # HR correction (what HR decided)
    corrected_intent: str = Field(max_length=50, nullable=False)
    previous_inbox_status: str | None = Field(default=None, max_length=30)

    # Who and when
    corrected_by_user_id: UUID = Field(foreign_key="users.id", nullable=False)
    corrected_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    # Versions — essential for reproducibility
    model_version: str | None = Field(default=None, max_length=100)
    prompt_version: str | None = Field(default=None, max_length=100)
    policy_version: str | None = Field(default=None, max_length=100)

    # Safe evidence metadata (never raw content)
    evidence: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSONB))
    source_hints: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSONB))

    # Evaluation opt-in status
    evaluation_status: str = Field(
        default=CorrectionEvaluationStatus.NONE, max_length=20, nullable=False
    )

    # Guard: correction never changes model behaviour
    triggers_online_learning: bool = Field(default=False, nullable=False)

    # Message reference for redaction (never stored raw)
    redacted_subject: str | None = Field(default=None, max_length=500)
    redacted_snippet: str | None = Field(default=None, max_length=2000)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class EvaluationSet(SQLModel, table=True):
    """A versioned evaluation set containing redacted samples.

    Only redacted data is written here. Each set has a semantic version
    and can be used to compare classifier versions reproducibly.
    """

    __tablename__ = "evaluation_sets"
    __table_args__ = (
        # 064 created the column with an inline UNIQUE (PostgreSQL named it
        # ``evaluation_sets_version_key``) and a separate non-unique index.
        # ``unique=True`` on the field would collapse both into one unique
        # index that the database does not have.
        UniqueConstraint("version", name="evaluation_sets_version_key"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    version: str = Field(max_length=30, nullable=False, index=True)
    description: str = Field(default="", max_length=500)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class EvaluationSample(SQLModel, table=True):
    """A redacted evaluation sample committed to a versioned set.

    Contains only redacted data safe for durable storage.
    Links back to the source CorrectionRecord for audit.
    """

    __tablename__ = "evaluation_samples"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    correction_record_id: UUID = Field(
        foreign_key="correction_records.id", nullable=False, index=True
    )
    evaluation_set_id: UUID = Field(foreign_key="evaluation_sets.id", nullable=False, index=True)

    # Redacted fields — no PII, no raw content
    redacted_subject: str = Field(default="", max_length=500)
    redacted_snippet: str = Field(default="", max_length=2000)
    redacted_sender_name: str = Field(default="", max_length=255)
    redacted_sender_email: str = Field(default="", max_length=255)

    # Ground truth from HR correction
    ground_truth_intent: str = Field(max_length=50, nullable=False)

    # Versions for reproducibility
    model_version: str | None = Field(default=None, max_length=100)
    prompt_version: str | None = Field(default=None, max_length=100)
    policy_version: str | None = Field(default=None, max_length=100)

    # Cohort labels for granular evaluation
    cohorts: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    redacted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
