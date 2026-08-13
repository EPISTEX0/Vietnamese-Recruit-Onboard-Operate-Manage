"""Error handler for the Identity & Auth module.

Registers FastAPI exception handlers that catch domain-specific
AuthError exceptions and return consistent JSON error responses.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.modules.identity.domain.exceptions import AuthError
from src.shared.error_logging import log_domain_exception
from src.shared.messages import get_request_language, resolve_error_message


def register_auth_error_handlers(app: FastAPI) -> None:
    """Register exception handlers for auth-related errors on the FastAPI app.

    Adds a single handler for the ``AuthError`` base class, which catches
    all subclass exceptions (InvalidStateError, GoogleAuthError,
    AccessDeniedError, etc.) and returns a uniform JSON error response.

    Args:
        app: The FastAPI application instance to register handlers on.

    Example:
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> register_auth_error_handlers(app)
    """

    @app.exception_handler(AuthError)
    async def _auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        """Handle AuthError exceptions and return a JSON error response.

        Args:
            request: The incoming request that triggered the exception.
            exc: The AuthError instance raised during request processing.

        Returns:
            A JSONResponse with the appropriate status code and a body
            containing the error code and human-readable message.
        """
        lang = get_request_language(request)
        log_domain_exception(exc, module="identity")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": resolve_error_message(exc, lang),
                }
            },
        )
