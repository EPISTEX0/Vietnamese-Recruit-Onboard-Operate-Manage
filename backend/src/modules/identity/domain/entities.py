"""Domain entities for the Identity & Auth module.

Defines the SQLModel table classes for User, OAuthGrant, and RefreshToken
that map to PostgreSQL tables used for authentication and authorization.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from src.shared.sql_types import EnumAsString


class UserRole(str, Enum):
    """Enumeration of user roles for access control."""

    SYSTEM_ADMIN = "system_admin"
    HR = "hr"
    USER = "user"


# Must match ``ROLE_COLUMN_LENGTH`` in migration 084, which widened the real
# column from 10 to hold ``'system_admin'`` (12 characters). Leaving the model
# at 10 would make the next ``alembic revision --autogenerate`` propose
# narrowing the column back and reintroduce the bug 084 fixed.
ROLE_COLUMN_LENGTH = 20


class User(SQLModel, table=True):
    """Represents an authenticated HR user in the system.

    Users are system accounts for HR and active Employees. Each user is
    tied to one email address and may optionally be linked to one Employee.
    """

    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(max_length=255, unique=True, nullable=False, index=True)
    name: str = Field(max_length=255, nullable=False)
    avatar_url: str | None = Field(default=None)
    employee_id: UUID | None = Field(
        default=None, foreign_key="employees.id", unique=True, index=True
    )
    password_hash: str = Field(default="", max_length=255, nullable=False)
    must_change_password: bool = Field(default=False, nullable=False)
    google_sub: str | None = Field(default=None, max_length=255, unique=True, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_login: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    is_active: bool = Field(default=True, nullable=False)
    role: UserRole = Field(
        default=UserRole.USER,
        sa_column=Column(
            EnumAsString(UserRole, ROLE_COLUMN_LENGTH),
            nullable=False,
            default=UserRole.USER,
            index=True,
        ),
    )


class WhitelistEntryType(str, Enum):
    """Enumeration of whitelist entry types."""

    EXACT_EMAIL = "exact_email"
    DOMAIN_PATTERN = "domain_pattern"


class WhitelistEntry(SQLModel, table=True):
    """Represents a whitelist entry for login access control.

    Entries can be either exact email addresses or domain patterns
    (e.g., @example.com) that allow all emails from that domain.
    """

    __tablename__ = "whitelist_entries"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    value: str = Field(max_length=255, unique=True, nullable=False, index=True)
    entry_type: WhitelistEntryType = Field(
        sa_column=Column(EnumAsString(WhitelistEntryType, 20), nullable=False),
    )
    added_by_user_id: UUID = Field(foreign_key="users.id", nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class OAuthGrant(SQLModel, table=True):
    """Stores encrypted Google OAuth2 tokens for a user.

    Access and refresh tokens are encrypted with AES-256-GCM before
    storage. Each user has at most one active grant. The grant tracks
    which scopes were authorized and whether the tokens are still valid.
    """

    __tablename__ = "oauth_grants"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(
        foreign_key="users.id", ondelete="CASCADE", nullable=False, index=True
    )
    provider: str = Field(default="google", max_length=50, nullable=False)
    access_token_enc: str = Field(nullable=False)
    refresh_token_enc: str = Field(nullable=False)
    scopes: list[str] = Field(sa_column=Column(ARRAY(String), nullable=False))
    token_expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    is_valid: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class RefreshToken(SQLModel, table=True):
    """Represents a system-issued refresh token for JWT session management.

    Only the SHA-256 hash of the token is stored. Each user should have
    at most one non-revoked refresh token at any time (single active
    session invariant). Old tokens are revoked on new login.
    """

    __tablename__ = "refresh_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(
        foreign_key="users.id", ondelete="CASCADE", nullable=False, index=True
    )
    token_hash: str = Field(max_length=64, unique=True, nullable=False, index=True)
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    user_agent: str | None = Field(default=None)


class OAuthConfig(SQLModel, table=True):
    """Stores OAuth provider credentials for admin-managed configuration.

    The client_secret is encrypted with AES-256-GCM before storage using
    the existing CryptoUtils. Only one active configuration per provider
    is allowed (enforced by unique constraint on provider + is_active).
    Falls back to environment variables when no active DB config exists.
    """

    __tablename__ = "oauth_configs"
    __table_args__ = (
        # At most one active config per provider. Unlike the five other
        # UNIQUE diffs in the drift report, nothing replaces this one — drop it
        # and two configs can be active for the same provider at once, leaving
        # the pick undefined.
        UniqueConstraint("provider", "is_active", name="uq_oauth_config_provider_active"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    provider: str = Field(default="google", max_length=50, nullable=False)
    client_id: str = Field(max_length=255, nullable=False)
    client_secret_enc: str = Field(nullable=False)
    redirect_uri: str = Field(max_length=500, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_by_user_id: UUID = Field(foreign_key="users.id", nullable=False)


class OrganizationGoogleConnection(SQLModel, table=True):
    """Stores singleton Google connection for Organization."""

    __tablename__ = "organization_google_connections"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_singleton_key: str = Field(
        default="default", max_length=32, nullable=False, unique=True
    )
    status: str = Field(default="disconnected", max_length=32, nullable=False)
    email: str | None = Field(default=None, max_length=255)
    google_sub: str | None = Field(default=None, max_length=255)
    email_domain: str | None = Field(default=None, max_length=255)
    selected_calendar_id: str | None = Field(default=None, max_length=255)
    credential_format_version: int = Field(default=1, nullable=False)
    credential_key_version: int = Field(default=1, nullable=False)
    access_token_enc: str | None = Field(default=None)
    refresh_token_enc: str | None = Field(default=None)
    client_secret_enc: str | None = Field(default=None)
    oauth_state_hash: str | None = Field(default=None, max_length=128, index=True)
    oauth_state_nonce: str | None = Field(default=None, max_length=128)
    oauth_state_session_id: str | None = Field(default=None, max_length=128, index=True)
    oauth_state_expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    token_expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    connected_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CredentialSource(str, Enum):
    """Source of the AI provider credential."""

    ORG_API_KEY = "org_api_key"
    DEPLOYMENT_KEY = "deployment_key"


class OrganizationAIConfiguration(SQLModel, table=True):
    """Stores the singleton Organization AI provider configuration."""

    __tablename__ = "organization_ai_configurations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_singleton_key: str = Field(
        default="default", max_length=32, nullable=False, unique=True
    )
    provider: str = Field(max_length=100, nullable=False)
    base_url: str = Field(max_length=500, nullable=False)
    model: str = Field(max_length=255, nullable=False)
    api_key_enc: str = Field(nullable=False, default="")
    credential_source: str = Field(
        default=CredentialSource.ORG_API_KEY.value,
        max_length=32,
        nullable=False,
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_by_user_id: UUID | None = Field(foreign_key="users.id", nullable=True)

    # Data policy consent
    data_policy_accepted: bool = Field(default=False, nullable=False)
    data_policy_accepted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    data_policy_accepted_by_user_id: UUID | None = Field(foreign_key="users.id", nullable=True)
    data_policy_version: str | None = Field(default=None, nullable=True)

    # Independent capability toggles
    ai_automation_enabled: bool = Field(default=False, nullable=False)
    ai_assistant_enabled: bool = Field(default=False, nullable=False)
    ai_automation_consent: bool = Field(default=False, nullable=False)
    ai_assistant_consent: bool = Field(default=False, nullable=False)
    ai_policy_preset: str = Field(default="balanced", max_length=32, nullable=False)
    ai_policy_preset_version: str = Field(default="balanced-v1", max_length=64, nullable=False)

    # Organization-owned Job Application classification rollout

    classification_policy: str = Field(default="recall_first", max_length=50, nullable=False)
    classification_policy_version: str = Field(
        default="recall-first-v1", max_length=100, nullable=False
    )
    stable_classifier_version: str = Field(default="classifier-v1", max_length=100, nullable=False)
    candidate_classifier_version: str | None = Field(default=None, max_length=100)
    candidate_classification_policy: str | None = Field(default=None, max_length=50)
    candidate_classification_policy_version: str | None = Field(default=None, max_length=100)
    rollout_mode: str = Field(default="stable", max_length=20, nullable=False)
    canary_percentage: int = Field(default=0, ge=0, le=100, nullable=False)


class AuditActionType(str, Enum):
    """Enumeration of admin audit action types."""

    WHITELIST_ADD = "whitelist_add"
    WHITELIST_REMOVE = "whitelist_remove"
    OAUTH_UPDATE = "oauth_update"
    ROLE_CHANGE = "role_change"
    ORG_DOMAIN_UPDATE = "org_domain_update"
    ATTENDANCE_NETWORK_UPDATE = "attendance_network_update"
    ATTENDANCE_NETWORK_ADD = "attendance_network_add"
    ATTENDANCE_NETWORK_REMOVE = "attendance_network_remove"
    ASSISTANT_TOOL_CONFIG = "assistant_tool_config"
    ASSISTANT_CHAT = "assistant_chat"
    ATTENDANCE_CORRECTION = "attendance_correction"
    REQUEST_APPROVE = "request_approve"
    REQUEST_REJECT = "request_reject"
    PAYSLIP_CREATE = "payslip_create"
    PAYSLIP_UPDATE = "payslip_update"
    PAYSLIP_PUBLISH = "payslip_publish"
    PAYSLIP_DELETE = "payslip_delete"
    PAYSLIP_UNPUBLISH = "payslip_unpublish"
    ORG_GOOGLE_CONNECT = "org_google_connect"
    ORG_GOOGLE_RECONNECT = "org_google_reconnect"
    ORG_GOOGLE_SWITCH_ACCOUNT = "org_google_switch_account"
    ORG_GOOGLE_DISCONNECT = "org_google_disconnect"
    ORG_AI_CONFIG_UPDATE = "org_ai_config_update"
    ORG_AI_CONFIG_ROTATE = "org_ai_config_rotate"
    ORG_AI_CONFIG_REVOKE = "org_ai_config_revoke"
    ORG_AI_CONFIG_SOURCE = "org_ai_config_source"
    ORG_AI_CONSENT = "org_ai_consent"
    ORG_AI_TOGGLE_AUTOMATION = "org_ai_toggle_automation"
    ORG_AI_CLASSIFICATION_ROLLOUT = "org_ai_classification_rollout"
    ORG_AI_TOGGLE_ASSISTANT = "org_ai_toggle_assistant"

    OUTBOUND_EMAIL_CREATED = "outbound_email_created"
    OUTBOUND_EMAIL_SENT = "outbound_email_sent"
    OUTBOUND_EMAIL_FAILED = "outbound_email_failed"
    OUTBOUND_EMAIL_RETRY = "outbound_email_retry"


class AuditLog(SQLModel, table=True):
    """Records admin actions for audit trail purposes.

    Each entry captures who performed the action, what type of action
    it was, and action-specific details stored as JSON. Secret values
    are never stored in the details field.
    """

    __tablename__ = "audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    admin_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    admin_email: str = Field(max_length=255, nullable=False)
    action_type: AuditActionType = Field(
        sa_column=Column(EnumAsString(AuditActionType, 50), nullable=False),
    )
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class PasswordResetToken(SQLModel, table=True):
    """Represents a password reset token for a user."""
    __tablename__ = "password_reset_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    token_hash: str = Field(nullable=False, unique=True, index=True)
    # These three are ``timestamptz`` on the real table. Declared as a bare
    # ``datetime`` the model renders naive ``TIMESTAMP``, and autogenerate
    # would strip the zone off a column that decides when a password reset
    # token dies — too long is a security hole, too short breaks the feature.
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_by_ip: str | None = Field(default=None)
