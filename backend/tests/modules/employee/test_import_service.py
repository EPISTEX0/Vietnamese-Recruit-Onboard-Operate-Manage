"""Unit tests for the ImportService module."""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from openpyxl import Workbook

from src.modules.employee.application.import_service import ImportService
from src.modules.employee.domain.entities import Department, Position


def _create_excel(headers: list[str], rows: list[list]) -> bytes:
    """Helper to create an in-memory .xlsx file from headers and rows."""
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _make_service(
    employee_repo: AsyncMock | None = None,
    department_repo: AsyncMock | None = None,
    position_repo: AsyncMock | None = None,
) -> ImportService:
    """Create an ImportService with mock repositories."""
    if employee_repo is None:
        employee_repo = AsyncMock()
    if department_repo is None:
        department_repo = AsyncMock()
    if position_repo is None:
        position_repo = AsyncMock()
    return ImportService(
        employee_repository=employee_repo,
        department_repository=department_repo,
        position_repository=position_repo,
    )


class TestImportServiceBasic:
    """Tests for basic import functionality."""

    @pytest.mark.asyncio
    async def test_import_empty_file(self):
        """An empty Excel file should return zero totals."""
        wb = Workbook()
        buffer = BytesIO()
        wb.save(buffer)
        file_bytes = buffer.getvalue()

        service = _make_service()
        result = await service.import_from_excel(file_bytes)

        assert result["total_rows"] == 0
        assert result["success_count"] == 0
        assert result["error_count"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_import_unreadable_file_result_matches_import_result_schema(self):
        """A file-level read failure must still produce a valid ImportResult.

        The parser's error dict must use the 'message' key the ImportResult
        schema requires; a mismatched key would make the router's
        ``ImportResult(**result)`` raise a pydantic ValidationError instead
        of returning the classified message to the client.
        """
        from src.modules.employee.api.schemas import ImportResult

        service = _make_service()
        result = await service.import_from_excel(b"not an excel file")

        assert result["error_count"] == 1
        ImportResult(**result)

    @pytest.mark.asyncio
    async def test_import_single_valid_row_creates_employee(self):
        """A valid row with no existing employee should create a new one."""
        headers = ["full_name", "email", "phone"]
        rows = [["Nguyen Van A", "a@example.com", "0901234567"]]
        file_bytes = _create_excel(headers, rows)

        employee_repo = AsyncMock()
        employee_repo.get_by_email.return_value = None
        employee_repo.get_next_code.return_value = "NV-001"
        employee_repo.create.return_value = MagicMock()

        department_repo = AsyncMock()
        position_repo = AsyncMock()

        service = _make_service(employee_repo, department_repo, position_repo)
        result = await service.import_from_excel(file_bytes)

        assert result["total_rows"] == 1
        assert result["success_count"] == 1
        assert result["error_count"] == 0
        assert result["errors"] == []
        employee_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_import_existing_email_updates_employee(self):
        """A row with an existing email should update the employee."""
        headers = ["full_name", "email", "phone"]
        rows = [["Nguyen Van A Updated", "a@example.com", "0909999999"]]
        file_bytes = _create_excel(headers, rows)

        existing_employee = MagicMock()
        existing_employee.id = uuid4()

        employee_repo = AsyncMock()
        employee_repo.get_by_email.return_value = existing_employee
        employee_repo.update.return_value = MagicMock()

        service = _make_service(employee_repo)
        result = await service.import_from_excel(file_bytes)

        assert result["total_rows"] == 1
        assert result["success_count"] == 1
        assert result["error_count"] == 0
        employee_repo.update.assert_called_once()
        # Should not create a new employee
        employee_repo.create.assert_not_called()


class TestImportServiceDepartmentResolution:
    """Tests for department name resolution."""

    @pytest.mark.asyncio
    async def test_valid_department_name_resolves(self):
        """A row with a valid department_name should resolve to department_id."""
        headers = ["full_name", "email", "department_name"]
        rows = [["Nguyen Van A", "a@example.com", "Engineering"]]
        file_bytes = _create_excel(headers, rows)

        dept = MagicMock(spec=Department)
        dept.id = uuid4()

        employee_repo = AsyncMock()
        employee_repo.get_by_email.return_value = None
        employee_repo.get_next_code.return_value = "NV-001"
        employee_repo.create.return_value = MagicMock()

        department_repo = AsyncMock()
        department_repo.get_by_name.return_value = dept

        position_repo = AsyncMock()

        service = _make_service(employee_repo, department_repo, position_repo)
        result = await service.import_from_excel(file_bytes)

        assert result["success_count"] == 1
        assert result["error_count"] == 0
        department_repo.get_by_name.assert_called_once_with("Engineering")

    @pytest.mark.asyncio
    async def test_unknown_department_name_auto_creates(self):
        """A row with an unknown department_name auto-creates the department."""
        headers = ["full_name", "email", "department_name"]
        rows = [["Nguyen Van A", "a@example.com", "NonExistent"]]
        file_bytes = _create_excel(headers, rows)

        new_dept = MagicMock(spec=Department)
        new_dept.id = uuid4()

        employee_repo = AsyncMock()
        employee_repo.get_by_email.return_value = None
        employee_repo.get_next_code.return_value = "NV-001"
        employee_repo.create.return_value = MagicMock()

        department_repo = AsyncMock()
        department_repo.get_by_name.return_value = None
        department_repo.create.return_value = new_dept

        position_repo = AsyncMock()

        service = _make_service(employee_repo, department_repo, position_repo)
        result = await service.import_from_excel(file_bytes)

        assert result["total_rows"] == 1
        assert result["success_count"] == 1
        assert result["error_count"] == 0
        department_repo.create.assert_called_once()


class TestImportServicePositionResolution:
    """Tests for position name resolution."""

    @pytest.mark.asyncio
    async def test_valid_position_name_resolves(self):
        """A row with a valid position_name should resolve to position_id."""
        headers = ["full_name", "email", "position_name"]
        rows = [["Nguyen Van A", "a@example.com", "Developer"]]
        file_bytes = _create_excel(headers, rows)

        pos = MagicMock(spec=Position)
        pos.id = uuid4()

        employee_repo = AsyncMock()
        employee_repo.get_by_email.return_value = None
        employee_repo.get_next_code.return_value = "NV-001"
        employee_repo.create.return_value = MagicMock()

        department_repo = AsyncMock()
        position_repo = AsyncMock()
        position_repo.get_by_name.return_value = pos

        service = _make_service(employee_repo, department_repo, position_repo)
        result = await service.import_from_excel(file_bytes)

        assert result["success_count"] == 1
        assert result["error_count"] == 0
        position_repo.get_by_name.assert_called_once_with("Developer")

    @pytest.mark.asyncio
    async def test_unknown_position_name_auto_creates(self):
        """A row with an unknown position_name auto-creates the position."""
        headers = ["full_name", "email", "position_name"]
        rows = [["Nguyen Van A", "a@example.com", "NonExistent"]]
        file_bytes = _create_excel(headers, rows)

        new_pos = MagicMock(spec=Position)
        new_pos.id = uuid4()

        employee_repo = AsyncMock()
        employee_repo.get_by_email.return_value = None
        employee_repo.get_next_code.return_value = "NV-001"
        employee_repo.create.return_value = MagicMock()

        department_repo = AsyncMock()
        position_repo = AsyncMock()
        position_repo.get_by_name.return_value = None
        position_repo.create.return_value = new_pos

        service = _make_service(employee_repo, department_repo, position_repo)
        result = await service.import_from_excel(file_bytes)

        assert result["total_rows"] == 1
        assert result["success_count"] == 1
        assert result["error_count"] == 0
        position_repo.create.assert_called_once()


class TestImportServiceMixedRows:
    """Tests for imports with a mix of valid and invalid rows."""

    @pytest.mark.asyncio
    async def test_mix_of_valid_and_invalid_rows(self):
        """All rows succeed — unknown departments are auto-created."""
        headers = ["full_name", "email", "department_name"]
        rows = [
            ["Nguyen Van A", "a@example.com", "Engineering"],
            ["Tran Thi B", "b@example.com", "NonExistent"],
            ["Le Van C", "c@example.com", "Engineering"],
        ]
        file_bytes = _create_excel(headers, rows)

        dept = MagicMock(spec=Department)
        dept.id = uuid4()

        new_dept = MagicMock(spec=Department)
        new_dept.id = uuid4()

        employee_repo = AsyncMock()
        employee_repo.get_by_email.return_value = None
        employee_repo.get_next_code.return_value = "NV-001"
        employee_repo.create.return_value = MagicMock()

        department_repo = AsyncMock()

        def get_by_name_side_effect(name):
            return dept if name == "Engineering" else None

        department_repo.get_by_name.side_effect = get_by_name_side_effect
        department_repo.create.return_value = new_dept

        position_repo = AsyncMock()

        service = _make_service(employee_repo, department_repo, position_repo)
        result = await service.import_from_excel(file_bytes)

        assert result["total_rows"] == 3
        assert result["success_count"] == 3
        assert result["error_count"] == 0

    @pytest.mark.asyncio
    async def test_parse_errors_included_in_result(self):
        """Rows that fail parsing should be included in the error count."""
        headers = ["full_name", "email"]
        rows = [
            ["Nguyen Van A", "a@example.com"],  # valid
            [None, "invalid"],  # parse error: missing full_name + invalid email
        ]
        file_bytes = _create_excel(headers, rows)

        employee_repo = AsyncMock()
        employee_repo.get_by_email.return_value = None
        employee_repo.get_next_code.return_value = "NV-001"
        employee_repo.create.return_value = MagicMock()

        service = _make_service(employee_repo)
        result = await service.import_from_excel(file_bytes)

        # 1 parsed row + errors from parse phase
        assert result["success_count"] == 1
        assert result["error_count"] >= 1
        assert result["total_rows"] >= 2
