import { API_BASE_URL } from "./client";
import { ApiError } from "./types";
import { isValidationErrorDetail, formatValidationErrors } from "./validation-errors";
import type { StaffRole, UserRole } from "@/lib/auth/roles";

/**
 * Admin API client — typed functions for all admin endpoints.
 *
 * Wired to NEXT_PUBLIC_API_URL via lib/api/client (Phase 0 TODO closed).
 * All requests send credentials: "include" for HttpOnly cookie auth.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type WhitelistEntryType = 'exact_email' | 'domain_pattern';

export interface WhitelistEntry {
  id: string | null;
  value: string;
  entry_type: WhitelistEntryType;
  added_by_email: string;
  created_at: string | null;
  source: 'database' | 'file';
  is_readonly: boolean;
}

export interface WhitelistListResponse {
  items: WhitelistEntry[];
  total: number;
}

export interface WhitelistEntryCreated {
  id: string;
  value: string;
  entry_type: WhitelistEntryType;
  created_at: string;
}

export interface OrganizationAIConfiguration {
  provider: string | null;
  base_url: string | null;
  model: string | null;
  api_key_masked: string | null;
  configured: boolean;
  updated_at: string | null;
  credential_source: string | null;
  deployment_key_available: boolean;
  data_policy_accepted: boolean;
  data_policy_accepted_at: string | null;
  data_policy_version: string | null;
  automation_enabled: boolean;
  automation_state: string;
  assistant_enabled: boolean;
  assistant_state: string;
  classification_policy: string;
  classification_policy_version: string;
  stable_classifier_version: string;
  candidate_classifier_version: string | null;
  candidate_classification_policy: string | null;
  candidate_classification_policy_version: string | null;
  rollout_mode: 'stable' | 'shadow' | 'canary' | 'full';
  canary_percentage: number;
  ai_automation_consent: boolean;
  ai_assistant_consent: boolean;
  ai_policy_preset: string;
  ai_policy_preset_version: string;
}

export interface DataPolicyResponse {
  version: string;
  items: Array<{ category: string; data_types: string; purpose: string; retention: string }>;
}

export interface AIConnectionTestResponse {
  success: boolean;
  message: string;
}

export interface OAuthConfig {
  client_id: string;
  client_secret_masked: string;
  redirect_uri: string;
  updated_at: string | null;
  source: string;
}

export interface GoogleWorkspaceConnection {
  status: 'disconnected' | 'connected' | 'reauthorization_required';
  email: string | null;
  has_secret: boolean;
  redirect_url: string | null;
  selected_calendar_id: string | null;
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  last_login: string;
}

export type AuditActionType =
  | 'whitelist_add'
  | 'whitelist_remove'
  | 'oauth_update'
  | 'role_change'
  | 'org_domain_update'
  | 'assistant_tool_config'
  | 'org_google_connect'
  | 'org_google_reconnect'
  | 'org_google_switch_account'
  | 'org_google_disconnect'
  | 'org_ai_config_update'
  | 'org_ai_config_rotate'
  | 'org_ai_config_revoke'
  | 'org_ai_config_source'
  | 'org_ai_classification_rollout';

export interface DomainListResponse {
  allowed_domains: string[];
}

export interface DomainRemoveResponse {
  removed: string;
  allowed_domains: string[];
}

export interface AuditLog {
  id: string;
  admin_email: string;
  action_type: AuditActionType;
  details: Record<string, unknown>;
  created_at: string;
}

export interface PaginatedAuditLogs {
  items: AuditLog[];
  total: number;
  page: number;
  page_size: number;
}

export type RuntimeHealthStatus = "healthy" | "degraded" | "unhealthy";

export interface RuntimeServiceStatus {
  name: string;
  status: RuntimeHealthStatus;
  latency_ms: number | null;
  detail: string | null;
}

export interface RuntimeHealthResponse {
  status: RuntimeHealthStatus;
  services: RuntimeServiceStatus[];
}

export interface AssistantToolConfig {
  tool_name: string;
  display_name: string;
  description: string;
  kind: string;
  enabled: boolean;
  updated_at: string | null;
}

export interface AssistantToolConfigListResponse {
  tools: AssistantToolConfig[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const BASE = `${API_BASE_URL}/api/system-admin`;
const AUTH_BASE = `${API_BASE_URL}/api/auth`;
/**
 * Internal fetch wrapper that always sends the HttpOnly auth cookie.
 */
