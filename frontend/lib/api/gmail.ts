/**
 * API client for the Gmail + Organization Google Connection modules.
 *
 * Wired to NEXT_PUBLIC_API_URL via lib/api/client (Phase 0 TODO closed).
 * All requests send credentials: "include" for HttpOnly cookie auth.
 */

import { API_BASE_URL } from "./client";
import type {
  CapabilityHealth,
  CapabilityHealthState,
  OrganizationGoogleConnectionResponse,
  SyncResponse,
  MessageBodyResponse,
  SendEmailRequest,
  SendEmailResponse,
  AttachmentsResponse,
  EmailMessage,
} from "./types";
import { ApiError } from "./types";
import type { ProcessingStatus } from "./recruitment";

const BASE = `${API_BASE_URL}/api/gmail`;
const AUTH_BASE = `${API_BASE_URL}/api/auth`;
const OUTBOUND_BASE = `${API_BASE_URL}/api/outbound-emails`;

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({
      detail: res.statusText,
    }));
    const message =
      body?.detail || body?.error?.message || `Request failed: ${res.status}`;
    const errorCode = body?.error_code || "UNKNOWN_ERROR";
    throw new ApiError(res.status, errorCode, message, body);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

function authInit(init?: RequestInit): RequestInit {
  return { ...init, credentials: "include" };
}

/** Wrapper cho fetch: bắt lỗi mạng và timeout, ném ApiError với mã tiếng Việt. */
async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "TIMEOUT", "Yêu cầu đã hết thời gian chờ");
    }
    throw new ApiError(0, "NETWORK_ERROR", "Lỗi kết nối mạng");
  }
}

// ---------------------------------------------------------------------------
// Organization Google Connection (identity router)
// ---------------------------------------------------------------------------

/**
 * Get the current Google Workspace connection status for the organization.
 */
export async function getConnectionStatus(): Promise<OrganizationGoogleConnectionResponse> {
  const res = await safeFetch(`${AUTH_BASE}/organization-google-connection`, authInit());
  return handleResponse<OrganizationGoogleConnectionResponse>(res);
}

/**
 * Get the Google OAuth authorize URL to start the connection flow.
 * Redirect the user to the returned redirect_url.
 */
export async function getAuthorizeUrl(): Promise<OrganizationGoogleConnectionResponse> {
  const res = await safeFetch(
    `${AUTH_BASE}/organization-google-connection/authorize-url`,
    authInit(),
  );
  return handleResponse<OrganizationGoogleConnectionResponse>(res);
}

/**
 * Reconnect (re-authorize) the Google Workspace connection.
 */
export async function reconnectConnection(): Promise<OrganizationGoogleConnectionResponse> {
  const res = await safeFetch(
    `${AUTH_BASE}/organization-google-connection/reconnect`,
    authInit({ method: "POST" }),
  );
  return handleResponse<OrganizationGoogleConnectionResponse>(res);
}

/**
 * Disconnect the organization Google Workspace connection.
 */
export async function disconnectConnection(): Promise<OrganizationGoogleConnectionResponse> {
  const res = await safeFetch(`${AUTH_BASE}/organization-google-connection`, authInit({ method: "DELETE" }));
  return handleResponse<OrganizationGoogleConnectionResponse>(res);
}

// ---------------------------------------------------------------------------
// Calendars + selected calendar (identity router)
// ---------------------------------------------------------------------------

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

export async function getCalendars(): Promise<CalendarListResponse> {
  const res = await safeFetch(`${AUTH_BASE}/organization-google-connection/calendars`, authInit());
  return handleResponse<CalendarListResponse>(res);
}

export async function selectCalendar(calendarId: string): Promise<void> {
  const res = await safeFetch(`${AUTH_BASE}/organization-google-connection/selected-calendar`, authInit({
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ calendar_id: calendarId }),
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body?.error_code || "UNKNOWN_ERROR", body?.detail || "Không thể chọn calendar", body);
  }
}

// ---------------------------------------------------------------------------
// Capability Health
// ---------------------------------------------------------------------------

export const CAPABILITIES = [
  { capability: "gmail_ingestion", label: "Gmail ingestion" },
  { capability: "gmail_sending", label: "Gmail sending" },
  { capability: "calendar_sync", label: "Calendar sync" },
] as const;

export function getCapabilityLabel(capability: string): string {
  const entry = CAPABILITIES.find((c) => c.capability === capability);
  return entry?.label ?? capability;
}

