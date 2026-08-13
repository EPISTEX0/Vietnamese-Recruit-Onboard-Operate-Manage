"""Domain entities for the Gmail Integration module.

Defines the SQLModel table classes for EmailMessage, SyncCursor,
EmailAttachment, GmailAuditLog, and OutboundEmail
that map to PostgreSQL tables used for Gmail email management,
synchronization, and outbound sending.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class EmailMessage(SQLModel, table=True):
    """Represents an email fetched from Gmail and stored locally.

    Stores email metadata (subject, sender, recipients, labels) for
    search and display without requiring Gmail API calls. The raw_payload
    is encrypted with AES-256-GCM for security.
    """

    __tablename__ = "email_messages"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    gmail_message_id: str = Field(max_length=255, unique=True, nullable=False, index=True)
    gmail_thread_id: str = Field(max_length=255, nullable=False, index=True)
    subject: str = Field(default="", max_length=998, nullable=False)
    sender_email: str = Field(default="", max_length=255, nullable=False)
    sender_name: str = Field(default="", max_length=255, nullable=False)
    recipient_emails: list[str] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    cc_emails: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    received_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    snippet: str = Field(default="", max_length=200, nullable=False)
    label_ids: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    has_attachments: bool = Field(default=False, nullable=False)
    raw_payload_enc: str | None = Field(default=None)
    processing_status: str = Field(default="unprocessed", max_length=30, nullable=False)
    category: str | None = Field(default=None, max_length=20)
    retry_count: int = Field(default=0, nullable=False)
    processing_error: str | None = Field(default=None, max_length=500)
    next_retry_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    last_retry_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    is_permanently_failed: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ClassificationRolloutEventRecord(SQLModel, table=True):
    """Minimal, content-free telemetry for classifier rollout decisions."""

    __tablename__ = "classification_rollout_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    gmail_message_id: str = Field(max_length=255, nullable=False, index=True)
    mode: str = Field(max_length=20, nullable=False)
    selected_classifier_version: str = Field(max_length=100, nullable=False)
    stable_intent: str | None = Field(default=None, max_length=50)
    candidate_intent: str | None = Field(default=None, max_length=50)
    policy_version: str = Field(max_length=100, nullable=False)
    prompt_version: str = Field(default="unknown", max_length=100, nullable=False)
    has_cv: bool = Field(default=False, nullable=False)
    stable_latency_ms: int = Field(default=0, nullable=False)
    candidate_latency_ms: int | None = Field(default=None)
    candidate_provider_error: bool = Field(default=False, nullable=False)
    retry_count: int = Field(default=0, nullable=False)
    retry_failure: bool = Field(default=False, nullable=False)
    prompt_tokens: int = Field(default=0, nullable=False)
    completion_tokens: int = Field(default=0, nullable=False)
    estimated_cost_usd: float = Field(default=0.0, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class SyncCursor(SQLModel, table=True):
    """Tracks the Gmail history_id for the Organization singleton.

    The history_id is used with Gmail's history.list API to fetch only
    emails newer than the last successful poll. HR/User identity is not
    part of cursor ownership.
    """

    __tablename__ = "sync_cursors"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_singleton_key: str = Field(
        default="default", max_length=32, unique=True, nullable=False
    )
    history_id: str = Field(max_length=50, nullable=False)
    last_poll_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class EmailAttachment(SQLModel, table=True):
    """Represents an attachment belonging to a fetched email.

    Stores attachment metadata and optional storage path for
    downloaded attachment binary data used by downstream pipelines.
    """

    __tablename__ = "email_attachments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email_message_id: UUID = Field(foreign_key="email_messages.id", nullable=False, index=True)
    gmail_attachment_id: str = Field(max_length=255, nullable=False)
    filename: str = Field(max_length=255, nullable=False)
    mime_type: str = Field(max_length=100, nullable=False)
    size_bytes: int = Field(nullable=False)
    storage_path: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class GmailAuditLog(SQLModel, table=True):
    """Audit trail entry for Gmail API operations.

    Records all Gmail operations (fetch, send, label_modify, connect,
    disconnect) for debugging and compliance. Does NOT store email body
    content, snippets, or attachment binary data.
    """

    __tablename__ = "gmail_audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    operation_type: str = Field(max_length=50, nullable=False)
    message_count: int = Field(default=0, nullable=False)
    success: bool = Field(nullable=False)
    metadata_: dict[str, Any] | None = Field(
        default=None, sa_column=Column("metadata", JSONB, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class OutboundEmail(SQLModel, table=True):
    """Tracks an outbound email command created by HR confirmation.

    Each row represents one send command. The lifecycle is:
    pending -> sending -> sent | failed.

    The idempotency_key prevents duplicate sends on retry.
    The email is sent using the Organization Google Connection
    token, not the creating HR user's personal token.
    """

    __tablename__ = "outbound_emails"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    idempotency_key: str = Field(max_length=64, unique=True, nullable=False, index=True)
    candidate_id: UUID | None = Field(
        default=None, foreign_key="candidates.id", nullable=True
    )
    subject: str = Field(max_length=998, nullable=False)
    body_html: str = Field(nullable=False)
    cc_recipients: str | None = Field(default=None, nullable=True)  # noqa: E501  # JSON array of CC email addresses
    reply_to_message_id: str | None = Field(default=None, max_length=255, nullable=True)
    recipient_email: str = Field(max_length=255, nullable=False)
    sender_email: str | None = Field(default=None, max_length=255)
    status: str = Field(default="pending", max_length=20, nullable=False, index=True)
    gmail_message_id: str | None = Field(default=None, max_length=255)
    gmail_thread_id: str | None = Field(default=None, max_length=255)
    error_message: str | None = Field(default=None)
    retry_count: int = Field(default=0, nullable=False)
    max_retries: int = Field(default=3, nullable=False)
    last_retry_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_by_user_id: UUID = Field(foreign_key="users.id", nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
