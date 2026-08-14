"""Tests for the Recruitment API error handler.

Covers two concerns for task 9.3:

* Endpoint request validation for ``ScheduleInterviewRequest`` — the
  ``duration_minutes`` (15–180), interviewer-count (1–10), and ``notes``
  (≤ 1000) bounds are enforced by Pydantic (a fast ``ValidationError`` the
  router surfaces as 422), while the past-``start`` rule is enforced in the
  service as a ``ValueError`` that the registered ``_value_error_handler`` maps
  to 422.
* Domain-exception → HTTP mapping for the six interview-calendar exceptions,
  asserted through the same throwaway-app + ``register_recruitment_error_handlers``
  harness the existing tests use.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.modules.recruitment.api.error_handler import (
    register_recruitment_error_handlers,
)
from src.modules.recruitment.api.schemas import ScheduleInterviewRequest
from src.modules.recruitment.application import interview_scheduler_service as candidate_service
from src.modules.recruitment.application.candidate_validators import (
    CandidateValidationError,
)
from src.modules.recruitment.application.review_service import (
    ReviewValidationError,
)
from src.modules.recruitment.domain.enums import CandidateStatus
from src.modules.recruitment.domain.exceptions import (
    CalendarEventCreateFailedError,
    CalendarEventUpdateFailedError,
    CalendarGrantMissingError,
    CandidateNotFoundError,
    CVDocumentNotFoundError,
    CVFileNotFoundError,
    GmailNotConnectedError,
    InterviewerMissingEmailError,
    InterviewerNotFoundError,
    InvalidStatusTransitionError,
    LLMParseError,
    NoInterviewToRescheduleError,
    OCRExtractionError,
    PipelineTimeoutError,
    RecruitmentError,
    StorageServiceUnavailableError,
)
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_employee,
)

# Fixed identifiers so the details payloads of the interviewer exceptions are
# deterministic and assertable.
UNMATCHED_INTERVIEWER_ID_1 = UUID("11111111-1111-1111-1111-111111111111")
UNMATCHED_INTERVIEWER_ID_2 = UUID("22222222-2222-2222-2222-222222222222")
MISSING_EMAIL_INTERVIEWER_ID = UUID("33333333-3333-3333-3333-333333333333")


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with recruitment error handlers registered."""
    app = FastAPI()
    register_recruitment_error_handlers(app)
    return app


@pytest.fixture
def app_with_routes(app: FastAPI) -> FastAPI:
    """Add test routes that raise each exception type."""

    @app.get("/test/candidate-not-found")
    async def raise_candidate_not_found():
        raise CandidateNotFoundError()

    @app.get("/test/cv-document-not-found")
    async def raise_cv_document_not_found():
        raise CVDocumentNotFoundError()

    @app.get("/test/invalid-status-transition")
    async def raise_invalid_status_transition():
        raise InvalidStatusTransitionError(current_status="rejected", attempted_action="accept")

    @app.get("/test/cv-file-not-found")
    async def raise_cv_file_not_found():
        raise CVFileNotFoundError()

    @app.get("/test/storage-unavailable")
    async def raise_storage_unavailable():
        raise StorageServiceUnavailableError()

    @app.get("/test/gmail-not-connected")
    async def raise_gmail_not_connected():
        raise GmailNotConnectedError()

    @app.get("/test/pipeline-timeout")
    async def raise_pipeline_timeout():
        raise PipelineTimeoutError()

    @app.get("/test/ocr-extraction-error")
    async def raise_ocr_extraction_error():
        raise OCRExtractionError()

    @app.get("/test/llm-parse-error")
    async def raise_llm_parse_error():
        raise LLMParseError()

    @app.get("/test/base-error")
    async def raise_base_error():
        raise RecruitmentError("Something went wrong")

    @app.get("/test/candidate-validation-error")
    async def raise_candidate_validation_error():
        raise CandidateValidationError([{"field": "email", "reason": "Invalid email format"}])

    @app.get("/test/review-validation-error")
    async def raise_review_validation_error():
        raise ReviewValidationError([{"field": "name", "reason": "Name is required"}])

    @app.get("/test/value-error")
    async def raise_value_error():
        raise ValueError("Invalid page number")

    @app.get("/test/calendar-grant-missing")
    async def raise_calendar_grant_missing():
        raise CalendarGrantMissingError()

    @app.get("/test/interviewer-not-found")
    async def raise_interviewer_not_found():
        raise InterviewerNotFoundError([UNMATCHED_INTERVIEWER_ID_1, UNMATCHED_INTERVIEWER_ID_2])

    @app.get("/test/interviewer-missing-email")
    async def raise_interviewer_missing_email():
        raise InterviewerMissingEmailError(MISSING_EMAIL_INTERVIEWER_ID)

    @app.get("/test/calendar-create-failed")
    async def raise_calendar_create_failed():
        raise CalendarEventCreateFailedError()

    @app.get("/test/calendar-update-failed")
    async def raise_calendar_update_failed():
        raise CalendarEventUpdateFailedError()

    @app.get("/test/no-interview-to-reschedule")
    async def raise_no_interview_to_reschedule():
        raise NoInterviewToRescheduleError()

    @app.get("/test/custom-message")
    async def raise_custom_message():
        raise CandidateNotFoundError("Candidate abc123 not found")

    return app