export function getCapabilityHealth(isConnected: boolean): CapabilityHealth[] {
  const baseHealth: CapabilityHealthState = isConnected ? "unknown" : "unavailable";
  return CAPABILITIES.map((c) => ({
    capability: c.capability,
    health: baseHealth,
    label: c.label,
    description: isConnected
      ? "Không thể xác thực trạng thái dịch vụ"
      : "Chưa kết nối Gmail",
  }));
}

// ---------------------------------------------------------------------------
// Gmail operations
// ---------------------------------------------------------------------------

export interface MessagesListResponse {
  messages: EmailMessage[];
  total: number;
}

/** List synced Gmail messages (optionally filtered by category, with pagination). */
export async function listMessages(
  category?: string,
  limit?: number,
  offset?: number,
): Promise<MessagesListResponse> {
  const params = new URLSearchParams();
  if (category) params.set('category', category);
  if (limit !== undefined) params.set('limit', String(limit));
  if (offset !== undefined) params.set('offset', String(offset));
  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await safeFetch(`${BASE}/messages${qs}`, authInit());
  return handleResponse<MessagesListResponse>(res);
}

export async function syncEmails(): Promise<SyncResponse> {
  const res = await safeFetch(`${BASE}/sync`, authInit({ method: "POST" }));
  return handleResponse<SyncResponse>(res);
}

export async function getMessageBody(messageId: string): Promise<MessageBodyResponse> {
  const res = await safeFetch(`${BASE}/messages/${messageId}/body`, authInit());
  return handleResponse<MessageBodyResponse>(res);
}

export async function removeLabel(messageId: string, labelName: string): Promise<void> {
  const res = await safeFetch(`${BASE}/messages/${messageId}/labels/remove`, authInit({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label_name: labelName }),
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body?.error_code || "UNKNOWN_ERROR", body?.detail || "Xóa nhãn thất bại", body);
  }
}

export async function sendEmail(data: SendEmailRequest): Promise<SendEmailResponse> {
  const res = await safeFetch(`${BASE}/send`, authInit({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }));
  return handleResponse<SendEmailResponse>(res);
}

export async function getAttachments(messageId: string): Promise<AttachmentsResponse> {
  const res = await safeFetch(`${BASE}/messages/${messageId}/attachments`, authInit({ method: "POST" }));
  return handleResponse<AttachmentsResponse>(res);
}

export interface ClassifyResponse {
  classified_count: number;
  cv_processed_count?: number;
  total: number;
  remaining: number;
  message: string;
  results: Array<{ subject: string; category: string | null }>;
}

/** Classify a small batch of emails (default 5). */
export async function classifyBatch(limit: number = 5): Promise<ClassifyResponse> {
  const res = await safeFetch(`${BASE}/classify?limit=${limit}`, authInit({ method: "POST" }));
  return handleResponse<ClassifyResponse>(res);
}

export interface ReviewEmailsResponse {
  messages: EmailMessage[];
  total: number;
}

/** List emails that need human review (needs_review status). */
export async function listEmailsNeedingReview(limit: number = 50, offset: number = 0): Promise<ReviewEmailsResponse> {
  const res = await safeFetch(`${BASE}/review/emails?limit=${limit}&offset=${offset}`, authInit());
  return handleResponse<ReviewEmailsResponse>(res);
}

/** Reclassify a needs_review email. */
export async function reclassifyEmail(messageId: string): Promise<EmailMessage> {
  const res = await safeFetch(`${BASE}/review/emails/${messageId}/reclassify`, authInit({ method: "POST" }));
  return handleResponse<EmailMessage>(res);
}

export interface ProcessAttachmentsResponse {
  processed_count: number;
  cv_documents?: Array<{
    id: string;
    original_filename: string;
    processing_status: ProcessingStatus;
    confidence_score: number | null;
  }>;
  message?: string;
}

/** Fetch attachments and trigger CV processing pipeline for an email. */
export async function processAttachments(messageId: string): Promise<ProcessAttachmentsResponse> {
  const res = await safeFetch(`${BASE}/messages/${encodeURIComponent(messageId)}/process-attachments`, authInit({ method: "POST" }));
  return handleResponse<ProcessAttachmentsResponse>(res);
}

// ---------------------------------------------------------------------------
// Historical import API
// ---------------------------------------------------------------------------

