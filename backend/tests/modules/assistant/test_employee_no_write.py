"""Test the Employee Assistant NEVER writes — structural safety verification.

Core invariants verified (CONTEXT.md § "Nội bộ AI Assistant"):
1. EmployeeToolRegistry has NO handlers that call DB write methods
2. EmployeeAssistantService returns draft_action in response — never auto-confirms
3. The chat loop never calls any write/confirm endpoint
4. System prompt instructs the LLM to never write directly
5. All tool calls use injected employee_id, never from LLM params
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.assistant.application.assistant_service import ChatMessage
from src.modules.assistant.application.employee_assistant_service import (
    EmployeeAssistantService,
)
from src.modules.assistant.application.employee_tool_registry import EmployeeToolRegistry
from src.modules.assistant.infrastructure.config import AssistantSettings
from src.modules.assistant.infrastructure.llm_client import LLMResponse


@pytest.fixture
def mock_llm_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def settings() -> AssistantSettings:
    return AssistantSettings(
        base_url="http://localhost:8000",
        api_key="test-key",
        model="test-model",
        max_history=20,
        timeout_seconds=30,
    )


def _make_service(
    mock_llm_client: MagicMock,
    settings: AssistantSettings,
) -> EmployeeAssistantService:
    return EmployeeAssistantService(
        llm_client=mock_llm_client,
        employee_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        employee_service=MagicMock(),
        attendance_repo=MagicMock(),
        leave_service=MagicMock(),
        overtime_service=MagicMock(),
        payslip_service=MagicMock(),
        document_service=MagicMock(),
        settings=settings,
    )


class TestDraftActionFlow:
    """Verify draft tools produce DraftAction in the response — never write."""

    @pytest.mark.asyncio
    async def test_draft_leave_request_returns_draft_action_in_response(
        self,
        mock_llm_client: MagicMock,
        settings: AssistantSettings,
    ) -> None:
        mock_llm_client.chat = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=None,
                    tool_calls=[
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "draft_leave_request",
                                "arguments": json.dumps(
                                    {
                                        "leave_type": "annual",
                                        "start_date": "2026-07-01",
                                        "end_date": "2026-07-03",
                                        "reason": "Nghỉ phép du lịch",
                                    }
                                ),
                            },
                        }
                    ],
                    token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                ),
                LLMResponse(
                    content="Tôi đã tạo draft đơn xin nghỉ phép cho bạn.",
                    tool_calls=None,
                    token_usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                ),
            ]
        )
        service = _make_service(mock_llm_client, settings)
        response = await service.chat(
            [
                ChatMessage(role="user", content="Cho tôi xin nghỉ phép từ 01/07 đến 03/07"),
            ]
        )
        assert response.draft_action is not None
        assert response.draft_action["action_type"] == "submit_leave_request"
        assert response.draft_action["confirm_endpoint"] == "/api/employee-requests/me/leave"
        assert response.draft_action["confirm_method"] == "POST"
        assert response.draft_action["confirm_body"]["leave_type"] == "annual"
        assert response.draft_action["confirm_body"]["start_date"] == "2026-07-01"
        assert response.draft_action["confirm_body"]["end_date"] == "2026-07-03"
        assert response.draft_action["confirm_body"]["reason"] == "Nghỉ phép du lịch"
        assert response.draft_action["parameters"]["leave_type"] == "annual"
        assert response.draft_action["parameters"]["start_date"] == "2026-07-01"

    @pytest.mark.asyncio
    async def test_draft_overtime_request_returns_draft_action_in_response(
        self,
        mock_llm_client: MagicMock,
        settings: AssistantSettings,
    ) -> None:
        mock_llm_client.chat = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=None,
                    tool_calls=[
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "draft_overtime_request",
                                "arguments": json.dumps(
                                    {
                                        "work_date": "2026-06-15",
                                        "start_time": "18:00",
                                        "end_time": "21:00",
                                        "reason": "Xử lý báo cáo Q2",
                                    }
                                ),
                            },
                        }
                    ],
                    token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                ),
                LLMResponse(
                    content="Tôi đã tạo draft đăng ký tăng ca cho bạn.",
                    tool_calls=None,
                    token_usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                ),
            ]
        )
        service = _make_service(mock_llm_client, settings)
        response = await service.chat(
            [
                ChatMessage(role="user", content="Đăng ký tăng ca ngày 15/06"),
            ]
        )
        assert response.draft_action is not None
        assert response.draft_action["action_type"] == "submit_overtime_request"

    @pytest.mark.asyncio
    async def test_no_draft_action_on_read_tool_only(
        self,
        mock_llm_client: MagicMock,
        settings: AssistantSettings,
    ) -> None:
        mock_llm_client.chat = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=None,
                    tool_calls=[
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "get_my_profile",
                                "arguments": "{}",
                            },
                        }
                    ],
                    token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                ),
                LLMResponse(
                    content="Đây là thông tin profile của bạn.",
                    tool_calls=None,
                    token_usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                ),
            ]
        )

        dummy_employee = MagicMock()
        dummy_employee.employee_code = "NV-001"
        dummy_employee.full_name = "Nguyễn Văn A"
        dummy_employee.email = "a@company.com"
        dummy_employee.phone = None
        dummy_employee.date_of_birth = None
        dummy_employee.gender = None
        dummy_employee.address = None
        dummy_employee.department_id = None
        dummy_employee.position_id = None
        dummy_employee.start_date = None
        dummy_employee.contract_type = None

        emp_service = MagicMock()
        emp_service.get_employee = AsyncMock(return_value=dummy_employee)

        service = EmployeeAssistantService(
            llm_client=mock_llm_client,
            employee_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            employee_service=emp_service,
            attendance_repo=MagicMock(),
            leave_service=MagicMock(),
            overtime_service=MagicMock(),
            payslip_service=MagicMock(),
            document_service=MagicMock(),
            settings=settings,
        )
        response = await service.chat(
            [
                ChatMessage(role="user", content="Xem profile của tôi"),
            ]
        )
        assert response.draft_action is None

    @pytest.mark.asyncio
    async def test_service_never_auto_confirms_draft(
        self,
        mock_llm_client: MagicMock,
        settings: AssistantSettings,
    ) -> None:
        """Draft tool returns draft_action but NEVER auto-confirms."""
        mock_llm_client.chat = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=None,
                    tool_calls=[
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "draft_leave_request",
                                "arguments": json.dumps(
                                    {
                                        "leave_type": "sick",
                                        "start_date": "2026-07-05",
                                        "end_date": "2026-07-05",
                                        "reason": "Bị ốm",
                                    }
                                ),
                            },
                        }
                    ],
                    token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                ),
                LLMResponse(
                    content="Tôi đã tạo draft đơn nghỉ ốm. Vui lòng kiểm tra và xác nhận.",
                    tool_calls=None,
                    token_usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                ),
            ]
        )
        service = _make_service(mock_llm_client, settings)
        response = await service.chat(
            [
                ChatMessage(role="user", content="Tôi bị ốm, xin nghỉ 1 ngày"),
            ]
        )
        assert response.draft_action is not None
        assert "confirmed" not in response.draft_action
        assert "submission_result" not in response.draft_action

        # Verify assistant text does not claim the request was submitted
        assistant_texts = [
            m.content for m in response.messages if m.role == "assistant" and m.content
        ]
        for text in assistant_texts:
            assert "đã gửi" not in text.lower()


class TestStructuralNoWrite:
    """Verify the Employee Assistant has NO write capability at any layer."""

    @staticmethod
    def _make_registry(**overrides: MagicMock) -> EmployeeToolRegistry:
        from unittest.mock import MagicMock

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
            "attendance_repo": MagicMock(spec=AttendanceRecordRepository),
            "leave_service": MagicMock(spec=LeaveService),
            "overtime_service": MagicMock(spec=OvertimeService),
            "payslip_service": MagicMock(spec=PayslipService),
            "document_service": MagicMock(spec=DocumentService),
        }
        defaults.update(overrides)
        return EmployeeToolRegistry(
            employee_id="00000000-0000-0000-0000-000000000001",
            **defaults,
        )

    """Verify the Employee Assistant has NO write capability at any layer."""

    def test_tool_registry_has_no_write_handlers(self) -> None:
        import inspect

        from src.modules.assistant.application.employee_tool_registry import (
            EmployeeToolRegistry,
        )

        source = inspect.getsource(EmployeeToolRegistry)

        # 1. Check no write-prefix methods exist in the entire class
        for prefix in ("_write_", "_submit_", "_create_", "_update_", "_delete_", "_approve_"):
            # Must not define any method starting with these prefixes
            for line in source.split("\n"):
                stripped = line.strip()
                if stripped.startswith(f"async def {prefix}") or stripped.startswith(
                    f"def {prefix}"
                ):
                    raise AssertionError(f"Found forbidden write handler: {stripped}")

        # 2. Check that handler implementations do not call session.write methods
        # Extract only the handler section (private async methods)
        handler_section = source[source.find("async def _get_my_profile") :]
        forbidden_calls = [
            "session.commit(",
            "session.add(",
            "session.flush(",
            ".create(",
            ".update(",
            ".delete(",
            ".soft_delete(",
            ".upsert(",
            ".save(",
        ]
        for pattern in forbidden_calls:
            lines = [line.strip() for line in handler_section.split("\n") if pattern in line]
            for line in lines:
                # Allow SELECT statements with .execute()
                if "execute(" in line and "SELECT" in line.upper():
                    continue
                raise AssertionError(f"Found forbidden call '{pattern}' in handler: {line}")

    def test_all_handler_methods_are_approved(self) -> None:
        approved_handlers = {
            "_get_my_profile",
            "_get_my_attendance",
            "_get_my_employee_requests",
            "_get_my_payslips",
            "_list_my_documents",
            "_get_today_attendance",
            "_list_my_attendance_records",
            "_list_my_employee_requests",
            "_get_my_leave_balance",
            "_list_my_payslips",
            "_draft_leave_request",
            "_draft_overtime_request",
        }

        import inspect

        from src.modules.assistant.application.employee_tool_registry import (
            EmployeeToolRegistry,
        )

        source_lines = inspect.getsource(EmployeeToolRegistry).split("\n")
        actual_methods = set()
        for line in source_lines:
            line = line.strip()
            if (line.startswith("async def ") or line.startswith("def ")) and "_" in line:
                name = line.split("def ")[1].split("(")[0].strip()
                if name.startswith("_"):
                    actual_methods.add(name)

        unknown = actual_methods - approved_handlers - {"__init__"}
        assert not unknown, f"Unknown handlers: {unknown}"

    def test_service_never_auto_confirms(self) -> None:
        import inspect

        from src.modules.assistant.application.employee_assistant_service import (
            EmployeeAssistantService,
        )

        source = inspect.getsource(EmployeeAssistantService)
        for pattern in [
            "requests.post",
            "requests.put",
            "httpx.",
            "aiohttp.",
            "session.commit",
            "confirm(",
        ]:
            assert pattern not in source
        assert "draft_action = result_data" in source

        # session.add() is allowed ONLY in _record_tool_event for quality monitoring
        add_lines = [line.strip() for line in source.split("\n") if "session.add(" in line]
        assert len(add_lines) == 1, (
            f"session.add() should appear only in _record_tool_event, found: {add_lines}"
        )
        assert "_record_tool_event" in source

    def test_system_prompt_instructs_no_write(self) -> None:
        import inspect

        from src.modules.assistant.application import employee_assistant_service as eas

        source = inspect.getsource(eas)
        assert "NEVER write" in source
        assert "human-in-the-loop" in source

    def test_draft_tools_never_call_service_write(self) -> None:
        """Verify each draft handler has ZERO service/repo calls."""
        import inspect

        from src.modules.assistant.application.employee_tool_registry import (
            EmployeeToolRegistry,
        )

        source = inspect.getsource(EmployeeToolRegistry)

        # Test each draft handler separately with proper bounds
        for handler_name in ["_draft_leave_request", "_draft_overtime_request"]:
            marker = f"async def {handler_name}"
            handler_start = source.find(marker)
            assert handler_start != -1, f"Draft handler '{handler_name}' not found"

            # Find next method boundary
            next_method = source.find("\n    async def ", handler_start + 1)
            if next_method == -1:
                next_method = source.find("\n    def ", handler_start + 1)
            if next_method == -1:
                next_method = len(source)

            handler_section = source[handler_start:next_method]

            # These patterns should NOT appear in draft handlers
            forbidden = [
                "self._employee_service.",
                "self._attendance_repo.",
                "self._leave_service.",
                "self._overtime_service.",
                "self._payslip_service.",
                "session.",
            ]
            for pattern in forbidden:
                assert pattern not in handler_section, (
                    f"Handler '{handler_name}' calls service/repo: '{pattern}'"
                )

    @pytest.mark.asyncio
    async def test_registry_unknown_tool_returns_generic_error(self) -> None:
        """Unknown tool must NOT leak internal details in error."""
        import json

        registry = self._make_registry()
        result = json.loads(await registry.execute("nonexistent_tool", {}))
        assert "error" in result
        assert "Không thể xử lý" in result["error"]
        # Must not contain the tool name or any internal detail
        assert "nonexistent_tool" not in result["error"]
