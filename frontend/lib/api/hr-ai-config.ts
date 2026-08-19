/**
 * HR API client for `/api/hr/organization/ai-config/*` — data policy consent,
 * capability consent, capability toggles, and the policy preset (#420).
 *
 * Deliberately separate from `lib/api/admin.ts`: that file's own docstring
 * documents the last time an HR surface lived under `admin` (the Google
 * connection) and 38 HR endpoints ended up gated by system admin as a
 * result. Nothing here may point at `/api/system-admin/*`.
 *
 * System Admin still owns provider credentials (`provider`, `base_url`,
 * `model`, API key) — `getProviderStatus` below is the only signal this file
 * exposes about that, and it is a bare boolean.
 */

import { apiFetch } from "./client";

const BASE = "/api/hr/organization/ai-config";

/**
 * Mirrors backend `AICapabilityState` (`organization_ai_config_service.py`).
 * Duplicated from `lib/api/admin.ts`'s copy of the same union rather than
 * imported from it — this file must not depend on the system-admin-scoped
 * module. Update both copies together.
 */
export type AICapabilityState = 'not_configured' | 'disabled' | 'ready' | 'unavailable';

/**
 * Narrow, credential-free view of Organization AI configuration for HR.
 * Excludes provider name, base_url, model, and API key entirely — those stay
 * on System Admin's `OrganizationAIConfiguration` (`lib/api/admin.ts`).
 */
export interface HRAIConfiguration {
  provider_configured: boolean;
  updated_at: string | null;
  data_policy_accepted: boolean;
  data_policy_accepted_at: string | null;
  data_policy_version: string | null;
  automation_enabled: boolean;
  automation_state: AICapabilityState;
  assistant_enabled: boolean;
  assistant_state: AICapabilityState;
  ai_automation_consent: boolean;
  ai_assistant_consent: boolean;
  ai_policy_preset: string;
  ai_policy_preset_version: string;
}

/** Whether System Admin has connected a usable AI provider — nothing more. */
export interface HRAIProviderStatus {
  connected: boolean;
}

export interface DataPolicyResponse {
  version: string;
  items: Array<{ category: string; data_types: string; purpose: string; retention: string }>;
}

// --- Current configuration (read-only, credential-free) ---

export function getConfiguration(): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(BASE);
}

// --- Provider status (read-only, credential-free) ---

export function getProviderStatus(): Promise<HRAIProviderStatus> {
  return apiFetch<HRAIProviderStatus>(`${BASE}/provider-status`);
}

// --- Data policy & consent ---

export function getDataPolicy(): Promise<DataPolicyResponse> {
  return apiFetch<DataPolicyResponse>(`${BASE}/data-policy`);
}

export function acceptDataPolicy(): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(`${BASE}/accept-data-policy`, { method: "POST" });
}

export function acceptAutomationConsent(): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(`${BASE}/automation/consent`, { method: "POST" });
}

export function acceptAssistantConsent(): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(`${BASE}/assistant/consent`, { method: "POST" });
}

export function setAIPolicyPreset(preset: 'conservative' | 'balanced' | 'high_recall'): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(`${BASE}/policy-preset`, {
    method: "PUT",
    body: JSON.stringify(preset),
  });
}

// --- Capability toggles: AI Automation ---

export function enableAutomation(): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(`${BASE}/automation/enable`, { method: "POST" });
}

export function disableAutomation(): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(`${BASE}/automation/disable`, { method: "POST" });
}

// --- Capability toggles: AI Assistant ---

export function enableAssistant(): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(`${BASE}/assistant/enable`, { method: "POST" });
}

export function disableAssistant(): Promise<HRAIConfiguration> {
  return apiFetch<HRAIConfiguration>(`${BASE}/assistant/disable`, { method: "POST" });
}
