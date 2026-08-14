"""Safety tests for Employee Assistant tools.

Each tool gets its own test class inheriting BaseToolSafetyTest.
All 9 employee tools in EmployeeToolRegistry are covered.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.assistant.application.employee_tool_registry import EmployeeToolRegistry
from tests.modules.assistant.test_base_safety import BaseToolSafetyTest

_EMPLOYEE_ID = "00000000-0000-0000-0000-000000000001"
_ANOTHER_EMPLOYEE_ID = "00000000-0000-0000-0000-999999999999"


def _make_registry(**overrides: MagicMock) -> EmployeeToolRegistry:
    """Create EmployeeToolRegistry with mocked deps for testing."""
    from src.modules.assistant.application.employee_tool_registry import (
        EmployeeToolRegistry,
    )
    from src.modules.attendance.infrastructure.attendance_record_repository import (
        AttendanceRecordRepository,
    )
    from src.modules.employee.application.document_service import DocumentService
    from src.modules.employee.application.employee_service import EmployeeService
    from src.modules.employee_request.application.leave_service import LeaveService
    from src.modules.employee_request.application.overtime_service import OvertimeService
    from src.modules.payslip.application.payslip_service import PayslipService

    defaults: dict = {
        "employee_service": MagicMock(spec=EmployeeService),
        "document_service": MagicMock(spec=DocumentService),
        "attendance_repo": MagicMock(spec=AttendanceRecordRepository),
        "leave_service": MagicMock(spec=LeaveService),
        "overtime_service": MagicMock(spec=OvertimeService),
        "payslip_service": MagicMock(spec=PayslipService),
    }
    defaults.update(overrides)
    return EmployeeToolRegistry(
        employee_id=_EMPLOYEE_ID,
        **defaults,
    )


# =========================================================================
# Read-Tools — employee-scoped (no entity lookup)
# =========================================================================


class TestGetMyProfileSafety(BaseToolSafetyTest):
    """Safety tests for get_my_profile (Read-Tool)."""

    TOOL_NAME = "get_my_profile"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_get_my_profile"
    HAS_ENTITY_LOOKUP = True
    ENTITY_ID_PARAM = None  # employee_id is injected, not a param
    NO_PARAMS = True

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry()

    @pytest.fixture
    def valid_args(self) -> dict:
        return {}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Tool reads the authenticated employee's own profile ONLY."""
        mock_employee = MagicMock()
        mock_employee.employee_code = "NV-001"
        mock_employee.full_name = "Nguyen Van A"
        mock_employee.email = "a@company.com"
        mock_employee.phone = ""
        mock_employee.date_of_birth = None
        mock_employee.gender = ""
        mock_employee.address = ""
        mock_employee.department_id = None
        mock_employee.position_id = None
        mock_employee.start_date = None
        mock_employee.contract_type = ""
        registry._employee_service.get_employee = AsyncMock(return_value=mock_employee)

        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert result["full_name"] == "Nguyen Van A"

        # Must be called with injected employee_id, NOT from params
        registry._employee_service.get_employee.assert_awaited_once_with(_EMPLOYEE_ID)

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: EmployeeToolRegistry) -> None:
        """Tool returns error when employee not found (not crash)."""
        registry._employee_service.get_employee = AsyncMock(
            side_effect=Exception("Employee not found")
        )
        result = await self.execute_tool(registry, {})
        self.assert_error(result)

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Tool accepts no params — extra params are ignored/stripped."""
        # LLM may send extra params — should be ignored
        mock_employee = MagicMock()
        mock_employee.employee_code = "NV-001"
        mock_employee.full_name = "Test"
        mock_employee.email = "t@t.com"
        mock_employee.phone = ""
        mock_employee.date_of_birth = None
        mock_employee.gender = ""
        mock_employee.address = ""
        mock_employee.department_id = None
        mock_employee.position_id = None
        mock_employee.start_date = None
        mock_employee.contract_type = ""
        registry._employee_service.get_employee = AsyncMock(return_value=mock_employee)

        # Extra params + employee_id sneaked in by LLM
        result = await self.execute_tool(
            registry,
            {"employee_id": _ANOTHER_EMPLOYEE_ID, "extra": "should_be_ignored"},
        )
        assert "error" not in result
        # Must have used injected employee_id, not the LLM-supplied one
        call_employee_id = registry._employee_service.get_employee.call_args[0][0]
        assert str(call_employee_id) == _EMPLOYEE_ID


class TestListMyDocumentsSafety(BaseToolSafetyTest):
    """Safety tests for list_my_documents (Read-Tool)."""

    TOOL_NAME = "list_my_documents"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_list_my_documents"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None
    NO_PARAMS = True

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry(document_service=MagicMock())

    @pytest.fixture
    def valid_args(self) -> dict:
        return {}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Tool lists only the authenticated employee's documents."""
        registry._document_service.list_documents = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert "documents" in result
        registry._document_service.list_documents.assert_awaited_once_with(_EMPLOYEE_ID)

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: EmployeeToolRegistry) -> None:
        """Tool returns empty list when no documents exist (not crash)."""
        registry._document_service.list_documents = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert result["documents"] == []

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Tool ignores extra params gracefully."""
        registry._document_service.list_documents = AsyncMock(return_value=[])
        result = await self.execute_tool(
            registry,
            {"employee_id": _ANOTHER_EMPLOYEE_ID, "unknown": "data"},
        )
        assert "error" not in result
        # Must have used injected employee_id
        registry._document_service.list_documents.assert_awaited_once_with(_EMPLOYEE_ID)


class TestGetTodayAttendanceSafety(BaseToolSafetyTest):
    """Safety tests for get_today_attendance (Read-Tool)."""

    TOOL_NAME = "get_today_attendance"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_get_today_attendance"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None
    NO_PARAMS = True

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry(attendance_repo=MagicMock())

    @pytest.fixture
    def valid_args(self) -> dict:
        return {}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Tool reads only the authenticated employee's attendance."""
        registry._attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        call_args = registry._attendance_repo.get_by_employee_and_date_range.call_args
        assert call_args is not None
        assert str(call_args[0][0]) == _EMPLOYEE_ID

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: EmployeeToolRegistry) -> None:
        """Tool returns 'not_checked_in' when no attendance record (not crash)."""
        registry._attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert result["status"] == "not_checked_in"

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Tool ignores extra params gracefully."""
        registry._attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])
        result = await self.execute_tool(
            registry,
            {"employee_id": _ANOTHER_EMPLOYEE_ID, "unknown": "param"},
        )
        assert "error" not in result
        # Must use injected employee_id
        call_args = registry._attendance_repo.get_by_employee_and_date_range.call_args
        assert str(call_args[0][0]) == _EMPLOYEE_ID


class TestListMyAttendanceRecordsSafety(BaseToolSafetyTest):
    """Safety tests for list_my_attendance_records (Read-Tool)."""

    TOOL_NAME = "list_my_attendance_records"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_list_my_attendance_records"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry(attendance_repo=MagicMock())

    @pytest.fixture
    def valid_args(self) -> dict:
        return {"month": 6, "year": 2026}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Tool lists only the authenticated employee's attendance records."""
        registry._attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {"month": 6, "year": 2026})
        assert "error" not in result
        assert "records" in result
        call_args = registry._attendance_repo.get_by_employee_and_date_range.call_args
        assert call_args is not None
        assert str(call_args[0][0]) == _EMPLOYEE_ID

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: EmployeeToolRegistry) -> None:
        """Tool returns empty records list when none exist (not crash)."""
        registry._attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {"month": 6, "year": 2026})
        assert "error" not in result
        assert result["records"] == []

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Tool validates month/year range."""
        # Invalid month
        result = await self.execute_tool(registry, {"month": 13, "year": 2026})
        self.assert_error(result)

        # Invalid year
        result = await self.execute_tool(registry, {"month": 6, "year": 1800})
        self.assert_error(result)

        # Extra params stripped
        registry._attendance_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])
        result = await self.execute_tool(
            registry,
            {"month": 6, "year": 2026, "employee_id": _ANOTHER_EMPLOYEE_ID},
        )
        assert "error" not in result
        call_args = registry._attendance_repo.get_by_employee_and_date_range.call_args
        assert str(call_args[0][0]) == _EMPLOYEE_ID


class TestListMyEmployeeRequestsSafety(BaseToolSafetyTest):
    """Safety tests for list_my_employee_requests (Read-Tool)."""

    TOOL_NAME = "list_my_employee_requests"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_list_my_employee_requests"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry(
            leave_service=MagicMock(),
            overtime_service=MagicMock(),
        )

    @pytest.fixture
    def valid_args(self) -> dict:
        return {}  # request_type is optional

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Tool lists only the authenticated employee's requests."""
        registry._leave_service.list_my_leaves = AsyncMock(return_value=[])
        registry._overtime_service.list_my_overtime = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert "requests" in result
        registry._leave_service.list_my_leaves.assert_awaited_once_with(_EMPLOYEE_ID)
        registry._overtime_service.list_my_overtime.assert_awaited_once_with(_EMPLOYEE_ID)

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: EmployeeToolRegistry) -> None:
        """Tool returns empty list when no requests exist (not crash)."""
        registry._leave_service.list_my_leaves = AsyncMock(return_value=[])
        registry._overtime_service.list_my_overtime = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert result["requests"] == []

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Invalid request_type returns error."""
        result = await self.execute_tool(registry, {"request_type": "invalid_type"})
        self.assert_error(result)

        # Extra params stripped
        registry._leave_service.list_my_leaves = AsyncMock(return_value=[])
        registry._overtime_service.list_my_overtime = AsyncMock(return_value=[])
        result = await self.execute_tool(
            registry,
            {"employee_id": _ANOTHER_EMPLOYEE_ID, "extra": "data"},
        )
        assert "error" not in result
        registry._leave_service.list_my_leaves.assert_awaited_once_with(_EMPLOYEE_ID)


class TestGetMyLeaveBalanceSafety(BaseToolSafetyTest):
    """Safety tests for get_my_leave_balance (Read-Tool)."""

    TOOL_NAME = "get_my_leave_balance"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_get_my_leave_balance"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None
    NO_PARAMS = True

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry(leave_service=MagicMock())

    @pytest.fixture
    def valid_args(self) -> dict:
        return {}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Tool returns only the authenticated employee's leave balance."""
        registry._leave_service.get_my_leave_balance = AsyncMock(
            return_value={"approved_days_used": 3, "remaining_days": 9}
        )
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        registry._leave_service.get_my_leave_balance.assert_awaited_once_with(_EMPLOYEE_ID)

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: EmployeeToolRegistry) -> None:
        """Tool returns error when leave service fails."""
        registry._leave_service.get_my_leave_balance = AsyncMock(
            side_effect=Exception("Employee not found")
        )
        result = await self.execute_tool(registry, {})
        self.assert_error(result)

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Tool ignores extra params and returns leave balance."""
        registry._leave_service.get_my_leave_balance = AsyncMock(
            return_value={"approved_days_used": 3, "remaining_days": 9}
        )
        result = await self.execute_tool(
            registry,
            {"employee_id": _ANOTHER_EMPLOYEE_ID, "extra": "param"},
        )
        assert "error" not in result
        registry._leave_service.get_my_leave_balance.assert_awaited_once_with(_EMPLOYEE_ID)


class TestListMyPayslipsSafety(BaseToolSafetyTest):
    """Safety tests for list_my_payslips (Read-Tool)."""

    TOOL_NAME = "list_my_payslips"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_list_my_payslips"
    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None
    NO_PARAMS = True

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry(payslip_service=MagicMock())

    @pytest.fixture
    def valid_args(self) -> dict:
        return {}

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Tool lists only the authenticated employee's payslips."""
        registry._payslip_service.get_my_payslips = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert "payslips" in result
        registry._payslip_service.get_my_payslips.assert_awaited_once_with(_EMPLOYEE_ID)

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: EmployeeToolRegistry) -> None:
        """Tool returns empty list when no payslips exist (not crash)."""
        registry._payslip_service.get_my_payslips = AsyncMock(return_value=[])
        result = await self.execute_tool(registry, {})
        assert "error" not in result
        assert result["payslips"] == []

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Tool ignores extra params gracefully."""
        registry._payslip_service.get_my_payslips = AsyncMock(return_value=[])
        result = await self.execute_tool(
            registry,
            {"employee_id": _ANOTHER_EMPLOYEE_ID, "extra": "data"},
        )
        assert "error" not in result
        registry._payslip_service.get_my_payslips.assert_awaited_once_with(_EMPLOYEE_ID)


# =========================================================================
# Draft-Tools — human-in-the-loop (CONTEXT.md, entry Draft Action)
# =========================================================================


class _BaseEmployeeDraftToolSafety(BaseToolSafetyTest):
    """Shared safety base for Employee Draft-Tools."""

    HAS_ENTITY_LOOKUP = False
    ENTITY_ID_PARAM = None

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Draft tool never calls any write method (structural safety)."""
        # Scope is verified by the source inspection in test_tool_is_read_only
        # Draft tools in EmployeeToolRegistry don't call service methods
        pass

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: EmployeeToolRegistry) -> None:
        """Draft tool returns error for missing entity (not crash)."""
        # This test is N/A for most draft tools — they validate params, not entities
        pytest.skip(f"{self.TOOL_NAME} validates params, not entity lookup")


