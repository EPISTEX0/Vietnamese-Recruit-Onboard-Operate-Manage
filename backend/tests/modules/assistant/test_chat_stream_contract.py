"""The SSE event contract, asserted against both assistants.

``AiChat`` sends every message through ``/chat/stream``.  The HR assistant has
served that path since streaming was added; the ESS one did not implement it at
all, so employee chat 404'd on every turn.  Rather than grow a second
independent copy of a loop that is already duplicated between
:meth:`AssistantService.chat` and :meth:`EmployeeAssistantService.chat`, both
streaming methods delegate to one generator in ``streaming_loop.py``.

That makes the event protocol a single contract with two entry points, so this
file runs the same assertions through both.  A test that only exercised the HR
service could not tell whether the ESS service streams the same events, and the
frontend's stream reader is shared between them — it has one parser for both.

The fakes come from ``assistant_support`` and are scripted rather than mocked
loosely: they replay the exact chunk sequence ``AssistantLLMClient.chat_stream``
produces — every text delta, followed by a terminal chunk whose
``final_content`` repeats the concatenation of those same deltas.  Reproducing
that shape is the point: a fake that omitted ``final_content`` would hide how
the two are combined.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from src.modules.assistant.application import employee_assistant_service as ess_module
from src.modules.assistant.application.assistant_service import (
    _MAX_TOOL_ITERATIONS,
    _TOOL_LOOP_FALLBACK,
    AssistantService,
    ChatMessage,
)
from src.modules.assistant.application.employee_assistant_service import (
    EmployeeAssistantService,
)
from src.modules.assistant.domain.tools import TOOL_ERROR_KEY
from src.modules.assistant.infrastructure.config import AssistantSettings
from src.modules.assistant.infrastructure.llm_client import LLMStreamChunk
from src.modules.assistant.infrastructure.quality_models import (
    AssistantChatSession,
    AssistantToolCallEvent,
)
from tests.modules.assistant.assistant_support import (
    FakeLLMClient,
    FakeRegistry,
    text_turn,
    tool_turn,
)

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _RecordingSession:
    """Stands in for ``AsyncSession`` and keeps whatever the loop adds."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        """Record an ORM object the loop wanted to persist."""
        self.added.append(obj)


@dataclass
class Subject:
    """One assistant under test, with the doubles it was built from."""

    kind: str
    service: Any
    llm: FakeLLMClient
    registry: FakeRegistry
    stream: Callable[..., AsyncIterator[dict[str, Any]]] = field(repr=False)


def _settings() -> AssistantSettings:
    return AssistantSettings(
        base_url="http://localhost:8000",
        api_key="test-key",
        model="test-model",
        max_history=20,
        timeout_seconds=30,
    )


@pytest.fixture(params=["hr", "ess"])
def make_subject(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., Subject]:
    """Build either assistant, both driven through ``chat_stream``.

    The ESS service constructs its ``EmployeeToolRegistry`` inside the call, so
    the fake is substituted at the class the module looked up rather than
    passed in — that keeps the production wiring under test on the HR side and
    still lets both share one scripted registry.
    """

    def _make(
        turns: list[list[LLMStreamChunk]],
        results: dict[str, str] | None = None,
        draft_tools: frozenset[str] = frozenset(),
        failing: frozenset[str] = frozenset(),
    ) -> Subject:
        llm = FakeLLMClient(turns)
        registry = FakeRegistry(results, draft_tools, failing)

        if request.param == "hr":
            service: Any = AssistantService(
                llm_client=llm,  # type: ignore[arg-type]
                tool_registry=registry,  # type: ignore[arg-type]
                settings=_settings(),
            )
        else:
            monkeypatch.setattr(
                ess_module,
                "EmployeeToolRegistry",
                lambda **_kwargs: registry,
            )
            service = EmployeeAssistantService(
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

        return Subject(
            kind=request.param,
            service=service,
            llm=llm,
            registry=registry,
            stream=service.chat_stream,
        )

    return _make


async def _collect(stream: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drain an SSE generator into a list of events."""
    return [event async for event in stream]


def _names(events: list[dict[str, Any]]) -> list[str]:
    return [event["event"] for event in events]


def _owned_session() -> AssistantChatSession:
    return AssistantChatSession(id=uuid4(), user_id=uuid4(), assistant_type="hr")


