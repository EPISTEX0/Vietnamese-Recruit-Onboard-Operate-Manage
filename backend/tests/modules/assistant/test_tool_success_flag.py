"""``success`` telemetry reflects what the registry actually returns (#400).

All three tool-calling loops (``AssistantService.chat``,
``EmployeeAssistantService.chat``, and the shared ``stream_tool_loop``) used to
detect tool failure by wrapping ``tool_registry.execute()`` in ``try/except``.
Neither real registry (``ToolRegistry``, ``EmployeeToolRegistry``) ever lets a
handler's exception escape ``execute()`` -- both catch it, log it, and return a
JSON error string -- so that ``except`` never fired in production and
``success`` was always ``True``. ``FakeRegistry`` masked this: it raised
straight out of ``execute()``, a shape the real registries do not have.

This file uses the real registries (never ``FakeRegistry``) so the exact
defect reported in #400 is exercised: a handler that raises must still read
as ``success=False`` through the registry's own error-sentinel contract
(``TOOL_ERROR_KEY``, ``domain/tools.py``), not through the loop's ``except``.
It is paired with the opposite case -- a handler's own ``{"error": ...}``
business-facing message (invalid month, invalid status) -- which must NOT
read as a failure, since that is valid tool output, not a tool-calling defect.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.modules.assistant.application.assistant_service import AssistantService, ChatMessage
from src.modules.assistant.application.employee_assistant_service import (
    EmployeeAssistantService,
)
from src.modules.assistant.application.employee_tool_registry import EmployeeToolRegistry
from src.modules.assistant.application.streaming_loop import stream_tool_loop
from src.modules.assistant.application.tool_registry import InterviewLister, ToolRegistry
from src.modules.assistant.infrastructure.config import AssistantSettings
from src.modules.assistant.infrastructure.quality_models import (
    AssistantChatSession,
    AssistantToolCallEvent,
)
from src.modules.attendance.infrastructure.attendance_record_repository import (
    AttendanceRecordRepository,
)
from src.modules.employee.application.document_service import DocumentService
from src.modules.employee.application.employee_service import EmployeeService
from src.modules.employee_request.application.leave_service import LeaveService
from src.modules.employee_request.application.overtime_service import OvertimeService
from src.modules.onboarding.application.onboarding_service import OnboardingService
from src.modules.payslip.application.payslip_service import PayslipService
from src.modules.recruitment.application.candidate_lifecycle_service import (
    CandidateLifecycleService,
)
from tests.modules.assistant.assistant_support import FakeLLMClient, text_turn, tool_turn

USER = [ChatMessage(role="user", content="Đếm ứng viên")]


def _settings() -> AssistantSettings:
    return AssistantSettings(
        base_url="http://localhost:8000",
        api_key="test-key",
        model="test-model",
        max_history=20,
        timeout_seconds=30,
    )


class _RecordingSession:
    """Stands in for ``AsyncSession`` and keeps whatever the loop adds."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _owned_session() -> AssistantChatSession:
    return AssistantChatSession(id=uuid4(), user_id=uuid4(), assistant_type="hr")


def _hr_registry(*, raising: bool = False) -> tuple[ToolRegistry, AsyncMock]:
    """A real ``ToolRegistry``, optionally with a handler dependency that raises."""
    candidate_service = AsyncMock(spec=CandidateLifecycleService)
    if raising:
        candidate_service.list_candidates = AsyncMock(side_effect=RuntimeError("db exploded"))
    registry = ToolRegistry(
        candidate_service=candidate_service,
        onboarding_service=AsyncMock(spec=OnboardingService),
        interview_lister=AsyncMock(spec=InterviewLister),
    )
    return registry, candidate_service


def _employee_registry(*, raising: bool = False) -> tuple[EmployeeToolRegistry, MagicMock]:
    """A real ``EmployeeToolRegistry``, optionally with a handler dependency that raises."""
    employee_service = MagicMock(spec=EmployeeService)
    if raising:
        employee_service.get_employee = AsyncMock(side_effect=RuntimeError("db exploded"))
    registry = EmployeeToolRegistry(
        employee_id=uuid4(),
        employee_service=employee_service,
        document_service=MagicMock(spec=DocumentService),
        attendance_repo=MagicMock(spec=AttendanceRecordRepository),
        leave_service=MagicMock(spec=LeaveService),
        overtime_service=MagicMock(spec=OvertimeService),
        payslip_service=MagicMock(spec=PayslipService),
    )
    return registry, employee_service


