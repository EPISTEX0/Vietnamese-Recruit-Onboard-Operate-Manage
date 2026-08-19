"""Pydantic request/response schemas for the Identity & Auth API.

Defines data transfer objects used by the auth router endpoints and
internal services for structured data validation and serialization.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from src.modules.identity.domain.entities import UserRole


class TokenPayload(BaseModel):
    sub: UUID
    email: str
    employee_id: UUID | None = None
    must_change_password: bool = False
    exp: datetime
    iat: datetime


class GoogleTokens(BaseModel):
    access_token: str
    refresh_token: str | None = None
    id_token: str
    expires_in: int
    scope: str


class GrantStatus(BaseModel):
    gmail_grant_valid: bool
    calendar_grant_valid: bool


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    avatar_url: str | None = None
    employee_id: UUID | None = None
    role: UserRole
    must_change_password: bool
    gmail_grant_valid: bool
    calendar_grant_valid: bool
    created_at: datetime
    last_login: datetime


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class FirstRunSetupRequest(BaseModel):
    organization_name: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=12, max_length=255)
    password_confirmation: str = Field(..., min_length=12, max_length=255)

    @field_validator("organization_name", "name", mode="before")
    @classmethod
    def require_identity(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field is required")
        return value.strip()

    @model_validator(mode="after")
    def passwords_match(self) -> "FirstRunSetupRequest":
        if self.password != self.password_confirmation:
            raise ValueError("Passwords do not match")
        self.organization_name = self.organization_name.strip()
        self.name = self.name.strip()
        if not self.organization_name or not self.name:
            raise ValueError("Organization name and name are required")
        self.email = self.email.lower()
        return self


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=12, max_length=255)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetTokenInfoResponse(BaseModel):
    valid: bool


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=12, max_length=255)


class ResetPasswordResponse(BaseModel):
    message: str


class AuthSessionResponse(BaseModel):
    user: UserResponse
    must_change_password: bool
    setup_complete: bool


class SetupStatusResponse(BaseModel):
    setup_complete: bool


class EmployeeAccountStatusResponse(BaseModel):
    exists: bool
    user_id: UUID | None = None
    email: str | None = None
    role: UserRole | None = None
    must_change_password: bool | None = None


class EmployeeAccountCreateResponse(BaseModel):
    user: EmployeeAccountStatusResponse
    temporary_password: str


class GrantStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    gmail_grant_valid: bool
    calendar_grant_valid: bool


class GoogleWorkspaceConnectionResponse(BaseModel):
    status: Literal[
        "disconnected",
        "connected",
        "degraded",
        "reauthorization_required",
    ]
    email: str | None = None
    has_secret: bool = False
    redirect_url: str | None = None
    selected_calendar_id: str | None = None


class GoogleWorkspaceCallbackRequest(BaseModel):
    code: str
    state: str


class OAuthConfigResponse(BaseModel):
    client_id: str
    client_secret_masked: str
    redirect_uri: str
    updated_at: datetime | None
    source: str


class OAuthConfigUpdateRequest(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str