@pytest.fixture
async def client(app_with_routes: FastAPI) -> AsyncClient:
    """Create an async test client."""
    transport = ASGITransport(app=app_with_routes)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestCandidateNotFoundError:
    @pytest.mark.anyio
    async def test_returns_404(self, client: AsyncClient):
        response = await client.get("/test/candidate-not-found")
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/candidate-not-found")
        data = response.json()
        assert data["error_code"] == "CANDIDATE_NOT_FOUND"
        # Vietnamese is the default response language for this HRM.
        assert data["message"] == "Không tìm thấy ứng viên"

    @pytest.mark.anyio
    async def test_accept_language_en_returns_english_message(self, client: AsyncClient):
        response = await client.get("/test/candidate-not-found", headers={"Accept-Language": "en"})
        data = response.json()
        assert data["message"] == "Candidate not found"


class TestCVDocumentNotFoundError:
    @pytest.mark.anyio
    async def test_returns_404(self, client: AsyncClient):
        response = await client.get("/test/cv-document-not-found")
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/cv-document-not-found")
        data = response.json()
        assert data["error_code"] == "CV_DOCUMENT_NOT_FOUND"
        # Vietnamese is the default response language for this HRM.
        assert data["message"] == "Không tìm thấy tài liệu CV"


class TestInvalidStatusTransitionError:
    @pytest.mark.anyio
    async def test_returns_409(self, client: AsyncClient):
        response = await client.get("/test/invalid-status-transition")
        assert response.status_code == 409

    @pytest.mark.anyio
    async def test_returns_error_code_and_message(self, client: AsyncClient):
        response = await client.get("/test/invalid-status-transition")
        data = response.json()
        assert data["error_code"] == "INVALID_STATUS_TRANSITION"
        assert "rejected" in data["message"]
        assert "accept" in data["message"]


class TestCVFileNotFoundError:
    @pytest.mark.anyio
    async def test_returns_404(self, client: AsyncClient):
        response = await client.get("/test/cv-file-not-found")
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/cv-file-not-found")
        data = response.json()
        assert data["error_code"] == "CV_FILE_MISSING"


class TestStorageServiceUnavailableError:
    @pytest.mark.anyio
    async def test_returns_502(self, client: AsyncClient):
        response = await client.get("/test/storage-unavailable")
        assert response.status_code == 502

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/storage-unavailable")
        data = response.json()
        assert data["error_code"] == "STORAGE_SERVICE_UNAVAILABLE"


