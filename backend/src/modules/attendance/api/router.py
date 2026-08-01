"""FastAPI router for Attendance network configuration.

Defines the /api/attendance/settings/network endpoints for managing
the office network allowlist. All endpoints require authentication,
and write operations require HR/Admin role. Every admin write action
is recorded in the audit log.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.modules.attendance.api.schemas import (
    AttendanceListResponse,
    AttendanceRecordResponse,
    CheckInResponse,
    CheckOutResponse,
    CorrectionRequest,
    CorrectionResponse,
    HistoryResponse,
    NetworkAddRequest,
    NetworkAllowlistResponse,
    NetworkAllowlistUpdate,
)
from src.modules.attendance.application.attendance_service import AttendanceService
from src.modules.attendance.application.attendance_settings_service import (
    AttendanceSettingsService,
)
from src.modules.attendance.container import (
    get_attendance_audit_service,
    get_attendance_service,
    get_attendance_settings_service,
)
from src.modules.employee.api.dependencies import get_current_employee
from src.modules.employee.container import get_employee_repository
from src.modules.employee.domain.entities import Employee
from src.modules.employee.infrastructure.employee_repository import EmployeeRepository
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import AuditActionType, User, UserRole
from src.modules.identity.domain.exceptions import AccessDeniedError
from src.shared.messages import get_message

attendance_router = APIRouter(prefix="/api/attendance", tags=["attendance"])


def _require_hr(user: User = Depends(get_current_user)) -> User:
    """Dependency that requires HR role."""
    if user.role != UserRole.HR:
        raise AccessDeniedError()
    return user

@attendance_router.get("/settings/network", response_model=NetworkAllowlistResponse)
async def get_network_allowlist(
    user: User = Depends(get_current_user),
    service: AttendanceSettingsService = Depends(get_attendance_settings_service),
) -> NetworkAllowlistResponse:
    """Get the current attendance network allowlist.

    HR/Admin can view the full allowlist. Other authenticated users
    can also view (for transparency).
    """
    networks = await service.get_allowed_networks()
    return NetworkAllowlistResponse(networks=networks)


@attendance_router.put("/settings/network", response_model=NetworkAllowlistResponse)
async def update_network_allowlist(
    update: NetworkAllowlistUpdate,
    user: User = Depends(_require_hr),
    service: AttendanceSettingsService = Depends(get_attendance_settings_service),
    audit_service: AuditService = Depends(get_attendance_audit_service),
) -> NetworkAllowlistResponse:
    """Replace the entire attendance network allowlist.

    HR/Admin only. Audit-logged.
    """
    networks = await service.set_allowed_networks(update.networks)
    await audit_service.log_action(
        admin=user,
        action_type=AuditActionType.ATTENDANCE_NETWORK_UPDATE,
        details={"networks": networks, "count": len(networks)},
    )
    return NetworkAllowlistResponse(networks=networks)


@attendance_router.post("/settings/network/add", response_model=NetworkAllowlistResponse)
async def add_to_network_allowlist(
    add_request: NetworkAddRequest,
    user: User = Depends(_require_hr),
    service: AttendanceSettingsService = Depends(get_attendance_settings_service),
    audit_service: AuditService = Depends(get_attendance_audit_service),
) -> NetworkAllowlistResponse:
    """Add one or more CIDRs to the allowlist.

    HR/Admin only. Audit-logged.
    """
    networks = await service.add_networks(add_request.networks)
    await audit_service.log_action(
        admin=user,
        action_type=AuditActionType.ATTENDANCE_NETWORK_ADD,
        details={"added": add_request.networks, "total": len(networks)},
    )
    return NetworkAllowlistResponse(networks=networks)


@attendance_router.delete(
    "/settings/network",
    response_model=NetworkAllowlistResponse,
)
async def remove_from_network_allowlist(
    cidr: str = Query(..., description="CIDR notation to remove"),
    user: User = Depends(_require_hr),
    service: AttendanceSettingsService = Depends(get_attendance_settings_service),
    audit_service: AuditService = Depends(get_attendance_audit_service),
) -> NetworkAllowlistResponse:
    """Remove a CIDR from the allowlist.

    HR/Admin only. Audit-logged.
    """
    networks = await service.remove_network(cidr)
    await audit_service.log_action(
        admin=user,
        action_type=AuditActionType.ATTENDANCE_NETWORK_REMOVE,
        details={"removed": cidr, "remaining": len(networks)},
    )
    return NetworkAllowlistResponse(networks=networks)


# Employee-owned attendance endpoints

# Trusted proxy list - only these originating IPs can provide X-Forwarded-For.
TRUSTED_PROXIES: frozenset[str] = frozenset({"127.0.0.1", "::1"})


async def get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For from trusted proxies."""
    remote_addr = request.client.host if request.client else ""
    forwarded_for = request.headers.get("x-forwarded-for")

    if forwarded_for and remote_addr in TRUSTED_PROXIES:
        ips = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
        # Traverse IPs from right to left, stopping at the first untrusted IP
        for ip in reversed(ips):
            if ip not in TRUSTED_PROXIES:
                return ip
        # If all IPs in the chain are trusted, the real client is the leftmost one
        return ips[0]

    return remote_addr if remote_addr else "127.0.0.1"


def _require_active_employee(
    employee: Employee = Depends(get_current_employee),
) -> Employee:
    """Dependency that requires an active employee."""
    if employee is None or not employee.is_active:
        raise AccessDeniedError()
    return employee


