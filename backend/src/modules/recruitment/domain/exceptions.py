"""Domain exceptions for the Recruitment CV Pipeline module.

This module defines the exception hierarchy used throughout the recruitment
module to represent business rule violations, resource errors, and external
service failures.
"""

from collections.abc import Sequence
from uuid import UUID

from src.shared.messages import MessageRef


class RecruitmentError(Exception):
    """Base exception for the recruitment module.

    All domain-specific exceptions inherit from this class, enabling
    a single exception handler to catch any recruitment-related error
    and return a consistent JSON error response.

    Attributes:
        status_code: HTTP status code to return to the client.
        error_code: Machine-readable error identifier.
        message: Human-readable error description. Server-side only: adapters
            build it by interpolating third-party exceptions, so it is logged
            but never rendered into an HTTP response body.
        public_context: Values a raise site has explicitly declared safe to
            publish, substituted into the translated catalog template (e.g.
            ``{"candidate_id": ...}``). Anything not named here stays internal.
        details: Optional structured payload with extra context for the
            client (e.g. the unmatched interviewer identifiers).
    """

    status_code: int = 500
    error_code: str = "RECRUITMENT_ERROR"
    message: str = "A recruitment module error occurred"
    public_context: dict[str, object] | None = None
    details: dict[str, object] | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        """Initialize RecruitmentError.

        Args:
            message: Optional custom message override. If not provided,
                the class-level default message is used.
            details: Optional structured payload carried alongside the
                error for the client. Defaults to ``None``.
        """
        if message is not None:
            self.message = message
        if details is not None:
            self.details = details
        super().__init__(self.message)


class JobApplicationNotFoundError(RecruitmentError):
    """JobApplication with given ID does not exist.

    Raised when an operation targets a Job Application ID that cannot be
    found in the database.
    """

    status_code = 404
    error_code = "JOB_APPLICATION_NOT_FOUND"
    message = "Job Application not found"


class JobApplicationPromotionBlockedError(RecruitmentError):
    """Job Application cannot be promoted to Candidate.

    Raised when the Job Application is dismissed, has invalid applicant identity,
    or references a promoted Candidate that no longer exists.
    """

    status_code = 409
    error_code = "JOB_APPLICATION_PROMOTION_BLOCKED"
    message = "Job Application cannot be promoted to Candidate"

    def __init__(self, reason: str, *, reason_code: str | None = None) -> None:
        """Initialize the error.

        Args:
            reason: Why promotion was refused, for the log.
            reason_code: Catalog code for the same reason, so the client sees
                it in its own language. Omit only if no catalog entry fits.
        """
        self.message = "Job Application cannot be promoted: " + reason
        if reason_code is not None:
            self.public_context = {"reason": MessageRef(reason_code)}
        super().__init__(self.message)


class JobApplicationAssignmentBlockedError(RecruitmentError):
    """Job Application cannot be assigned to a Job Opening.

    Raised when the Job Application is dismissed or has already been promoted.
    """

    status_code = 409
    error_code = "JOB_APPLICATION_ASSIGNMENT_BLOCKED"
    message = "Job Application assignment is blocked"

    def __init__(self, reason: str) -> None:
        self.public_context = {"reason": reason}
        self.message = "Job Application assignment is blocked: " + reason
        super().__init__(self.message)


class CalendarConflictNotFoundError(RecruitmentError):
    """CalendarConflict with given ID does not exist.

    Raised when a resolution request targets a conflict ID that cannot
    be found in the database.
    """

    status_code = 404
    error_code = "CALENDAR_CONFLICT_NOT_FOUND"
    message = "Calendar conflict not found"


class CandidateNotFoundError(RecruitmentError):
    """Candidate with given ID does not exist.

    Raised when an operation targets a candidate ID that cannot be
    found in the database.
    """

    status_code = 404
    error_code = "CANDIDATE_NOT_FOUND"
    message = "Candidate not found"