class TestDraftLeaveRequestSafety(_BaseEmployeeDraftToolSafety):
    """Safety tests for draft_leave_request (Draft-Tool)."""

    TOOL_NAME = "draft_leave_request"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_draft_leave_request"

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry()

    @pytest.fixture
    def valid_args(self) -> dict:
        return {
            "leave_type": "annual",
            "start_date": "2026-07-01",
            "end_date": "2026-07-03",
            "reason": "Family vacation",
        }

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Draft tool returns DraftAction without writing to DB."""
        result = await self.execute_tool(
            registry,
            {
                "leave_type": "annual",
                "start_date": "2026-07-01",
                "end_date": "2026-07-03",
                "reason": "Nghỉ phép",
            },
        )
        assert "draft_action" in result
        draft = result["draft_action"]
        assert draft["action_type"] == "submit_leave_request"
        assert draft["confirm_method"] == "POST"
        # Verify no write was called
        registry._leave_service.create_leave.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Missing or invalid params return error (not crash)."""
        # Missing all params
        result = await self.execute_tool(registry, {})
        self.assert_error(result)

        # Invalid date format
        result = await self.execute_tool(
            registry,
            {
                "leave_type": "annual",
                "start_date": "not-a-date",
                "end_date": "2026-07-03",
                "reason": "Test",
            },
        )
        self.assert_error(result)

        # End date before start date
        result = await self.execute_tool(
            registry,
            {
                "leave_type": "annual",
                "start_date": "2026-07-03",
                "end_date": "2026-07-01",
                "reason": "Test",
            },
        )
        self.assert_error(result)

        # Invalid leave type
        result = await self.execute_tool(
            registry,
            {
                "leave_type": "invalid_type",
                "start_date": "2026-07-01",
                "end_date": "2026-07-03",
                "reason": "Test",
            },
        )
        self.assert_error(result)