class TestGmailNotConnectedError:
    @pytest.mark.anyio
    async def test_returns_409(self, client: AsyncClient):
        response = await client.get("/test/gmail-not-connected")
        assert response.status_code == 409

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/gmail-not-connected")
        data = response.json()
        assert data["error_code"] == "GMAIL_NOT_CONNECTED"


class TestPipelineTimeoutError:
    @pytest.mark.anyio
    async def test_returns_504(self, client: AsyncClient):
        response = await client.get("/test/pipeline-timeout")
        assert response.status_code == 504

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/pipeline-timeout")
        data = response.json()
        assert data["error_code"] == "PIPELINE_TIMEOUT"


class TestOCRExtractionError:
    @pytest.mark.anyio
    async def test_returns_502(self, client: AsyncClient):
        response = await client.get("/test/ocr-extraction-error")
        assert response.status_code == 502

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/ocr-extraction-error")
        data = response.json()
        assert data["error_code"] == "OCR_EXTRACTION_FAILED"


class TestLLMParseError:
    @pytest.mark.anyio
    async def test_returns_502(self, client: AsyncClient):
        response = await client.get("/test/llm-parse-error")
        assert response.status_code == 502

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/llm-parse-error")
        data = response.json()
        assert data["error_code"] == "LLM_PARSE_FAILED"


class TestBaseRecruitmentError:
    @pytest.mark.anyio
    async def test_returns_500(self, client: AsyncClient):
        response = await client.get("/test/base-error")
        assert response.status_code == 500

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/base-error")
        data = response.json()
        assert data["error_code"] == "RECRUITMENT_ERROR"
        # The free-form constructor message is log-only; the body keeps the
        # catalog entry so a wrapped third-party error cannot leak through.
        assert data["message"] == "Lỗi module tuyển dụng"
        assert "Something went wrong" not in response.text


class TestCandidateValidationError:
    @pytest.mark.anyio
    async def test_returns_422(self, client: AsyncClient):
        response = await client.get("/test/candidate-validation-error")
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_returns_error_code_and_details(self, client: AsyncClient):
        response = await client.get("/test/candidate-validation-error")
        data = response.json()
        assert data["error_code"] == "CANDIDATE_VALIDATION_ERROR"
        assert data["message"] == "Candidate validation failed"
        assert data["details"] == {"errors": [{"field": "email", "reason": "Invalid email format"}]}


class TestReviewValidationError:
    @pytest.mark.anyio
    async def test_returns_422(self, client: AsyncClient):
        response = await client.get("/test/review-validation-error")
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_returns_error_code_and_details(self, client: AsyncClient):
        response = await client.get("/test/review-validation-error")
        data = response.json()
        assert data["error_code"] == "REVIEW_VALIDATION_ERROR"
        assert data["message"] == "Review validation failed"
        assert data["details"] == {"errors": [{"field": "name", "reason": "Name is required"}]}


class TestValueError:
    @pytest.mark.anyio
    async def test_returns_422(self, client: AsyncClient):
        response = await client.get("/test/value-error")
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_returns_error_code_and_message(self, client: AsyncClient):
        response = await client.get("/test/value-error")
        data = response.json()
        assert data["error_code"] == "VALIDATION_ERROR"
        assert data["message"] == "Invalid page number"
        assert data["details"] is None


class TestCustomMessage:
    @pytest.mark.anyio
    async def test_free_form_message_is_withheld(self, client: AsyncClient):
        """Only declared ``public_context`` reaches the body, never the message."""
        response = await client.get("/test/custom-message")
        data = response.json()
        assert data["message"] == "Không tìm thấy ứng viên"
        assert "abc123" not in response.text


class TestResponseFormat:
    @pytest.mark.anyio
    async def test_response_matches_error_schema(self, client: AsyncClient):
        """Verify response body matches ErrorResponse schema structure."""
        response = await client.get("/test/candidate-not-found")
        data = response.json()
        assert "error_code" in data
        assert "message" in data
        assert "details" in data
        assert data["details"] is None