class CVDocumentNotFoundError(RecruitmentError):
    """CV document not found or doesn't belong to candidate.

    Raised when a CV document ID cannot be found in the database,
    or when the document does not belong to the specified candidate.
    """

    status_code = 404
    error_code = "CV_DOCUMENT_NOT_FOUND"
    message = "CV document not found"


class InvalidStatusTransitionError(RecruitmentError):
    """Attempted status transition is not allowed.

    Raised when an action would result in an invalid state machine
    transition for a candidate's lifecycle status.

    Attributes:
        current_status: The candidate's current status.
        attempted_action: The action that was attempted.
    """

    status_code = 409
    error_code = "INVALID_STATUS_TRANSITION"
    message = "Invalid status transition"

    def __init__(self, current_status: str, attempted_action: str) -> None:
        """Initialize InvalidStatusTransitionError.

        Args:
            current_status: The candidate's current status value.
            attempted_action: The action that was attempted on the candidate.
        """
        self.current_status = current_status
        self.attempted_action = attempted_action
        self.public_context = {
            "current_status": current_status,
            "attempted_action": attempted_action,
        }
        self.message = (
            f"Cannot perform '{attempted_action}' on candidate with status '{current_status}'"
        )
        super().__init__(self.message)


class CVFileNotFoundError(RecruitmentError):
    """File exists in DB but missing from MinIO storage.

    Raised when a CV document record exists in the database but
    the corresponding file cannot be found in MinIO object storage,
    indicating storage corruption or premature deletion.
    """

    status_code = 404
    error_code = "CV_FILE_MISSING"
    message = "CV file not found in storage"


class StorageServiceUnavailableError(RecruitmentError):
    """MinIO service is unreachable.

    Raised when the MinIO object storage service cannot be reached
    during upload, download, or presigned URL generation operations.
    """

    status_code = 502
    error_code = "STORAGE_SERVICE_UNAVAILABLE"
    message = "Storage service is unavailable"


class GmailNotConnectedError(RecruitmentError):
    """Gmail OAuth connection is not active.

    Raised when an operation requires Gmail access but the HR user's
    OAuth connection is not in 'connected' status.
    """

    status_code = 409
    error_code = "GMAIL_NOT_CONNECTED"
    message = "Gmail is not connected"


class PipelineTimeoutError(RecruitmentError):
    """CV processing pipeline exceeded maximum time.

    Raised when the overall CV processing pipeline (OCR + LLM parse +
    validation) exceeds the configured timeout (default 660 seconds).
    """

    status_code = 504
    error_code = "PIPELINE_TIMEOUT"
    message = "CV processing pipeline timed out"


class OCRExtractionError(RecruitmentError):
    """OCR text extraction failed.

    Raised when the olmOCR server fails to extract text from a CV
    document after all retry attempts are exhausted.
    """

    status_code = 502
    error_code = "OCR_EXTRACTION_FAILED"
    message = "OCR text extraction failed"


class LLMParseError(RecruitmentError):
    """LLM failed to parse CV into structured data.

    Raised when the LLM service fails to return valid structured
    JSON from the OCR text after all retry attempts, including
    the simplified prompt retry.
    """

    status_code = 502
    error_code = "LLM_PARSE_FAILED"
    message = "LLM CV parsing failed"


class CalendarGrantMissingError(RecruitmentError):
    """Acting HR user lacks a valid Google Calendar grant.

    Raised before any Calendar call when the acting HR user's
    ``calendar_grant_valid`` is false, so the user is directed to
    re-consent to Calendar access (R9.1, R9.2).
    """

    status_code = 403
    error_code = "CALENDAR_GRANT_MISSING"
    message = "Google Calendar access is not granted; please re-consent to Calendar access"


