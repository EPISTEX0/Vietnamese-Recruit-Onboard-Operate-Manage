"""FastAPI router for HR admin review of Employee Requests.

All endpoints require HR (admin) authentication.
Provides review queue listing, approve, and reject operations.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from src.modules.employee_request.api.schemas import (
    AdminEmployeeRequestItem,
    AdminReviewQueueResponse,
    RejectRequest,
    ReviewQueueFilterParams,
    ReviewRequest,
    ReviewResponse,
)
from src.modules.employee_request.application.review_service import (
    EmployeeRequestReviewService,
)
from src.modules.employee_request.container import (
    get_employee_request_repository,
    get_employee_request_review_service,
)
from src.modules.employee_request.domain.enums import RequestStatus
from src.modules.employee_request.infrastructure.employee_request_repository import (
    EmployeeRequestRepository,
)
from src.modules.identity.api.admin_router import HRUserDep

admin_employee_request_router = APIRouter(
    prefix="/api/hr/employee-requests",
    tags=["hr", "employee-requests"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@admin_employee_request_router.get(
    "",
    response_model=AdminReviewQueueResponse,
)
async def list_review_queue(
    hr_user: HRUserDep,
    filters: ReviewQueueFilterParams = Depends(),
    repo: EmployeeRequestRepository = Depends(get_employee_request_repository),
) -> AdminReviewQueueResponse:
    """List employee requests for HR review with optional filters.

    Supports filtering by request_type, status, date range, and employee.
    Defaults to returning all submitted requests when no filters provided.
    Results are newest first with employee name.
    """
    status_filter = filters.status if filters.status is not None else RequestStatus.SUBMITTED
    results = await repo.get_all_filtered(
        request_type=filters.request_type,
        status=status_filter,
        date_from=filters.date_from,
        date_to=filters.date_to,
        employee_id=filters.employee_id,
    )
    items = []
    for sw in results:
        item = AdminEmployeeRequestItem(
            **sw.request.model_dump(),
            employee_name=sw.employee_name,
        )
        items.append(item)
    return AdminReviewQueueResponse(requests=items)


@admin_employee_request_router.post(
    "/{request_id}/approve",
    response_model=ReviewResponse,
)
async def approve_request(
    request_id: UUID,
    body: ReviewRequest,
    hr_user: HRUserDep,
    review_service: EmployeeRequestReviewService = Depends(
        get_employee_request_review_service,
    ),
) -> ReviewResponse:
    """Approve a submitted employee request."""
    updated = await review_service.approve_request(
        request_id=request_id,
        admin_user=hr_user,
        review_reason=body.review_reason,
    )
    return ReviewResponse(
        message="Request approved",
        request=AdminEmployeeRequestItem.model_validate(updated),
    )


@admin_employee_request_router.post(
    "/{request_id}/reject",
    response_model=ReviewResponse,
)
async def reject_request(
    request_id: UUID,
    body: RejectRequest,
    hr_user: HRUserDep,
    review_service: EmployeeRequestReviewService = Depends(
        get_employee_request_review_service,
    ),
) -> ReviewResponse:
    """Reject a submitted employee request (reason required)."""
    updated = await review_service.reject_request(
        request_id=request_id,
        admin_user=hr_user,
        review_reason=body.decision_reason,
    )
    return ReviewResponse(
        message="Request rejected",
        request=AdminEmployeeRequestItem.model_validate(updated),
    )
