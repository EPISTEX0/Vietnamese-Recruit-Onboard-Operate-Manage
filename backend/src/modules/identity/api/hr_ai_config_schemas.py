"""Pydantic response schemas for HR-facing AI configuration endpoints.

Deliberately separate from ``admin_schemas.OrganizationAIConfigurationResponse``:
that model carries ``provider``, ``base_url``, ``model``, ``api_key_masked``,
and ``credential_source`` -- credential detail HR must never see (ADR-0009).
HR only sees the business state it owns: data policy consent, capability
toggles, and the policy preset, plus a binary provider-connected signal for
the fields it deliberately cannot see.
"""

from datetime import datetime

from pydantic import BaseModel


class HRAIConfigurationResponse(BaseModel):
    """Narrow, credential-free view of Organization AI configuration for HR.

    Excludes provider name, base_url, model, API key, credential_source, and
    classification-rollout fields entirely -- those stay on System Admin's
    ``OrganizationAIConfigurationResponse``.
    """

    provider_configured: bool
    updated_at: datetime | None
    data_policy_accepted: bool
    data_policy_accepted_at: datetime | None
    data_policy_version: str | None
    automation_enabled: bool
    automation_state: str
    assistant_enabled: bool
    assistant_state: str
    ai_automation_consent: bool
    ai_assistant_consent: bool
    ai_policy_preset: str
    ai_policy_preset_version: str


class HRAIProviderStatusResponse(BaseModel):
    """Read-only signal of whether System Admin has connected a usable provider.

    Deliberately just a boolean: no provider name, base_url, model, or any
    part of the API key. HR sees *whether* AI is usable, never *how* it is
    wired -- that stays System Admin's exclusive view.
    """

    connected: bool