class InterviewerNotFoundError(RecruitmentError):
    """One or more interviewer identifiers do not match an Employee.

    Raised when a schedule request references interviewer Employee
    identifiers that cannot be found, listing the unmatched identifiers
    in ``details`` (R1.7).

    Attributes:
        unmatched_ids: The interviewer identifiers with no matching Employee.
    """

    status_code = 422
    error_code = "INTERVIEWER_NOT_FOUND"
    message = "One or more interviewers were not found"

    def __init__(self, unmatched_ids: Sequence[UUID]) -> None:
        """Initialize InterviewerNotFoundError.

        Args:
            unmatched_ids: The interviewer identifiers that do not correspond
                to an existing Employee record.
        """
        self.unmatched_ids = list(unmatched_ids)
        details: dict[str, object] = {
            "unmatched_ids": [str(id_) for id_ in self.unmatched_ids],
        }
        super().__init__(details=details)


class InterviewerMissingEmailError(RecruitmentError):
    """An interviewer Employee has no email address.

    Raised when a matched interviewer Employee lacks an email address,
    so the meeting cannot invite a required participant. Identifies the
    offending interviewer in ``details`` (R10.1).

    Attributes:
        interviewer_id: The identifier of the interviewer that cannot be invited.
    """

    status_code = 422
    error_code = "INTERVIEWER_MISSING_EMAIL"
    message = "An interviewer cannot be invited because they have no email address"

    def __init__(self, interviewer_id: UUID) -> None:
        """Initialize InterviewerMissingEmailError.

        Args:
            interviewer_id: The identifier of the interviewer Employee that
                lacks an email address.
        """
        self.interviewer_id = interviewer_id
        details: dict[str, object] = {"interviewer_id": str(interviewer_id)}
        super().__init__(details=details)


class CalendarEventSyncError(RecruitmentError):
    """Google Calendar event sync (events.list) failed.

    Raised when the Calendar events.list request fails after retries,
    wrapping non-401 non-410 errors so the sync service can distinguish
    transient API failures from auth/expired-sync-token problems.
    """

    status_code = 502
    error_code = "CALENDAR_SYNC_FAILED"
    message = "Failed to sync calendar events"


class CalendarEventCreateFailedError(RecruitmentError):
    """Google Calendar event creation failed.

    Raised when the Calendar event could not be created during a
    schedule-interview request, after retries and a token refresh,
    so the operation is rolled back atomically (R3.3).
    """

    status_code = 502
    error_code = "CALENDAR_CREATE_FAILED"
    message = "Failed to create the Google Calendar event"


class CalendarEventConflictError(RecruitmentError):
    """Google Calendar rejected a conditional write because the event changed."""

    status_code = 412
    error_code = "CALENDAR_CONFLICT"
    message = "The Google Calendar event changed; review the conflict before retrying"


class CalendarRelinkRequiredError(RecruitmentError):
    """Calendar event binding is stale; HR intervention required.

    Raised when an Interview has ``needs_relink=True`` and the requested
    operation requires writing to the existing Calendar event. HR must
    cancel the Interview and create a replacement (which sends fresh
    Calendar invitations) rather than silently patching or deleting a
    potentially inaccessible event.
    """

    status_code = 409
    error_code = "CALENDAR_RELINK_REQUIRED"
    message = (
        "This interview's Calendar event binding is stale. "
        "Cancel this interview and create a replacement instead."
    )


class CalendarEventUpdateFailedError(RecruitmentError):
    """Google Calendar event update failed.

    Raised when the existing Calendar event could not be patched during
    a reschedule request, leaving the stored references unchanged (R7.4).
    """

    status_code = 502
    error_code = "CALENDAR_UPDATE_FAILED"
    message = "Failed to update the Google Calendar event"


class NoInterviewToRescheduleError(RecruitmentError):
    """Reschedule requested for a Candidate without a stored event.

    Raised when a reschedule request targets a Candidate that has no
    stored ``calendar_event_id``, so there is no interview to reschedule
    (R7.5).
    """

    status_code = 409
    error_code = "NO_INTERVIEW_TO_RESCHEDULE"
    message = "No interview exists to reschedule for this candidate"


