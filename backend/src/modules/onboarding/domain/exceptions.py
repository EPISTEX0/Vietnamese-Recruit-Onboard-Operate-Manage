"""Domain exceptions for the Onboarding module.

This module defines the exception hierarchy used throughout the onboarding
module to represent business rule violations, resource errors, authorization
failures, and consumer-side event validation failures.

The status codes and error codes below mirror the design's Error Handling table
so that a single registered FastAPI handler can map any ``OnboardingError`` to a
uniform JSON error body, consistent with ``EmployeeError``/``RecruitmentError``.
"""

from uuid import UUID


class OnboardingError(Exception):
    """Base exception for the onboarding module.

    All domain-specific exceptions inherit from this class, enabling a single
    exception handler to catch any onboarding-related error and return a
    consistent JSON error response.

    Attributes:
        status_code: HTTP status code to return to the client.
        error_code: Machine-readable error identifier.
        message: Human-readable error description.
    """

    status_code: int = 500
    error_code: str = "ONBOARDING_ERROR"
    message: str = "An onboarding module error occurred"

    def __init__(self, message: str | None = None) -> None:
        """Initialize OnboardingError.

        Args:
            message: Optional custom message override. If not provided,
                the class-level default message is used.
        """
        if message is not None:
            self.message = message
        super().__init__(self.message)


class OnboardingProcessNotFoundError(OnboardingError):
    """Onboarding process with given ID does not exist.

    Raised when a read or update operation targets a process ID that cannot
    be found in the database (R6.6).
    """

    status_code = 404
    error_code = "ONBOARDING_PROCESS_NOT_FOUND"
    message = "Onboarding process not found"


class OnboardingTaskNotFoundError(OnboardingError):
    """Onboarding task with given ID does not exist.

    Raised when a task operation targets a task ID that cannot be found in the
    database. Per R4.4 this condition is evaluated before the requester
    authorization check in the complete-task flow.
    """

    status_code = 404
    error_code = "ONBOARDING_TASK_NOT_FOUND"
    message = "Onboarding task not found"


class InvalidTaskStatusError(OnboardingError):
    """Requested task status is not a defined value.

    Raised when a task status update supplies a value outside the allowed set
    ``{pending, done}`` (R3.5, R4.6). The offending value is named in the
    message.

    Attributes:
        value: The invalid status value that was supplied.
    """

    status_code = 422
    error_code = "ONBOARDING_INVALID_TASK_STATUS"
    message = "Invalid task status"

    def __init__(self, value: str) -> None:
        """Initialize InvalidTaskStatusError.

        Args:
            value: The invalid status value that was supplied.
        """
        self.value = value
        self.public_context = {"value": value}
        self.message = f"Invalid task status '{value}'; expected one of: pending, done"
        super().__init__(self.message)


class InvalidProcessStatusFilterError(OnboardingError):
    """List filter status is not a defined value.

    Raised when the process list ``status`` filter supplies a value outside the
    allowed set ``{in_progress, complete}`` (R6.5). The offending value is named
    in the message.

    Attributes:
        value: The invalid status filter value that was supplied.
    """

    status_code = 422
    error_code = "ONBOARDING_INVALID_STATUS_FILTER"
    message = "Invalid process status filter"

    def __init__(self, value: str) -> None:
        """Initialize InvalidProcessStatusFilterError.

        Args:
            value: The invalid status filter value that was supplied.
        """
        self.value = value
        self.public_context = {"value": value}
        self.message = (
            f"Invalid process status filter '{value}'; expected one of: in_progress, complete"
        )
        super().__init__(self.message)


class OnboardingAuthorizationError(OnboardingError):
    """Actor is not authorized to perform the onboarding action.

    Raised when an authenticated actor whose role is not ``admin`` attempts to
    change onboarding state (R4.5). In the complete-task flow this check is
    performed after task existence is confirmed.
    """

    status_code = 403
    error_code = "ONBOARDING_FORBIDDEN"
    message = "Admin privileges are required to perform this action"


class OnboardingActivationError(OnboardingError):
    """Employee activation transaction failed.

    Raised when the task completion / employee activation transaction fails and
    is rolled back, leaving the task, process, and employee state unchanged
    (R5.6).
    """

    status_code = 500
    error_code = "ONBOARDING_ACTIVATION_FAILED"
    message = "Onboarding activation failed"


class AuditWriteError(OnboardingError):
    """Audit entry could not be appended.

    Raised when the mandatory audit append fails. Because the audit write
    participates in the same transaction as the state change, the action is
    rejected and the state is left unchanged (R8.2).
    """

    status_code = 500
    error_code = "ONBOARDING_AUDIT_WRITE_FAILED"
    message = "Failed to write onboarding audit entry"


class OnboardingProcessAlreadyCompletedError(OnboardingError):
    """Update attempted on a completed process.

    Raised when an operation attempts to update a task but the parent
    onboarding process is already marked complete.
    """

    status_code = 409
    error_code = "ONBOARDING_PROCESS_ALREADY_COMPLETED"
    message = "Cannot update task status after process is complete"


class InvalidEventPayloadError(OnboardingError):
    """Consumed event payload is malformed or invalid.

    Raised by the consumer when a ``candidate_accepted`` event is missing a
    required field (``candidate_id``, ``full_name``, ``email``), has an empty
    ``candidate_id``, or carries a syntactically invalid email (R1.6, R2.6).

    This is a consumer-side error and is not surfaced as an HTTP response; the
    consumer rejects the event and records a rejection audit entry without
    invoking the creation path. The ``status_code`` is retained for
    consistency with the base class but is not mapped to a client response.
    """

    status_code = 422
    error_code = "ONBOARDING_INVALID_EVENT"
    message = "Invalid onboarding event payload"


class InactiveEmployeeAccessError(OnboardingError):
    """Self-service access targeted an Employee that is not active.

    Raised by the active-Employee boundary guard when a self-service request
    targets an Employee record whose ``is_active`` is ``false``. The request is
    rejected with this access error and the Employee record is left unchanged
    (R7.2). This is the access error the future self-service (ESS) module's
    callers receive when they attempt to reach an Employee still in onboarding.

    Attributes:
        employee_id: The identifier of the inactive Employee that was targeted,
            when known.
    """

    status_code = 403
    error_code = "ONBOARDING_EMPLOYEE_NOT_ACTIVE"
    message = "Employee record is not active"

    def __init__(self, employee_id: UUID | None = None) -> None:
        """Initialize InactiveEmployeeAccessError.

        Args:
            employee_id: The identifier of the inactive Employee that was
                targeted, when known. Recorded for diagnostics and included in
                the message so callers can identify the rejected record.
        """
        self.employee_id = employee_id
        if employee_id is not None:
            self.public_context = {"employee_id": str(employee_id)}
            self.message = f"Employee record {employee_id} is not active"
        super().__init__(self.message)
