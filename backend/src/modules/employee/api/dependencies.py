"""FastAPI dependencies for Employee Self-Service authorization.

Provides the ``get_current_employee`` dependency that resolves the
authenticated Employee from the JWT user's email and enforces
the active-account boundary.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException

from src.modules.employee.container import get_employee_repository
from src.modules.employee.domain.entities import Employee
from src.modules.employee.infrastructure.employee_repository import (
    EmployeeRepository,
)
from src.modules.identity.container import get_current_user
from src.modules.identity.domain.entities import User, UserRole


async def get_current_employee(
    current_user: User = Depends(get_current_user),
    employee_repo: EmployeeRepository = Depends(get_employee_repository),
) -> Employee | None:
    """Resolve the Employee record linked to the authenticated user.

    Returns ``None`` for HR and SYSTEM_ADMIN users, who are not required to
    have a linked Employee record. Returns the Employee for regular users.

    Raises:
        HTTPException 403: If a self-service user has no Employee record
            or the Employee is inactive.
    """
    employee = await employee_repo.get_by_email(current_user.email)

    if employee is None:
        if current_user.role != UserRole.USER:
            return None
        raise HTTPException(status_code=403, detail="Employee record not found")

    if not employee.is_active:
        raise HTTPException(status_code=403, detail="Employee account is inactive")

    return employee


CurrentUserEmployee = Annotated[Employee | None, Depends(get_current_employee)]
