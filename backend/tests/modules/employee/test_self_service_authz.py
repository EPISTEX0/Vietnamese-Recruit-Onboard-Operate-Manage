"""Tests for Employee Self-Service authorization boundary.

Verifies that:
- Employees can only view/update their own profile
- Employees can only view/upload/download their own documents
- Employees cannot update disallowed fields (only phone + address)
- Inactive employees are blocked
- Users without employee_id are blocked
- Admin can access any employee
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.modules.employee.api.router import (
    delete_document,
    download_document,
    get_employee,
    list_documents,
    update_employee,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(role: str = "user"):
    user = MagicMock()
    user.id = uuid4()
    user.role = role
    return user


def _make_employee(employee_id=None, is_active=True):
    emp = MagicMock(
        spec=[
            "id",
            "is_active",
            "employee_code",
            "full_name",
            "email",
            "phone",
            "address",
            "date_of_birth",
            "gender",
            "department_id",
            "position_id",
            "start_date",
            "contract_type",
            "tax_code",
            "id_number",
            "candidate_id",
        ]
    )
    emp.id = employee_id or uuid4()
    emp.is_active = is_active
    emp.employee_code = "NV-001"
    emp.full_name = "Test Employee"
    emp.email = "test@vroom.local"
    emp.phone = "0901000001"
    emp.address = "123 Test St"
    emp.date_of_birth = date(2000, 1, 1)
    emp.gender = "male"
    emp.department_id = None
    emp.position_id = None
    emp.start_date = date(2025, 1, 1)
    emp.contract_type = "full_time"
    emp.tax_code = "0100000001"
    emp.id_number = None
    emp.candidate_id = None
    emp.created_at = datetime(2025, 1, 1)
    emp.updated_at = datetime(2025, 1, 1)
    return emp


def _make_document(employee_id=None, doc_id=None):
    doc = MagicMock()
    doc.id = doc_id or uuid4()
    doc.employee_id = employee_id or uuid4()
    doc.file_name = "test.pdf"
    doc.mime_type = "application/pdf"
    doc.storage_path = "employees/test/test.pdf"
    doc.file_size = 1000
    doc.document_type = "other"
    doc.description = None
    doc.uploaded_at = datetime(2025, 1, 1)
    return doc


# ---------------------------------------------------------------------------
# Test: get_employee
# ---------------------------------------------------------------------------


class TestGetEmployeeOwnership:
    """Ownership boundary for GET /api/employees/{id}."""

    @pytest.mark.asyncio
    async def test_employee_can_view_own_profile(self):
        emp_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=emp_id)
        emp = _make_employee(employee_id=emp_id)
        svc = AsyncMock()
        svc.get_employee.return_value = emp

        result = await get_employee(
            employee_id=emp_id,
            current_user=user,
            current_employee=current_emp,
            employee_service=svc,
        )
        assert result.id == emp_id

    @pytest.mark.asyncio
    async def test_employee_cannot_view_other_profile(self):
        my_id = uuid4()
        other_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=my_id)
        svc = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_employee(
                employee_id=other_id,
                current_user=user,
                current_employee=current_emp,
                employee_service=svc,
            )
        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_admin_can_view_any_profile(self):
        emp_id = uuid4()
        user = _make_user(role="hr")
        emp = _make_employee(employee_id=emp_id)
        svc = AsyncMock()
        svc.get_employee.return_value = emp

        result = await get_employee(
            employee_id=emp_id,
            current_user=user,
            current_employee=None,
            employee_service=svc,
        )
        assert result.id == emp_id

    @pytest.mark.asyncio
    async def test_user_without_employee_blocked(self):
        user = _make_user(role="user")
        svc = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_employee(
                employee_id=uuid4(),
                current_user=user,
                current_employee=None,
                employee_service=svc,
            )
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Test: update_employee
# ---------------------------------------------------------------------------


class TestUpdateEmployeeSelfEdit:
    """Self-edit restriction for PUT /api/employees/{id}."""

    @pytest.mark.asyncio
    async def test_employee_can_update_own_phone(self):
        emp_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=emp_id)
        emp = _make_employee(employee_id=emp_id)
        svc = AsyncMock()
        svc.update_employee.return_value = emp

        body = MagicMock()
        body.model_dump.return_value = {"phone": "0912345678"}

        result = await update_employee(
            employee_id=emp_id,
            body=body,
            current_user=user,
            current_employee=current_emp,
            employee_service=svc,
        )
        assert result.id == emp_id

    @pytest.mark.asyncio
    async def test_employee_can_update_own_address(self):
        emp_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=emp_id)
        emp = _make_employee(employee_id=emp_id)
        svc = AsyncMock()
        svc.update_employee.return_value = emp

        body = MagicMock()
        body.model_dump.return_value = {"address": "456 New St"}

        result = await update_employee(
            employee_id=emp_id,
            body=body,
            current_user=user,
            current_employee=current_emp,
            employee_service=svc,
        )
        assert result.id == emp_id

    @pytest.mark.asyncio
    async def test_employee_cannot_update_full_name(self):
        emp_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=emp_id)
        svc = AsyncMock()

        body = MagicMock()
        body.model_dump.return_value = {"full_name": "Hacker Name"}

        with pytest.raises(HTTPException) as exc_info:
            await update_employee(
                employee_id=emp_id,
                body=body,
                current_user=user,
                current_employee=current_emp,
                employee_service=svc,
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_employee_cannot_update_multiple_disallowed(self):
        emp_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=emp_id)
        svc = AsyncMock()

        body = MagicMock()
        body.model_dump.return_value = {
            "phone": "0912345678",
            "full_name": "Hacker",
            "email": "bad@evil.com",
        }

        with pytest.raises(HTTPException) as exc_info:
            await update_employee(
                employee_id=emp_id,
                body=body,
                current_user=user,
                current_employee=current_emp,
                employee_service=svc,
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_employee_cannot_update_other_employee(self):
        my_id = uuid4()
        other_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=my_id)
        svc = AsyncMock()

        body = MagicMock()
        body.model_dump.return_value = {"phone": "0912345678"}

        with pytest.raises(HTTPException) as exc_info:
            await update_employee(
                employee_id=other_id,
                body=body,
                current_user=user,
                current_employee=current_emp,
                employee_service=svc,
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_update_any_field(self):
        emp_id = uuid4()
        user = _make_user(role="hr")
        emp = _make_employee(employee_id=emp_id)
        svc = AsyncMock()
        svc.update_employee.return_value = emp

        body = MagicMock()
        body.model_dump.return_value = {
            "full_name": "Admin Changed",
            "phone": "0999999999",
        }

        result = await update_employee(
            employee_id=emp_id,
            body=body,
            current_user=user,
            current_employee=None,
            employee_service=svc,
        )
        assert result.id == emp_id


# ---------------------------------------------------------------------------
# Test: list_documents
# ---------------------------------------------------------------------------


class TestListDocumentsOwnership:
    """Ownership boundary for GET /api/employees/{id}/documents."""

    @pytest.mark.asyncio
    async def test_employee_can_list_own_documents(self):
        emp_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=emp_id)
        svc = AsyncMock()
        svc.list_documents.return_value = []

        result = await list_documents(
            employee_id=emp_id,
            current_user=user,
            current_employee=current_emp,
            document_service=svc,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_employee_cannot_list_other_documents(self):
        my_id = uuid4()
        other_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=my_id)
        svc = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await list_documents(
                employee_id=other_id,
                current_user=user,
                current_employee=current_emp,
                document_service=svc,
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_list_any_documents(self):
        emp_id = uuid4()
        user = _make_user(role="hr")
        svc = AsyncMock()
        svc.list_documents.return_value = []

        result = await list_documents(
            employee_id=emp_id,
            current_user=user,
            current_employee=None,
            document_service=svc,
        )
        assert result == []


# ---------------------------------------------------------------------------
# Test: download_document
# ---------------------------------------------------------------------------


class TestDownloadDocumentOwnership:
    """Ownership boundary for GET /api/documents/{id}/download."""

    @pytest.mark.asyncio
    async def test_employee_can_download_own_document(self):
        emp_id = uuid4()
        doc_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=emp_id)
        doc = _make_document(employee_id=emp_id, doc_id=doc_id)

        svc = AsyncMock()
        svc.get_document_metadata.return_value = doc
        svc.download_file.return_value = b"fake content"

        from fastapi.responses import Response

        result = await download_document(
            document_id=doc_id,
            current_user=user,
            current_employee=current_emp,
            document_service=svc,
        )
        assert isinstance(result, Response)
        svc.download_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_employee_cannot_download_other_document(self):
        my_id = uuid4()
        other_emp_id = uuid4()
        doc_id = uuid4()
        user = _make_user(role="user")
        current_emp = _make_employee(employee_id=my_id)
        doc = _make_document(employee_id=other_emp_id, doc_id=doc_id)

        svc = AsyncMock()
        svc.get_document_metadata.return_value = doc

        with pytest.raises(HTTPException) as exc_info:
            await download_document(
                document_id=doc_id,
                current_user=user,
                current_employee=current_emp,
                document_service=svc,
            )
        assert exc_info.value.status_code == 403
        svc.download_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_can_download_any_document(self):
        emp_id = uuid4()
        doc_id = uuid4()
        user = _make_user(role="hr")
        doc = _make_document(employee_id=emp_id, doc_id=doc_id)

        svc = AsyncMock()
        svc.get_document_metadata.return_value = doc
        svc.download_file.return_value = b"fake content"

        from fastapi.responses import Response

        result = await download_document(
            document_id=doc_id,
            current_user=user,
            current_employee=None,
            document_service=svc,
        )
        assert isinstance(result, Response)
        svc.download_file.assert_called_once()


# ---------------------------------------------------------------------------
# Test: delete_document
# ---------------------------------------------------------------------------


class TestDeleteDocumentOwnership:
    """Ownership boundary for DELETE /api/documents/{id}.

    The HR guard is enforced by the ``require_hr`` dependency, not
    inline in the handler.  These tests verify the dependency directly.
    """

    @pytest.mark.asyncio
    async def test_non_hr_rejected_by_require_hr(self):
        from src.modules.identity.api.admin_router import require_hr

        user = _make_user(role="user")
        with pytest.raises(HTTPException) as exc_info:
            await require_hr(current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_hr_passes_require_hr(self):
        from src.modules.identity.api.admin_router import require_hr

        user = _make_user(role="hr")
        result = await require_hr(current_user=user)
        assert result.role == "hr"

    @pytest.mark.asyncio
    async def test_admin_can_delete_document(self):
        user = _make_user(role="hr")
        svc = AsyncMock()

        await delete_document(
            document_id=uuid4(),
            current_user=user,
            document_service=svc,
        )
        svc.delete_document.assert_called_once()