async function adminFetch(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  try {
    return await fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init.headers || {}),
      },
      credentials: "include",
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "TIMEOUT", "Yêu cầu đã hết thời gian chờ");
    }
    throw new ApiError(0, "NETWORK_ERROR", "Lỗi kết nối mạng");
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const payload = await res.json().catch(() => null);

    // ── Pydantic validation error (422 detail array) → Vietnamese ──
    if (isValidationErrorDetail(payload?.detail)) {
      const { fieldErrors, summary } = formatValidationErrors(payload.detail);
      throw new ApiError(
        res.status,
        "VALIDATION_ERROR",
        summary,
        undefined,
        fieldErrors,
      );
    }

    const message =
      payload?.detail?.message ??
      (typeof payload?.detail === "string" ? payload.detail : undefined) ??
      `Request failed: ${res.status}`;
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

function jsonBody(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// ---------------------------------------------------------------------------
// Organization AI Configuration Endpoints
// ---------------------------------------------------------------------------

export async function getOrganizationAIConfiguration(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config`);
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function testOrganizationAIConfiguration(data: {
  provider: string;
  base_url: string;
  model: string;
  api_key: string;
}): Promise<AIConnectionTestResponse> {
  const res = await adminFetch(`${BASE}/organization/ai-config/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<AIConnectionTestResponse>(res);
}

export async function updateOrganizationAIConfiguration(data: {
  provider: string;
  base_url: string;
  model: string;
  api_key: string;
}): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function setCredentialSource(credentialSource: string): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/source`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credential_source: credentialSource }),
  });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function activateOrgApiKey(apiKey: string): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/activate-key`, jsonBody({ api_key: apiKey }));
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function revokeOrgApiKey(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/revoke-key`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function testDeploymentKey(): Promise<AIConnectionTestResponse> {
  const res = await adminFetch(`${BASE}/organization/ai-config/test-deployment-key`, { method: "POST" });
  return handleResponse<AIConnectionTestResponse>(res);
}

export async function updateProviderConfig(data: {
  provider: string;
  base_url: string;
  model: string;
}): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/provider`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<OrganizationAIConfiguration>(res);
}

// --- Data Policy & Consent ---

export async function getDataPolicy(): Promise<DataPolicyResponse> {
  const res = await adminFetch(`${BASE}/organization/ai-config/data-policy`);
  return handleResponse<DataPolicyResponse>(res);
}

export async function acceptDataPolicy(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/accept-data-policy`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function acceptAutomationConsent(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/automation/consent`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function acceptAssistantConsent(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/assistant/consent`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function setAIPolicyPreset(preset: 'conservative' | 'balanced' | 'high_recall'): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/policy-preset`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(preset),
  });
  return handleResponse<OrganizationAIConfiguration>(res);
}

// --- Capability toggles: AI Automation ---

