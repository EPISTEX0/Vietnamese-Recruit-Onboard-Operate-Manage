"""Employee Assistant Service — orchestrates the tool-calling loop.

Shares the AssistantLLMClient singleton with the HR Assistant, but uses its
own system prompt and tool set (employee-scoped). The tool-calling loop is
identical to the HR Assistant (see `assistant_service.py`).

This service is created per-request with the authenticated employee_id,
ensuring every tool call is scoped to that employee.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.modules.assistant.application.assistant_service import ChatMessage, ChatResponse
from src.modules.assistant.application.context_builder import ContextBuilder
from src.modules.assistant.application.employee_tool_registry import (
    EmployeeToolRegistry,
)
from src.modules.assistant.application.streaming_loop import (
    MAX_TOOL_ITERATIONS,
    TOOL_LOOP_FALLBACK,
    stream_tool_loop,
)
from src.modules.assistant.infrastructure.config import AssistantSettings
from src.modules.assistant.infrastructure.llm_client import AssistantLLMClient
from src.modules.assistant.infrastructure.quality_models import AssistantChatSession

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.modules.attendance.infrastructure.attendance_record_repository import (
        AttendanceRecordRepository,
    )
    from src.modules.employee.application.document_service import DocumentService
    from src.modules.employee.application.employee_service import EmployeeService
    from src.modules.employee_request.application.leave_service import LeaveService
    from src.modules.employee_request.application.overtime_service import OvertimeService
    from src.modules.payslip.application.payslip_service import PayslipService

logger = logging.getLogger(__name__)

# Shared with the HR assistant rather than re-declared, so the two cannot
# disagree about how many tool rounds are allowed or what to say when they run
# out — they used to hold identical copies of both.
_MAX_TOOL_ITERATIONS = MAX_TOOL_ITERATIONS

_TOOL_LOOP_FALLBACK = TOOL_LOOP_FALLBACK

_EMPLOYEE_SYSTEM_PROMPT = """You are the Employee Assistant for Vroom HR.

You help active employees with their own HR data.
You speak Vietnamese when the employee speaks Vietnamese, English otherwise.

Rules:
- You can ONLY access the current employee's own data — never ask for
  another employee's information.
- You NEVER write to the database directly.
- You only propose actions for the employee to review and confirm
  (human-in-the-loop).
- For Read-Tools: call them to answer data questions accurately.
- For Draft-Tools: use them when the employee asks to submit a leave
  or overtime request — the tool returns a draft for preview.
- Be concise and helpful. Use Vietnamese when the employee does.
- If a tool fails, tell the employee clearly and suggest they try again.
- When you use information from [TÀI LIỆU NỘI BỘ LIÊN QUAN] in the context,
  answer based on that information but DO NOT include citations.
  Employees should not see document source references.