class JobOpeningNotFoundError(RecruitmentError):
    """Job Opening with given ID does not exist.

    Raised when an operation targets a Job Opening ID that cannot be
    found in the database.
    """

    status_code = 404
    error_code = "JOB_OPENING_NOT_FOUND"
    message = "Job Opening not found"


class JobOpeningInvalidStatusTransitionError(RecruitmentError):
    """Attempted status transition is not allowed for Job Opening.

    Raised when an action would result in an invalid state machine
    transition for a Job Opening's lifecycle status.

    Attributes:
        current_status: The Job Opening's current status.
        attempted_action: The action that was attempted.
    """

    status_code = 409
    error_code = "JOB_OPENING_INVALID_STATUS_TRANSITION"
    message = "Invalid status transition for Job Opening"

    def __init__(self, current_status: str, attempted_action: str) -> None:
        """Initialize JobOpeningInvalidStatusTransitionError.

        Args:
            current_status: The Job Opening's current status value.
            attempted_action: The action that was attempted.
        """
        self.current_status = current_status
        self.attempted_action = attempted_action
        self.public_context = {
            "current_status": current_status,
            "attempted_action": attempted_action,
        }
        self.message = (
            f"Cannot perform '{attempted_action}' on Job Opening with status '{current_status}'"
        )
        super().__init__(self.message)


class PositionNotFoundError(RecruitmentError):
    """Position with given ID does not exist.

    Raised when a Job Opening is created with a position_id that
    cannot be found in the database.
    """

    status_code = 404
    error_code = "POSITION_NOT_FOUND"
    message = "Position not found"


class JobOpeningNotOpenError(RecruitmentError):
    """Job Opening is not in 'open' status for assignment.

    Raised when attempting to assign a Candidate to a Job Opening
    whose status is not 'open'.
    """

    status_code = 409
    error_code = "JOB_OPENING_NOT_OPEN"
    message = "Cannot assign candidate to a Job Opening that is not open"

    def __init__(self, job_opening_id: UUID, current_status: str) -> None:
        self.job_opening_id = job_opening_id
        self.current_status = current_status
        self.public_context = {
            "job_opening_id": str(job_opening_id),
            "current_status": current_status,
        }
        self.message = (
            f"Cannot assign candidate to Job Opening {job_opening_id} "
            f"with status '{current_status}'"
        )
        super().__init__(self.message)


class CandidateAssignmentBlockedError(RecruitmentError):
    """Candidate is in a terminal status and cannot be assigned/reassigned/unassigned.

    Raised when attempting to change assignment for a Candidate whose
    status is accepted, rejected, or archived.
    """

    status_code = 409
    error_code = "CANDIDATE_ASSIGNMENT_BLOCKED"
    message = "Cannot change assignment for a candidate in terminal status"


class InterviewNotFoundError(RecruitmentError):
    """Interview with given ID does not exist.

    Raised when an operation targets an Interview ID that cannot be
    found in the database.
    """

    status_code = 404
    error_code = "INTERVIEW_NOT_FOUND"
    message = "Interview not found"


class InterviewStatusTransitionError(RecruitmentError):
    """Attempted status transition is not allowed for Interview.

    Raised when an action would result in an invalid state machine
    transition for an Interview's lifecycle status.

    Attributes:
        current_status: The Interview's current status.
        attempted_action: The action that was attempted.
    """

    status_code = 409
    error_code = "INTERVIEW_INVALID_STATUS_TRANSITION"
    message = "Invalid status transition for Interview"

    def __init__(self, current_status: str, attempted_action: str) -> None:
        self.current_status = current_status
        self.attempted_action = attempted_action
        self.public_context = {
            "current_status": current_status,
            "attempted_action": attempted_action,
        }
        self.message = (
            f"Cannot perform '{attempted_action}' on Interview with status '{current_status}'"
        )
        super().__init__(self.message)