# ---------------------------------------------------------------------------
# Task 9.3 — interview-calendar domain-exception mapping
# ---------------------------------------------------------------------------


class TestCalendarGrantMissingError:
    @pytest.mark.anyio
    async def test_returns_403(self, client: AsyncClient):
        response = await client.get("/test/calendar-grant-missing")
        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/calendar-grant-missing")
        data = response.json()
        assert data["error_code"] == "CALENDAR_GRANT_MISSING"
        assert "re-consent" in data["message"].lower()


class TestInterviewerNotFoundError:
    @pytest.mark.anyio
    async def test_returns_422(self, client: AsyncClient):
        response = await client.get("/test/interviewer-not-found")
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_returns_error_code_and_unmatched_ids(self, client: AsyncClient):
        response = await client.get("/test/interviewer-not-found")
        data = response.json()
        assert data["error_code"] == "INTERVIEWER_NOT_FOUND"
        assert data["details"] == {
            "unmatched_ids": [
                str(UNMATCHED_INTERVIEWER_ID_1),
                str(UNMATCHED_INTERVIEWER_ID_2),
            ]
        }


class TestInterviewerMissingEmailError:
    @pytest.mark.anyio
    async def test_returns_422(self, client: AsyncClient):
        response = await client.get("/test/interviewer-missing-email")
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_returns_error_code_and_interviewer_id(self, client: AsyncClient):
        response = await client.get("/test/interviewer-missing-email")
        data = response.json()
        assert data["error_code"] == "INTERVIEWER_MISSING_EMAIL"
        assert data["details"] == {"interviewer_id": str(MISSING_EMAIL_INTERVIEWER_ID)}


class TestCalendarEventCreateFailedError:
    @pytest.mark.anyio
    async def test_returns_502(self, client: AsyncClient):
        response = await client.get("/test/calendar-create-failed")
        assert response.status_code == 502

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/calendar-create-failed")
        data = response.json()
        assert data["error_code"] == "CALENDAR_CREATE_FAILED"


class TestCalendarEventUpdateFailedError:
    @pytest.mark.anyio
    async def test_returns_502(self, client: AsyncClient):
        response = await client.get("/test/calendar-update-failed")
        assert response.status_code == 502

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/calendar-update-failed")
        data = response.json()
        assert data["error_code"] == "CALENDAR_UPDATE_FAILED"


class TestNoInterviewToRescheduleError:
    @pytest.mark.anyio
    async def test_returns_409(self, client: AsyncClient):
        response = await client.get("/test/no-interview-to-reschedule")
        assert response.status_code == 409

    @pytest.mark.anyio
    async def test_returns_error_code(self, client: AsyncClient):
        response = await client.get("/test/no-interview-to-reschedule")
        data = response.json()
        assert data["error_code"] == "NO_INTERVIEW_TO_RESCHEDULE"


# ---------------------------------------------------------------------------
# Task 9.3 — ScheduleInterviewRequest Pydantic field validation (fast 422)
# ---------------------------------------------------------------------------


def _make_request(
    *,
    start: datetime | None = None,
    duration_minutes: int = 60,
    interviewer_ids: list[UUID] | None = None,
    notes: str | None = "Initial screening round.",
) -> ScheduleInterviewRequest:
    """Build a ``ScheduleInterviewRequest`` overriding one field at a time.

    Defaults form a fully valid request; ``start`` is well into the future so
    the model never trips the (service-enforced) future-``start`` rule, letting
    each field-bound test isolate a single Pydantic rule. Out-of-range overrides
    stay the correct *type* (e.g. an int duration of 14), so the model still
    raises ``ValidationError`` at runtime.
    """
    return ScheduleInterviewRequest(
        start=start if start is not None else datetime.now(UTC) + timedelta(days=1),
        duration_minutes=duration_minutes,
        interviewer_ids=interviewer_ids if interviewer_ids is not None else [uuid4()],
        notes=notes,
    )


