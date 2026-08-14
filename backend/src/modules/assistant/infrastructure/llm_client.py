"""LLM Client for the AI Assistant module.

Own AsyncOpenAI client built for tool-calling.
Shares only configuration (endpoint, API key, model) with recruitment's
LLMAdapter — not code. The two clients are intentionally separate.

Features:
- Tool-calling loop: pass tool definitions, handle tool_calls, iterate
- Configurable max history (20 messages per grill decision)
- Token usage tracking
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from src.modules.assistant.domain.exceptions import LLMConnectionError
from src.modules.assistant.infrastructure.config import AssistantSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMResponse:
    """Result from the LLM including content and tool calls.

    Attributes:
        content: Text content from the assistant (may be None if only tool calls).
        tool_calls: List of tool call dicts from the LLM.
        token_usage: Token usage statistics.
    """

    content: str | None
    tool_calls: list[dict[str, Any]]
    token_usage: dict[str, int]


@dataclass
class LLMStreamChunk:
    """A single chunk from a streaming LLM response.

    Attributes:
        content_delta: Partial text content (None if not a text chunk).
        tool_calls_acc: Accumulated tool calls so far (None if none).
        done: Whether the stream is complete.
        final_content: The complete content string (only set when done=True).
    """

    content_delta: str | None = None
    tool_calls_acc: list[dict[str, Any]] | None = None
    done: bool = False
    final_content: str | None = None


class AssistantLLMClient:
    """LLM client for the AI Assistant, built for tool-calling.

    Args:
        settings: AssistantSettings with LLM connection details.
    """

    def __init__(self, settings: AssistantSettings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key or "not-needed",
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        self._model = settings.model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request with optional tool definitions.

        Args:
            messages: Full message history (system + user + assistant + tool results).
            tools: Optional tool definitions in OpenAI format.

        Returns:
            LLMResponse with content and/or tool_calls.

        Raises:
            ConnectionError: If the LLM endpoint is unreachable.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except APITimeoutError as exc:
            logger.error("LLM request timed out: %s", exc)
            raise LLMConnectionError(f"LLM timeout: {exc}") from exc
        except APIConnectionError as exc:
            logger.error("LLM connection failed: %s", exc)
            raise LLMConnectionError(f"LLM connection failed: {exc}") from exc
        except APIStatusError as exc:
            logger.error("LLM API error: %s", exc)
            raise LLMConnectionError(f"LLM API error {exc.status_code}: {exc}") from exc

        choices = response.choices
        if choices:
            choice = choices[0]
            message = choice.message
            content = message.content
            raw_tool_calls = message.tool_calls or []
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in raw_tool_calls
            ]
        else:
            # Some OpenAI-compatible gateways wrap the completion in a
            # top-level ``data`` object. OpenAI's SDK preserves that payload
            # in ``model_extra`` instead of populating ``choices``.
            extra = getattr(response, "model_extra", None) or {}
            wrapped_choices = (extra.get("data") or {}).get("choices", [])
            if not wrapped_choices:
                raise LLMConnectionError("LLM response contained no choices")
            raw_message = wrapped_choices[0].get("message") or {}
            content = raw_message.get("content")
            tool_calls = raw_message.get("tool_calls") or []

        token_usage = self._extract_token_usage(response)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            token_usage=token_usage,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """Stream a chat completion, yielding text deltas and tool calls.

        Uses OpenAI's streaming API (stream=True). For tool-calling
        responses the tool calls are accumulated and yielded once complete.

        Args:
            messages: Full message history (system + user + assistant + tool results).
            tools: Optional tool definitions in OpenAI format.

        Yields:
            LLMStreamChunk with content_delta or tool_calls_acc.

        Raises:
            LLMConnectionError: If the LLM endpoint is unreachable.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except APITimeoutError as exc:
            logger.error("LLM stream request timed out: %s", exc)
            raise LLMConnectionError(f"LLM timeout: {exc}") from exc
        except APIConnectionError as exc:
            logger.error("LLM stream connection failed: %s", exc)
            raise LLMConnectionError(f"LLM stream connection failed: {exc}") from exc
        except APIStatusError as exc:
            logger.error("LLM stream API error: %s", exc)
            raise LLMConnectionError(f"LLM stream API error {exc.status_code}: {exc}") from exc

        # Track tool calls across chunks (keyed by index)
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        content_chunks: list[str] = []

        async for chunk in stream:
            if not chunk.choices:
                # May be a usage-only chunk at the end
                continue

            delta = chunk.choices[0].delta
            if delta is None:
                continue

            if delta.content:
                content_chunks.append(delta.content)
                yield LLMStreamChunk(content_delta=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments

        # Build final result
        final_content = "".join(content_chunks) if content_chunks else None
        final_tool_calls = list(tool_calls_acc.values()) if tool_calls_acc else None

        yield LLMStreamChunk(
            done=True,
            final_content=final_content,
            tool_calls_acc=final_tool_calls,
        )

    @staticmethod
    def _extract_token_usage(response: object) -> dict[str, int]:
        """Extract token usage from the API response."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