class TestDraftOvertimeRequestSafety(_BaseEmployeeDraftToolSafety):
    """Safety tests for draft_overtime_request (Draft-Tool)."""

    TOOL_NAME = "draft_overtime_request"
    HANDLER_CLASS = EmployeeToolRegistry
    HANDLER_METHOD = "_draft_overtime_request"

    @pytest.fixture
    def registry(self) -> EmployeeToolRegistry:
        return _make_registry()

    @pytest.fixture
    def valid_args(self) -> dict:
        return {
            "work_date": "2026-07-05",
            "start_time": "18:00",
            "end_time": "21:00",
            "reason": "Project deadline",
        }

    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: EmployeeToolRegistry) -> None:
        """Draft tool returns DraftAction without writing to DB."""
        result = await self.execute_tool(
            registry,
            {
                "work_date": "2026-07-05",
                "start_time": "18:00",
                "end_time": "21:00",
                "reason": "Project deadline",
            },
        )
        assert "draft_action" in result
        draft = result["draft_action"]
        assert draft["action_type"] == "submit_overtime_request"
        assert draft["confirm_method"] == "POST"
        # Verify no write was called
        registry._overtime_service.create_overtime.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: EmployeeToolRegistry) -> None:
        """Missing or invalid params return error (not crash)."""
        # Missing all params
        result = await self.execute_tool(registry, {})
        self.assert_error(result)

        # Invalid time format
        result = await self.execute_tool(
            registry,
            {
                "work_date": "2026-07-05",
                "start_time": "not-a-time",
                "end_time": "21:00",
                "reason": "Test",
            },
        )
        self.assert_error(result)

        # End time before start time
        result = await self.execute_tool(
            registry,
            {
                "work_date": "2026-07-05",
                "start_time": "21:00",
                "end_time": "18:00",
                "reason": "Test",
            },
        )
        self.assert_error(result)

        # Missing reason
        result = await self.execute_tool(
            registry,
            {
                "work_date": "2026-07-05",
                "start_time": "18:00",
                "end_time": "21:00",
                # missing reason
            },
        )
        self.assert_error(result)