export async function enableAutomation(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/automation/enable`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function disableAutomation(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/automation/disable`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

// --- Capability toggles: AI Assistant ---

export async function enableAssistant(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/assistant/enable`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function disableAssistant(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/assistant/disable`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

// --- Classification rollout ---

export interface ClassificationReleaseMetrics {
  job_application_recall: number;
  baseline_recall: number;
  needs_classification_rate: number;
  no_cv_recall: number | null;
  correction_rate: number;
  review_rate: number;
  p95_latency_ms: number;
  provider_error_rate: number;
  duplicate_count: number;
}

export interface ClassificationRolloutInput {
  mode: 'shadow' | 'canary' | 'full';
  business_policy: 'recall_first';
  policy_version: string;
  classifier_version: string;
  canary_percentage: number;
  release_metrics?: ClassificationReleaseMetrics;
}

export async function configureClassificationRollout(data: ClassificationRolloutInput): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/classification-rollout`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<OrganizationAIConfiguration>(res);
}

export async function rollbackClassificationRollout(): Promise<OrganizationAIConfiguration> {
  const res = await adminFetch(`${BASE}/organization/ai-config/classification-rollout/rollback`, { method: "POST" });
  return handleResponse<OrganizationAIConfiguration>(res);
}

// ---------------------------------------------------------------------------
// Whitelist Endpoints
// ---------------------------------------------------------------------------

export async function listWhitelist(): Promise<WhitelistListResponse> {
  const res = await adminFetch(`${BASE}/whitelist`);
  return handleResponse<WhitelistListResponse>(res);
}

export async function addWhitelistEntry(value: string): Promise<WhitelistEntryCreated> {
  const res = await adminFetch(`${BASE}/whitelist`, jsonBody({ value }));
  return handleResponse<WhitelistEntryCreated>(res);
}

export async function removeWhitelistEntry(id: string): Promise<void> {
  const res = await adminFetch(`${BASE}/whitelist/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    const error = await res.json().catch(() => ({ detail: { message: 'Delete failed' } }));
    const message = error?.detail?.message ?? error?.detail ?? 'Delete failed';
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
  }
}

// ---------------------------------------------------------------------------
// OAuth Config Endpoints
// ---------------------------------------------------------------------------

export async function getOAuthConfig(): Promise<OAuthConfig> {
  const res = await adminFetch(`${BASE}/oauth/config`);
  return handleResponse<OAuthConfig>(res);
}

export async function updateOAuthConfig(data: {
  client_id: string;
  client_secret: string;
  redirect_uri: string;
}): Promise<OAuthConfig> {
  const res = await adminFetch(`${BASE}/oauth/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<OAuthConfig>(res);
}

// ---------------------------------------------------------------------------
// Google Workspace connection (identity router) — wired to API_BASE_URL
// ---------------------------------------------------------------------------

export async function getGoogleWorkspaceConnection(): Promise<GoogleWorkspaceConnection> {
  const res = await adminFetch(`${AUTH_BASE}/organization-google-connection`);
  return handleResponse<GoogleWorkspaceConnection>(res);
}

export async function getGoogleWorkspaceAuthorizeUrl(): Promise<GoogleWorkspaceConnection> {
  const res = await adminFetch(`${AUTH_BASE}/organization-google-connection/reconnect`, { method: "POST" });
  return handleResponse<GoogleWorkspaceConnection>(res);
}

export async function saveGoogleWorkspaceConnection(): Promise<GoogleWorkspaceConnection> {
  return getGoogleWorkspaceAuthorizeUrl();
}

export async function completeGoogleWorkspaceCallback(code: string, state: string): Promise<GoogleWorkspaceConnection> {
  const res = await adminFetch(`${AUTH_BASE}/organization-google-connection/callback`, jsonBody({ code, state }));
  return handleResponse<GoogleWorkspaceConnection>(res);
}

export async function disconnectGoogleWorkspaceConnection(): Promise<GoogleWorkspaceConnection> {
  const res = await adminFetch(`${AUTH_BASE}/organization-google-connection`, { method: "DELETE" });
  return handleResponse<GoogleWorkspaceConnection>(res);
}

export interface CalendarEntry {
  id: string;
  summary: string;
  description: string | null;
  primary: boolean;
  access_role: string;
}

export interface CalendarListResponse {
  calendars: CalendarEntry[];
  selected_calendar_id: string | null;
}

export async function getGoogleWorkspaceCalendars(): Promise<CalendarListResponse> {
  const res = await adminFetch(`${AUTH_BASE}/organization-google-connection/calendars`);
  return handleResponse<CalendarListResponse>(res);
}

export async function selectGoogleWorkspaceCalendar(calendarId: string): Promise<void> {
  const res = await adminFetch(`${AUTH_BASE}/organization-google-connection/selected-calendar`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ calendar_id: calendarId }),
  });
  if (!res.ok) throw new Error('Failed to select calendar');
}

// ---------------------------------------------------------------------------
// User Management Endpoints
// ---------------------------------------------------------------------------

export interface StaffAccountCreateResponse {
  user: AdminUser;
  /** One-time password. Returned only here — never readable again. */
  temporary_password: string;
}

export async function listUsers(): Promise<AdminUser[]> {
  const res = await adminFetch(`${BASE}/users`);
  return handleResponse<AdminUser[]>(res);
}

/**
 * Provision an HR or System Admin account (`POST /api/system-admin/users`).
 *
 * First-run setup mints exactly one system admin and every other
 * account-creation route sits behind HR, so this is the only way a deployment
 * gets its first HR account. The response carries a temporary password the
 * system admin must hand over out of band; the account lands with
 * `must_change_password` set.
 */
export async function createStaffAccount(data: {
  email: string;
  name: string;
  role: StaffRole;
}): Promise<StaffAccountCreateResponse> {
  const res = await adminFetch(`${BASE}/users`, jsonBody(data));
  return handleResponse<StaffAccountCreateResponse>(res);
}

export async function updateUserRole(userId: string, role: UserRole): Promise<AdminUser> {
  const res = await adminFetch(`${BASE}/users/${userId}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  return handleResponse<AdminUser>(res);
}

// ---------------------------------------------------------------------------
// Audit Log Endpoints
// ---------------------------------------------------------------------------

export interface AuditLogQueryParams {
  page?: number;
  page_size?: number;
  action_type?: string;
  start_date?: string;
  end_date?: string;
}

export async function getAuditLogs(params?: AuditLogQueryParams): Promise<PaginatedAuditLogs> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.page_size) searchParams.set('page_size', String(params.page_size));
  if (params?.action_type) searchParams.set('action_type', params.action_type);
  if (params?.start_date) searchParams.set('start_date', params.start_date);
  if (params?.end_date) searchParams.set('end_date', params.end_date);

  const url = searchParams.toString() ? `${BASE}/audit-logs?${searchParams}` : `${BASE}/audit-logs`;
  const res = await adminFetch(url);
  return handleResponse<PaginatedAuditLogs>(res);
}

