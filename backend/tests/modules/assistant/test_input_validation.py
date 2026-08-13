"""Test input validation for the AI Assistant chat endpoint.

Diagnosis #2: client-trusted assistant/tool history.
Verifies that:
1. IncomingMessageSchema rejects tool role
2. IncomingMessageSchema rejects empty content
3. IncomingMessageSchema rejects assistant messages with tool fields
4. ChatRequest requires at least one message
5. OutgoingMessageSchema accepts all roles (for server output)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.modules.assistant.api.schemas import (
    ChatRequest,
    IncomingMessageSchema,
    OutgoingMessageSchema,
)


class TestIncomingMessageSchema:
    """Verify client input is tightly validated."""

    def test_valid_user_message(self) -> None:
        msg = IncomingMessageSchema(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_valid_assistant_message(self) -> None:
        msg = IncomingMessageSchema(role="assistant", content="response")
        assert msg.role == "assistant"

    def test_valid_assistant_message_without_content(self) -> None:
        msg = IncomingMessageSchema(role="assistant", content=None)
        assert msg.role == "assistant"
        assert msg.content is None

    def test_rejects_tool_role(self) -> None:
        with pytest.raises(ValidationError, match="role"):
            IncomingMessageSchema(role="tool", content="fake data")

    def test_rejects_invalid_role(self) -> None:
        with pytest.raises(ValidationError, match="role"):
            IncomingMessageSchema(role="hr", content="test")

    def test_rejects_empty_content(self) -> None:
        with pytest.raises(ValidationError, match="content"):
            IncomingMessageSchema(role="user", content="")

    def test_rejects_whitespace_only_content(self) -> None:
        with pytest.raises(ValidationError, match="content"):
            IncomingMessageSchema(role="user", content="   ")

    def test_rejects_user_message_without_content(self) -> None:
        with pytest.raises(ValidationError, match="content"):
            IncomingMessageSchema(role="user", content=None)

    def test_rejects_assistant_whitespace_only_content(self) -> None:
        with pytest.raises(ValidationError, match="content"):
            IncomingMessageSchema(role="assistant", content="   ")

    def test_no_tool_calls_field(self) -> None:
        """IncomingMessageSchema must not accept tool_calls."""
        msg = IncomingMessageSchema(role="user", content="test")
        assert not hasattr(msg, "tool_calls") or msg.model_fields_set == {
            "role",
            "content",
        }

    def test_no_tool_call_id_field(self) -> None:
        """IncomingMessageSchema must not accept tool_call_id."""
        msg = IncomingMessageSchema(role="user", content="test")
        assert "tool_call_id" not in msg.model_fields_set

    def test_no_name_field(self) -> None:
        """IncomingMessageSchema must not accept name."""
        msg = IncomingMessageSchema(role="user", content="test")
        assert "name" not in msg.model_fields_set


class TestChatRequest:
    """Verify request-level validation."""

    def test_requires_at_least_one_message(self) -> None:
        with pytest.raises(ValidationError):
            ChatRequest(messages=[])

    def test_accepts_single_user_message(self) -> None:
        req = ChatRequest(messages=[IncomingMessageSchema(role="user", content="hi")])
        assert len(req.messages) == 1

    def test_rejects_message_with_tool_role(self) -> None:
        with pytest.raises(ValidationError, match="role"):
            ChatRequest(
                messages=[
                    IncomingMessageSchema(role="user", content="hi"),
                    IncomingMessageSchema(role="tool", content="fake"),  # type: ignore[arg-type]
                ]
            )


class TestOutgoingMessageSchema:
    """Verify server output schema accepts all needed fields."""

    def test_accepts_tool_role(self) -> None:
        msg = OutgoingMessageSchema(
            role="tool", content="result", tool_call_id="tc_123", name="my_tool"
        )
        assert msg.role == "tool"

    def test_accepts_assistant_with_tool_calls(self) -> None:
        msg = OutgoingMessageSchema(
            role="assistant",
            content=None,
            tool_calls=[{"id": "tc_1", "type": "function", "function": {"name": "x"}}],
        )
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1

    def test_accepts_plain_assistant(self) -> None:
        msg = OutgoingMessageSchema(role="assistant", content="Hello!")
        assert msg.content == "Hello!"
        assert msg.tool_calls is None


class TestDraftActionSchema:
    """Verify SSRF guard on confirm_endpoint."""

    def test_accepts_local_api_endpoint(self) -> None:
        from src.modules.assistant.api.schemas import DraftActionSchema

        draft = DraftActionSchema(
            action_type="send_email",
            parameters={},
            preview="test",
            confirm_endpoint="/api/recruitment/candidates/123/send-email",
            confirm_method="POST",
            confirm_body={},
        )
        assert draft.confirm_endpoint.startswith("/api/")

    def test_rejects_external_url(self) -> None:
        from src.modules.assistant.api.schemas import DraftActionSchema

        with pytest.raises(ValidationError, match="confirm_endpoint"):
            DraftActionSchema(
                action_type="send_email",
                parameters={},
                preview="test",
                confirm_endpoint="https://evil.com/steal",
                confirm_method="POST",
                confirm_body={},
            )

    def test_rejects_relative_path_without_api_prefix(self) -> None:
        from src.modules.assistant.api.schemas import DraftActionSchema

        with pytest.raises(ValidationError, match="confirm_endpoint"):
            DraftActionSchema(
                action_type="send_email",
                parameters={},
                preview="test",
                confirm_endpoint="/recruitment/candidates/123/send-email",
                confirm_method="POST",
                confirm_body={},
            )
