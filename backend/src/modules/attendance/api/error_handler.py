"""Error handlers for Attendance module."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.modules.attendance.domain.exceptions import (
    AlreadyCheckedInError,
    AttendanceError,
    CidrNotFoundError,
    DuplicateCidrError,
    InvalidCidrError,
    NotCheckedInError,
    OfficeNetworkRequiredError,
    TooManyNetworksError,
)
from src.shared.messages import get_request_language, resolve_error_message


def register_attendance_error_handlers(app: FastAPI) -> None:
    """Register error handlers for attendance module exceptions."""

    @app.exception_handler(AttendanceError)
    async def attendance_error_handler(
        request: Request,
        exc: AttendanceError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(AlreadyCheckedInError)
    async def already_checked_in_handler(
        request: Request,
        exc: AlreadyCheckedInError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(NotCheckedInError)
    async def not_checked_in_handler(
        request: Request,
        exc: NotCheckedInError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(OfficeNetworkRequiredError)
    async def office_network_required_handler(
        request: Request,
        exc: OfficeNetworkRequiredError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(InvalidCidrError)
    async def invalid_cidr_handler(
        request: Request,
        exc: InvalidCidrError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(DuplicateCidrError)
    async def duplicate_cidr_handler(
        request: Request,
        exc: DuplicateCidrError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(TooManyNetworksError)
    async def too_many_networks_handler(
        request: Request,
        exc: TooManyNetworksError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )

    @app.exception_handler(CidrNotFoundError)
    async def cidr_not_found_handler(
        request: Request,
        exc: CidrNotFoundError,
    ) -> JSONResponse:
        lang = get_request_language(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "detail": resolve_error_message(exc, lang),
            },
        )