def _events(session: _RecordingSession) -> list[AssistantToolCallEvent]:
    return [o for o in session.added if isinstance(o, AssistantToolCallEvent)]


class TestAssistantServiceSuccessFlag:
    """``AssistantService.chat`` — the HR assistant's own loop, real ``ToolRegistry``."""

    async def test_handler_raise_is_recorded_as_failure(self) -> None:
        """A handler exception the registry swallows must still read as success=False.

        This is the exact defect #400 reports: ``ToolRegistry.execute()`` never
        lets a handler's exception escape (it catches, logs, and returns a JSON
        error), so the loop's own ``except Exception`` around ``execute()``
        never fires. Before #400 this test is red -- success stays True.
        """
        registry, candidate_service = _hr_registry(raising=True)
        llm = FakeLLMClient(
            [
                tool_turn(name="count_candidates_by_status", arguments='{"status": "reviewing"}'),
                text_turn("Lỗi rồi"),
            ]
        )
        service = AssistantService(
            llm_client=llm,  # type: ignore[arg-type]
            tool_registry=registry,
            settings=_settings(),
        )
        session = _RecordingSession()

        await service.chat(USER, session=session, chat_session=_owned_session())  # type: ignore[arg-type]

        assert [e.success for e in _events(session)] == [False]
        candidate_service.list_candidates.assert_awaited_once()

    async def test_business_validation_error_is_not_recorded_as_a_failure(self) -> None:
        """A handler's own {"error": ...} to the LLM is not a tool-calling failure.

        ``count_candidates_by_status`` returns this for an out-of-range status
        -- the tool ran correctly and told the LLM the input was bad. Keying
        off the "error" field alone would misclassify this as success=False.
        """
        registry, candidate_service = _hr_registry()
        llm = FakeLLMClient(
            [
                tool_turn(
                    name="count_candidates_by_status", arguments='{"status": "not_a_real_status"}'
                ),
                text_turn("Trạng thái không hợp lệ"),
            ]
        )
        service = AssistantService(
            llm_client=llm,  # type: ignore[arg-type]
            tool_registry=registry,
            settings=_settings(),
        )
        session = _RecordingSession()

        await service.chat(USER, session=session, chat_session=_owned_session())  # type: ignore[arg-type]

        assert [e.success for e in _events(session)] == [True]
        candidate_service.list_candidates.assert_not_awaited()


