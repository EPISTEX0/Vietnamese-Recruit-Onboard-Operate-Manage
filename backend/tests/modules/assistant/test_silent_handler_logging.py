"""Logging for the three silent tool-call failure shapes in the assistant loop.

The tool-calling loop is duplicated three times — ``AssistantService.chat``,
``EmployeeAssistantService.chat``, and the streaming ``stream_tool_loop``
shared by both ``chat_stream`` methods — and each copy independently
swallowed the same three failure shapes without a trace (#386 C1):

* malformed JSON in a tool call's *arguments* — the loop falls back to
  calling the tool with ``{}``. That fallback is kept, not changed: every
  handler in both tool registries already validates its own required
  arguments and returns a structured, LLM-visible error instead of crashing
  (see ``tool_registry.py``/``employee_tool_registry.py``), and several tools
  (``list_in_progress_onboarding``, ``get_my_profile``, ...) have no required
  arguments at all, so ``{}`` is a legitimate call for them. Skipping the
  tool call instead would deny those legitimate zero-arg calls and replace a
  tool's own specific error with a generic one.
* a tool that raises during execution — already degrades safely via
  ``success=False``, just lost the traceback.
* malformed JSON in a *draft tool's own result string* — the draft silently
  vanishes from the response.

One class per loop implementation, so a fix applied to one file and forgotten
in another shows up here rather than being masked by a shared fixture.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.modules.assistant.application import employee_assistant_service as ess_module
from src.modules.assistant.application.assistant_service import AssistantService, ChatMessage
from src.modules.assistant.application.employee_assistant_service import (
    EmployeeAssistantService,
)
from src.modules.assistant.application.streaming_loop import stream_tool_loop
from src.modules.assistant.infrastructure.config import AssistantSettings
from tests.modules.assistant.assistant_support import (
    FakeLLMClient,
    FakeRegistry,
    text_turn,
    tool_turn,
)

USER = [ChatMessage(role="user", content="Đếm ứng viên")]


def _settings() -> AssistantSettings:
    return AssistantSettings(
        base_url="http://localhost:8000",
        api_key="test-key",
        model="test-model",
        max_history=20,
        timeout_seconds=30,
    )


class TestAssistantServiceLogging:
    """``AssistantService.chat`` — the HR assistant's own (non-streaming) loop."""

    LOGGER = "src.modules.assistant.application.assistant_service"

    def _service(self, llm: FakeLLMClient, registry: FakeRegistry) -> AssistantService:
        return AssistantService(
            llm_client=llm,  # type: ignore[arg-type]
            tool_registry=registry,  # type: ignore[arg-type]
            settings=_settings(),
        )

    async def test_malformed_tool_arguments_are_logged_and_call_proceeds_with_empty_args(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="a_tool", arguments="{not json"), text_turn("Xong")])
        registry = FakeRegistry(results={"a_tool": "{}"})
        service = self._service(llm, registry)

        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            await service.chat(USER)

        assert registry.executed == [("a_tool", {})], "fallback to {} is unchanged behaviour"
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.WARNING
            and "a_tool" in r.getMessage()
            and "malformed JSON arguments" in r.getMessage()
            for r in matching
        ), caplog.text

    async def test_tool_execution_failure_is_logged_with_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="a_tool"), text_turn("Lỗi rồi")])
        registry = FakeRegistry(raising=frozenset({"a_tool"}))
        service = self._service(llm, registry)

        with caplog.at_level(logging.ERROR, logger=self.LOGGER):
            response = await service.chat(USER)

        assert response.messages, "the degrade path still returns a response, not a crash"
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.ERROR
            and "a_tool" in r.getMessage()
            and "execution raised" in r.getMessage()
            and r.exc_info is not None
            for r in matching
        ), caplog.text

    async def test_draft_result_parse_failure_is_logged_and_draft_action_is_omitted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="draft_x"), text_turn("Xong")])
        registry = FakeRegistry(
            results={"draft_x": "not valid json"}, draft_tools=frozenset({"draft_x"})
        )
        service = self._service(llm, registry)

        with caplog.at_level(logging.ERROR, logger=self.LOGGER):
            response = await service.chat(USER)

        assert response.draft_action is None
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.ERROR
            and "draft_x" in r.getMessage()
            and "draft result JSON parse failed" in r.getMessage()
            and r.exc_info is not None
            for r in matching
        ), caplog.text


