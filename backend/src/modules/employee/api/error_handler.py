"""Error handler for the Employee Management module.

Registers FastAPI exception handlers that catch domain-specific
EmployeeError exceptions and return consistent JSON error responses.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.modules.employee.domain.exceptions import EmployeeError
from src.shared.messages import get_request_language, resolve_error_message


def register_employee_error_handlers(app: FastAPI) -> None:
    """Register exception handlers for employee-related errors on the FastAPI app.

    Adds a single handler for the ``EmployeeError`` base class, which catches
    all subclass exceptions (DuplicateEmailError, EmployeeNotFoundError,
    DepartmentNotFoundError, etc.) and returns a uniform JSON error response.

    Args:
        app: The FastAPI application instance to register handlers on.

    Example:
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> register_employee_error_handlers(app)
    """

    @app.exception_handler(EmployeeError)
    async def _employee_error_handler(request: Request, exc: EmployeeError) -> JSONResponse:
        """Handle EmployeeError exceptions and return a JSON error response.

        Args:
            request: The incoming request that triggered the exception.
            exc: The EmployeeError instance raised during request processing.

        Returns:
            A JSONResponse with the appropriate status code and a body
            containing the error code and human-readable message.
        """
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": resolve_error_message(exc, lang),
                }
            },
        )