// ---------------------------------------------------------------------------
// Runtime Health Endpoint
// ---------------------------------------------------------------------------

export async function getRuntimeHealth(): Promise<RuntimeHealthResponse> {
  const res = await adminFetch(`${BASE}/runtime/health`);
  return handleResponse<RuntimeHealthResponse>(res);
}

// ---------------------------------------------------------------------------
// Organization Domain Endpoints
// ---------------------------------------------------------------------------

export async function listDomains(): Promise<DomainListResponse> {
  const res = await adminFetch(`${BASE}/organization/domains`);
  return handleResponse<DomainListResponse>(res);
}

export async function addDomains(domains: string[]): Promise<DomainListResponse> {
  const res = await adminFetch(`${BASE}/organization/domains`, jsonBody({ domains }));
  return handleResponse<DomainListResponse>(res);
}

export async function replaceDomains(domains: string[]): Promise<DomainListResponse> {
  const res = await adminFetch(`${BASE}/organization/domains`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ domains }),
  });
  return handleResponse<DomainListResponse>(res);
}

export async function removeDomain(domain: string): Promise<DomainRemoveResponse> {
  const res = await adminFetch(`${BASE}/organization/domains/${encodeURIComponent(domain)}`, { method: "DELETE" });
  return handleResponse<DomainRemoveResponse>(res);
}

// ---------------------------------------------------------------------------
// Assistant Tool Config Endpoints
// ---------------------------------------------------------------------------

export async function listAssistantTools(): Promise<AssistantToolConfigListResponse> {
  const res = await adminFetch(`${BASE}/assistant-tools`);
  return handleResponse<AssistantToolConfigListResponse>(res);
}

export async function updateAssistantTools(tools: Record<string, boolean>): Promise<AssistantToolConfigListResponse> {
  const res = await adminFetch(`${BASE}/assistant-tools`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tools }),
  });
  return handleResponse<AssistantToolConfigListResponse>(res);
}