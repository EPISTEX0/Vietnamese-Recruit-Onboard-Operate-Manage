"""Test the Employee Assistant safety boundary — data isolation AND draft-only.

Core invariants verified:
1. Employee tools are Read-Tool or Draft-Tool only — structural safety per
   CONTEXT.md § "Nội bộ AI Assistant", entry **Tool**
2. No tool accepts employee_id as a parameter (injected from auth) — CONTEXT.md
   § "Ngôn ngữ domain", entry **Employee Assistant**
3. Employee tool registry always scopes queries to authenticated employee
4. Draft-Tools return DraftAction — they never write to the database
5. Tools use additionalProperties: false + format patterns
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.assistant.application.employee_tool_registry import EmployeeToolRegistry
from src.modules.assistant.domain.employee_tools import (
    EMPLOYEE_TOOL_DEFINITIONS,
    EMPLOYEE_TOOL_NAMES,
)
from src.modules.assistant.domain.tools import ToolKind


def _make_registry(**overrides: MagicMock) -> EmployeeToolRegistry:
    """Create EmployeeToolRegistry with mocked deps."""
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

    defaults = {
        "employee_service": MagicMock(spec=EmployeeService),
        "document_service": MagicMock(spec=DocumentService),
        "attendance_repo": MagicMock(spec=AttendanceRecordRepository),
        "leave_service": MagicMock(spec=LeaveService),
        "overtime_service": MagicMock(spec=OvertimeService),
        "payslip_service": MagicMock(spec=PayslipService),
    }
    defaults.update(overrides)
    return EmployeeToolRegistry(
        employee_id="00000000-0000-0000-0000-000000000001",
        **defaults,
    )


class TestEmployeeToolBoundary:
    """Verify the Employee Assistant tool set is structurally safe."""

    def test_all_tools_are_read_or_draft(self) -> None:
        for tool in EMPLOYEE_TOOL_DEFINITIONS:
            assert tool.kind in (ToolKind.READ, ToolKind.DRAFT)

    def test_no_write_tools_exist(self) -> None:
        write_kinds = {"write", "execute", "send", "mutate", "delete", "create"}
        for tool in EMPLOYEE_TOOL_DEFINITIONS:
            assert tool.kind.value not in write_kinds

    def test_employee_id_never_a_parameter(self) -> None:
        for tool in EMPLOYEE_TOOL_DEFINITIONS:
            params = tool.parameters.get("properties", {})
            assert "employee_id" not in params
            required = tool.parameters.get("required", [])
            assert "employee_id" not in required

    def test_known_tool_names(self) -> None:
        expected_names = {
            "get_my_profile",
            "list_my_documents",
            "get_today_attendance",
            "list_my_attendance_records",
            "list_my_employee_requests",
            "get_my_leave_balance",
            "list_my_payslips",
            "draft_leave_request",
            "draft_overtime_request",
        }
        assert EMPLOYEE_TOOL_NAMES == expected_names

    def test_descriptions_do_not_imply_other_employees(self) -> None:
        for tool in EMPLOYEE_TOOL_DEFINITIONS:
            desc_lower = tool.description.lower()
            assert "other employee" not in desc_lower
            assert "all employees" not in desc_lower

    def test_draft_tools_do_not_write(self) -> None:
        draft_tools = [t for t in EMPLOYEE_TOOL_DEFINITIONS if t.kind == ToolKind.DRAFT]
        for t in draft_tools:
            assert "draft" in t.description.lower() or "preview" in t.description.lower()

    def test_draft_tool_count(self) -> None:
        draft_tools = [t for t in EMPLOYEE_TOOL_DEFINITIONS if t.kind == ToolKind.DRAFT]
        assert len(draft_tools) == 2
        assert {t.name for t in draft_tools} == {"draft_leave_request", "draft_overtime_request"}

    def test_read_tool_count(self) -> None:
        read_tools = [t for t in EMPLOYEE_TOOL_DEFINITIONS if t.kind == ToolKind.READ]
        assert len(read_tools) == 7

    def test_all_tools_have_additional_properties_false(self) -> None:
        for tool in EMPLOYEE_TOOL_DEFINITIONS:
            msg = f"Tool '{tool.name}' missing additionalProperties=False"
            assert tool.parameters.get("additionalProperties") is False, msg

    def test_draft_tools_have_format_patterns(self) -> None:
        draft_tools = [t for t in EMPLOYEE_TOOL_DEFINITIONS if t.kind == ToolKind.DRAFT]
        for t in draft_tools:
            props = t.parameters.get("properties", {})
            for date_field in ["start_date", "end_date", "work_date"]:
                if date_field in props:
                    assert "pattern" in props[date_field], f"{t.name}.{date_field} missing pattern"
            for time_field in ["start_time", "end_time"]:
                if time_field in props:
                    assert "pattern" in props[time_field], f"{t.name}.{time_field} missing pattern"
            for str_field in ["reason", "project_or_task"]:
                if str_field in props:
                    assert "maxLength" in props[str_field], (
                        f"{t.name}.{str_field} missing maxLength"
                    )

    def test_read_tools_have_additional_properties_false(self) -> None:
        read_tools = [t for t in EMPLOYEE_TOOL_DEFINITIONS if t.kind == ToolKind.READ]
        for t in read_tools:
            assert t.parameters.get("additionalProperties") is False

    def test_hr_tools_not_in_employee_set(self) -> None:
        """HR tools should not overlap with employee tools."""
        from src.modules.assistant.domain.tools import TOOL_DEFINITIONS

        hr_names = {t.name for t in TOOL_DEFINITIONS}
        overlap = hr_names & EMPLOYEE_TOOL_NAMES
        assert len(overlap) == 0, f"HR tools leaked to employee set: {overlap}"

    def test_list_my_documents_has_no_parameters(self) -> None:
        """list_my_documents should not accept any parameters."""
        for t in EMPLOYEE_TOOL_DEFINITIONS:
            if t.name == "list_my_documents":
                props = t.parameters.get("properties", {})
                assert len(props) == 0

    def test_get_today_attendance_has_no_parameters(self) -> None:
        """get_today_attendance should not accept any parameters."""
        for t in EMPLOYEE_TOOL_DEFINITIONS:
            if t.name == "get_today_attendance":
                props = t.parameters.get("properties", {})
                assert len(props) == 0


class TestEmployeeToolRegistryDraftTools:
    """Verify Draft-Tools return DraftAction and don't write."""

    @pytest.mark.asyncio
    async def test_draft_leave_returns_draft_action(self) -> None:
        leave_service = MagicMock()
        registry = _make_registry(leave_service=leave_service)
        result_json = await registry.execute(
            "draft_leave_request",
            {
                "leave_type": "annual",
                "start_date": "2026-07-01",
                "end_date": "2026-07-03",
                "reason": "Family vacation",
            },
        )
        result = json.loads(result_json)
        assert "draft_action" in result
        assert result["draft_action"]["action_type"] == "submit_leave_request"
        leave_service.create_leave.assert_not_called()

    @pytest.mark.asyncio
    async def test_draft_overtime_returns_draft_action(self) -> None:
        overtime_service = MagicMock()
        registry = _make_registry(overtime_service=overtime_service)
        result_json = await registry.execute(
            "draft_overtime_request",
            {
                "work_date": "2026-07-05",
                "start_time": "18:00",
                "end_time": "21:00",
                "reason": "Project deadline",
            },
        )
        result = json.loads(result_json)
        assert "draft_action" in result
        assert result["draft_action"]["action_type"] == "submit_overtime_request"
        overtime_service.create_overtime.assert_not_called()

    @pytest.mark.asyncio
    async def test_draft_leave_validates_dates(self) -> None:
        leave_service = MagicMock()
        registry = _make_registry(leave_service=leave_service)
        result_json = await registry.execute(
            "draft_leave_request",
            {
                "leave_type": "annual",
                "start_date": "2026-07-03",
                "end_date": "2026-07-01",
                "reason": "Invalid range",
            },
        )
        result = json.loads(result_json)
        assert "error" in result
        leave_service.create_leave.assert_not_called()

    @pytest.mark.asyncio
    async def test_draft_overtime_validates_times(self) -> None:
        overtime_service = MagicMock()
        registry = _make_registry(overtime_service=overtime_service)
        result_json = await registry.execute(
            "draft_overtime_request",
            {
                "work_date": "2026-07-05",
                "start_time": "21:00",
                "end_time": "18:00",
                "reason": "Invalid range",
            },
        )
        result = json.loads(result_json)
        assert "error" in result
        overtime_service.create_overtime.assert_not_called()


