"""Domain exceptions for the Employee Request module."""

from datetime import date as _date
from uuid import UUID


class EmployeeRequestError(Exception):
    """Base exception for the employee request module."""

    status_code: int = 500
    error_code: str = "EMPLOYEE_REQUEST_ERROR"
    message: str = "An employee request error occurred"

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class OvertimeEndBeforeStartError(EmployeeRequestError):
    """Overtime end_time is before or equal to start_time."""

    status_code = 400
    error_code = "OVERTIME_END_BEFORE_START"
    message = "End time must be after start time"


class OvertimeOverlapError(EmployeeRequestError):
    """Employee already has a submitted/approved overtime on the same date."""

    status_code = 409
    error_code = "OVERTIME_OVERLAP"
    message = "You already have a submitted or approved overtime request on this date"

    def __init__(self, work_date: _date) -> None:
        self.public_context = {"work_date": work_date.isoformat()}
        self.message = (
            f"You already have a submitted or approved overtime request on {work_date.isoformat()}"
        )
        super().__init__(self.message)


class LeaveEndBeforeStartError(EmployeeRequestError):
    """Leave end_date is before start_date."""

    status_code = 400
    error_code = "LEAVE_END_BEFORE_START"
    message = "End date must be on or after start date"


class LeaveOverlapError(EmployeeRequestError):
    """Employee already has a submitted/approved leave overlapping the date range."""

    status_code = 409
    error_code = "LEAVE_OVERLAP"
    message = "You already have a submitted or approved leave in this date range"

    def __init__(self, start: _date | None = None, end: _date | None = None) -> None:
        if start and end:
            self.public_context = {"start": start.isoformat(), "end": end.isoformat()}
            self.message = (
                f"You already have a submitted or approved leave "
                f"overlapping {start.isoformat()} to {end.isoformat()}"
            )
        super().__init__(self.message)


class RequestNotFoundError(EmployeeRequestError):
    """Employee request not found."""

    status_code = 404
    error_code = "REQUEST_NOT_FOUND"
    message = "Employee request not found"

    def __init__(self, request_id: UUID) -> None:
        self.public_context = {"request_id": str(request_id)}
        self.message = f"Employee request {request_id} not found"
        super().__init__(self.message)


class RequestNotOwnedByEmployeeError(EmployeeRequestError):
    """Employee does not own this request."""

    status_code = 403
    error_code = "REQUEST_NOT_OWNED"
    message = "You do not own this request"


class RequestNotCancellableError(EmployeeRequestError):
    """Request is not in a cancellable state (only submitted)."""

    status_code = 400
    error_code = "REQUEST_NOT_CANCELLABLE"
    message = "Only submitted requests can be cancelled"


class RequestNotReviewableError(EmployeeRequestError):
    """Request is not in a reviewable state (only submitted can be reviewed)."""

    status_code = 400
    error_code = "REQUEST_NOT_REVIEWABLE"
    message = "Only submitted requests can be reviewed"

    def __init__(self, request_id: UUID, current_status: str) -> None:
        status = current_status
        self.public_context = {"request_id": str(request_id), "status": status}
        self.message = f"Request {request_id} is {status}, only submitted requests can be reviewed"
        super().__init__(self.message)