class TestScheduleInterviewRequestValidation:
    """Pydantic-level bounds on ``ScheduleInterviewRequest`` (R1.2, R1.3, R1.5)."""

    def test_accepts_a_valid_request(self):
        request = _make_request()
        assert request.duration_minutes == 60
        assert len(request.interviewer_ids) == 1

    @pytest.mark.parametrize("duration", [14, 0, -30, 181, 240])
    def test_rejects_out_of_range_duration(self, duration: int):
        with pytest.raises(ValidationError):
            _make_request(duration_minutes=duration)

    @pytest.mark.parametrize("duration", [15, 90, 180])
    def test_accepts_boundary_durations(self, duration: int):
        request = _make_request(duration_minutes=duration)
        assert request.duration_minutes == duration

    def test_rejects_empty_interviewer_list(self):
        with pytest.raises(ValidationError):
            _make_request(interviewer_ids=[])

    def test_rejects_too_many_interviewers(self):
        with pytest.raises(ValidationError):
            _make_request(interviewer_ids=[uuid4() for _ in range(11)])

    @pytest.mark.parametrize("count", [1, 10])
    def test_accepts_boundary_interviewer_counts(self, count: int):
        request = _make_request(interviewer_ids=[uuid4() for _ in range(count)])
        assert len(request.interviewer_ids) == count

    def test_rejects_over_long_notes(self):
        with pytest.raises(ValidationError):
            _make_request(notes="x" * 1001)

    def test_accepts_notes_at_max_length(self):
        request = _make_request(notes="x" * 1000)
        assert request.notes is not None
        assert len(request.notes) == 1000

    def test_accepts_omitted_notes(self):
        request = _make_request(notes=None)
        assert request.notes is None


# ---------------------------------------------------------------------------
# Task 9.3 — past-``start`` rejection: service ValueError → 422 mapping
# ---------------------------------------------------------------------------


class TestPastStartValueErrorMapping:
    """The past-``start`` rule (R1.4) is a service ``ValueError`` → 422."""

    def test_schedule_with_past_start_raises_value_error(self):
        """``schedule_interview`` rejects a past ``start`` with a ``ValueError``.

        The future-``start`` rule is enforced inside the service (not by a
        Pydantic validator), and request-field validation runs before any
        Calendar call, so a past ``start`` is rejected with the Calendar adapter
        left untouched and the Candidate unchanged.
        """

        async def _run() -> None:
            candidate = make_candidate(status=CandidateStatus.NEW)
            interviewer = make_employee(email="interviewer@example.com")
            harness = build_calendar_harness(candidates=[candidate], employees=[interviewer])
            past_start = datetime.now(UTC) - timedelta(hours=1)

            with patch.object(candidate_service, "log_audit", harness.audit_sink):
                with pytest.raises(ValueError, match="future"):
                    await harness.service.schedule_interview(
                        candidate.id,
                        start=past_start,
                        duration_minutes=60,
                        interviewer_ids=[interviewer.id],
                        notes=None,
                    )

            # Rejected before any Calendar call; Candidate left unchanged.
            assert harness.calendar.was_called is False
            live = await harness.candidate_repo.get_by_id(candidate.id)
            assert live is not None
            assert live.status == CandidateStatus.NEW
            assert getattr(live, "calendar_event_id", None) is None

        asyncio.run(_run())

    @pytest.mark.anyio
    async def test_value_error_maps_to_422(self, client: AsyncClient):
        """A service ``ValueError`` surfaces as a 422 via ``_value_error_handler``.

        This is the same mapping the past-``start`` rejection relies on: the
        ``ScheduleInterviewRequest`` model accepts the value (it carries no
        future-``start`` validator) and the service raises ``ValueError``, which
        the registered handler turns into a 422 ``VALIDATION_ERROR`` response.
        """
        response = await client.get("/test/value-error")
        assert response.status_code == 422
        data = response.json()
        assert data["error_code"] == "VALIDATION_ERROR"