export interface ImportPreviewResponse {
  days: number;
  estimated_count: number;
  already_imported_count: number;
  query_window_start: string;
  query_window_end: string;
}

export interface ImportStartResponse {
  job_id: string;
  // Backend constructs this literally (`gmail/api/router.py:240`); the
  // docstring calls it "always 'running'" (`gmail/api/schemas.py:246`).
  status: "running";
  days: number;
  message: string;
}

// Backend docstring: "One of 'running', 'completed', 'cancelled', 'failed',
// 'none'." (`gmail/api/schemas.py:262`, mirrored in
// `gmail/application/import_service.py:86`).
export type ImportJobStatus = "running" | "completed" | "cancelled" | "failed" | "none";

export interface ImportStatusResponse {
  job_id: string | null;
  status: ImportJobStatus;
  days: number | null;
  total_count: number;
  processed_count: number;
  job_application_count: number;
  errors: number;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

// Backend docstring: "Result status ('cancelled' or 'no_active_job')."
// (`gmail/api/schemas.py:289`).
export type ImportCancelStatus = "cancelled" | "no_active_job";

export interface ImportCancelResponse {
  status: ImportCancelStatus;
  message: string;
}

/** Preview the number of importable emails in a time window. */
export async function previewImport(days: number): Promise<ImportPreviewResponse> {
  const res = await safeFetch(`${BASE}/import/preview`, authInit({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ days }),
  }));
  return handleResponse<ImportPreviewResponse>(res);
}

/** Start a historical email import job. */
export async function startImport(days: number): Promise<ImportStartResponse> {
  const res = await safeFetch(`${BASE}/import/start`, authInit({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ days }),
  }));
  return handleResponse<ImportStartResponse>(res);
}

/** Get the status of the current or last historical import job. */
export async function getImportStatus(): Promise<ImportStatusResponse> {
  const res = await safeFetch(`${BASE}/import/status`, authInit());
  return handleResponse<ImportStatusResponse>(res);
}

/** Cancel a running historical import job. */
export async function cancelImport(): Promise<ImportCancelResponse> {
  const res = await safeFetch(`${BASE}/import/cancel`, authInit({ method: "POST" }));
  return handleResponse<ImportCancelResponse>(res);
}

// ---------------------------------------------------------------------------
// Outbound emails (vòng đời pending → sending → sent/failed)
// ---------------------------------------------------------------------------

export type OutboundEmailStatus = "pending" | "sending" | "sent" | "failed";

export interface OutboundEmail {
  id: string;
  to: string[];
  cc: string[] | null;
  subject: string;
  body_text: string | null;
  body_html: string | null;
  status: OutboundEmailStatus;
  error_message: string | null;
  created_at: string;
  sent_at: string | null;
  reply_to_message_id: string | null;
}

export interface OutboundEmailListResponse {
  items: OutboundEmail[];
  total: number;
}

/** List outbound emails (drafts pending send + sent history). */
export async function listOutboundEmails(): Promise<OutboundEmailListResponse> {
  const res = await safeFetch(`${OUTBOUND_BASE}`, authInit());
  return handleResponse<OutboundEmailListResponse>(res);
}

/** Get a single outbound email by id. */
export async function getOutboundEmail(id: string): Promise<OutboundEmail> {
  const res = await safeFetch(`${OUTBOUND_BASE}/${id}`, authInit());
  return handleResponse<OutboundEmail>(res);
}

/** Create an outbound email in pending status (HR reviews before actual send). */
export async function createOutboundEmail(data: SendEmailRequest): Promise<OutboundEmail> {
  const res = await safeFetch(`${OUTBOUND_BASE}`, authInit({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }));
  return handleResponse<OutboundEmail>(res);
}

/**
 * Confirm (actually send) a pending outbound email.
 * The real send only happens after this HR confirmation (human-in-the-loop).
 */
export async function sendOutboundEmail(id: string): Promise<OutboundEmail> {
  const res = await safeFetch(`${OUTBOUND_BASE}/${id}/send`, authInit({ method: "POST" }));
  return handleResponse<OutboundEmail>(res);
}

/** Delete a draft outbound email (only allowed while pending). */
export async function deleteOutboundEmail(id: string): Promise<void> {
  const res = await safeFetch(`${OUTBOUND_BASE}/${id}`, authInit({ method: "DELETE" }));
  if (!res.ok && res.status !== 204) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body?.error_code || "UNKNOWN_ERROR", body?.detail || "Xóa email thất bại", body);
  }
}