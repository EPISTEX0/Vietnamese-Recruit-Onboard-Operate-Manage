"""Pydantic request/response schemas for the Admin API endpoints.

Defines data transfer objects for user management, role changes,
and audit log retrieval under /api/system-admin/*.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.modules.gmail.application.classification_rollout import BusinessPolicy, RolloutMode
from src.modules.identity.domain.entities import AuditActionType, UserRole


class OrganizationAIConfigurationResponse(BaseModel):
    provider: str | None
    base_url: str | None
    model: str | None
    api_key_masked: str | None
    configured: bool
    updated_at: datetime | None
    credential_source: str | None = None
    deployment_key_available: bool = False
    data_policy_accepted: bool = False
    data_policy_accepted_at: datetime | None = None
    data_policy_version: str | None = None
    automation_enabled: bool = False
    automation_state: str = "not_configured"
    assistant_enabled: bool = False
    assistant_state: str = "not_configured"
    classification_policy: str = "recall_first"
    classification_policy_version: str = "recall-first-v1"
    stable_classifier_version: str = "classifier-v1"
    candidate_classifier_version: str | None = None
    candidate_classification_policy: str | None = None
    candidate_classification_policy_version: str | None = None
    rollout_mode: str = "stable"
    canary_percentage: int = 0
    ai_automation_consent: bool = False
    ai_assistant_consent: bool = False
    ai_policy_preset: str = "balanced"
    ai_policy_preset_version: str = "balanced-v1"


class OrganizationAIConfigurationRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    model: str = Field(..., min_length=1, max_length=255)
    api_key: str = Field(..., min_length=1, max_length=4096)


class ActivateOrgApiKeyRequest(BaseModel):
    """Request to activate an Organization API key."""

    api_key: str = Field(..., min_length=1, max_length=4096)


class SetCredentialSourceRequest(BaseModel):
    """Request to change the credential source."""

    credential_source: str = Field(..., min_length=1, max_length=32)


class UpdateProviderConfigRequest(BaseModel):
    """Request to update provider/model without changing API key."""

    provider: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    model: str = Field(..., min_length=1, max_length=255)


class AIConnectionTestResponse(BaseModel):
    success: bool
    message: str


class ClassificationReleaseMetricsRequest(BaseModel):
    job_application_recall: float = Field(..., ge=0, le=1)
    baseline_recall: float = Field(..., ge=0, le=1)
    needs_classification_rate: float = Field(..., ge=0, le=1)
    no_cv_recall: float | None = Field(default=None, ge=0, le=1)
    correction_rate: float = Field(..., ge=0, le=1)
    review_rate: float = Field(..., ge=0, le=1)
    p95_latency_ms: int = Field(..., ge=0)
    provider_error_rate: float = Field(..., ge=0, le=1)
    duplicate_count: int = Field(..., ge=0)
    retry_failure_rate: float = Field(default=0, ge=0, le=1)


class ClassificationRolloutRequest(BaseModel):
    """Organization business policy selection and rollout target."""

    mode: RolloutMode
    business_policy: BusinessPolicy
    policy_version: str = Field(..., min_length=1, max_length=100)
    classifier_version: str = Field(..., min_length=1, max_length=100)
    canary_percentage: int = Field(default=0, ge=0, le=100)
    release_metrics: ClassificationReleaseMetricsRequest | None = None


class ClassificationRolloutTelemetryResponse(BaseModel):
    sample_size: int
    job_application_recall_proxy: float
    stable_recall_proxy: float
    no_cv_recall_proxy: float | None
    correction_rate: float
    review_rate: float
    needs_classification_rate: float
    p95_latency_ms: int
    provider_error_rate: float
    duplicate_count: int
    retry_failure_rate: float
    total_prompt_tokens: int
    total_completion_tokens: int
    estimated_cost_usd: float


class DataPolicyResponse(BaseModel):
    """Response schema for the AI data policy."""

    version: str
    items: list[dict[str, str]]


class RoleUpdateRequest(BaseModel):
    """Request schema for PATCH /api/system-admin/users/{id}/role.

    Attributes:
        role: The new role to assign. One of 'system_admin', 'hr', or 'user'.
    """

    role: UserRole


class StaffAccountCreateRequest(BaseModel):
    """Request schema for POST /api/system-admin/users.

    Attributes:
        email: The new account's email address.
        name: The new account's display name.
        role: The staff role to assign. Only 'hr' and 'system_admin' are
            accepted -- self-service 'user' accounts are provisioned by HR
            against an Employee record.
    """

    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    role: Literal[UserRole.HR, UserRole.SYSTEM_ADMIN]


class AdminUserResponse(BaseModel):
    """Response schema for user entries in the admin user list.

    Attributes:
        id: The user's unique identifier.
        email: The user's email address.
        name: The user's display name.
        avatar_url: URL to the user's avatar image, if available.
        role: The user's current role.
        is_active: Whether the user account is active.
        created_at: When the user account was created.
        last_login: When the user last authenticated.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    avatar_url: str | None = None
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login: datetime


