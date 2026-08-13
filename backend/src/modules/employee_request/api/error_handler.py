"""Error handlers for Employee Request module."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.modules.employee_request.domain.exceptions import (
    EmployeeRequestError,
    LeaveEndBeforeStartError,
    LeaveOverlapError,
    OvertimeEndBeforeStartError,
    OvertimeOverlapError,
    RequestNotCancellableError,
    RequestNotFoundError,
    RequestNotOwnedByEmployeeError,
    RequestNotReviewableError,
)
from src.shared.error_logging import log_domain_exception
from src.shared.messages import get_request_language, resolve_error_message


def register_employee_request_error_handlers(app: FastAPI) -> None:
    """Register error handlers for employee request module exceptions."""

    @app.exception_handler(EmployeeRequestError)
    async def employee_request_error_handler(
        request: Request,
        exc: EmployeeRequestError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(OvertimeEndBeforeStartError)
    async def overtime_end_before_start_handler(
        request: Request,
        exc: OvertimeEndBeforeStartError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(OvertimeOverlapError)
    async def overtime_overlap_handler(
        request: Request,
        exc: OvertimeOverlapError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(LeaveEndBeforeStartError)
    async def leave_end_before_start_handler(
        request: Request,
        exc: LeaveEndBeforeStartError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(LeaveOverlapError)
    async def leave_overlap_handler(
        request: Request,
        exc: LeaveOverlapError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(RequestNotFoundError)
    async def request_not_found_handler(
        request: Request,
        exc: RequestNotFoundError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(RequestNotOwnedByEmployeeError)
    async def request_not_owned_handler(
        request: Request,
        exc: RequestNotOwnedByEmployeeError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(RequestNotCancellableError)
    async def request_not_cancellable_handler(
        request: Request,
        exc: RequestNotCancellableError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(RequestNotReviewableError)
    async def request_not_reviewable_handler(
        request: Request,
        exc: RequestNotReviewableError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee_request")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )
