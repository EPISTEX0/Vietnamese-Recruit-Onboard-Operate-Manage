"""Third-party exception text must never reach an HTTP response body.

The infrastructure adapters wrap library failures by interpolating the
original exception into the message::

    raise StorageServiceUnavailableError(f"Cannot connect to MinIO: {exc}") from exc

A botocore ``S3Error``/``EndpointConnectionError`` string carries the endpoint
host, the bucket, and a request id. That belongs in the log and nowhere else,
so the API error body is built from the message catalog plus explicitly
declared context -- never from the raw instance message.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.modules.attendance.domain.exceptions import AttendanceError
from src.modules.employee.domain.exceptions import EmployeeError
from src.modules.employee_request.domain.exceptions import EmployeeRequestError
from src.modules.gmail.domain.exceptions import GmailError
from src.modules.identity.domain.exceptions import AuthError
from src.modules.onboarding.domain.exceptions import OnboardingError
from src.modules.payslip.domain.exceptions import PayslipError
from src.modules.recruitment.api.error_handler import (
    register_recruitment_error_handlers,
)
from src.modules.recruitment.domain.exceptions import (
    OCRExtractionError,
    RecruitmentError,
    StorageServiceUnavailableError,
)
from src.shared.messages import resolve_error_message

_BASE_EXCEPTIONS = (
    AttendanceError,
    AuthError,
    EmployeeError,
    EmployeeRequestError,
    GmailError,
    OnboardingError,
    PayslipError,
    RecruitmentError,
)

# A string no catalog entry could plausibly contain, standing in for the host,
# bucket, and request id a real botocore error would carry.
INFRA_DETAIL = "minio-internal.svc.cluster.local:9000/vroom-cv-bucket reqid=AF41C9"


class _FakeS3Error(Exception):
    """Stands in for a botocore error whose ``str()`` names internal hosts."""


def _build_app(exc: Exception) -> FastAPI:
    """An app whose single route raises ``exc`` through the real handlers."""
    app = FastAPI()
    register_recruitment_error_handlers(app)

    @app.get("/boom")
    async def _boom() -> None:
        raise exc

    return app


async def _get_body(app: FastAPI) -> tuple[int, str]:
    """Call ``/boom`` and return the status and the raw response text."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")
    return response.status_code, response.text


def _wrapped_storage_error() -> StorageServiceUnavailableError:
    """Build the error exactly as ``minio_client`` builds it."""
    cause = _FakeS3Error(INFRA_DETAIL)
    return StorageServiceUnavailableError(f"Cannot connect to MinIO: {cause}")


class TestInfrastructureDetailIsNotInTheBody:
    async def test_storage_error_body_omits_third_party_text(self) -> None:
        status, body = await _get_body(_build_app(_wrapped_storage_error()))

        assert status == 502
        assert INFRA_DETAIL not in body
        assert "minio-internal.svc.cluster.local" not in body
        assert "reqid=AF41C9" not in body

    async def test_ocr_error_body_omits_third_party_text(self) -> None:
        cause = _FakeS3Error(INFRA_DETAIL)
        exc = OCRExtractionError(f"OCR extraction failed for 'cv.pdf': {cause}")

        _status, body = await _get_body(_build_app(exc))

        assert INFRA_DETAIL not in body

    async def test_body_still_explains_the_failure(self) -> None:
        """Withholding the detail must not leave the client an empty message."""
        _status, body = await _get_body(_build_app(_wrapped_storage_error()))

        assert "STORAGE_SERVICE_UNAVAILABLE" in body
        payload = _wrapped_storage_error()
        assert payload.message  # the detail is still carried on the exception


class TestNoDomainExceptionRendersItsInstanceMessage:
    """The guarantee has to hold for classes nobody has written a test for."""

    @staticmethod
    def _concrete_subclasses() -> list[type]:
        """Every domain exception reachable from the module base classes."""
        seen: list[type] = []
        pending = list(_BASE_EXCEPTIONS)
        while pending:
            cls = pending.pop()
            for sub in cls.__subclasses__():
                if sub not in seen:
                    seen.append(sub)
                    pending.append(sub)
        return seen

    def test_every_subclass_withholds_a_poisoned_message(self) -> None:
        poison = "host=internal.svc.cluster.local reqid=DEADBEEF"
        offenders = []

        for cls in self._concrete_subclasses():
            exc = cls.__new__(cls)  # bypass varied __init__ signatures
            exc.message = f"wrapped failure: {poison}"
            if poison in resolve_error_message(exc, "vi"):
                offenders.append(cls.__name__)

        assert offenders == []


class TestInfrastructureDetailStillReachesTheLog:
    async def test_handler_logs_the_wrapped_message(self) -> None:
        """The detail is only useful if it is still recorded server-side.

        Capture is attached to the module's own logger rather than going
        through ``caplog``, whose root-propagation the rest of the suite can
        reconfigure -- this assertion must not depend on test ordering.
        """
        records: list[logging.LogRecord] = []

        class _Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Collect(level=logging.ERROR)
        target = logging.getLogger("src.shared.error_logging")
        previous_level, previous_disable = target.level, logging.root.manager.disable

        target.addHandler(handler)
        target.setLevel(logging.ERROR)
        logging.disable(logging.NOTSET)
        try:
            await _get_body(_build_app(_wrapped_storage_error()))
        finally:
            target.removeHandler(handler)
            target.setLevel(previous_level)
            logging.disable(previous_disable)

        assert any(INFRA_DETAIL in record.getMessage() for record in records)
