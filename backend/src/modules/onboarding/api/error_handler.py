"""Error handler for the Onboarding module.

Registers FastAPI exception handlers that catch domain-specific
``OnboardingError`` exceptions and return consistent JSON error responses.

The module follows the established pattern: a single base exception
``OnboardingError`` (with ``status_code``, ``error_code``, ``message``) and a
registered FastAPI handler that maps any subclass to a uniform JSON body,
consistent with ``RecruitmentError``/``EmployeeError``.

Requirements: 3.5, 4.4, 4.5, 4.6, 5.6, 6.5, 6.6, 8.2
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.modules.onboarding.domain.exceptions import OnboardingError
from src.shared.error_logging import log_domain_exception
from src.shared.messages import get_request_language, resolve_error_message


def register_onboarding_error_handlers(app: FastAPI) -> None:
    """Register exception handlers for onboarding-related errors on the FastAPI app.

    Adds a single handler for the ``OnboardingError`` base class, which catches
    all subclass exceptions (OnboardingProcessNotFoundError,
    OnboardingTaskNotFoundError, InvalidTaskStatusError,
    InvalidProcessStatusFilterError, OnboardingAuthorizationError,
    OnboardingActivationError, AuditWriteError, InactiveEmployeeAccessError,
    etc.) and returns a uniform JSON error response using the exception's
    ``status_code``, ``error_code``, and ``message``.

    Args:
        app: The FastAPI application instance to register handlers on.

    Example:
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> register_onboarding_error_handlers(app)
    """

    @app.exception_handler(OnboardingError)
    async def _onboarding_error_handler(request: Request, exc: OnboardingError) -> JSONResponse:
        """Handle OnboardingError exceptions and return a JSON error response.

        Args:
            request: The incoming request that triggered the exception.
            exc: The OnboardingError instance raised during request processing.

        Returns:
            A JSONResponse with the appropriate status code and a body
            containing the error code and human-readable message.
        """
        lang = get_request_language(request)
        log_domain_exception(exc, module="onboarding")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": resolve_error_message(exc, lang),
                "details": None,
            },
        )
