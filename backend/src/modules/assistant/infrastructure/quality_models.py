"""SQLAlchemy models for AI Assistant quality metrics.

Defines the SQLModel table classes for assistant_chat_sessions,
assistant_feedback_events, and assistant_tool_call_events that
map to PostgreSQL tables for monitoring assistant quality.
"""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from src.shared.sql_types import EnumAsString


class AssistantType(str, Enum):
    """Type of AI assistant involved in a chat session."""

    HR = "hr"
    EMPLOYEE = "employee"


class FeedbackType(str, Enum):
    """User feedback classification for assistant messages."""

    UP = "up"
    DOWN = "down"


class AssistantChatSession(SQLModel, table=True):
    """Tracks a single chat session between a user and an AI assistant.

    Each session belongs to one user and one assistant type. Sessions
    may optionally be linked to an employee record. The session records
    when it started, ended, how many messages were exchanged, and the
    last error (if any).
    """

    __tablename__ = "assistant_chat_sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    assistant_type: AssistantType = Field(
        sa_column=Column(EnumAsString(AssistantType, 10), nullable=False),
    )
    employee_id: UUID | None = Field(default=None, foreign_key="employees.id", index=True)
    start_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    end_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    message_count: int = Field(default=0, nullable=False)
    last_error: str | None = Field(default=None)


class AssistantFeedbackEvent(SQLModel, table=True):
    """Records user feedback (up/down) on a specific message in a session.

    Each event is tied to a session and a message index within that
    session. Users may optionally include free-text feedback alongside
    the thumbs-up/down rating.
    """

    __tablename__ = "assistant_feedback_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    session_id: UUID = Field(
        foreign_key="assistant_chat_sessions.id",
        nullable=False,
        index=True,
    )
    message_index: int = Field(nullable=False)
    feedback_type: FeedbackType = Field(
        sa_column=Column(EnumAsString(FeedbackType, 4), nullable=False),
    )
    optional_text: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AssistantToolCallEvent(SQLModel, table=True):
    """Records every tool invocation made during an assistant chat session.

    Tracks which tool was called, whether it succeeded, how long it took,
    and any error message. This powers latency monitoring and failure
    analysis dashboards.
    """

    __tablename__ = "assistant_tool_call_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    session_id: UUID = Field(
        foreign_key="assistant_chat_sessions.id",
        nullable=False,
        index=True,
    )
    tool_name: str = Field(nullable=False)
    arguments_hash: str | None = Field(default=None)
    success: bool = Field(nullable=False)
    error_message: str | None = Field(default=None)
    duration_ms: int = Field(nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
