"""Error handler for the Gmail Integration module.

Registers FastAPI exception handlers that catch domain-specific
GmailError exceptions and return consistent JSON error responses.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.modules.gmail.domain.exceptions import GmailError, RateLimitedException
from src.shared.messages import get_message, get_request_language


def register_gmail_error_handlers(app: FastAPI) -> None:
    """Register exception handlers for Gmail-related errors on the FastAPI app.

    Adds handlers for the ``GmailError`` base class and the
    ``RateLimitedException`` subclass (which includes Retry-After header).

    Args:
        app: The FastAPI application instance to register handlers on.
    """

    @app.exception_handler(RateLimitedException)
    async def _rate_limited_handler(request: Request, exc: RateLimitedException) -> JSONResponse:
        """Handle RateLimitedException with Retry-After header.

        Args:
            request: The incoming request that triggered the exception.
            exc: The RateLimitedException instance.

        Returns:
            A JSONResponse with 429 status and Retry-After header.
        """
        lang = get_request_language(request)
        headers = {}
        if exc.retry_after > 0:
            headers["Retry-After"] = str(exc.retry_after)

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": get_message(exc.error_code, lang),
                "details": {"retry_after": exc.retry_after},
            },
            headers=headers,
        )

    @app.exception_handler(GmailError)
    async def _gmail_error_handler(request: Request, exc: GmailError) -> JSONResponse:
        """Handle GmailError exceptions and return a JSON error response.

        Args:
            request: The incoming request that triggered the exception.
            exc: The GmailError instance raised during request processing.

        Returns:
            A JSONResponse with the appropriate status code and a body
            containing the error code and human-readable message.
        """
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": get_message(exc.error_code, lang),
                "details": None,
            },
        )
