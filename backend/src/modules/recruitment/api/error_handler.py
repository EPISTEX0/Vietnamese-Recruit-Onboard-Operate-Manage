"""Error handler for the Recruitment CV Pipeline module.

Registers FastAPI exception handlers that catch domain-specific
RecruitmentError exceptions and return consistent JSON error responses.

Requirements: 6.8, 7.3-7.5, 8.2-8.5, 9.3, 9.5-9.7, 10.4-10.8,
11.3, 11.5-11.6, 12.3, 12.5, 13.5-13.6, 14.3, 14.8
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.modules.recruitment.application.candidate_validators import (
    CandidateValidationError,
)
from src.modules.recruitment.application.review_service import (
    ReviewValidationError,
)
from src.modules.recruitment.domain.exceptions import RecruitmentError
from src.shared.error_logging import log_domain_exception
from src.shared.messages import get_message, get_request_language, resolve_error_message

logger = logging.getLogger(__name__)


def register_recruitment_error_handlers(app: FastAPI) -> None:
    """Register exception handlers for recruitment-related errors on the FastAPI app.

    Adds handlers for:
    - ``RecruitmentError`` base class (catches all domain exceptions)
    - ``CandidateValidationError`` (422 with field-level details)
    - ``ReviewValidationError`` (422 with field-level details)
    - ``ValueError`` (422 for general validation errors)

    Args:
        app: The FastAPI application instance to register handlers on.
    """

    @app.exception_handler(RecruitmentError)
    async def _recruitment_error_handler(request: Request, exc: RecruitmentError) -> JSONResponse:
        lang = get_request_language(request)
        """Handle RecruitmentError exceptions and return a JSON error response."""
        log_domain_exception(exc, module="recruitment")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": resolve_error_message(exc, lang),
                "details": exc.details,
            },
        )

    @app.exception_handler(CandidateValidationError)
    async def _candidate_validation_error_handler(
        request: Request, exc: CandidateValidationError
    ) -> JSONResponse:
        """Handle CandidateValidationError with 422 and field-level details."""
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "CANDIDATE_VALIDATION_ERROR",
                "message": "Candidate validation failed",
                "details": {"errors": exc.errors},
            },
        )

    @app.exception_handler(ReviewValidationError)
    async def _review_validation_error_handler(
        request: Request, exc: ReviewValidationError
    ) -> JSONResponse:
        """Handle ReviewValidationError with 422 and field-level details."""
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "REVIEW_VALIDATION_ERROR",
                "message": "Review validation failed",
                "details": {"errors": exc.errors},
            },
        )

    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        """Handle an unanticipated ``ValueError`` (incl. ``pydantic.ValidationError``,
        which subclasses it) with a fixed, localized 422 message.

        Registered app-wide, this is the catch-all for any ``ValueError`` a route
        does not itself translate into a domain exception -- including a Pydantic
        model failing to build outside request parsing, whose ``str(exc)`` echoes
        the raw ``input_value`` back. The instance message is logged server-side
        only, never placed in the response body.
        """
        lang = get_request_language(request)
        logger.exception("Unhandled ValueError on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "VALIDATION_ERROR",
                "message": get_message("VALIDATION_ERROR", lang),
                "details": None,
            },
        )
