"""Assistant Service — orchestrates the tool-calling loop.

This is the core of the AI Assistant. It:
1. Receives user message + full conversation history
2. Trims to max_history messages (20, per grill decision)
3. Sends to LLM with tool definitions
4. If LLM returns tool_calls → execute tools → send results back → loop
5. If LLM returns text → return final response to frontend

The tool-calling loop continues until the LLM returns text without
tool_calls, or max iterations are reached (safety cap).
"""

from __future__ import annotations

import json
import logging
import time
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.modules.assistant.application.context_builder import ContextBuilder
from src.modules.assistant.application.streaming_loop import (
    MAX_TOOL_ITERATIONS,
    TOOL_LOOP_FALLBACK,
    stream_tool_loop,
)
from src.modules.assistant.application.tool_registry import ToolRegistry
from src.modules.assistant.domain.tools import TOOL_DEFINITIONS, get_openai_tools
from src.modules.assistant.infrastructure.config import AssistantSettings
from src.modules.assistant.infrastructure.llm_client import AssistantLLMClient
from src.modules.assistant.infrastructure.quality_models import AssistantChatSession

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)

# Safety cap and exhaustion message. Defined in ``streaming_loop`` — the module
# both assistants share — and re-exported here under the names this module has
# always used, so ``chat`` and ``chat_stream`` cannot drift apart on either.
_MAX_TOOL_ITERATIONS = MAX_TOOL_ITERATIONS

_TOOL_LOOP_FALLBACK = TOOL_LOOP_FALLBACK

# System prompt — static, hardcoded per grill decision
_SYSTEM_PROMPT = """You are the AI Assistant for Vroom HR, a Vietnamese HR management platform.

You help HR administrators with recruitment and onboarding data.
You speak Vietnamese when the user speaks Vietnamese, English otherwise.

Rules:
- You NEVER write to the database directly.
- You only propose actions for HR to confirm (human-in-the-loop).
- For Read-Tools: call them to answer data questions accurately.
- For Draft-Tools: use draft tools when the user asks to compose/send an email.
- Be concise and helpful. Use Vietnamese when the user does.
- If a tool fails, tell the user clearly and suggest they try again.
- When you use information from [TÀI LIỆU NỘI BỘ LIÊN QUAN] in the context,
  cite the source at the end of your answer using the format:
  📎 Nguồn: Tên tài liệu 1, Tên tài liệu 2
  Only include documents you actually used in your answer.
"""


