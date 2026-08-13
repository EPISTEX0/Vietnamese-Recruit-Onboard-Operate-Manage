"""FastAPI router for the Employee Management module.

Defines the /api/employees/*, /api/departments/*, /api/positions/*,
and /api/documents/* endpoints for employee CRUD, department/position
management, Excel import, and document vault operations.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from src.modules.employee.api.dependencies import CurrentUserEmployee
from src.modules.employee.api.schemas import (
    DepartmentCreate,
    DepartmentResponse,
    DepartmentUpdate,
    DocumentResponse,
    EmployeeCreate,
    EmployeeListResponse,
    EmployeeResponse,
    EmployeeUpdate,
    ImportResult,
    PositionCreate,
    PositionResponse,
    PositionUpdate,
    PromoteCandidateRequest,
)
from src.modules.employee.application.department_service import DepartmentService
from src.modules.employee.application.document_service import DocumentService
from src.modules.employee.application.employee_service import EmployeeService
from src.modules.employee.application.import_service import ImportService
from src.modules.employee.application.position_service import PositionService
from src.modules.employee.container import (
    get_department_service,
    get_document_service,
    get_employee_service,
    get_import_service,
    get_position_service,
)
from src.modules.identity.api.admin_router import require_hr
from src.modules.identity.api.schemas import (
    EmployeeAccountCreateResponse,
    EmployeeAccountStatusResponse,
)
from src.modules.identity.application.auth_service import AuthService
from src.modules.identity.container import get_auth_service, get_current_user, get_user_repository
from src.modules.identity.domain.entities import User, UserRole
from src.modules.identity.infrastructure.user_repository import UserRepository

# ---------------------------------------------------------------------------
# Type aliases for injected dependencies
# ---------------------------------------------------------------------------

CurrentUserDep = Annotated[User, Depends(get_current_user)]
HRUserDep = Annotated[User, Depends(require_hr)]
EmployeeServiceDep = Annotated[EmployeeService, Depends(get_employee_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
DepartmentServiceDep = Annotated[DepartmentService, Depends(get_department_service)]
PositionServiceDep = Annotated[PositionService, Depends(get_position_service)]
ImportServiceDep = Annotated[ImportService, Depends(get_import_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

router = APIRouter(tags=["employees"])

employee_router = APIRouter(prefix="/api/employees", tags=["employees"])
department_router = APIRouter(prefix="/api/departments", tags=["departments"])
position_router = APIRouter(prefix="/api/positions", tags=["positions"])
document_router = APIRouter(prefix="/api/documents", tags=["documents"])

# ---------------------------------------------------------------------------
# Employee endpoints
# ---------------------------------------------------------------------------


@employee_router.get("", response_model=EmployeeListResponse)
async def list_employees(
    current_user: CurrentUserDep,
    current_employee: CurrentUserEmployee,
    employee_service: EmployeeServiceDep,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    search: str | None = Query(default=None, description="Search in name or email"),
    department_id: UUID | None = Query(default=None, description="Filter by department"),
    position_id: UUID | None = Query(default=None, description="Filter by position"),
    is_active: bool | None = Query(default=True, description="Filter by active status"),
) -> EmployeeListResponse:
    """List employees with pagination and optional filters.

    Ownership check: non-HR users can only see their own profile.
    """
    if current_user.role != UserRole.HR:
        if current_employee is None:
            return EmployeeListResponse(items=[], total=0, page=1, page_size=page_size)
        return EmployeeListResponse(
            items=[EmployeeResponse.model_validate(current_employee)],
            total=1,
            page=1,
            page_size=page_size,
        )

    items, total = await employee_service.list_employees(
        page=page,
        page_size=page_size,
        search=search,
        department_id=department_id,
        position_id=position_id,
        is_active=is_active,
    )
    return EmployeeListResponse(
        items=[EmployeeResponse.model_validate(emp) for emp in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@employee_router.get("/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: UUID,
    current_user: CurrentUserDep,
    current_employee: CurrentUserEmployee,
    employee_service: EmployeeServiceDep,
) -> EmployeeResponse:
    """Get a single employee by ID.

    Ownership check: non-HR users can only view their own profile.
    """
    employee = await employee_service.get_employee(employee_id)
    if current_user.role != UserRole.HR:
        if current_employee is None or employee.id != current_employee.id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: cannot view another employee's profile",
            )
    return EmployeeResponse.model_validate(employee)


@employee_router.get("/{employee_id}/account", response_model=EmployeeAccountStatusResponse)
async def get_employee_account(
    employee_id: UUID,
    current_user: HRUserDep,
    employee_service: EmployeeServiceDep,
    user_repository: UserRepositoryDep,
) -> EmployeeAccountStatusResponse:
    """Inspect Employee Account status."""
    employee = await employee_service.get_employee(employee_id)
    user = await user_repository.get_by_employee_id(employee.id)
    if user is None:
        return EmployeeAccountStatusResponse(exists=False)
    return EmployeeAccountStatusResponse(
        exists=True,
        user_id=user.id,
        email=user.email,
        role=user.role,
        must_change_password=user.must_change_password,
    )


@employee_router.post("/{employee_id}/account", response_model=EmployeeAccountCreateResponse)
async def create_employee_account(
    employee_id: UUID,
    current_user: HRUserDep,
    employee_service: EmployeeServiceDep,
    auth_service: AuthServiceDep,
) -> EmployeeAccountCreateResponse:
    """Create Employee Account with temporary password."""
    employee = await employee_service.get_employee(employee_id)
    if not employee.is_active:
        raise HTTPException(
            status_code=400,
            detail="Employee must be active before creating account",
        )
    user, temporary_password = await auth_service.create_employee_account(employee)
    return EmployeeAccountCreateResponse(
        user=EmployeeAccountStatusResponse(
            exists=True,
            user_id=user.id,
            email=user.email,
            role=user.role,
            must_change_password=user.must_change_password,
        ),
        temporary_password=temporary_password,
    )


@employee_router.delete("/{employee_id}/account", status_code=204)
async def delete_employee_account(
    employee_id: UUID,
    current_user: HRUserDep,
    employee_service: EmployeeServiceDep,
    auth_service: AuthServiceDep,
) -> None:
    """Delete Employee Account. Idempotent: succeeds whether or not an account exists."""
    employee = await employee_service.get_employee(employee_id)
    await auth_service.delete_employee_account(employee)


@employee_router.post("", response_model=EmployeeResponse, status_code=201)
async def create_employee(
    body: EmployeeCreate,
    current_user: HRUserDep,
    employee_service: EmployeeServiceDep,
) -> EmployeeResponse:
    """Create a new employee."""
    data = body.model_dump(exclude_unset=True)
    employee = await employee_service.create_employee(data)
    return EmployeeResponse.model_validate(employee)


@employee_router.put("/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: UUID,
    body: EmployeeUpdate,
    current_user: CurrentUserDep,
    current_employee: CurrentUserEmployee,
    employee_service: EmployeeServiceDep,
) -> EmployeeResponse:
    """Update an existing employee.

    Self-edit restriction: non-HR users can only update phone and address.
    """
    data = body.model_dump(exclude_unset=True)
    if current_user.role != UserRole.HR:
        allowed_fields = {"phone", "address"}
        disallowed = set(data.keys()) - allowed_fields
        if disallowed:
            raise HTTPException(
                status_code=403,
                detail="Employees can only update phone and address. Disallowed: "
                f"{', '.join(sorted(disallowed))}",
            )
        if current_employee is None or employee_id != current_employee.id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: cannot update another employee",
            )
    employee = await employee_service.update_employee(employee_id, data)
    return EmployeeResponse.model_validate(employee)


@employee_router.delete("/{employee_id}", response_model=EmployeeResponse)
async def delete_employee(
    employee_id: UUID,
    current_user: HRUserDep,
    employee_service: EmployeeServiceDep,
) -> EmployeeResponse:
    """Soft-delete an employee (set is_active=False)."""
    employee = await employee_service.delete_employee(employee_id)
    return EmployeeResponse.model_validate(employee)


@employee_router.post("/promote", response_model=EmployeeResponse, status_code=201)
async def promote_candidate(
    body: PromoteCandidateRequest,
    current_user: HRUserDep,
    employee_service: EmployeeServiceDep,
) -> EmployeeResponse:
    """Promote a candidate to employee."""
    data = body.model_dump(exclude_unset=True)
    employee = await employee_service.promote_candidate(data)
    return EmployeeResponse.model_validate(employee)


@employee_router.post("/import", response_model=ImportResult)
async def import_employees(
    current_user: HRUserDep,
    import_service: ImportServiceDep,
    file: UploadFile = File(..., description="Excel .xlsx file to import"),
) -> ImportResult:
    """Import employees from an Excel (.xlsx) file."""
    file_bytes = await file.read()
    result = await import_service.import_from_excel(file_bytes)
    return ImportResult(**result)


# ---------------------------------------------------------------------------
# Employee document endpoints
# ---------------------------------------------------------------------------


@employee_router.get("/{employee_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    employee_id: UUID,
    current_user: CurrentUserDep,
    current_employee: CurrentUserEmployee,
    document_service: DocumentServiceDep,
) -> list[DocumentResponse]:
    """List all documents for an employee.

    Ownership check: non-HR users can only view their own documents.
    """
    if current_user.role != UserRole.HR:
        if current_employee is None or employee_id != current_employee.id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: cannot view another employee's documents",
            )
    documents = await document_service.list_documents(employee_id)
    return [DocumentResponse.model_validate(doc) for doc in documents]


@employee_router.post(
    "/{employee_id}/documents",
    response_model=DocumentResponse,
    status_code=201,
)
async def upload_document(
    employee_id: UUID,
    current_user: CurrentUserDep,
    current_employee: CurrentUserEmployee,
    document_service: DocumentServiceDep,
    file: UploadFile = File(..., description="Document file to upload"),
    document_type: str = Form(..., description="Document category"),
    description: str | None = Form(default=None, description="Optional document description"),
) -> DocumentResponse:
    """Upload a document to an employee's document vault.

    Ownership check: non-HR users can only upload to their own vault.
    """
    if current_user.role != UserRole.HR:
        if current_employee is None or employee_id != current_employee.id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: cannot upload to another employee's documents",
            )

    file_data = await file.read()
    content_type = file.content_type or "application/octet-stream"
    file_name = file.filename or "unnamed"

    document = await document_service.upload_document(
        employee_id=employee_id,
        document_type=document_type,
        file_name=file_name,
        file_data=file_data,
        content_type=content_type,
    )
    return DocumentResponse.model_validate(document)


# ---------------------------------------------------------------------------
# Document download/delete endpoints (by document ID)
# ---------------------------------------------------------------------------


@document_router.get("/{document_id}/download")
async def download_document(
    document_id: UUID,
    current_user: CurrentUserDep,
    current_employee: CurrentUserEmployee,
    document_service: DocumentServiceDep,
) -> Response:
    """Download a document by its ID.

    Ownership check: non-HR users can only download their own documents.
    Fetches metadata first, checks ownership, then downloads from MinIO.
    """
    document = await document_service.get_document_metadata(document_id)

    if current_user.role != UserRole.HR:
        if current_employee is None or document.employee_id != current_employee.id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: cannot download another employee's document",
            )

    file_data = await document_service.download_file(document.storage_path)

    return Response(
        content=file_data,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{document.file_name}"',
        },
    )


@document_router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID,
    current_user: HRUserDep,
    document_service: DocumentServiceDep,
) -> None:
    """Delete a document by its ID.

    Only admins can delete documents.
    """
    await document_service.delete_document(document_id)


# ---------------------------------------------------------------------------
# Department endpoints
# ---------------------------------------------------------------------------


@department_router.get("", response_model=list[DepartmentResponse])
async def list_departments(
    current_user: CurrentUserDep,
    department_service: DepartmentServiceDep,
) -> list[DepartmentResponse]:
    """List all departments."""
    departments = await department_service.list_departments()
    return [DepartmentResponse.model_validate(dept) for dept in departments]


@department_router.post("", response_model=DepartmentResponse, status_code=201)
async def create_department(
    body: DepartmentCreate,
    current_user: HRUserDep,
    department_service: DepartmentServiceDep,
) -> DepartmentResponse:
    """Create a new department."""
    data = body.model_dump(exclude_unset=True)
    department = await department_service.create_department(data)
    return DepartmentResponse.model_validate(department)


@department_router.put("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: UUID,
    body: DepartmentUpdate,
    current_user: HRUserDep,
    department_service: DepartmentServiceDep,
) -> DepartmentResponse:
    """Update an existing department."""
    data = body.model_dump(exclude_unset=True)
    department = await department_service.update_department(department_id, data)
    return DepartmentResponse.model_validate(department)


@department_router.delete("/{department_id}", status_code=204)
async def delete_department(
    department_id: UUID,
    current_user: HRUserDep,
    department_service: DepartmentServiceDep,
) -> None:
    """Delete a department (cascade-protected)."""
    await department_service.delete_department(department_id)


# ---------------------------------------------------------------------------
# Position endpoints
# ---------------------------------------------------------------------------


@position_router.get("", response_model=list[PositionResponse])
async def list_positions(
    current_user: CurrentUserDep,
    position_service: PositionServiceDep,
) -> list[PositionResponse]:
    """List all positions."""
    positions = await position_service.list_positions()
    return [PositionResponse.model_validate(pos) for pos in positions]


@position_router.post("", response_model=PositionResponse, status_code=201)
async def create_position(
    body: PositionCreate,
    current_user: HRUserDep,
    position_service: PositionServiceDep,
) -> PositionResponse:
    """Create a new position."""
    data = body.model_dump(exclude_unset=True)
    position = await position_service.create_position(data)
    return PositionResponse.model_validate(position)


@position_router.put("/{position_id}", response_model=PositionResponse)
async def update_position(
    position_id: UUID,
    body: PositionUpdate,
    current_user: HRUserDep,
    position_service: PositionServiceDep,
) -> PositionResponse:
    """Update an existing position."""
    data = body.model_dump(exclude_unset=True)
    position = await position_service.update_position(position_id, data)
    return PositionResponse.model_validate(position)


@position_router.delete("/{position_id}", status_code=204)
async def delete_position(
    position_id: UUID,
    current_user: HRUserDep,
    position_service: PositionServiceDep,
) -> None:
    """Delete a position (cascade-protected)."""
    await position_service.delete_position(position_id)


# ---------------------------------------------------------------------------
# Include sub-routers into the main router
# ---------------------------------------------------------------------------

router.include_router(employee_router)
router.include_router(department_router)
router.include_router(position_router)
router.include_router(document_router)