"""


class EmployeeAssistantService:
    """Orchestrates the Employee AI Assistant conversation loop.

    Created per-request with the authenticated employee_id. Uses the shared
    AssistantLLMClient singleton and its own tool set.

    Args:
        llm_client: The assistant's own LLM client (shared singleton).
        employee_id: The authenticated employee's UUID.
        employee_service: For profile reads.
        document_service: For document vault reads.
        attendance_repo: For attendance record reads.
        leave_service: For leave request reads.
        overtime_service: For overtime request reads.
        payslip_service: For payslip reads.
        settings: Assistant settings with max_history, etc.
    """

    def __init__(
        self,
        llm_client: AssistantLLMClient,
        employee_id: UUID,
        employee_service: EmployeeService,
        document_service: DocumentService,
        attendance_repo: AttendanceRecordRepository,
        leave_service: LeaveService,
        overtime_service: OvertimeService,
        payslip_service: PayslipService,
        settings: AssistantSettings,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._employee_id = employee_id
        self._employee_service = employee_service
        self._document_service = document_service
        self._attendance_repo = attendance_repo
        self._leave_service = leave_service
        self._overtime_service = overtime_service
        self._payslip_service = payslip_service
        self._settings = settings
        self._context_builder = context_builder

    async def chat(
        self,
        messages: list[ChatMessage],
        session: AsyncSession | None = None,
        chat_session: AssistantChatSession | None = None,
    ) -> ChatResponse:
        """Process a user message through the tool-calling loop.

        Args:
            messages: Full conversation history including the new user message.
            session: Optional DB session for logging tool call events.
            chat_session: The caller's own chat session, already resolved and
                ownership-checked by the router. Taking the row rather than an
                id is what keeps an unauthorized id from reaching this layer:
                there is no lookup here to skip the owner filter.

        Returns:
            ChatResponse with updated messages and optional draft_action.
        """
        round_trip_start = time.monotonic()
        tool_registry = EmployeeToolRegistry(
            employee_id=self._employee_id,
            employee_service=self._employee_service,
            document_service=self._document_service,
            attendance_repo=self._attendance_repo,
            leave_service=self._leave_service,
            overtime_service=self._overtime_service,
            payslip_service=self._payslip_service,
        )

        openai_messages = await self._build_messages(messages, tool_registry)

        draft_action: dict[str, Any] | None = None
        all_new_messages: list[ChatMessage] = []

        for _iteration in range(_MAX_TOOL_ITERATIONS):
            llm_start = time.monotonic()
            response = await self._llm_client.chat(
                messages=openai_messages,
                tools=tool_registry.get_openai_tools(),
            )
            llm_duration_ms = (time.monotonic() - llm_start) * 1000
            logger.debug("Employee LLM response took %.0f ms", llm_duration_ms)

            if not response.tool_calls:
                assistant_msg = ChatMessage(
                    role="assistant",
                    content=response.content or "",
                )
                all_new_messages.append(assistant_msg)
                break

            assistant_msg = ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            all_new_messages.append(assistant_msg)

            assistant_openai: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            }
            openai_messages.append(assistant_openai)

            for tc in response.tool_calls:
                tool_name = tc["function"]["name"]
                session_id = chat_session.id if chat_session is not None else None
                try:
                    tool_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    logger.warning(
                        "Tool %s (session=%s): LLM sent malformed JSON arguments, "
                        "calling with empty args",
                        tool_name,
                        session_id,
                        exc_info=True,
                    )
                    tool_args = {}

                tool_start = time.monotonic()
                success = True
                try:
                    result_str = await tool_registry.execute(tool_name, tool_args)
                except Exception:
                    success = False
                    result_str = json.dumps({"error": f"Tool execution failed: {tool_name}"})
                    logger.exception(
                        "Tool %s (session=%s): execution raised, returning failure result to LLM",
                        tool_name,
                        session_id,
                    )
                tool_duration_ms = (time.monotonic() - tool_start) * 1000
                logger.debug(
                    "Employee tool %s took %.0f ms (success=%s)",
                    tool_name,
                    tool_duration_ms,
                    success,
                )

                # Record tool call event to DB if session is available
                if session is not None and chat_session is not None:
                    await self._record_tool_event(
                        session=session,
                        session_id=chat_session.id,
                        tool_name=tool_name,
                        duration_ms=tool_duration_ms,
                        success=success,
                    )

                if tool_registry.is_draft_tool(tool_name):
                    try:
                        result_data = json.loads(result_str)
                        if "draft_action" in result_data:
                            draft_action = result_data["draft_action"]
                    except json.JSONDecodeError:
                        logger.exception(
                            "Tool %s (session=%s): draft result JSON parse failed, "
                            "draft_action omitted from response",
                            tool_name,
                            session_id,
                        )

                tool_msg = ChatMessage(
                    role="tool",
                    content=result_str,
                    tool_call_id=tc["id"],
                    name=tool_name,
                )
                all_new_messages.append(tool_msg)

                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    }
                )

        has_text_response = any(m.role == "assistant" and m.content for m in all_new_messages)
        if not has_text_response:
            all_new_messages.append(ChatMessage(role="assistant", content=_TOOL_LOOP_FALLBACK))

        # Increment message_count for this exchange
        if session is not None and chat_session is not None:
            chat_session.message_count += 1

        total_duration_ms = (time.monotonic() - round_trip_start) * 1000
        logger.info(
            "Employee assistant round-trip took %.0f ms (%d messages, %d new)",
            total_duration_ms,
            len(messages),
            len(all_new_messages),
        )

        return ChatResponse(
            messages=all_new_messages,
            draft_action=draft_action,
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        session: AsyncSession | None = None,
        chat_session: AssistantChatSession | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Process a user message and stream SSE events via async generator.

        Runs the same tool-calling loop as :meth:`chat` and emits the same
        events as the HR assistant — ``text_delta``, ``tool_start``,
        ``tool_end``, ``draft_action``, ``done`` — because it is literally the
        same loop (:func:`stream_tool_loop`). The frontend has one stream reader
        for both assistants, so a second event vocabulary here would be a bug
        even if it were internally consistent.

        The tool registry is built per call with the authenticated employee's
        id, exactly as :meth:`chat` builds it: employee scoping does not weaken
        because the transport changed.

        Args:
            messages: Full conversation history including the new user message.
            session: Optional DB session for logging tool call events.
            chat_session: The caller's own chat session, already resolved and
                ownership-checked by the router. Taking the row rather than an
                id is what keeps an unauthorized id from reaching this layer:
                there is no lookup here to skip the owner filter.

        Yields:
            Dicts with ``event`` and ``data`` keys for SSE serialisation.
        """
        tool_registry = EmployeeToolRegistry(
            employee_id=self._employee_id,
            employee_service=self._employee_service,
            document_service=self._document_service,
            attendance_repo=self._attendance_repo,
            leave_service=self._leave_service,
            overtime_service=self._overtime_service,
            payslip_service=self._payslip_service,
        )

        openai_messages = await self._build_messages(messages, tool_registry)

        async for event in stream_tool_loop(
            llm_client=self._llm_client,
            tool_registry=tool_registry,
            openai_messages=openai_messages,
            openai_tools=tool_registry.get_openai_tools(),
            session=session,
            chat_session=chat_session,
            log_prefix="Employee assistant",
        ):
            yield event

    async def _build_messages(
        self,
        messages: list[ChatMessage],
        tool_registry: EmployeeToolRegistry,
    ) -> list[dict[str, Any]]:
        """Build OpenAI-format messages with employee system prompt + context."""
        tools = tool_registry.get_openai_tools()
        tools_str = "\n".join(
            f"{i + 1}. {t['function']['name']} — {t['function']['description']}"
            for i, t in enumerate(tools)
        )

        system_content = (
            _EMPLOYEE_SYSTEM_PROMPT + f"\nYou have access to these tools:\n{tools_str}\n"
        )

        result: list[dict[str, Any]] = [{"role": "system", "content": system_content}]

        # Extract last user message for KB retrieval (ticket #259)
        user_query: str | None = None
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                user_query = msg.content
                break

        # Inject dynamic context block as second system message (ticket #227, #259)
        if self._context_builder is not None:
            try:
                context_block = await self._context_builder.build_employee_context(
                    self._employee_id,
                    user_query=user_query,
                )
                if context_block:
                    result.append({"role": "system", "content": context_block})
            except Exception:
                logger.warning("Failed to build employee assistant context", exc_info=True)

        start_idx = max(0, len(messages) - self._settings.max_history)
        while start_idx > 0 and messages[start_idx].role != "user":
            start_idx -= 1
        history = messages[start_idx:]

        for msg in history:
            if msg.role == "tool":
                logger.warning("Stripped unexpected tool message from client history")
                continue

            if msg.role == "assistant" and msg.content is None:
                logger.warning("Stripped assistant history message without content")
                continue

            entry: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                entry["content"] = msg.content

            if msg.role == "assistant" and msg.tool_calls and msg.content is None:
                entry["tool_calls"] = msg.tool_calls

            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            if msg.name:
                entry["name"] = msg.name
            result.append(entry)

        return result

    async def _record_tool_event(
        self,
        session: AsyncSession,
        session_id: UUID,
        tool_name: str,
        duration_ms: float,
        success: bool,
    ) -> None:
        """Record a tool call event to assistant_tool_call_events table."""
        from src.modules.assistant.infrastructure.quality_models import (
            AssistantToolCallEvent,
        )

        event = AssistantToolCallEvent(
            session_id=session_id,
            tool_name=tool_name,
            duration_ms=int(duration_ms),
            success=success,
        )
        session.add(event)