class TestEmployeeAssistantServiceLogging:
    """``EmployeeAssistantService.chat`` — the ESS loop, a separate copy."""

    LOGGER = "src.modules.assistant.application.employee_assistant_service"

    def _service(
        self,
        monkeypatch: pytest.MonkeyPatch,
        llm: FakeLLMClient,
        registry: FakeRegistry,
    ) -> EmployeeAssistantService:
        monkeypatch.setattr(ess_module, "EmployeeToolRegistry", lambda **_kwargs: registry)
        return EmployeeAssistantService(
            llm_client=llm,  # type: ignore[arg-type]
            employee_id=uuid4(),
            employee_service=MagicMock(),
            document_service=MagicMock(),
            attendance_repo=MagicMock(),
            leave_service=MagicMock(),
            overtime_service=MagicMock(),
            payslip_service=MagicMock(),
            settings=_settings(),
            context_builder=None,
        )

    async def test_malformed_tool_arguments_are_logged_and_call_proceeds_with_empty_args(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="a_tool", arguments="{not json"), text_turn("Xong")])
        registry = FakeRegistry(results={"a_tool": "{}"})
        service = self._service(monkeypatch, llm, registry)

        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            await service.chat(USER)

        assert registry.executed == [("a_tool", {})], "fallback to {} is unchanged behaviour"
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.WARNING
            and "a_tool" in r.getMessage()
            and "malformed JSON arguments" in r.getMessage()
            for r in matching
        ), caplog.text

    async def test_tool_execution_failure_is_logged_with_traceback(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="a_tool"), text_turn("Lỗi rồi")])
        registry = FakeRegistry(raising=frozenset({"a_tool"}))
        service = self._service(monkeypatch, llm, registry)

        with caplog.at_level(logging.ERROR, logger=self.LOGGER):
            response = await service.chat(USER)

        assert response.messages
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.ERROR
            and "a_tool" in r.getMessage()
            and "execution raised" in r.getMessage()
            and r.exc_info is not None
            for r in matching
        ), caplog.text

    async def test_draft_result_parse_failure_is_logged_and_draft_action_is_omitted(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="draft_x"), text_turn("Xong")])
        registry = FakeRegistry(
            results={"draft_x": "not valid json"}, draft_tools=frozenset({"draft_x"})
        )
        service = self._service(monkeypatch, llm, registry)

        with caplog.at_level(logging.ERROR, logger=self.LOGGER):
            response = await service.chat(USER)

        assert response.draft_action is None
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.ERROR
            and "draft_x" in r.getMessage()
            and "draft result JSON parse failed" in r.getMessage()
            and r.exc_info is not None
            for r in matching
        ), caplog.text


class TestStreamingLoopLogging:
    """``stream_tool_loop`` — the module shared by both ``chat_stream`` methods."""

    LOGGER = "src.modules.assistant.application.streaming_loop"

    async def _run(self, llm: FakeLLMClient, registry: FakeRegistry) -> list[dict[str, Any]]:
        openai_messages: list[dict[str, Any]] = [{"role": "user", "content": "Đếm ứng viên"}]
        return [
            event
            async for event in stream_tool_loop(
                llm_client=llm,  # type: ignore[arg-type]
                tool_registry=registry,  # type: ignore[arg-type]
                openai_messages=openai_messages,
                openai_tools=[],
            )
        ]

    async def test_malformed_tool_arguments_are_logged_and_call_proceeds_with_empty_args(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="a_tool", arguments="{not json"), text_turn("Xong")])
        registry = FakeRegistry(results={"a_tool": "{}"})

        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            await self._run(llm, registry)

        assert registry.executed == [("a_tool", {})], "fallback to {} is unchanged behaviour"
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.WARNING
            and "a_tool" in r.getMessage()
            and "malformed JSON arguments" in r.getMessage()
            for r in matching
        ), caplog.text

    async def test_tool_execution_failure_is_logged_with_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="a_tool"), text_turn("Lỗi rồi")])
        registry = FakeRegistry(raising=frozenset({"a_tool"}))

        with caplog.at_level(logging.ERROR, logger=self.LOGGER):
            events = await self._run(llm, registry)

        assert any(e["event"] == "tool_end" for e in events), "the stream still finishes the turn"
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.ERROR
            and "a_tool" in r.getMessage()
            and "execution raised" in r.getMessage()
            and r.exc_info is not None
            for r in matching
        ), caplog.text

    async def test_draft_result_parse_failure_is_logged_and_draft_action_is_omitted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm = FakeLLMClient([tool_turn(name="draft_x"), text_turn("Xong")])
        registry = FakeRegistry(
            results={"draft_x": "not valid json"}, draft_tools=frozenset({"draft_x"})
        )

        with caplog.at_level(logging.ERROR, logger=self.LOGGER):
            events = await self._run(llm, registry)

        assert not any(e["event"] == "draft_action" for e in events)
        matching = [r for r in caplog.records if r.name == self.LOGGER]
        assert any(
            r.levelno == logging.ERROR
            and "draft_x" in r.getMessage()
            and "draft result JSON parse failed" in r.getMessage()
            and r.exc_info is not None
            for r in matching
        ), caplog.text
