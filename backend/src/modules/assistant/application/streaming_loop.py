"""The streaming tool-calling loop, shared by the HR and ESS assistants.

Both assistants run the same conversation: ask the model, execute whatever
tools it asks for, feed the results back, stop when it answers in words.  The
non-streaming halves of that loop are already duplicated between
:meth:`AssistantService.chat` and :meth:`EmployeeAssistantService.chat`, and
that duplication is why the ESS side reached production without a streaming
path at all — streaming was added to one copy and there was no second copy to
add it to.

So the streaming loop lives here once and both services delegate to it.  What
differs between them is passed in, and it is only ever two things: which tool
schema goes to the model, and which registry executes the calls.  Everything
downstream of that — the event names, when telemetry is written, what happens
when a tool raises — is identical by construction rather than by review.

The registry is taken structurally (:class:`StreamableToolRegistry`) rather
than as a concrete class, because ``ToolRegistry`` is built once per process
and ``EmployeeToolRegistry`` once per request with the caller's employee id
baked in.  Only the two methods below are shared, and only those are required.

This module deliberately performs no session lookup.  It receives an
``AssistantChatSession`` the router already resolved through ``ChatSessionGuard``
and can only attribute telemetry to that row — there is nothing here to query
by id, so there is nothing here to forget an owner filter on.
"""

from __future__ import annotations

import json
import logging
import time
import typing
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.assistant.domain.tools import is_registry_error
from src.modules.assistant.infrastructure.llm_client import AssistantLLMClient
from src.modules.assistant.infrastructure.quality_models import AssistantChatSession

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

TOOL_LOOP_FALLBACK = (
    "Xin lỗi, trợ lý đã xử lý quá nhiều bước. Vui lòng thử lại với câu hỏi cụ thể hơn."
)


class StreamableToolRegistry(Protocol):
    """The part of a tool registry the streaming loop uses."""

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Run the named tool and return its JSON result string."""
        ...

    def is_draft_tool(self, tool_name: str) -> bool:
        """Report whether the tool produces a Draft Action for human review."""
        ...


async def record_tool_event(
    session: AsyncSession,
    session_id: UUID,
    tool_name: str,
    duration_ms: float,
    success: bool,
) -> None:
    """Stage an ``AssistantToolCallEvent`` row for this tool call.

    Staged, not committed: the loop runs inside a request whose session is
    committed (or discarded) by the caller, and a commit here would also flush
    whatever else that request had pending.

    Args:
        session: Active DB session.
        session_id: The owning chat session's id.
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


async def stream_tool_loop(
    *,
    llm_client: AssistantLLMClient,
    tool_registry: StreamableToolRegistry,
    openai_messages: list[dict[str, Any]],
    openai_tools: list[dict[str, Any]],
    session: AsyncSession | None = None,
    chat_session: AssistantChatSession | None = None,
    log_prefix: str = "Assistant",
) -> typing.AsyncGenerator[dict[str, Any], None]:
    """Run the tool-calling loop, yielding SSE events as they happen.

    Event vocabulary, which the frontend's single stream reader parses for both
    assistants:

    * ``text_delta`` — a fragment of the model's answer, as it arrives
    * ``tool_start`` — a tool is about to run (name + raw arguments)
    * ``tool_end`` — that tool's result string
    * ``draft_action`` — a Draft-Tool proposed an action for the user to confirm
    * ``done`` — nothing further will be sent

    Args:
        llm_client: The assistant's LLM client.
        tool_registry: Executes tool calls; scoped by whoever built it.
        openai_messages: The prompt, already built by the calling service —
            this is where the two assistants' system prompts and context
            injection differ, and it is settled before the loop starts.
        openai_tools: The tool schema offered to the model.
        session: DB session for telemetry, or ``None`` to record none.
        chat_session: The caller's own chat session, already resolved and
            ownership-checked by the router. Taking the row rather than an id
            is what keeps an unauthorized id from reaching this layer: there is
            no lookup here to skip the owner filter.
        log_prefix: Distinguishes the two assistants in the logs.

    Yields:
        Dicts with ``event`` and ``data`` keys, for SSE serialisation.
    """
    draft_action: dict[str, Any] | None = None
    has_text_response = False

    for _iteration in range(MAX_TOOL_ITERATIONS):
        tool_calls_result: list[dict[str, Any]] | None = None
        streamed_parts: list[str] = []
        terminal_content: str | None = None

        async for chunk in llm_client.chat_stream(
            messages=openai_messages,
            tools=openai_tools,
        ):
            if chunk.content_delta:
                streamed_parts.append(chunk.content_delta)
                yield {"event": "text_delta", "data": {"content": chunk.content_delta}}
            if chunk.done:
                terminal_content = chunk.final_content
                tool_calls_result = chunk.tool_calls_acc

        # The client sends every delta *and* a terminal chunk whose
        # ``final_content`` is those same deltas joined, so the two are two
        # views of one answer rather than two halves of it. Concatenating both
        # — which this loop used to do — told the model on the next iteration
        # that it had said everything twice. The terminal chunk wins where it
        # exists; the deltas are the fallback for a client that omits it.
        if terminal_content is not None:
            final_content: str | None = terminal_content
        else:
            final_content = "".join(streamed_parts) if streamed_parts else None

        has_text_response = has_text_response or bool(final_content)

        if not tool_calls_result:
            break

        openai_messages.append(
            {
                "role": "assistant",
                "content": final_content,
                "tool_calls": tool_calls_result,
            }
        )

        for tc in tool_calls_result:
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

            yield {
                "event": "tool_start",
                "data": {"name": tool_name, "arguments": tc["function"]["arguments"]},
            }

            tool_start = time.monotonic()
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
            else:
                success = not is_registry_error(result_str)
            tool_duration_ms = (time.monotonic() - tool_start) * 1000
            logger.debug(
                "%s tool %s took %.0f ms (success=%s)",
                log_prefix,
                tool_name,
                tool_duration_ms,
                success,
            )

            if session is not None and chat_session is not None:
                await record_tool_event(
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

            openai_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                }
            )

            yield {
                "event": "tool_end",
                "data": {"name": tool_name, "result": result_str},
            }

    # The frontend hides tool messages, so a loop that ran out of iterations
    # without the model ever answering in words would render as an empty bubble.
    if not has_text_response:
        yield {"event": "text_delta", "data": {"content": TOOL_LOOP_FALLBACK}}

    if session is not None and chat_session is not None:
        chat_session.message_count += 1

    if draft_action:
        yield {"event": "draft_action", "data": draft_action}

    yield {"event": "done", "data": {}}