class TestEmployeeAssistantServiceSuccessFlag:
    """``EmployeeAssistantService.chat`` — builds its own real ``EmployeeToolRegistry``."""

    def _service(self, llm: FakeLLMClient, employee_service: MagicMock) -> EmployeeAssistantService:
        return EmployeeAssistantService(
            llm_client=llm,  # type: ignore[arg-type]
            employee_id=uuid4(),
            employee_service=employee_service,
            document_service=MagicMock(spec=DocumentService),
            attendance_repo=MagicMock(spec=AttendanceRecordRepository),
            leave_service=MagicMock(spec=LeaveService),
            overtime_service=MagicMock(spec=OvertimeService),
            payslip_service=MagicMock(spec=PayslipService),
            settings=_settings(),
            context_builder=None,
        )

    async def test_handler_raise_is_recorded_as_failure(self) -> None:
        """Same defect as the HR loop, through ``EmployeeToolRegistry`` instead.

        Before #400 this test is red -- success stays True.
        """
        employee_service = MagicMock(spec=EmployeeService)
        employee_service.get_employee = AsyncMock(side_effect=RuntimeError("db exploded"))
        llm = FakeLLMClient([tool_turn(name="get_my_profile"), text_turn("Lỗi rồi")])
        service = self._service(llm, employee_service)
        session = _RecordingSession()

        await service.chat(USER, session=session, chat_session=_owned_session())  # type: ignore[arg-type]

        assert [e.success for e in _events(session)] == [False]
        employee_service.get_employee.assert_awaited_once()

    async def test_business_validation_error_is_not_recorded_as_a_failure(self) -> None:
        """``list_my_attendance_records`` rejecting month=13 is not a failure."""
        employee_service = MagicMock(spec=EmployeeService)
        llm = FakeLLMClient(
            [
                tool_turn(name="list_my_attendance_records", arguments='{"month": 13}'),
                text_turn("Tháng không hợp lệ"),
            ]
        )
        service = self._service(llm, employee_service)
        session = _RecordingSession()

        await service.chat(USER, session=session, chat_session=_owned_session())  # type: ignore[arg-type]

        assert [e.success for e in _events(session)] == [True]

    async def test_scope_denied_refusal_is_not_recorded_as_a_failure(self) -> None:
        """A policy refusal (asking for an HR-only tool) is not a tool-calling failure.

        Decision (#400 brief, point 3): ``scope_denied`` is the registry
        behaving exactly as designed -- structurally identical to a handler's
        own business-validation message, not a registry-generated error. It
        gets no ``TOOL_ERROR_KEY`` (see ``employee_tool_registry.py``), so it
        reads as success=True, the same bucket as "invalid month".
        """
        employee_service = MagicMock(spec=EmployeeService)
        llm = FakeLLMClient(
            [
                tool_turn(name="search_candidates", arguments='{"query": "Alice"}'),
                text_turn("Không thể truy cập"),
            ]
        )
        service = self._service(llm, employee_service)
        session = _RecordingSession()

        await service.chat(USER, session=session, chat_session=_owned_session())  # type: ignore[arg-type]

        assert [e.success for e in _events(session)] == [True]


class TestStreamToolLoopSuccessFlag:
    """``stream_tool_loop`` — the module shared by both ``chat_stream`` methods.

    Driven directly (not through either service) so it is provably the shared
    code under test, not one assistant's copy standing in for it.
    """

    async def _run(
        self,
        llm: FakeLLMClient,
        registry: ToolRegistry | EmployeeToolRegistry,
        session: _RecordingSession,
    ) -> None:
        openai_messages: list[dict[str, Any]] = [{"role": "user", "content": "Đếm ứng viên"}]
        async for _event in stream_tool_loop(
            llm_client=llm,  # type: ignore[arg-type]
            tool_registry=registry,
            openai_messages=openai_messages,
            openai_tools=[],
            session=session,  # type: ignore[arg-type]
            chat_session=_owned_session(),
        ):
            pass

    async def test_hr_registry_handler_raise_is_recorded_as_failure(self) -> None:
        """Before #400 this test is red -- success stays True."""
        registry, _candidate_service = _hr_registry(raising=True)
        llm = FakeLLMClient(
            [
                tool_turn(name="count_candidates_by_status", arguments='{"status": "reviewing"}'),
                text_turn("Lỗi"),
            ]
        )
        session = _RecordingSession()

        await self._run(llm, registry, session)

        assert [e.success for e in _events(session)] == [False]

    async def test_employee_registry_handler_raise_is_recorded_as_failure(self) -> None:
        """Same, through ``EmployeeToolRegistry`` -- the shared loop must not care which."""
        registry, _employee_service = _employee_registry(raising=True)
        llm = FakeLLMClient([tool_turn(name="get_my_profile"), text_turn("Lỗi")])
        session = _RecordingSession()

        await self._run(llm, registry, session)

        assert [e.success for e in _events(session)] == [False]

    async def test_business_validation_error_is_not_recorded_as_a_failure(self) -> None:
        """Covers the sentinel-absent path once at the shared loop, not per assistant."""
        registry, candidate_service = _hr_registry()
        llm = FakeLLMClient(
            [
                tool_turn(name="count_candidates_by_status", arguments='{"status": "bogus"}'),
                text_turn("..."),
            ]
        )
        session = _RecordingSession()

        await self._run(llm, registry, session)

        assert [e.success for e in _events(session)] == [True]
        candidate_service.list_candidates.assert_not_awaited()
