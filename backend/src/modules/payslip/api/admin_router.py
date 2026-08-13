"""FastAPI router for HR admin Payslip management.

All endpoints require HR (admin) authentication.
Provides create, update, publish, delete, and list operations for Payslips.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from src.modules.identity.api.admin_router import HRUserDep
from src.modules.payslip.api.schemas import (
    AdminPayslipListResponse,
    CreatePayslipRequest,
    PayslipResponse,
    UpdatePayslipRequest,
)
from src.modules.payslip.application.payslip_hr_service import PayslipHRService
from src.modules.payslip.container import get_payslip_hr_service
from src.modules.payslip.domain.entities import PayslipStatus
from src.modules.payslip.domain.exceptions import (
    PayslipAlreadyExistsError,
    PayslipAlreadyPublishedError,
    PayslipNotDraftError,
    PayslipNotFoundError,
)

admin_payslip_router = APIRouter(
    prefix="/api/hr/payslips",
    tags=["hr", "payslips"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@admin_payslip_router.get(
    "",
    response_model=AdminPayslipListResponse,
)
async def list_payslips(
    hr_user: HRUserDep,
    page: int = 1,
    page_size: int = 20,
    employee_id: str | None = None,
    status: str | None = None,
    period_month: str | None = None,
    service: PayslipHRService = Depends(get_payslip_hr_service),
) -> AdminPayslipListResponse:
    """List payslips with optional filters.

    Supports filtering by employee_id, status (draft/published),
    and period_month (YYYY-MM format).
    """
    # Parse optional filters
    emp_id = None
    if employee_id:
        try:
            from uuid import UUID

            emp_id = UUID(employee_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid employee_id")

    stat = None
    if status:
        try:
            stat = PayslipStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid status. Must be 'draft' or 'published'",
            )

    per = None
    if period_month:
        try:
            per = date.fromisoformat(period_month + "-01")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid period_month. Use YYYY-MM format",
            )

    # Clamp pagination parameters
    page = max(1, page)
    page_size = min(max(1, page_size), 100)

    payslips, total = await service.list_payslips(
        admin=hr_user,
        page=page,
        page_size=page_size,
        employee_id=emp_id,
        status=stat,
        period_month=per,
    )

    return AdminPayslipListResponse(
        payslips=[PayslipResponse.model_validate(p) for p in payslips],
        total=total,
        page=page,
        page_size=page_size,
    )


@admin_payslip_router.post(
    "",
    response_model=PayslipResponse,
    status_code=201,
)
async def create_payslip(
    hr_user: HRUserDep,
    request: CreatePayslipRequest,
    service: PayslipHRService = Depends(get_payslip_hr_service),
) -> PayslipResponse:
    """Create a new draft Payslip for an Employee and period.

    Enforces uniqueness: at most one payslip per Employee per period.
    """
    # Normalize period_month to first day of month
    normalized_period = request.period_month.replace(day=1)

    try:
        payslip = await service.create_draft(
            admin=hr_user,
            employee_id=request.employee_id,
            period_month=normalized_period,
            gross_salary=request.gross_salary,
            deductions=request.deductions,
            insurance_employee=request.insurance_employee,
            taxable_income=request.taxable_income,
            pit_amount=request.pit_amount,
            net_salary=request.net_salary,
            pdf_url=request.pdf_url,
        )
    except PayslipAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=e.message)

    return PayslipResponse.model_validate(payslip)


@admin_payslip_router.get(
    "/{payslip_id}",
    response_model=PayslipResponse,
)
async def get_payslip(
    payslip_id: str,
    hr_user: HRUserDep,
    service: PayslipHRService = Depends(get_payslip_hr_service),
) -> PayslipResponse:
    """Get a specific payslip by ID (any status)."""
    try:
        from uuid import UUID

        pid = UUID(payslip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payslip_id")

    try:
        payslip = await service.get_payslip_by_id(
            admin=hr_user,
            payslip_id=pid,
        )
    except PayslipNotFoundError:
        raise HTTPException(status_code=404, detail="Payslip not found")

    return PayslipResponse.model_validate(payslip)


@admin_payslip_router.patch(
    "/{payslip_id}",
    response_model=PayslipResponse,
)
async def update_payslip(
    payslip_id: str,
    hr_user: HRUserDep,
    request: UpdatePayslipRequest,
    service: PayslipHRService = Depends(get_payslip_hr_service),
) -> PayslipResponse:
    """Update a draft Payslip values.

    Only draft payslips can be updated. Only provided fields are modified.
    """
    try:
        from uuid import UUID

        pid = UUID(payslip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payslip_id")

    try:
        payslip = await service.update_draft(
            admin=hr_user,
            payslip_id=pid,
            gross_salary=request.gross_salary,
            deductions=request.deductions,
            insurance_employee=request.insurance_employee,
            taxable_income=request.taxable_income,
            pit_amount=request.pit_amount,
            net_salary=request.net_salary,
            pdf_url=request.pdf_url,
        )
    except PayslipNotFoundError:
        raise HTTPException(status_code=404, detail="Payslip not found")
    except PayslipNotDraftError as e:
        raise HTTPException(status_code=400, detail=e.message)

    return PayslipResponse.model_validate(payslip)


@admin_payslip_router.post(
    "/{payslip_id}/publish",
    response_model=PayslipResponse,
)
async def publish_payslip(
    payslip_id: str,
    hr_user: HRUserDep,
    service: PayslipHRService = Depends(get_payslip_hr_service),
) -> PayslipResponse:
    """Publish a draft Payslip.

    Once published, the payslip becomes visible to the Employee
    and can no longer be modified or deleted.
    """
    try:
        from uuid import UUID

        pid = UUID(payslip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payslip_id")

    try:
        payslip = await service.publish(
            admin=hr_user,
            payslip_id=pid,
        )
    except PayslipNotFoundError:
        raise HTTPException(status_code=404, detail="Payslip not found")
    except PayslipAlreadyPublishedError as e:
        raise HTTPException(status_code=400, detail=e.message)

    return PayslipResponse.model_validate(payslip)


@admin_payslip_router.delete(
    "/{payslip_id}",
    status_code=204,
)
async def delete_payslip(
    payslip_id: str,
    hr_user: HRUserDep,
    service: PayslipHRService = Depends(get_payslip_hr_service),
) -> None:
    """Delete a draft Payslip.

    Only draft payslips can be deleted.
    """
    try:
        from uuid import UUID

        pid = UUID(payslip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payslip_id")

    try:
        await service.delete(
            admin=hr_user,
            payslip_id=pid,
        )
    except PayslipNotFoundError:
        raise HTTPException(status_code=404, detail="Payslip not found")
    except PayslipNotDraftError as e:
        raise HTTPException(status_code=400, detail=e.message)
