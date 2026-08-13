"""API-layer journey tests for Job Application assignment and promotion (GH #186).

Tests the /api/recruitment/job-applications/{id}/assignment and
/api/recruitment/job-applications/{id}/promote endpoints.

Uses the established pattern from test_inbox_api_auth.py: build a minimal
test app with overridden dependencies and TestClient.

Covers:
- HR/Admin can assign, unassign, and promote.
- Non-HR (Employee) gets 403 on all endpoints.
- Unauthenticated requests get 401.
- Dismissed Job Applications cannot be assigned or promoted.
- Missing applicant_name/applicant_email returns 409.
- Promotion is idempotent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.api.error_handler import register_auth_error_handlers
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import UserRole
from src.modules.recruitment.api.job_application_router import (
    _build_service,
)
from src.modules.recruitment.api.job_application_router import (
    router as ja_router,
)
from src.modules.recruitment.domain.entities import Candidate, JobApplication
from src.modules.recruitment.domain.enums import (
    ApplicationSource,
    CandidateStatus,
    JobApplicationStatus,
)
from src.modules.recruitment.domain.exceptions import (
    JobApplicationAssignmentBlockedError,
    JobApplicationNotFoundError,
    JobApplicationPromotionBlockedError,
    JobOpeningNotFoundError,
    JobOpeningNotOpenError,
)


class FakeAdminUser:
    """Fake HR/Admin user for dependency override."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.role = UserRole.HR
        self.email = "admin@example.com"


class FakeEmployeeUser:
    """Fake non-admin user for dependency override."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.role = UserRole.USER
        self.email = "employee@example.com"


class FakeServiceCredential:
    """Non-user principal representing an automation/service credential."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.role = "service"
        self.email = "automation@service.invalid"


