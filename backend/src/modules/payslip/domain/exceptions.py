"""Domain exceptions for the Payslip module."""


class PayslipError(Exception):
    """Base exception for the payslip module."""

    status_code: int = 500
    error_code: str = "PAYSLIP_ERROR"
    message: str = "A payslip module error occurred"

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class PayslipNotFoundError(PayslipError):
    """Payslip not found."""

    status_code = 404
    error_code = "PAYSLIP_NOT_FOUND"
    message = "Payslip not found"

    def __init__(self, payslip_id: str) -> None:
        self.public_context = {"payslip_id": payslip_id}
        self.message = f"Payslip not found: {payslip_id}"
        super().__init__(self.message)


class PayslipNotPublishedError(PayslipError):
    """Payslip is not published yet (404 to avoid leaking existence)."""

    status_code = 404
    error_code = "PAYSLIP_NOT_PUBLISHED"
    message = "Payslip is not yet published"

    def __init__(self, payslip_id: str) -> None:
        self.public_context = {"payslip_id": payslip_id}
        self.message = f"Payslip not yet published: {payslip_id}"
        super().__init__(self.message)


class PayslipAlreadyExistsError(PayslipError):
    """Payslip already exists for this employee and period."""

    status_code = 409
    error_code = "PAYSLIP_ALREADY_EXISTS"
    message = "A payslip already exists for this employee and period"

    def __init__(self, employee_id: str, period_month: str) -> None:
        self.public_context = {"employee_id": employee_id, "period_month": period_month}
        self.message = f"Payslip already exists for employee {employee_id} period {period_month}"
        super().__init__(self.message)


class PayslipAlreadyPublishedError(PayslipError):
    """Payslip is already published and cannot be modified."""

    status_code = 400
    error_code = "PAYSLIP_ALREADY_PUBLISHED"
    message = "Cannot modify a published payslip"

    def __init__(self, payslip_id: str) -> None:
        self.public_context = {"payslip_id": payslip_id}
        self.message = f"Payslip already published: {payslip_id}"
        super().__init__(self.message)


class PayslipNotDraftError(PayslipError):
    """Payslip is not in draft status for the requested operation."""

    status_code = 400
    error_code = "PAYSLIP_NOT_DRAFT"
    message = "Payslip must be in draft status"

    def __init__(self, payslip_id: str) -> None:
        self.public_context = {"payslip_id": payslip_id}
        self.message = f"Payslip is not draft: {payslip_id}"
        super().__init__(self.message)
