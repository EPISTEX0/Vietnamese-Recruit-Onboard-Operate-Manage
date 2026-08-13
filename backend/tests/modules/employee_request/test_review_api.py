"""Integration tests for HR review API endpoints.

Tests the admin review queue, approve, and reject endpoints with
mocked dependencies following the project's test patterns.
"""

from __future__ import annotations

import os

os.environ["AUTH_GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["AUTH_GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ["AUTH_JWT_SECRET_KEY"] = "test-secret-key-32-chars-min-for-hs256!"
os.environ["AUTH_OAUTH_TOKEN_ENCRYPTION_KEY"] = "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw=="

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.employee_request.api.admin_router import (
    admin_employee_request_router,
)
from src.modules.employee_request.api.error_handler import (
    register_employee_request_error_handlers,
)
from src.modules.employee_request.api.schemas import (
    AdminEmployeeRequestItem,
)
from src.modules.employee_request.container import (
    get_employee_request_repository,
    get_employee_request_review_service,
)
from src.modules.employee_request.domain.exceptions import (
    RequestNotFoundError,
    RequestNotReviewableError,
)
from src.modules.employee_request.infrastructure.employee_request_repository import (
    SubmittedRequestWithEmployee,
)
from src.modules.identity.domain.entities import User, UserRole


def _make_admin() -> User:
    return User(
        id=uuid4(),
        email="admin@example.com",
        name="Admin User",
        avatar_url=None,
        google_sub=f"google-sub-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
        last_login=datetime.now(UTC),
        is_active=True,
        role=UserRole.HR,
    )


def _build_app(
    admin_user: User | None = None,
    mock_repo: AsyncMock | None = None,
    mock_review_service: AsyncMock | None = None,
) -> FastAPI:
    """Create a test app with optional overrides."""
    app = FastAPI()
    app.include_router(admin_employee_request_router)
    register_employee_request_error_handlers(app)

    if admin_user is not None:
        # Override get_current_user so require_admin still runs its role check
        from src.modules.identity.container import get_current_user

        async def _override_current_user() -> User:
            return admin_user

        app.dependency_overrides[get_current_user] = _override_current_user

    if mock_repo is not None:

        async def _override_repo() -> AsyncMock:
            return mock_repo

        app.dependency_overrides[get_employee_request_repository] = _override_repo

    if mock_review_service is not None:

        async def _override_service() -> AsyncMock:
            return mock_review_service

        app.dependency_overrides[get_employee_request_review_service] = _override_service

    return app


def _model_dump_kwargs(
    request_id,
    employee_id,
    status="submitted",
    request_type="leave",
) -> dict:
    """Simulates EmployeeRequest.model_dump() — no employee_name field."""
    return {
        "id": str(request_id),
        "employee_id": str(employee_id),
        "request_type": request_type,
        "status": status,
        "submitted_at": None,
        "updated_at": None,
        "reason": None,
        "work_date": None,
        "start_time": None,
        "end_time": None,
        "duration_minutes": None,
        "leave_type": None,
        "start_date": None,
        "end_date": None,
        "cancellation_reason": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListReviewQueue:
    """Tests for GET /api/hr/employee-requests."""

    def test_returns_200_with_submitted_requests(self) -> None:
        """Returns 200 with list of submitted requests."""
        admin = _make_admin()
        mock_repo = AsyncMock()

        req_id = uuid4()
        emp_id = uuid4()
        mock_request = MagicMock()
        mock_request.model_dump.return_value = _model_dump_kwargs(
            req_id,
            emp_id,
            status="submitted",
        )

        mock_repo.get_all_filtered = AsyncMock(
            return_value=[
                SubmittedRequestWithEmployee(
                    request=mock_request,
                    employee_name="Nguyen Van A",
                ),
            ],
        )

        app = _build_app(admin_user=admin, mock_repo=mock_repo)
        client = TestClient(app)
        response = client.get("/api/hr/employee-requests")

        assert response.status_code == 200
        data = response.json()
        assert len(data["requests"]) == 1
        assert data["requests"][0]["id"] == str(req_id)
        assert data["requests"][0]["employee_name"] == "Nguyen Van A"
        assert data["requests"][0]["request_type"] == "leave"
        assert data["requests"][0]["status"] == "submitted"


class TestApproveRequest:
    """Tests for POST /api/hr/employee-requests/{id}/approve."""

    def test_returns_200_on_success(self) -> None:
        """Returns 200 when approve succeeds."""
        admin = _make_admin()
        mock_service = AsyncMock()

        request_id = uuid4()
        emp_id = uuid4()
        item = AdminEmployeeRequestItem(
            **_model_dump_kwargs(request_id, emp_id, status="approved"),
            employee_name="Nguyen Van A",
        )
        mock_service.approve_request = AsyncMock(return_value=item)

        app = _build_app(admin_user=admin, mock_review_service=mock_service)
        client = TestClient(app)
        response = client.post(
            f"/api/hr/employee-requests/{request_id}/approve",
            json={"review_reason": "Approved by HR"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Request approved"
        assert data["request"]["status"] == "approved"
        assert data["request"]["id"] == str(request_id)

    def test_returns_400_when_not_submitted(self) -> None:
        """Returns 400 when request is not in SUBMITTED state."""
        admin = _make_admin()
        mock_service = AsyncMock()

        request_id = uuid4()
        mock_service.approve_request = AsyncMock(
            side_effect=RequestNotReviewableError(
                request_id=request_id,
                current_status="approved",
            ),
        )

        app = _build_app(admin_user=admin, mock_review_service=mock_service)
        client = TestClient(app)
        response = client.post(
            f"/api/hr/employee-requests/{request_id}/approve",
            json={"review_reason": None},
        )

        assert response.status_code == 400
        mock_service.approve_request.assert_awaited_once_with(
            request_id=request_id,
            admin_user=admin,
            review_reason=None,
        )
        data = response.json()
        # Vietnamese by default; the offending status and id are the context
        # the raise site declared publishable.
        assert "chỉ duyệt được yêu cầu đã gửi" in data["detail"].lower()
        assert "approved" in data["detail"]
        assert str(request_id) in data["detail"]

    def test_returns_404_when_not_found(self) -> None:
        """Returns 404 when request does not exist."""
        admin = _make_admin()
        mock_service = AsyncMock()

        request_id = uuid4()
        mock_service.approve_request = AsyncMock(
            side_effect=RequestNotFoundError(request_id),
        )

        app = _build_app(admin_user=admin, mock_review_service=mock_service)
        client = TestClient(app)
        response = client.post(
            f"/api/hr/employee-requests/{request_id}/approve",
            json={},
        )

        assert response.status_code == 404
        mock_service.approve_request.assert_awaited_once_with(
            request_id=request_id,
            admin_user=admin,
            review_reason=None,
        )
        data = response.json()
        assert str(request_id) in data["detail"]


class TestRejectRequest:
    """Tests for POST /api/hr/employee-requests/{id}/reject."""

    def test_returns_200_on_success(self) -> None:
        """Returns 200 when reject succeeds."""
        admin = _make_admin()
        mock_service = AsyncMock()

        request_id = uuid4()
        emp_id = uuid4()
        item = AdminEmployeeRequestItem(
            **_model_dump_kwargs(request_id, emp_id, status="rejected"),
            employee_name="Nguyen Van A",
        )
        mock_service.reject_request = AsyncMock(return_value=item)

        app = _build_app(admin_user=admin, mock_review_service=mock_service)
        client = TestClient(app)
        response = client.post(
            f"/api/hr/employee-requests/{request_id}/reject",
            json={"decision_reason": "Budget constraints"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Request rejected"
        assert data["request"]["status"] == "rejected"
        assert data["request"]["id"] == str(request_id)
        mock_service.reject_request.assert_awaited_once_with(
            request_id=request_id,
            admin_user=admin,
            review_reason="Budget constraints",
        )

    def test_returns_403_when_not_admin(self) -> None:
        """Returns 403 when authenticated user is not admin."""
        non_admin = User(
            id=uuid4(),
            email="user@example.com",
            name="Regular User",
            avatar_url=None,
            google_sub=f"google-sub-{uuid4().hex[:8]}",
            created_at=datetime.now(UTC),
            last_login=datetime.now(UTC),
            is_active=True,
            role=UserRole.USER,
        )
        app = _build_app(admin_user=non_admin)
        client = TestClient(app)
        response = client.post(
            f"/api/hr/employee-requests/{uuid4()}/reject",
            json={"decision_reason": "Not needed"},
        )

        assert response.status_code == 403


class TestListReviewQueueFilters:
    """Tests for GET /api/hr/employee-requests with filters."""

    def test_filters_by_request_type(self) -> None:
        """Filter by leave type returns only leave requests."""
        admin = _make_admin()
        mock_repo = AsyncMock()
        mock_repo.get_all_filtered = AsyncMock(return_value=[])

        app = _build_app(admin_user=admin, mock_repo=mock_repo)
        client = TestClient(app)
        response = client.get("/api/hr/employee-requests?request_type=leave")

        assert response.status_code == 200
        mock_repo.get_all_filtered.assert_awaited_once()
        call_kwargs = mock_repo.get_all_filtered.call_args.kwargs
        assert call_kwargs["request_type"].value == "leave"

    def test_filters_by_status(self) -> None:
        """Filter by approved status returns only approved requests."""
        admin = _make_admin()
        mock_repo = AsyncMock()
        mock_repo.get_all_filtered = AsyncMock(return_value=[])

        app = _build_app(admin_user=admin, mock_repo=mock_repo)
        client = TestClient(app)
        response = client.get("/api/hr/employee-requests?status=approved")

        assert response.status_code == 200
        mock_repo.get_all_filtered.assert_awaited_once()
        call_kwargs = mock_repo.get_all_filtered.call_args.kwargs
        assert call_kwargs["status"].value == "approved"

    def test_filters_by_employee_id(self) -> None:
        """Filter by employee_id returns only that employee's requests."""
        admin = _make_admin()
        mock_repo = AsyncMock()
        mock_repo.get_all_filtered = AsyncMock(return_value=[])

        emp_id = uuid4()
        app = _build_app(admin_user=admin, mock_repo=mock_repo)
        client = TestClient(app)
        response = client.get(f"/api/hr/employee-requests?employee_id={emp_id}")

        assert response.status_code == 200
        mock_repo.get_all_filtered.assert_awaited_once()
        call_kwargs = mock_repo.get_all_filtered.call_args.kwargs
        assert call_kwargs["employee_id"] == emp_id

    def test_filters_by_date_range(self) -> None:
        """Filter by date_from and date_to returns requests in range."""
        admin = _make_admin()
        mock_repo = AsyncMock()
        mock_repo.get_all_filtered = AsyncMock(return_value=[])

        app = _build_app(admin_user=admin, mock_repo=mock_repo)
        client = TestClient(app)
        response = client.get(
            "/api/hr/employee-requests?date_from=2026-06-01&date_to=2026-06-30"
        )

        assert response.status_code == 200
        mock_repo.get_all_filtered.assert_awaited_once()
        call_kwargs = mock_repo.get_all_filtered.call_args.kwargs
        assert call_kwargs["date_from"] == date(2026, 6, 1)
        assert call_kwargs["date_to"] == date(2026, 6, 30)

    def test_combined_filters(self) -> None:
        """Multiple filters can be combined."""
        admin = _make_admin()
        mock_repo = AsyncMock()
        mock_repo.get_all_filtered = AsyncMock(return_value=[])

        emp_id = uuid4()
        app = _build_app(admin_user=admin, mock_repo=mock_repo)
        client = TestClient(app)
        response = client.get(
            f"/api/hr/employee-requests?request_type=overtime&status=submitted&employee_id={emp_id}"
        )

        assert response.status_code == 200
        mock_repo.get_all_filtered.assert_awaited_once()
        call_kwargs = mock_repo.get_all_filtered.call_args.kwargs
        assert call_kwargs["request_type"].value == "overtime"
        assert call_kwargs["status"].value == "submitted"
        assert call_kwargs["employee_id"] == emp_id

    def test_reject_requires_review_reason(self) -> None:
        """Reject without review_reason returns 422."""
        admin = _make_admin()
        mock_service = AsyncMock()

        app = _build_app(admin_user=admin, mock_review_service=mock_service)
        client = TestClient(app)
        response = client.post(
            f"/api/hr/employee-requests/{uuid4()}/reject",
            json={},
        )

        assert response.status_code == 422

    def test_reject_with_empty_reason_returns_422(self) -> None:
        """Reject with empty review_reason returns 422."""
        admin = _make_admin()
        mock_service = AsyncMock()

        app = _build_app(admin_user=admin, mock_review_service=mock_service)
        client = TestClient(app)
        response = client.post(
            f"/api/hr/employee-requests/{uuid4()}/reject",
            json={"decision_reason": ""},
        )

        assert response.status_code == 422

    def test_reject_with_whitespace_only_reason_returns_422(self) -> None:
        """Reject with whitespace-only reason returns 422."""
        admin = _make_admin()
        mock_service = AsyncMock()

        app = _build_app(admin_user=admin, mock_review_service=mock_service)
        client = TestClient(app)
        response = client.post(
            f"/api/hr/employee-requests/{uuid4()}/reject",
            json={"decision_reason": "   "},
        )

        assert response.status_code == 422
