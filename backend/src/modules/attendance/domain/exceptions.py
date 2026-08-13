"""Domain exceptions for the Attendance module."""


class AttendanceError(Exception):
    """Base exception for the attendance module."""

    status_code: int = 500
    error_code: str = "ATTENDANCE_ERROR"
    message: str = "An attendance module error occurred"

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class AlreadyCheckedInError(AttendanceError):
    """Employee already checked in for today."""

    status_code = 409
    error_code = "ALREADY_CHECKED_IN"
    message = "Already checked in for today"


class NotCheckedInError(AttendanceError):
    """Employee has not checked in yet."""

    status_code = 400
    error_code = "NOT_CHECKED_IN"
    message = "Must check in before checking out"


class OfficeNetworkRequiredError(AttendanceError):
    """Employee must be on office network to check in/out.

    Raised when an employee attempts to check in or out from an IP
    address that is not in the organization's allowed network list.
    """

    status_code = 403
    error_code = "OFFICE_NETWORK_REQUIRED"
    message = "Attendance check-in is only allowed from approved office network."


class InvalidCidrError(AttendanceError):
    """CIDR format is invalid.

    Raised when a provided CIDR notation does not match the expected
    format (X.X.X.X/N where X is 0-255 and N is 0-32).
    """

    status_code = 400
    error_code = "INVALID_CIDR"
    message = "Invalid CIDR format"

    def __init__(self, cidr: str) -> None:
        self.public_context = {"cidr": cidr}
        self.message = f"Invalid CIDR: {cidr}"
        super().__init__(self.message)


class DuplicateCidrError(AttendanceError):
    """CIDR already exists in the allowlist."""

    status_code = 400
    error_code = "DUPLICATE_CIDR"
    message = "CIDR already exists in allowlist"

    def __init__(self, cidr: str) -> None:
        self.public_context = {"cidr": cidr}
        self.message = f"CIDR already exists: {cidr}"
        super().__init__(self.message)


class TooManyNetworksError(AttendanceError):
    """Exceeded maximum number of allowed networks."""

    status_code = 400
    error_code = "TOO_MANY_NETWORKS"
    message = "Too many network entries"

    def __init__(self, max_count: int) -> None:
        self.public_context = {"max_count": max_count}
        self.message = f"Too many entries (max {max_count})"
        super().__init__(self.message)


class CidrNotFoundError(AttendanceError):
    """CIDR not found in the allowlist."""

    status_code = 404
    error_code = "CIDR_NOT_FOUND"
    message = "CIDR not found in allowlist"

    def __init__(self, cidr: str) -> None:
        self.public_context = {"cidr": cidr}
        self.message = f"CIDR not found: {cidr}"
        super().__init__(self.message)