@attendance_router.post("/me/check-in", response_model=CheckInResponse)
async def check_in(
    request: Request,
    employee: Employee = Depends(_require_active_employee),
    service: AttendanceService = Depends(get_attendance_service),
) -> CheckInResponse:
    """Check in the current employee for today.

    Idempotent: returns existing record if already checked in.
    """
    client_ip = await get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    record = await service.check_in(
        employee_id=employee.id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return CheckInResponse(
        message=get_message("CHECKED_IN_SUCCESS"),
        record=AttendanceRecordResponse.model_validate(record),
    )


@attendance_router.post("/me/check-out", response_model=CheckOutResponse)
async def check_out(
    request: Request,
    employee: Employee = Depends(_require_active_employee),
    service: AttendanceService = Depends(get_attendance_service),
) -> CheckOutResponse:
    """Check out the current employee for today.

    Idempotent: returns existing record if already checked out.
    """
    client_ip = await get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    record = await service.check_out(
        employee_id=employee.id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return CheckOutResponse(
        message=get_message("CHECKED_OUT_SUCCESS"),
        record=AttendanceRecordResponse.model_validate(record),
    )


@attendance_router.get("/me/today", response_model=AttendanceRecordResponse | None)
async def get_today_record(
    employee: Employee = Depends(_require_active_employee),
    service: AttendanceService = Depends(get_attendance_service),
) -> AttendanceRecordResponse | None:
    """Get today's attendance record for the current employee."""
    record = await service.get_today(employee.id)
    if record is None:
        return None
    return AttendanceRecordResponse.model_validate(record)


@attendance_router.get("/me/history", response_model=HistoryResponse)
async def get_attendance_history(
    year: int | None = Query(default=None, description="Year (e.g., 2026)", ge=2020, le=2100),
    month: int | None = Query(default=None, description="Month (1-12)", ge=1, le=12),
    days: int = Query(
        default=7,
        description="Recent days (used when year/month not specified)",
        ge=1,
        le=365,
    ),
    employee: Employee = Depends(_require_active_employee),
    service: AttendanceService = Depends(get_attendance_service),
) -> HistoryResponse:
    """Get attendance records for the current employee.

    If year and month are provided, returns records for that month.
    Otherwise returns records for the last ``days`` days (default 7).
    """
    # Validate: must provide both year+month or neither
    if (year is None) != (month is None):
        raise HTTPException(
            status_code=422,
            detail="Either provide both year and month, or neither (use days param)",
        )

    records = await service.get_history(
        employee_id=employee.id,
        year=year,
        month=month,
        days=days,
    )
    # Compute response metadata
    today = date.today()
    resp_year = year if year is not None else today.year
    resp_month = month if month is not None else today.month
    return HistoryResponse(
        records=[AttendanceRecordResponse.model_validate(r) for r in records],
        year=resp_year,
        month=resp_month,
    )


# HR Admin endpoints


@attendance_router.get("/records", response_model=AttendanceListResponse)
async def list_attendance_records(
    start_date: date = Query(..., description="Start date"),
    end_date: date = Query(..., description="End date"),
    employee_id: UUID | None = Query(default=None, description="Filter by employee ID"),
    status: Literal["checked_in", "completed"] | None = Query(
        default=None, description="Filter by status"
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Records per page"),
    user: User = Depends(_require_hr),
    service: AttendanceService = Depends(get_attendance_service),
    employee_repo: EmployeeRepository = Depends(get_employee_repository),
) -> AttendanceListResponse:
    """List attendance records across employees with filters.

    HR/Admin only. Returns paginated results with employee info joined.
    """
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must be >= start_date")
    records, total = await service.list_records(
        start_date=start_date,
        end_date=end_date,
        employee_id=employee_id,
        status=status,
        page=page,
        page_size=page_size,
    )

    # Batch-fetch employee details in one query
    employee_ids = list({r.employee_id for r in records})
    employees = await employee_repo.get_by_ids(employee_ids)

    enriched_records = []
    for record in records:
        record_dict = AttendanceRecordResponse.model_validate(record).model_dump()
        emp = employees.get(record.employee_id)
        if emp:
            record_dict["employee_name"] = emp.full_name
            record_dict["employee_code"] = emp.employee_code
        enriched_records.append(AttendanceRecordResponse(**record_dict))

    return AttendanceListResponse(
        records=enriched_records,
        total=total,
        page=page,
        page_size=page_size,
    )


@attendance_router.put("/records/{record_id}/correct", response_model=CorrectionResponse)
async def correct_attendance_record(
    record_id: UUID,
    correction: CorrectionRequest,
    user: User = Depends(_require_hr),
    service: AttendanceService = Depends(get_attendance_service),
    audit_service: AuditService = Depends(get_attendance_audit_service),
) -> CorrectionResponse:
    """Correct an attendance record with required reason.

    HR/Admin only. Correction and audit log are written atomically
    within the same DB transaction.
    """
    record = await service.correct_record(
        record_id=record_id,
        check_in_at=correction.check_in_at,
        check_out_at=correction.check_out_at,
        correction_reason=correction.correction_reason,
        corrected_by_user_id=user.id,
        admin=user,
        audit_service=audit_service,
    )

    return CorrectionResponse(
        message=get_message("ATTENDANCE_CORRECTED_SUCCESS"),
        record=AttendanceRecordResponse.model_validate(record),
    )