class AuditLogResponse(BaseModel):
    """Response schema for individual audit log entries.

    Attributes:
        id: The audit log entry's unique identifier.
        admin_email: Email of the admin who performed the action.
        action_type: The type of admin action recorded.
        details: Action-specific details (sensitive values masked).
        created_at: When the action was performed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    admin_email: str
    action_type: AuditActionType
    details: dict[str, Any]
    created_at: datetime


class PaginatedAuditLogsResponse(BaseModel):
    """Response schema for paginated audit log queries.

    Attributes:
        items: The list of audit log entries for the current page.
        total: The total number of entries matching the query filters.
        page: The current page number (1-indexed).
        page_size: The number of entries per page.
    """

    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int


# --- Admin Organization Domain Schemas ---


class DomainListResponse(BaseModel):
    """Response schema for listing allowed Organization domains.

    Attributes:
        allowed_domains: The list of email domains permitted for login.
    """

    model_config = ConfigDict(from_attributes=True)

    allowed_domains: list[str]


class DomainAddRequest(BaseModel):
    """Request schema for adding domains to the allowed list.

    Attributes:
        domains: One or more bare domain strings (e.g. ``company.vn``).
    """

    domains: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="List of domain strings to add (e.g. company.vn)",
    )


class DomainReplaceRequest(BaseModel):
    """Request schema for replacing the entire allowed domain list.

    Attributes:
        domains: The new complete list of domain strings.
    """

    domains: list[str] = Field(
        ...,
        max_length=50,
        description="Complete list of domain strings to set",
    )


class DomainRemoveResponse(BaseModel):
    """Response schema after removing a domain.

    Attributes:
        removed: The domain that was removed.
        allowed_domains: The updated list of allowed domains.
    """

    removed: str
    allowed_domains: list[str]


class AssistantToolConfigResponse(BaseModel):
    """Response schema for a single assistant tool config entry.

    Attributes:
        tool_name: The tool's machine-readable name.
        description: Human-readable description for the LLM.
        kind: Tool kind (read or draft).
        enabled: Whether the tool is currently active.
        updated_at: When the tool config was last changed.
    """

    tool_name: str
    display_name: str
    description: str
    kind: str
    enabled: bool
    updated_at: datetime | None = None


class AssistantToolConfigListResponse(BaseModel):
    """Response schema for the list of all assistant tool configs.

    Attributes:
        tools: All tools with their enabled status.
    """

    tools: list[AssistantToolConfigResponse]


class AssistantToolConfigUpdateRequest(BaseModel):
    """Request schema for PUT /api/admin/assistant-tools.

    Attributes:
        tools: Mapping of tool_name to enabled status.
    """

    tools: dict[str, bool]


class StaffAccountCreateResponse(BaseModel):
    """Response schema for POST /api/system-admin/users.

    Attributes:
        user: The created account.
        temporary_password: One-time password; the account must change it at
            first login. Returned only here and never persisted in clear text.
    """

    user: AdminUserResponse
    temporary_password: str