class TestEmployeeToolRegistryReadTools:
    """Verify Read-Tools scope queries to authenticated employee."""

    @pytest.mark.asyncio
    async def test_get_my_profile_called_with_authenticated_employee_id(self) -> None:
        """Verify employee_service.get_employee called with injected employee_id."""
        emp_service = MagicMock()
        emp_service.get_employee = AsyncMock()

        registry = _make_registry(employee_service=emp_service)
        await registry.execute("get_my_profile", {})

        emp_service.get_employee.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000001",
        )

    @pytest.mark.asyncio
    async def test_list_my_documents_called_with_authenticated_employee_id(self) -> None:
        """Verify document_service.list_documents called with injected employee_id."""
        doc_service = MagicMock()
        doc_service.list_documents = AsyncMock(return_value=[])

        registry = _make_registry(document_service=doc_service)
        await registry.execute("list_my_documents", {})

        doc_service.list_documents.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000001",
        )

    @pytest.mark.asyncio
    async def test_get_today_attendance_called_with_authenticated_employee_id(
        self,
    ) -> None:
        """Verify attendance repo is called with the injected employee_id."""
        att_repo = MagicMock()
        att_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])

        registry = _make_registry(attendance_repo=att_repo)
        await registry.execute("get_today_attendance", {})

        # First positional arg to get_by_employee_and_date_range = employee_id
        call_args = att_repo.get_by_employee_and_date_range.call_args
        assert call_args is not None
        assert str(call_args[0][0]) == "00000000-0000-0000-0000-000000000001"

    @pytest.mark.asyncio
    async def test_list_my_attendance_records_called_with_authenticated_employee_id(
        self,
    ) -> None:
        """Verify attendance repo is called with the injected employee_id."""
        att_repo = MagicMock()
        att_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])

        registry = _make_registry(attendance_repo=att_repo)
        await registry.execute("list_my_attendance_records", {})

        call_args = att_repo.get_by_employee_and_date_range.call_args
        assert call_args is not None
        assert str(call_args[0][0]) == "00000000-0000-0000-0000-000000000001"

    @pytest.mark.asyncio
    async def test_list_my_employee_requests_called_with_authenticated_employee_id(
        self,
    ) -> None:
        """Verify leave/overtime services are called with the injected employee_id."""
        leave_service = MagicMock()
        leave_service.list_my_leaves = AsyncMock(return_value=[])
        overtime_service = MagicMock()
        overtime_service.list_my_overtime = AsyncMock(return_value=[])

        registry = _make_registry(
            leave_service=leave_service,
            overtime_service=overtime_service,
        )
        await registry.execute("list_my_employee_requests", {})

        leave_service.list_my_leaves.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000001",
        )
        overtime_service.list_my_overtime.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000001",
        )

    @pytest.mark.asyncio
    async def test_list_my_payslips_called_with_authenticated_employee_id(
        self,
    ) -> None:
        """Verify payslip service is called with the injected employee_id."""
        payslip_service = MagicMock()
        payslip_service.get_my_payslips = AsyncMock(return_value=[])

        registry = _make_registry(payslip_service=payslip_service)
        await registry.execute("list_my_payslips", {})

        payslip_service.get_my_payslips.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000001",
        )

    @pytest.mark.asyncio
    async def test_registry_never_calls_write_commit(self) -> None:
        """Verify EmployeeToolRegistry handler section has zero write calls."""
        import inspect

        from src.modules.assistant.application.employee_tool_registry import (
            EmployeeToolRegistry,
        )

        source = inspect.getsource(EmployeeToolRegistry)

        handler_start = source.find("async def _get_my_profile")
        assert handler_start != -1, "Handler section not found"
        handler_section = source[handler_start:]

        # Forbidden patterns — each must NOT appear in handler code
        forbidden = [
            "session.commit",
            "session.add(",
            "session.flush(",
            ".create(",
            ".update(",
            ".delete(",
            ".soft_delete(",
            ".upsert(",
            ".save(",
        ]

        for pattern in forbidden:
            lines = [ln.strip() for ln in handler_section.split("\n") if pattern in ln]
            # Allow SELECT with execute
            lines = [ln for ln in lines if not ("select" in ln.lower() and "execute" in ln.lower())]
            assert not lines, f"Handler section contains forbidden pattern '{pattern}': {lines}"

    @pytest.mark.asyncio
    async def test_registry_ignores_employee_id_from_llm(self) -> None:
        """Registry must ignore employee_id if LLM supplies it in args."""
        emp_service = MagicMock()
        emp_service.get_employee = AsyncMock()

        registry = _make_registry(employee_service=emp_service)

        # LLM sends employee_id in args — registry must use auth session id
        await registry.execute(
            "get_my_profile",
            {
                "employee_id": "00000000-0000-0000-0000-999999999999",
            },
        )

        # Must have been called with Registry's injected id, not the LLM-supplied one
        call_employee_id = emp_service.get_employee.call_args[0][0]
        assert str(call_employee_id) == "00000000-0000-0000-0000-000000000001", (
            f"Registry used LLM-supplied employee_id {call_employee_id} instead of authenticated id"
        )

    @pytest.mark.asyncio
    async def test_error_does_not_leak_pii(self) -> None:
        """Errors return generic message, not PII/DB details."""
        emp_service = MagicMock()
        emp_service.get_employee = AsyncMock(
            side_effect=Exception("Internal: connection to DB 'vroom_hr' failed"),
        )

        registry = _make_registry(employee_service=emp_service)
        result = json.loads(await registry.execute("get_my_profile", {}))

        assert "error" in result
        # Must NOT contain DB name, stack trace, or sensitive detail
        assert "vroom_hr" not in result["error"]
        assert "connection" not in result["error"]
        assert "session" not in result["error"]
        assert "Không thể xử lý" in result["error"]

    @pytest.mark.asyncio
    async def test_get_today_attendance_uses_todays_date(self) -> None:
        """get_today_attendance should query for today only."""
        from datetime import date

        att_repo = MagicMock()
        att_repo.get_by_employee_and_date_range = AsyncMock(return_value=[])

        registry = _make_registry(attendance_repo=att_repo)
        await registry.execute("get_today_attendance", {})

        call_args = att_repo.get_by_employee_and_date_range.call_args
        assert call_args is not None
        start_date = call_args[0][1]
        end_date = call_args[0][2]
        today = date.today()
        assert start_date == today
        assert end_date == today

    @pytest.mark.asyncio
    async def test_list_my_documents_returns_expected_shape(self) -> None:
        """list_my_documents should return documents in expected shape."""
        from datetime import datetime
        from uuid import uuid4

        doc_service = MagicMock()

        class FakeDoc:
            id = uuid4()
            document_type = "cccd"
            file_name = "cccd.pdf"
            file_size = 1024
            mime_type = "application/pdf"
            uploaded_at = datetime.now()

        doc_service.list_documents = AsyncMock(return_value=[FakeDoc()])

        registry = _make_registry(document_service=doc_service)
        result = json.loads(await registry.execute("list_my_documents", {}))

        assert "documents" in result
        assert len(result["documents"]) == 1
        doc = result["documents"][0]
        assert doc["document_type"] == "cccd"
        assert doc["file_name"] == "cccd.pdf"

    @pytest.mark.asyncio
    async def test_list_my_payslips_returns_expected_shape(self) -> None:
        """list_my_payslips should return payslips in expected shape matching Payslip model."""
        from datetime import date, datetime
        from uuid import uuid4

        payslip_service = MagicMock()

        class FakePayslip:
            id = uuid4()
            period_month = date(2026, 6, 1)
            gross_salary = 25000000.00
            deductions = 2500000.00
            insurance_employee = 1000000.00
            taxable_income = 24000000.00
            pit_amount = 500000.00
            net_salary = 22500000.00
            currency = "VND"
            published_at = datetime.now()

        payslip_service.get_my_payslips = AsyncMock(return_value=[FakePayslip()])

        registry = _make_registry(payslip_service=payslip_service)
        result = json.loads(await registry.execute("list_my_payslips", {}))

        assert "payslips" in result
        assert len(result["payslips"]) == 1
        p = result["payslips"][0]
        assert p["period_month"] == "2026-06-01"
        assert p["gross_salary"] == 25000000.00
        assert p["deductions"] == 2500000.00
        assert p["insurance_employee"] == 1000000.00
        assert p["taxable_income"] == 24000000.00
        assert p["pit_amount"] == 500000.00
        assert p["net_salary"] == 22500000.00
        assert p["currency"] == "VND"

    @pytest.mark.asyncio
    async def test_leave_balance_is_scoped_and_fresh(self) -> None:
        leave_service = MagicMock()
        leave_service.get_my_leave_balance = AsyncMock(
            return_value={
                "approved_days_used": 3,
                "remaining_days": 9,
                "as_of": "2026-07-14T00:00:00+00:00",
            }
        )
        registry = _make_registry(leave_service=leave_service)

        result = json.loads(await registry.execute("get_my_leave_balance", {}))

        leave_service.get_my_leave_balance.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000001",
        )
        assert result["approved_days_used"] == 3
        assert result["remaining_days"] == 9
        assert result["as_of"]

    @pytest.mark.asyncio
    async def test_forbidden_scope_is_refused_without_service_access(self) -> None:
        candidate_service = MagicMock()
        registry = _make_registry()
        result = json.loads(await registry.execute("search_candidates", {"query": "Alice"}))

        assert result["code"] == "scope_denied"
        assert "Employee" in result["error"]
        candidate_service.list_candidates.assert_not_called()