@dataclass
class ChatMessage:
    """A single message in the conversation.

    Attributes:
        role: Message role (user, assistant, tool).
        content: Text content.
        tool_calls: Optional tool calls from the assistant.
        tool_call_id: Optional ID linking a tool result to its call.
        name: Optional tool name for tool result messages.
        draft_action: Optional Draft Action from a draft tool.
    """

    role: str
    content: str | None = None
    tool_calls: list[dict[str, typing.Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    draft_action: dict[str, typing.Any] | None = None


@dataclass
class ChatResponse:
    """Response from the assistant chat endpoint.

    Attributes:
        messages: Updated conversation messages.
        draft_action: Optional Draft Action if a draft tool was called.
    """

    messages: list[ChatMessage]
    draft_action: dict[str, typing.Any] | None = None


class AssistantService:
    """Orchestrates the AI Assistant conversation loop.

    Args:
        llm_client: The assistant's own LLM client.
        tool_registry: Tool executor for Read-Tools and Draft-Tools.
        settings: Assistant settings with max_history, etc.
    """

    def __init__(
        self,
        llm_client: AssistantLLMClient,
        tool_registry: ToolRegistry,
        settings: AssistantSettings,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._settings = settings
        self._context_builder = context_builder

    async def chat(
        self,
        messages: list[ChatMessage],
        enabled_tool_names: set[str] | None = None,
        session: AsyncSession | None = None,
        chat_session: AssistantChatSession | None = None,
    ) -> ChatResponse:
        """Process a user message through the tool-calling loop.

        Args:
            messages: Full conversation history including the new user message.
            enabled_tool_names: If provided, only these tools are sent to the LLM.
            session: Optional DB session for logging tool call events.
            chat_session: The caller's own chat session, already resolved and
                ownership-checked by the router. Taking the row rather than an
                id is what keeps an unauthorized id from reaching this layer:
                there is no lookup here to skip the owner filter.

        Returns:
            ChatResponse with updated messages and optional draft_action.
        """
        round_trip_start = time.monotonic()

        # Build OpenAI-format messages with system prompt + context
        openai_messages = await self._build_messages(messages, enabled_tool_names)

        draft_action: dict[str, typing.Any] | None = None
        all_new_messages: list[ChatMessage] = []

        # Tool-calling loop
        for _iteration in range(_MAX_TOOL_ITERATIONS):
            llm_start = time.monotonic()
            response = await self._llm_client.chat(
                messages=openai_messages,
                tools=get_openai_tools(enabled_tool_names),
            )
            llm_duration_ms = (time.monotonic() - llm_start) * 1000
            logger.debug("LLM response took %.0f ms", llm_duration_ms)

            # No tool calls → LLM is done, return text response
            if not response.tool_calls:
                assistant_msg = ChatMessage(
                    role="assistant",
                    content=response.content or "",
                )
                all_new_messages.append(assistant_msg)
                break

            # Has tool calls → execute tools, add results, loop
            assistant_msg = ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            all_new_messages.append(assistant_msg)

            # Add assistant message to context for the next LLM call
            assistant_openai: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            }
            openai_messages.append(assistant_openai)

            # Execute each tool call
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
                    result_str = await self._tool_registry.execute(tool_name, tool_args)
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
                    "Tool %s took %.0f ms (success=%s)",
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

                # Check if this is a draft tool → extract Draft Action
                if self._tool_registry.is_draft_tool(tool_name):
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

                # Add tool result to messages
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

        # Diagnosis #3: fallback when loop exhausts without a text response.
        # Frontend hides tool messages, so user would see silence otherwise.
        has_text_response = any(m.role == "assistant" and m.content for m in all_new_messages)
        if not has_text_response:
            all_new_messages.append(ChatMessage(role="assistant", content=_TOOL_LOOP_FALLBACK))

        # Increment message_count for this exchange
        if session is not None and chat_session is not None:
            chat_session.message_count += 1

        total_duration_ms = (time.monotonic() - round_trip_start) * 1000
        logger.info(
            "Assistant round-trip took %.0f ms (%d messages, %d new)",
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
        enabled_tool_names: set[str] | None = None,
        session: AsyncSession | None = None,
        chat_session: AssistantChatSession | None = None,
    ) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
        """Process a user message and stream SSE events via async generator.

        Runs the same tool-calling loop as :meth:`chat`, but yields
        Server-Sent Events for real-time frontend display:
        - ``text_delta`` — partial text content from the LLM
        - ``tool_start`` — a tool is being called (name + arguments)
        - ``tool_end`` — a tool result is ready (name + result)
        - ``draft_action`` — a Draft Action was generated
        - ``done`` — stream complete

        The loop itself is :func:`stream_tool_loop`, shared with the ESS
        assistant. All this method decides is what the HR assistant puts in
        front of it: its own system prompt and the admin-configured tool subset.

        Args:
            messages: Full conversation history including the new user message.
            enabled_tool_names: If provided, only these tools are sent to the LLM.
            session: Optional DB session for logging tool call events.
            chat_session: The caller's own chat session, already resolved and
                ownership-checked by the router. Taking the row rather than an
                id is what keeps an unauthorized id from reaching this layer:
                there is no lookup here to skip the owner filter.

        Yields:
            Dicts with ``event`` and ``data`` keys for SSE serialisation.
        """

        openai_messages = await self._build_messages(messages, enabled_tool_names)

        async for event in stream_tool_loop(
            llm_client=self._llm_client,
            tool_registry=self._tool_registry,
            openai_messages=openai_messages,
            openai_tools=get_openai_tools(enabled_tool_names),
            session=session,
            chat_session=chat_session,
            log_prefix="HR assistant",
        ):
            yield event

    async def _record_tool_event(
        self,
        session: AsyncSession,
        session_id: UUID,
        tool_name: str,
        duration_ms: float,
        success: bool,
    ) -> None:
        """Record a tool call event to assistant_tool_call_events table.

        Args:
            session: Active DB session.
            session_id: Session UUID from chat session.
            tool_name: Name of the tool that was called.
            duration_ms: Execution duration in milliseconds.
            success: Whether the tool execution succeeded.
        """
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

    async def _build_messages(
        self, messages: list[ChatMessage], enabled_tool_names: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Build OpenAI-format messages with system prompt and history trimming.

        Applies max_history limit per grill decision (20 messages).
        Defense-in-depth: strips tool_calls from client-provided assistant
        messages and skips tool messages, since only the backend should
        produce these (diagnosis #2).
        """
        available_tools = TOOL_DEFINITIONS
        if enabled_tool_names is not None:
            available_tools = [t for t in TOOL_DEFINITIONS if t.name in enabled_tool_names]

        tools_str = "\n".join(
            f"{i + 1}. {t.name} — {t.description}" for i, t in enumerate(available_tools)
        )

        system_content = _SYSTEM_PROMPT + f"\nYou have access to these tools:\n{tools_str}\n"

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
                context_block = await self._context_builder.build_hr_context(
                    user_query=user_query,
                )
                if context_block:
                    result.append({"role": "system", "content": context_block})
            except Exception:
                logger.warning("Failed to build HR assistant context", exc_info=True)

        # Trim to max_history (excluding system messages),
        # ensuring we start at a user turn
        start_idx = max(0, len(messages) - self._settings.max_history)
        while start_idx > 0 and messages[start_idx].role != "user":
            start_idx -= 1
        history = messages[start_idx:]

        for msg in history:
            # Defense-in-depth: only user and assistant messages are allowed.
            # Tool messages should never come from client history.
            if msg.role == "tool":
                logger.warning("Stripped unexpected tool message from client history")
                continue

            if msg.role == "assistant" and msg.content is None:
                logger.warning("Stripped assistant history message without content")
                continue

            entry: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                entry["content"] = msg.content

            # Defense-in-depth: only include tool_calls if the message
            # was produced by the backend (i.e. has content=None with
            # tool_calls, which is the backend pattern). Client-provided
            # assistant messages must not carry tool_calls.
            if msg.role == "assistant" and msg.tool_calls and msg.content is None:
                entry["tool_calls"] = msg.tool_calls

            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            if msg.name:
                entry["name"] = msg.name
            result.append(entry)

        return result
