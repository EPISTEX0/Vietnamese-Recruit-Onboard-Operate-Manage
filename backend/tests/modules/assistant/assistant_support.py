"""Scripted doubles for the assistant services.

Shared by the streaming contract tests, the ESS endpoint integration tests and
the quality-metric tests, following the ``tests/*_support.py`` convention
already used for the Postgres and MinIO harnesses.

One script drives both transports.  A "turn" is written once and
:class:`FakeLLMClient` serves it either as a chunk sequence to ``chat_stream``
or as a single :class:`LLMResponse` to ``chat``, so a test about message counts
or tool-call telemetry reads the same whichever endpoint it drives — and the
two endpoints cannot be shown to agree by being given different inputs.

The streaming side is deliberately faithful to one detail:
:meth:`AssistantLLMClient.chat_stream` yields every text delta *and* a terminal
chunk whose ``final_content`` is those same deltas joined.  A loose mock that
returned only one of the two would let a loop that mishandles their
relationship pass — which is exactly the defect these fakes were written to
catch.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from src.modules.assistant.infrastructure.llm_client import LLMResponse, LLMStreamChunk


def text_turn(*deltas: str) -> list[LLMStreamChunk]:
    """One LLM turn that streams ``deltas`` and calls no tool."""
    joined = "".join(deltas) if deltas else None
    return [LLMStreamChunk(content_delta=d) for d in deltas] + [
        LLMStreamChunk(done=True, final_content=joined, tool_calls_acc=None)
    ]


def tool_turn(
    *deltas: str,
    name: str = "count_candidates_by_status",
    arguments: str = "{}",
    call_id: str = "tc_1",
) -> list[LLMStreamChunk]:
    """One LLM turn that streams ``deltas`` and then requests a tool call."""
    joined = "".join(deltas) if deltas else None
    return [LLMStreamChunk(content_delta=d) for d in deltas] + [
        LLMStreamChunk(
            done=True,
            final_content=joined,
            tool_calls_acc=[
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        )
    ]


class FakeLLMClient:
    """Replays a scripted turn per ``chat_stream`` call and records its inputs.

    Once the script runs out the last turn repeats, so a test about loop
    exhaustion can script a single tool turn instead of five identical ones.
    """

    def __init__(self, turns: list[list[LLMStreamChunk]]) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def _next_turn(self, messages: list[dict[str, Any]], tools: Any) -> list[LLMStreamChunk]:
        """Record the call and return the turn to replay for it."""
        # Deep-copied: the loop keeps mutating the list it passed in, so a
        # stored reference would show every call the history's final state.
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools})
        return self._turns[min(len(self.calls) - 1, len(self._turns) - 1)]

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Yield the next scripted turn, remembering the messages it was sent."""
        for chunk in self._next_turn(messages, tools):
            yield chunk

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Collapse the next scripted turn into a non-streaming response.

        The terminal chunk already holds what a non-streaming call returns —
        the joined text and the accumulated tool calls — so the same script
        describes both transports rather than each needing its own.
        """
        terminal = self._next_turn(messages, tools)[-1]
        return LLMResponse(
            content=terminal.final_content,
            tool_calls=terminal.tool_calls_acc or [],
            token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )


class ExplodingLLMClient:
    """Fails part-way through a turn, after some text has already been sent."""

    def __init__(self, message: str = "upstream died") -> None:
        self._message = message

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream one delta, then raise."""
        yield LLMStreamChunk(content_delta="Đang tra c")
        raise RuntimeError(self._message)


class FakeRegistry:
    """A tool registry with scripted results, usable by either assistant."""

    def __init__(
        self,
        results: dict[str, str] | None = None,
        draft_tools: frozenset[str] = frozenset(),
        failing: frozenset[str] = frozenset(),
    ) -> None:
        self._results = results or {}
        self._draft_tools = draft_tools
        self._failing = failing
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Record the call and return the scripted result, or raise."""
        self.executed.append((tool_name, arguments))
        if tool_name in self._failing:
            raise RuntimeError("tool blew up")
        return self._results.get(tool_name, "{}")

    def is_draft_tool(self, tool_name: str) -> bool:
        """Report whether the tool yields a draft action."""
        return tool_name in self._draft_tools

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Minimal tool schema — the ESS prompt builder reads this."""
        return [
            {
                "type": "function",
                "function": {"name": "a_tool", "description": "a tool"},
            }
        ]
