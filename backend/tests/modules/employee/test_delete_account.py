"""Tests for DELETE /api/employees/{id}/account endpoint.

Verifies:
- HR can delete an existing employee account
- Deleting a non-existent account is idempotent (success)
- Non-admin users are rejected
- Employee row is preserved after account deletion
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.modules.employee.api.router import delete_employee_account

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(role: str = "hr"):
    user = MagicMock()
    user.id = uuid4()
    user.role = role
    return user


def _make_employee(employee_id=None, is_active=True):
    emp = MagicMock()
    emp.id = employee_id or uuid4()
    emp.is_active = is_active
    emp.email = "test@vroom.local"
    return emp


# ---------------------------------------------------------------------------
# Test: delete_employee_account
# ---------------------------------------------------------------------------


class TestDeleteEmployeeAccount:
    """Endpoint handler tests for DELETE /api/employees/{id}/account."""

    @pytest.mark.asyncio
    async def test_hr_deletes_existing_account(self):
        """HR admin deletes an existing employee account → 204, user gone, employee stays."""
        emp_id = uuid4()
        user = _make_user(role="hr")
        emp = _make_employee(employee_id=emp_id)

        employee_svc = AsyncMock()
        employee_svc.get_employee.return_value = emp

        auth_svc = AsyncMock()
        auth_svc.delete_employee_account.return_value = True

        result = await delete_employee_account(
            employee_id=emp_id,
            current_user=user,
            employee_service=employee_svc,
            auth_service=auth_svc,
        )
        # Returns None (204 No Content)
        assert result is None
        employee_svc.get_employee.assert_awaited_once_with(emp_id)
        auth_svc.delete_employee_account.assert_awaited_once_with(emp)

    @pytest.mark.asyncio
    async def test_hr_deletes_nonexistent_account_idempotent(self):
        """HR admin deletes an account that doesn't exist → idempotent 204."""
        emp_id = uuid4()
        user = _make_user(role="hr")
        emp = _make_employee(employee_id=emp_id)

        employee_svc = AsyncMock()
        employee_svc.get_employee.return_value = emp

        auth_svc = AsyncMock()
        auth_svc.delete_employee_account.return_value = False  # no user existed

        result = await delete_employee_account(
            employee_id=emp_id,
            current_user=user,
            employee_service=employee_svc,
            auth_service=auth_svc,
        )
        assert result is None
        auth_svc.delete_employee_account.assert_awaited_once_with(emp)

    @pytest.mark.asyncio
    async def test_non_hr_rejected_by_require_hr(self):
        """Non-HR users are blocked by the HRUserDep guard.

        The guard is enforced by ``require_hr``, not inline in the handler.
        This test verifies the dependency directly.
        """
        from src.modules.identity.api.admin_router import require_hr

        user = _make_user(role="user")
        with pytest.raises(HTTPException) as exc_info:
            await require_hr(current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_employee_row_preserved_after_delete(self):
        """Employee row is untouched after account deletion — only user row removed."""
        emp_id = uuid4()
        user = _make_user(role="hr")
        emp = _make_employee(employee_id=emp_id)

        employee_svc = AsyncMock()
        employee_svc.get_employee.return_value = emp

        auth_svc = AsyncMock()
        auth_svc.delete_employee_account.return_value = True

        await delete_employee_account(
            employee_id=emp_id,
            current_user=user,
            employee_service=employee_svc,
            auth_service=auth_svc,
        )
        # Employee service was only called for lookup, not delete
        employee_svc.get_employee.assert_awaited_once()
        # No employee deletion was triggered
        assert (
            not hasattr(employee_svc, "delete_employee") or not employee_svc.delete_employee.called
        )