USER = [ChatMessage(role="user", content="Còn bao nhiêu phép?")]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTextTurn:
    """A turn with no tool call."""

    async def test_streams_each_delta_then_done(self, make_subject: Callable[..., Subject]) -> None:
        """Every text delta reaches the client, and the stream ends with ``done``.

        This is the whole visible contract for the common case: the frontend
        renders text from ``text_delta`` events and stops reading at ``done``.
        """
        subject = make_subject([text_turn("Xin ", "chào ", "bạn")])

        events = await _collect(subject.stream(USER))

        assert _names(events) == ["text_delta", "text_delta", "text_delta", "done"]
        assert [e["data"]["content"] for e in events[:3]] == ["Xin ", "chào ", "bạn"]

    async def test_no_tool_events_when_no_tool_is_called(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """A plain answer touches no tool."""
        subject = make_subject([text_turn("Không có gì")])

        events = await _collect(subject.stream(USER))

        assert subject.registry.executed == []
        assert "tool_start" not in _names(events)


class TestToolTurn:
    """A turn that calls a tool, then answers."""

    async def test_emits_tool_start_and_tool_end_around_execution(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """The client is told a tool began and what it returned."""
        subject = make_subject(
            [tool_turn(name="a_tool", arguments='{"x": 1}'), text_turn("Xong")],
            results={"a_tool": '{"total": 5}'},
        )

        events = await _collect(subject.stream(USER))

        assert _names(events) == ["tool_start", "tool_end", "text_delta", "done"]
        assert events[0]["data"] == {"name": "a_tool", "arguments": '{"x": 1}'}
        assert events[1]["data"] == {"name": "a_tool", "result": '{"total": 5}'}
        assert subject.registry.executed == [("a_tool", {"x": 1})]

    async def test_tool_failure_is_reported_as_a_result_not_a_crash(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """A failing tool ends its own turn, not the stream.

        The registry turns the failure into a JSON error the LLM can read (its
        own contract, per #400 -- it never raises out of ``execute()``) and the
        loop keeps going, so the employee gets a sentence rather than a dead stream.
        """
        subject = make_subject(
            [tool_turn(name="a_tool"), text_turn("Công cụ lỗi rồi")],
            failing=frozenset({"a_tool"}),
        )

        events = await _collect(subject.stream(USER))

        assert _names(events) == ["tool_start", "tool_end", "text_delta", "done"]
        result = json.loads(events[1]["data"]["result"])
        assert result["error"] == "Tool execution failed: a_tool"
        assert result[TOOL_ERROR_KEY] is True

    async def test_draft_action_is_announced_before_done(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """A Draft-Tool result surfaces as its own event, last before ``done``."""
        draft = {"action_type": "leave_request", "payload": {"days": 2}}
        subject = make_subject(
            [tool_turn(name="draft_leave_request"), text_turn("Đây là bản nháp")],
            results={"draft_leave_request": json.dumps({"draft_action": draft})},
            draft_tools=frozenset({"draft_leave_request"}),
        )

        events = await _collect(subject.stream(USER))

        assert _names(events)[-2:] == ["draft_action", "done"]
        assert events[-2]["data"] == draft


class TestAssistantTextFedBackToTheModel:
    """What the loop tells the model it said on the previous turn."""

    async def test_text_is_not_repeated_when_the_turn_also_calls_a_tool(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """The assistant turn sent back to the model holds its text once.

        The real client yields each delta *and* a terminal chunk whose
        ``final_content`` is those same deltas joined.  A loop that appends both
        builds ``"Để tôi kiểmĐể tôi kiểm"`` and feeds that to the next LLM call
        as the assistant's own words.  Nothing user-facing shows it — the
        frontend renders the deltas — which is why it needs asserting here.
        """
        subject = make_subject(
            [tool_turn("Để tôi ", "kiểm tra", name="a_tool"), text_turn("Xong")],
            results={"a_tool": "{}"},
        )

        await _collect(subject.stream(USER))

        assert len(subject.llm.calls) == 2, "the second turn must have happened"
        second_call = subject.llm.calls[1]["messages"]
        assistant_entries = [m for m in second_call if m["role"] == "assistant"]
        assert [m["content"] for m in assistant_entries] == ["Để tôi kiểm tra"]


class TestToolLoopExhaustion:
    """The loop refuses to spin forever."""

    async def test_fallback_text_is_streamed_when_no_answer_arrives(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """A model that only ever calls tools still produces a sentence.

        Without this the frontend hides every message it received (they are all
        tool messages) and the employee watches an empty bubble.
        """
        subject = make_subject([tool_turn(name="a_tool")], results={"a_tool": "{}"})

        events = await _collect(subject.stream(USER))

        assert len(subject.llm.calls) == _MAX_TOOL_ITERATIONS
        assert _names(events)[-2:] == ["text_delta", "done"]
        assert events[-2]["data"]["content"] == _TOOL_LOOP_FALLBACK

    async def test_no_fallback_when_the_model_did_answer(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """A normal answer is not followed by the apology."""
        subject = make_subject([text_turn("5 ngày")])

        events = await _collect(subject.stream(USER))

        contents = [e["data"]["content"] for e in events if e["event"] == "text_delta"]
        assert _TOOL_LOOP_FALLBACK not in contents


class TestTelemetry:
    """What the loop writes, and only for a session the caller owns."""

    async def test_tool_events_are_recorded_against_the_owned_session(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """Each tool call becomes an ``AssistantToolCallEvent`` on that session."""
        subject = make_subject(
            [tool_turn(name="a_tool"), text_turn("Xong")],
            results={"a_tool": "{}"},
        )
        session = _RecordingSession()
        chat_session = _owned_session()

        await _collect(subject.stream(USER, session=session, chat_session=chat_session))

        events = [o for o in session.added if isinstance(o, AssistantToolCallEvent)]
        assert len(events) == 1
        assert events[0].tool_name == "a_tool"
        assert events[0].session_id == chat_session.id
        assert events[0].success is True

    async def test_failed_tool_is_recorded_as_a_failure(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """Telemetry distinguishes a tool that failed from one that worked."""
        subject = make_subject(
            [tool_turn(name="a_tool"), text_turn("Lỗi")],
            failing=frozenset({"a_tool"}),
        )
        session = _RecordingSession()

        await _collect(subject.stream(USER, session=session, chat_session=_owned_session()))

        events = [o for o in session.added if isinstance(o, AssistantToolCallEvent)]
        assert [e.success for e in events] == [False]

    async def test_nothing_is_recorded_without_an_owned_session(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """An id the guard refused resolves to ``None`` and writes no telemetry.

        This is the security property the guard exists for, seen from the
        service side: with no owned row there is nothing to attribute a tool
        call to, and the loop must not invent one.
        """
        subject = make_subject(
            [tool_turn(name="a_tool"), text_turn("Xong")],
            results={"a_tool": "{}"},
        )
        session = _RecordingSession()

        events = await _collect(subject.stream(USER, session=session, chat_session=None))

        assert session.added == []
        assert _names(events) == ["tool_start", "tool_end", "text_delta", "done"]

    async def test_message_count_is_incremented_once_per_stream(
        self, make_subject: Callable[..., Subject]
    ) -> None:
        """One completed stream counts as one exchange."""
        subject = make_subject([text_turn("Xong")])
        chat_session = _owned_session()
        assert chat_session.message_count == 0

        await _collect(subject.stream(USER, session=_RecordingSession(), chat_session=chat_session))

        assert chat_session.message_count == 1


class TestEmployeeScoping:
    """The ESS loop stays bound to the authenticated employee."""

    async def test_employee_registry_is_built_with_the_session_employee_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``EmployeeToolRegistry`` receives the id from the service, not the LLM.

        Deliberately not parametrised over both assistants: this is the ESS-only
        half of the Employee Assistant scoping rule (CONTEXT.md § "Ngôn ngữ
        domain"), and it asserts the streaming path injects the employee id the
        same way the non-streaming path does.
        """
        captured: dict[str, Any] = {}
        registry = FakeRegistry({"a_tool": "{}"})

        def _capture(**kwargs: Any) -> FakeRegistry:
            captured.update(kwargs)
            return registry

        monkeypatch.setattr(ess_module, "EmployeeToolRegistry", _capture)

        employee_id = uuid4()
        service = EmployeeAssistantService(
            llm_client=FakeLLMClient([tool_turn(name="a_tool"), text_turn("Xong")]),  # type: ignore[arg-type]
            employee_id=employee_id,
            employee_service=MagicMock(),
            document_service=MagicMock(),
            attendance_repo=MagicMock(),
            leave_service=MagicMock(),
            overtime_service=MagicMock(),
            payslip_service=MagicMock(),
            settings=_settings(),
            context_builder=None,
        )

        await _collect(service.chat_stream(USER))

        assert captured["employee_id"] == employee_id
        assert isinstance(captured["employee_id"], UUID)
