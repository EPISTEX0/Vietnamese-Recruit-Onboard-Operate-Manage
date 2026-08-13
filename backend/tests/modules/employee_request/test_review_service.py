"""Unit tests for EmployeeRequestReviewService.

Tests approve and reject logic including state validation,
audit logging, and error handling at the service layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.modules.employee_request.application.review_service import (
    EmployeeRequestReviewService,
)
from src.modules.employee_request.domain.entities import EmployeeRequest
from src.modules.employee_request.domain.enums import RequestStatus, RequestType
from src.modules.employee_request.domain.exceptions import (
    RequestNotFoundError,
    RequestNotReviewableError,
)
from src.modules.identity.domain.entities import AuditActionType, User, UserRole


@pytest.fixture
def admin_user() -> User:
    """Create a test admin user."""
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


@pytest.fixture
def mock_repo() -> AsyncMock:
    """Create a mock repository."""
    return AsyncMock()


@pytest.fixture
def mock_audit_service() -> AsyncMock:
    """Create a mock audit service."""
    return AsyncMock()


@pytest.fixture
def review_service(
    mock_repo: AsyncMock,
    mock_audit_service: AsyncMock,
) -> EmployeeRequestReviewService:
    """Create a review service with mocked dependencies."""
    return EmployeeRequestReviewService(
        repo=mock_repo,
        audit_service=mock_audit_service,
    )


def _make_submitted_request(
    employee_id: UUID | None = None,
    request_type: RequestType = RequestType.LEAVE,
) -> EmployeeRequest:
    """Helper to create a submitted EmployeeRequest."""
    return EmployeeRequest(
        id=uuid4(),
        employee_id=employee_id or uuid4(),
        request_type=request_type,
        status=RequestStatus.SUBMITTED,
        submitted_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


class TestApproveRequest:
    """Tests for approve_request."""

    async def test_approve_submitted_request_success(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Approving a submitted request succeeds and writes audit log."""
        request = _make_submitted_request()
        mock_repo.get_by_id_with_lock.return_value = request
        mock_repo.update.return_value = request

        result = await review_service.approve_request(
            request_id=request.id,
            admin_user=admin_user,
            review_reason="Looks good",
        )

        assert result.status == RequestStatus.APPROVED
        assert result.reviewed_by_user_id == admin_user.id
        assert result.review_reason == "Looks good"
        assert result.reviewed_at is not None

        mock_repo.get_by_id_with_lock.assert_awaited_once_with(request.id)
        mock_repo.update.assert_awaited_once_with(request)
        mock_audit_service.log_action.assert_awaited_once_with(
            admin=admin_user,
            action_type=AuditActionType.REQUEST_APPROVE,
            details={
                "request_id": str(request.id),
                "employee_id": str(request.employee_id),
                "request_type": request.request_type.value,
                "review_reason": "Looks good",
            },
        )

    async def test_approve_without_reason_succeeds(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Approving without a reason still succeeds."""
        request = _make_submitted_request()
        mock_repo.get_by_id_with_lock.return_value = request
        mock_repo.update.return_value = request

        result = await review_service.approve_request(
            request_id=request.id,
            admin_user=admin_user,
            review_reason=None,
        )

        assert result.status == RequestStatus.APPROVED
        assert result.review_reason is None

    async def test_approve_nonexistent_request_raises_not_found(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Approving a non-existent request raises RequestNotFoundError."""
        request_id = uuid4()
        mock_repo.get_by_id_with_lock.return_value = None

        with pytest.raises(RequestNotFoundError) as exc:
            await review_service.approve_request(
                request_id=request_id,
                admin_user=admin_user,
            )

        assert str(request_id) in str(exc.value)
        mock_repo.update.assert_not_called()
        mock_audit_service.log_action.assert_not_called()

    async def test_approve_non_submitted_request_raises_not_reviewable(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Approving a non-submitted request raises RequestNotReviewableError."""
        request = _make_submitted_request()
        request.status = RequestStatus.APPROVED
        mock_repo.get_by_id_with_lock.return_value = request

        with pytest.raises(RequestNotReviewableError):
            await review_service.approve_request(
                request_id=request.id,
                admin_user=admin_user,
            )

        mock_repo.update.assert_not_called()
        mock_audit_service.log_action.assert_not_called()


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


class TestRejectRequest:
    """Tests for reject_request."""

    async def test_reject_submitted_request_success(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Rejecting a submitted request succeeds and writes audit log."""
        request = _make_submitted_request()
        mock_repo.get_by_id_with_lock.return_value = request
        mock_repo.update.return_value = request

        result = await review_service.reject_request(
            request_id=request.id,
            admin_user=admin_user,
            review_reason="Budget constraints",
        )

        assert result.status == RequestStatus.REJECTED
        assert result.reviewed_by_user_id == admin_user.id
        assert result.review_reason == "Budget constraints"
        assert result.reviewed_at is not None

        mock_repo.get_by_id_with_lock.assert_awaited_once_with(request.id)
        mock_repo.update.assert_awaited_once_with(request)
        mock_audit_service.log_action.assert_awaited_once_with(
            admin=admin_user,
            action_type=AuditActionType.REQUEST_REJECT,
            details={
                "request_id": str(request.id),
                "employee_id": str(request.employee_id),
                "request_type": request.request_type.value,
                "review_reason": "Budget constraints",
            },
        )

    async def test_reject_nonexistent_request_raises_not_found(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Rejecting a non-existent request raises RequestNotFoundError."""
        request_id = uuid4()
        mock_repo.get_by_id_with_lock.return_value = None

        with pytest.raises(RequestNotFoundError):
            await review_service.reject_request(
                request_id=request_id,
                admin_user=admin_user,
                review_reason="Not needed",
            )

        mock_repo.update.assert_not_called()
        mock_audit_service.log_action.assert_not_called()

    async def test_reject_already_approved_request_raises_not_reviewable(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Rejecting an already approved request raises RequestNotReviewableError."""
        request = _make_submitted_request()
        request.status = RequestStatus.APPROVED
        mock_repo.get_by_id_with_lock.return_value = request

        with pytest.raises(RequestNotReviewableError):
            await review_service.reject_request(
                request_id=request.id,
                admin_user=admin_user,
                review_reason="Changed mind",
            )

        mock_repo.update.assert_not_called()
        mock_audit_service.log_action.assert_not_called()

    async def test_reject_already_cancelled_request_raises_not_reviewable(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Rejecting a cancelled request raises RequestNotReviewableError."""
        request = _make_submitted_request()
        request.status = RequestStatus.CANCELLED
        mock_repo.get_by_id_with_lock.return_value = request

        with pytest.raises(RequestNotReviewableError):
            await review_service.reject_request(
                request_id=request.id,
                admin_user=admin_user,
                review_reason="Too late",
            )

        mock_repo.update.assert_not_called()
        mock_audit_service.log_action.assert_not_called()

    async def test_reject_without_reason_raises_error(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
    ) -> None:
        """Rejecting without a decision_reason raises ValueError."""
        request = _make_submitted_request()
        mock_repo.get_by_id_with_lock.return_value = request

        with pytest.raises(ValueError, match="Rejection reason"):
            await review_service.reject_request(
                request_id=request.id,
                admin_user=admin_user,
                review_reason=None,
            )

        with pytest.raises(ValueError, match="Rejection reason"):
            await review_service.reject_request(
                request_id=request.id,
                admin_user=admin_user,
                review_reason="   ",
            )

        mock_repo.get_by_id_with_lock.assert_not_called()
        mock_repo.update.assert_not_called()
        mock_audit_service.log_action.assert_not_called()


# ---------------------------------------------------------------------------
# Cross-type behaviour
# ---------------------------------------------------------------------------


class TestCrossTypeReview:
    """Tests that review logic works for both leave and overtime."""

    @pytest.mark.parametrize(
        "request_type",
        [RequestType.LEAVE, RequestType.OVERTIME],
    )
    async def test_approve_both_types(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
        request_type: RequestType,
    ) -> None:
        """Both leave and overtime requests can be approved."""
        req = _make_submitted_request(request_type=request_type)
        mock_repo.get_by_id_with_lock.return_value = req
        mock_repo.update.return_value = req

        result = await review_service.approve_request(
            request_id=req.id,
            admin_user=admin_user,
        )

        assert result.status == RequestStatus.APPROVED

    @pytest.mark.parametrize(
        "request_type",
        [RequestType.LEAVE, RequestType.OVERTIME],
    )
    async def test_reject_both_types(
        self,
        review_service: EmployeeRequestReviewService,
        mock_repo: AsyncMock,
        mock_audit_service: AsyncMock,
        admin_user: User,
        request_type: RequestType,
    ) -> None:
        """Both leave and overtime requests can be rejected."""
        req = _make_submitted_request(request_type=request_type)
        mock_repo.get_by_id_with_lock.return_value = req
        mock_repo.update.return_value = req

        result = await review_service.reject_request(
            request_id=req.id,
            admin_user=admin_user,
            review_reason="Not approved",
        )

        assert result.status == RequestStatus.REJECTED
        assert result.review_reason == "Not approved"