class FakeJobApplicationService:
    """Stand-in for JobApplicationService returning canned data.

    Maintains state to support idempotent test scenarios:
    subsequent promote calls for the same JA ID return the same Candidate.
    """

    def __init__(self) -> None:
        self.last_applicant_name: str | None = None
        self.last_applicant_email: str | None = None
        self._promoted: dict[str, tuple[JobApplication, Candidate]] = {}

    async def assign_to_job_opening(
        self,
        job_application_id: UUID,
        job_opening_id: UUID | None,
        user_id: UUID | None = None,
    ) -> JobApplication:
        return JobApplication(
            id=job_application_id,
            source_email_message_id=uuid4(),
            gmail_message_id="msg_assign",
            gmail_thread_id="thread_assign",
            source=ApplicationSource.DIRECT,
            sender_name="Sender",
            sender_email="sender@example.com",
            status=JobApplicationStatus.NEW,
            job_opening_id=job_opening_id,
            candidate_id=None,
            audit_history=[],
        )

    async def correct_source(
        self,
        job_application_id: UUID,
        source: ApplicationSource,
        user_id: UUID | None = None,
    ) -> JobApplication:
        return JobApplication(
            id=job_application_id,
            source_email_message_id=uuid4(),
            gmail_message_id="msg_source",
            gmail_thread_id="thread_source",
            source=source,
            sender_name="Sender",
            sender_email="sender@example.com",
            status=JobApplicationStatus.NEW,
            audit_history=[],
        )

    async def promote_to_candidate(
        self,
        job_application_id: UUID,
        applicant_name: str,
        applicant_email: str,
        job_opening_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> tuple[JobApplication, Candidate]:
        ja_key = str(job_application_id)
        if ja_key in self._promoted:
            return self._promoted[ja_key]

        self.last_applicant_name = applicant_name
        self.last_applicant_email = applicant_email
        candidate = Candidate(
            id=uuid4(),
            name=applicant_name,
            email=applicant_email,
            job_opening_id=job_opening_id,
            status=CandidateStatus.NEW,
        )
        app = JobApplication(
            id=job_application_id,
            source_email_message_id=uuid4(),
            gmail_message_id="msg_promote",
            gmail_thread_id="thread_promote",
            source=ApplicationSource.DIRECT,
            applicant_name=applicant_name,
            applicant_email=applicant_email,
            sender_name="Sender",
            sender_email="sender@example.com",
            status=JobApplicationStatus.PROMOTED,
            job_opening_id=job_opening_id,
            candidate_id=candidate.id,
            audit_history=[],
        )
        self._promoted[ja_key] = (app, candidate)
        return app, candidate


class FakeJobApplicationServiceError(FakeJobApplicationService):
    """Fake service that simulates various error conditions."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self._error = error

    async def assign_to_job_opening(
        self,
        job_application_id: UUID,
        job_opening_id: UUID | None,
        user_id: UUID | None = None,
    ) -> JobApplication:
        if self._error:
            raise self._error
        return await super().assign_to_job_opening(job_application_id, job_opening_id, user_id)

    async def promote_to_candidate(
        self,
        job_application_id: UUID,
        applicant_name: str,
        applicant_email: str,
        job_opening_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> tuple[JobApplication, Candidate]:
        if self._error:
            raise self._error
        return await super().promote_to_candidate(
            job_application_id, applicant_name, applicant_email, job_opening_id, user_id
        )


async def _raise_401() -> None:
    """Simulate unauthenticated user."""
    raise HTTPException(status_code=401, detail="Not authenticated")


def _build_app(
    user: object,
    service: FakeJobApplicationService | None = None,
) -> FastAPI:
    """Build a test FastAPI app with overridden dependencies."""
    app = FastAPI()
    app.include_router(ja_router)
    register_auth_error_handlers(app)
    from src.modules.recruitment.api.error_handler import register_recruitment_error_handlers

    register_recruitment_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: user
    if service is not None:
        app.dependency_overrides[_build_service] = lambda: service
    app.dependency_overrides[get_db_session] = lambda: AsyncMock(spec=AsyncSession)
    return app


# ─── Assignment Access Control ────────────────────────────────────────


class TestSourceCorrection:
    def test_hr_can_correct_application_source(self) -> None:
        app = _build_app(FakeAdminUser(), FakeJobApplicationService())
        client = TestClient(app)

        response = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/source",
            json={"source": "agency"},
        )

        assert response.status_code == 200
        assert response.json()["source"] == "agency"

    def test_employee_cannot_correct_application_source(self) -> None:
        app = _build_app(FakeEmployeeUser(), FakeJobApplicationService())
        client = TestClient(app)

        response = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/source",
            json={"source": "agency"},
        )

        assert response.status_code == 403


class TestAssignmentAccessControl:
    """Access control: HR/Admin can assign, Employee gets 403."""

    def test_hr_can_assign(self) -> None:
        """HR/Admin can assign a Job Application to a Job Opening."""
        service = FakeJobApplicationService()
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/assignment",
            json={"job_opening_id": str(uuid4())},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "job_opening_id" in data
        assert "status" in data

    def test_hr_can_unassign(self) -> None:
        """HR/Admin can unassign with null job_opening_id."""
        service = FakeJobApplicationService()
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/assignment",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_opening_id"] is None

    def test_employee_cannot_assign(self) -> None:
        """Non-admin Employee gets 403 on assignment."""
        service = FakeJobApplicationService()
        app = _build_app(FakeEmployeeUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/assignment",
            json={"job_opening_id": str(uuid4())},
        )
        assert resp.status_code == 403

    def test_unauthenticated_gets_401_on_assign(self) -> None:
        """Unauthenticated gets 401 on assignment."""
        app = FastAPI()
        app.include_router(ja_router)
        register_auth_error_handlers(app)
        app.dependency_overrides[get_current_user] = _raise_401
        app.dependency_overrides[get_db_session] = lambda: AsyncMock(spec=AsyncSession)

        client = TestClient(app)
        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/assignment",
            json={"job_opening_id": str(uuid4())},
        )
        assert resp.status_code == 401

    def test_assign_dismissed_returns_409(self) -> None:
        """Dismissed Job Application returns 409 on assignment."""
        err = JobApplicationAssignmentBlockedError("Job Application is dismissed")
        service = FakeJobApplicationServiceError(err)
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/assignment",
            json={"job_opening_id": str(uuid4())},
        )
        assert resp.status_code == 409

    def test_assign_not_found_returns_404(self) -> None:
        """Non-existent Job Application returns 404."""
        err = JobApplicationNotFoundError("Job Application not found: 123")
        service = FakeJobApplicationServiceError(err)
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/assignment",
            json={"job_opening_id": str(uuid4())},
        )
        assert resp.status_code == 404

    def test_assign_jo_not_open_returns_409(self) -> None:
        """Non-open Job Opening returns 409."""
        err = JobOpeningNotOpenError(uuid4(), "draft")
        service = FakeJobApplicationServiceError(err)
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/assignment",
            json={"job_opening_id": str(uuid4())},
        )
        assert resp.status_code == 409

    def test_assign_jo_not_found_returns_404(self) -> None:
        """Non-existent Job Opening returns 404."""
        err = JobOpeningNotFoundError("Job Opening not found: 123")
        service = FakeJobApplicationServiceError(err)
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/assignment",
            json={"job_opening_id": str(uuid4())},
        )
        assert resp.status_code == 404


# ─── Promotion Access Control ─────────────────────────────────────────


class TestPromotionAccessControl:
    """Access control: HR/Admin can promote, Employee gets 403."""

    def test_hr_can_promote(self) -> None:
        """HR/Admin can promote a Job Application to Candidate."""
        service = FakeJobApplicationService()
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Nguyen Van A",
                "applicant_email": "a@example.com",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "candidate_id" in data
        assert data["candidate_name"] == "Nguyen Van A"
        assert data["candidate_email"] == "a@example.com"

    def test_hr_can_promote_with_job_opening(self) -> None:
        """HR/Admin can promote with optional job_opening_id."""
        service = FakeJobApplicationService()
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        jo_id = str(uuid4())
        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Nguyen Van B",
                "applicant_email": "b@example.com",
                "job_opening_id": jo_id,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["candidate_name"] == "Nguyen Van B"
        assert data["candidate_email"] == "b@example.com"
        assert data["job_opening_id"] == jo_id

    def test_employee_cannot_promote(self) -> None:
        """Non-admin Employee gets 403 on promote."""
        service = FakeJobApplicationService()
        app = _build_app(FakeEmployeeUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Test",
                "applicant_email": "test@example.com",
            },
        )
        assert resp.status_code == 403

    def test_service_credential_cannot_promote(self) -> None:
        """Automation credentials are never accepted as HR principals."""
        service = FakeJobApplicationService()
        app = _build_app(FakeServiceCredential(), service)
        client = TestClient(app)

        response = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Test",
                "applicant_email": "test@example.com",
            },
        )

        assert response.status_code == 403
        assert not service._promoted

    def test_unauthenticated_gets_401_on_promote(self) -> None:
        """Unauthenticated gets 401 on promote."""
        app = FastAPI()
        app.include_router(ja_router)
        register_auth_error_handlers(app)
        app.dependency_overrides[get_current_user] = _raise_401
        app.dependency_overrides[get_db_session] = lambda: AsyncMock(spec=AsyncSession)

        client = TestClient(app)
        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Test",
                "applicant_email": "test@example.com",
            },
        )
        assert resp.status_code == 401

    def test_promote_dismissed_returns_409(self) -> None:
        """Dismissed Job Application returns 409 on promotion."""
        err = JobApplicationPromotionBlockedError("Job Application is dismissed")
        service = FakeJobApplicationServiceError(err)
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Test",
                "applicant_email": "test@example.com",
            },
        )
        assert resp.status_code == 409

    def test_promote_not_found_returns_404(self) -> None:
        """Non-existent Job Application returns 404."""
        err = JobApplicationNotFoundError("Job Application not found: 123")
        service = FakeJobApplicationServiceError(err)
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Test",
                "applicant_email": "test@example.com",
            },
        )
        assert resp.status_code == 404

    def test_promote_missing_name_returns_422(self) -> None:
        """Missing applicant_name returns 422 from Pydantic validation."""
        service = FakeJobApplicationService()
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_email": "test@example.com",
            },
        )
        assert resp.status_code == 422

    def test_promote_missing_email_returns_422(self) -> None:
        """Missing applicant_email returns 422 from Pydantic validation."""
        service = FakeJobApplicationService()
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Test",
            },
        )
        assert resp.status_code == 422

    def test_promote_jo_not_open_returns_409(self) -> None:
        """Non-open Job Opening returns 409."""
        err = JobOpeningNotOpenError(uuid4(), "closed")
        service = FakeJobApplicationServiceError(err)
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Test",
                "applicant_email": "test@example.com",
                "job_opening_id": str(uuid4()),
            },
        )
        assert resp.status_code == 409

    def test_promote_jo_not_found_returns_404(self) -> None:
        """Non-existent Job Opening returns 404."""
        err = JobOpeningNotFoundError("Job Opening not found: 123")
        service = FakeJobApplicationServiceError(err)
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        resp = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Test",
                "applicant_email": "test@example.com",
                "job_opening_id": str(uuid4()),
            },
        )
        assert resp.status_code == 404

    def test_promote_idempotent_returns_same(self) -> None:
        """Repeated promotion with same data returns success."""
        service = FakeJobApplicationService()
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)

        ja_id = uuid4()
        resp1 = client.post(
            f"/api/recruitment/job-applications/{ja_id}/promote",
            json={
                "applicant_name": "Nguyen Van A",
                "applicant_email": "a@example.com",
            },
        )
        assert resp1.status_code == 200
        data1 = resp1.json()

        resp2 = client.post(
            f"/api/recruitment/job-applications/{ja_id}/promote",
            json={
                "applicant_name": "Nguyen Van A",
                "applicant_email": "a@example.com",
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["candidate_id"] == data1["candidate_id"]

    def test_same_person_can_be_promoted_for_separate_job_applications(self) -> None:
        """Separate applications preserve independent outcomes for the same person."""
        service = FakeJobApplicationService()
        app = _build_app(FakeAdminUser(), service)
        client = TestClient(app)
        first_opening_id = uuid4()
        second_opening_id = uuid4()

        first = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Nguyen Van A",
                "applicant_email": "a@example.com",
                "job_opening_id": str(first_opening_id),
            },
        )
        second = client.post(
            f"/api/recruitment/job-applications/{uuid4()}/promote",
            json={
                "applicant_name": "Nguyen Van A",
                "applicant_email": "a@example.com",
                "job_opening_id": str(second_opening_id),
            },
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["candidate_id"] != second.json()["candidate_id"]
        assert first.json()["job_opening_id"] == str(first_opening_id)
        assert second.json()["job_opening_id"] == str(second_opening_id)
