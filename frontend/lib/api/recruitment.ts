import { ApiError } from "./types";
import { API_BASE_URL } from "./client";
import type { OutboundEmailStatus } from "./gmail";


// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BASE = `${API_BASE_URL}/api/recruitment`;
const TIMEOUT_MS = 30_000;

// ---------------------------------------------------------------------------
// Enums / Type Aliases
// ---------------------------------------------------------------------------

export type CandidateStatus =
  | "new"
  | "reviewing"
  | "interview_scheduled"
  | "accepted"
  | "rejected"
  | "archived";

export type ProcessingStatus =
  | "pending"
  | "ocr_processing"
  | "llm_parsing"
  | "completed"
  | "needs_review"
  | "failed"
  | "skipped"
  | "dismissed"
  | "upload_failed"
  | "permanently_failed"
  | "ai_unavailable";

// ---------------------------------------------------------------------------
// Response Interfaces
// ---------------------------------------------------------------------------

export interface CandidateListItem {
  id: string;
  name: string;
  email: string;
  phone: string;
  skills: string[];
  status: CandidateStatus;
  confidence_score: number;
  created_at: string;
  has_cv: boolean;
  job_opening_id: string | null;
  job_opening_title: string;
}

export interface CandidateListResponse {
  candidates: CandidateListItem[];
  total_count: number;
  page: number;
  page_size: number;
}

export interface ExperienceItem {
  company: string;
  role: string;
  duration: string;
}

export interface EducationItem {
  institution: string;
  degree: string;
  year: string;
}

export interface CVDocument {
  id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  uploaded_at: string;
  presigned_url: string | null;
  processing_status: ProcessingStatus;
}

export interface CandidateDetail {
  id: string;
  name: string;
  email: string;
  phone: string;
  skills: string[];
  experience: ExperienceItem[];
  education: EducationItem[];
  summary: string;
  status: CandidateStatus;
  confidence_score: number;
  source_email_message_id: string | null;
  rejection_reason: string | null;
  rejected_at: string | null;
  accepted_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  job_opening_id: string | null;
  job_opening_title: string;
  cv_documents: CVDocument[];
  // Interview calendar fields (ADR-0008). Optional/nullable so the UI degrades
  // gracefully when the backend response does not yet include them.
  interview_start_at?: string | null; // ISO 8601 datetime
  interview_timezone?: string | null; // IANA timezone (e.g. "Asia/Ho_Chi_Minh")
  calendar_event_id?: string | null;
  meet_link?: string | null;
      interviews?: InterviewResponse[];
}

// ---------------------------------------------------------------------------
// CV Review Types
// ---------------------------------------------------------------------------

export interface ParsedCVData {
  name?: string;
  email?: string;
  phone?: string;
  skills?: string[];
  experience?: ExperienceItem[];
  education?: EducationItem[];
  summary?: string;
}

export interface ValidationError {
  field: string;
  message: string;
}

export interface CVReviewItem {
  id: string;
  candidate_id: string | null;
  gmail_message_id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  ocr_output: string | null;
  parsed_cv_data: ParsedCVData | null;
  confidence_score: number | null;
  processing_status: ProcessingStatus;
  processing_error: string | null;
  validation_errors: ValidationError[] | null;
  retry_count: number;
  uploaded_at: string;
  created_at: string;
}

export interface CVReviewListResponse {
  items: CVReviewItem[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// Metrics Types
// ---------------------------------------------------------------------------

export interface MetricsResponse {
  average_processing_time_ms: number;
  success_rate: number; // 0.0–1.0
  failure_rate: number; // 0.0–1.0
  queue_depth: number;
}

// ---------------------------------------------------------------------------
// Request Types
// ---------------------------------------------------------------------------

export interface CandidateListParams {
  page?: number;
  page_size?: number;
  search?: string;
  status?: CandidateStatus[];
  from_date?: string; // YYYY-MM-DD
  to_date?: string; // YYYY-MM-DD
  min_confidence?: number; // 0.0–1.0
  skills?: string; // comma-separated
}


  // ---------------------------------------------------------------------------
// Interview Types (GH #148 / #154 / #155)
// ---------------------------------------------------------------------------

export interface InterviewParticipant {
  id: string;
  interview_id: string;
  type: "candidate" | "employee" | "external";
  email: string;
  name: string | null;
  employee_id: string | null;
}

// `Interview.status` (backend `entities.py:361`) is a raw `str` column with
// no backend StrEnum. Closed set confirmed by grepping every literal
// assignment site (#363): `interview_scheduler_service.py:525,693,720,881,1842`,
// `calendar_sync_service.py:251`.
export type InterviewStatus = "scheduled" | "completed" | "cancelled";

export interface InterviewResponse {
  id: string;
  candidate_id: string;
  status: InterviewStatus;
  round_name: string;
  start_at: string;
  end_at: string;
  timezone: string;
  calendar_event_id: string | null;
  needs_relink: boolean;
  participants: InterviewParticipant[];
}

export interface CreateInterviewRequest {
  round_name: string;
  start: string;
  end: string;
  timezone: string;
  mode: "google_meet" | "in_person" | "custom_link";
  meeting_link?: string | null;
  interviewer_ids: string[];
  external_participant_emails?: string[];
  notes?: string | null;
}

export type ConflictStatus = "unresolved" | "resolved_keep_google" | "resolved_overwrite_vroom";

export interface CalendarConflict {
  id: string;
  interview_id: string;
  candidate_id: string;
  calendar_event_id: string;
  local_snapshot: Record<string, unknown>;
  remote_snapshot: Record<string, unknown>;
  conflict_details: Record<string, unknown>;
  status: ConflictStatus;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CalendarConflictListResponse {
  conflicts: CalendarConflict[];
  total_count: number;
}

export interface ResolveConflictRequest {
  choice: "keep_google" | "overwrite_vroom";
}

export interface SendEmailRequest {
  subject: string;
  body_html: string;
  template_name?: string;
}

export interface RejectRequest {
  reason: string;
}

export interface ParsedCVInput {
  name: string;
  email: string;
  phone: string;
  skills: string[];
  experience: ExperienceItem[];
  education: EducationItem[];
  summary: string;
}

export interface CVPresignedUrlResponse {
  presigned_url: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

// ---------------------------------------------------------------------------
// Internal Helpers
// ---------------------------------------------------------------------------

async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, {
      ...options,
      credentials: "include",
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "TIMEOUT", "Yêu cầu đã hết thời gian chờ");
    }
    throw new ApiError(0, "NETWORK_ERROR", "Lỗi kết nối mạng");
  } finally {
    clearTimeout(timeoutId);
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    window.location.href = "/login";
    return new Promise(() => {}); // never resolves
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const message =
      body?.detail || body?.error?.message || `Yêu cầu thất bại: ${res.status}`;
        const errorCode = body?.error_code || body?.error?.code || body?.detail?.code || "UNKNOWN_ERROR";
    throw new ApiError(res.status, errorCode, message, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---------------------------------------------------------------------------
// Exported API Functions
// ---------------------------------------------------------------------------

/**
 * List candidates with pagination, search, and filters.
 */
export async function listCandidates(
  params: CandidateListParams = {},
): Promise<CandidateListResponse> {
  const searchParams = new URLSearchParams();

  if (params.page !== undefined) searchParams.set("page", String(params.page));
  if (params.page_size !== undefined) searchParams.set("page_size", String(params.page_size));
  if (params.search) searchParams.set("search", params.search);
  if (params.status && params.status.length > 0) {
    for (const s of params.status) {
      searchParams.append("status", s);
    }
  }
  if (params.from_date) searchParams.set("from_date", params.from_date);
  if (params.to_date) searchParams.set("to_date", params.to_date);
  if (params.min_confidence !== undefined && params.min_confidence > 0) {
    searchParams.set("min_confidence", String(params.min_confidence));
  }
  if (params.skills) searchParams.set("skills", params.skills);

  const query = searchParams.toString();
  const url = `${BASE}/candidates${query ? `?${query}` : ""}`;
  const res = await fetchWithTimeout(url);
  return handleResponse<CandidateListResponse>(res);
}

/**
 * Get full candidate detail by ID.
 */
export async function getCandidate(id: string): Promise<CandidateDetail> {
  const res = await fetchWithTimeout(`${BASE}/candidates/${id}`);
  return handleResponse<CandidateDetail>(res);
}

/**
 * Get a presigned URL for viewing/downloading a candidate's CV document.
 */
export async function getCVPresignedUrl(
  candidateId: string,
  documentId: string,
): Promise<CVPresignedUrlResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/candidates/${candidateId}/cv/${documentId}`,
  );
  return handleResponse<CVPresignedUrlResponse>(res);
}

/**
 * Send an email to a candidate.
 */
export async function sendEmail(
  id: string,
  data: SendEmailRequest,
): Promise<OutboundEmailResponse> {
  const res = await fetchWithTimeout(`${BASE}/candidates/${id}/send-email`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<OutboundEmailResponse>(res);
}

/**
 * Reject a candidate with a reason.
 */
export async function rejectCandidate(
  id: string,
  data: RejectRequest,
): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/candidates/${id}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  await handleResponse<unknown>(res);
}

/**
 * Accept a candidate.
 */
export async function acceptCandidate(id: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/candidates/${id}/accept`, {
    method: "POST",
  });
  await handleResponse<unknown>(res);
}

/**
 * Archive a candidate.
 */
export async function archiveCandidate(id: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/candidates/${id}/archive`, {
    method: "POST",
  });
  await handleResponse<unknown>(res);
}

/**
 * List CV documents in the review queue.
 */
export async function listReviewQueue(
  params: { page?: number; page_size?: number } = {},
): Promise<CVReviewListResponse> {
  const searchParams = new URLSearchParams();
  if (params.page !== undefined) searchParams.set("page", String(params.page));
  if (params.page_size !== undefined) searchParams.set("page_size", String(params.page_size));

  const query = searchParams.toString();
  const url = `${BASE}/cv-review${query ? `?${query}` : ""}`;
  const res = await fetchWithTimeout(url);
  return handleResponse<CVReviewListResponse>(res);
}

/**
 * Submit corrected CV data for a document in the review queue.
 */
export async function submitCorrection(
  cvDocumentId: string,
  data: ParsedCVInput,
): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/cv-review/${cvDocumentId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  await handleResponse<unknown>(res);
}

/**
 * Retry LLM parse for a CV document in the review queue.
 */
export async function retryParse(cvDocumentId: string): Promise<CVReviewItem> {
  const res = await fetchWithTimeout(
    `${BASE}/cv-review/${cvDocumentId}/retry`,
    {
      method: "POST",
    },
  );
  return handleResponse<CVReviewItem>(res);
}

/**
 * Dismiss a CV document from the review queue.
 */
export async function dismissReview(cvDocumentId: string): Promise<void> {
  const res = await fetchWithTimeout(
    `${BASE}/cv-review/${cvDocumentId}/dismiss`,
    {
      method: "DELETE",
    },
  );
  await handleResponse<void>(res);
}

/**
 * Get pipeline processing metrics (rolling 24-hour window).
 */
export async function getMetrics(): Promise<MetricsResponse> {
  const res = await fetchWithTimeout(`${BASE}/metrics`);
  return handleResponse<MetricsResponse>(res);
}


// ---------------------------------------------------------------------------
// Candidate Job Opening Assignment Functions
// ---------------------------------------------------------------------------

/**
 * Assign an unassigned Candidate to an open Job Opening.
 */
export async function assignCandidate(
  candidateId: string,
  jobOpeningId: string,
): Promise<void> {
  const res = await fetchWithTimeout(
    `${BASE}/candidates/${candidateId}/assign`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_opening_id: jobOpeningId }),
    },
  );
  await handleResponse<unknown>(res);
}

/**
 * Reassign a Candidate to a different open Job Opening.
 */
export async function reassignCandidate(
  candidateId: string,
  jobOpeningId: string,
): Promise<void> {
  const res = await fetchWithTimeout(
    `${BASE}/candidates/${candidateId}/reassign`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_opening_id: jobOpeningId }),
    },
  );
  await handleResponse<unknown>(res);
}

/**
 * Remove a Candidate's assignment to a Job Opening.
 */
export async function unassignCandidate(
  candidateId: string,
): Promise<void> {
  const res = await fetchWithTimeout(
    `${BASE}/candidates/${candidateId}/unassign`,
    {
      method: "POST",
    },
  );
  await handleResponse<unknown>(res);
}

/**
 * List only open Job Openings (for the assignment picker).
 */
export async function listOpenJobOpenings(
  params: JobOpeningListParams = {},
): Promise<JobOpeningListResponse> {
  return listJobOpenings({ ...params, status: ["open"] });
}

// ---------------------------------------------------------------------------
// Job Opening Types
// ---------------------------------------------------------------------------

export type JobOpeningStatus = "draft" | "open" | "closed" | "cancelled";

export interface JobOpeningListItem {
  id: string;
  title: string;
  position_id: string;
  position_name: string;
  target_headcount: number;
  status: JobOpeningStatus;
  created_at: string;
  total_candidates: number;
  accepted_count: number;
}

export interface JobOpeningListResponse {
  job_openings: JobOpeningListItem[];
  total_count: number;
  page: number;
  page_size: number;
}

export interface JobOpeningDetail {
  id: string;
  title: string;
  description: string;
  position_id: string;
  position_name: string;
  target_headcount: number;
  status: JobOpeningStatus;
  opened_at: string | null;
  closed_at: string | null;
  cancelled_at: string | null;
  created_at: string;
  updated_at: string;
  candidate_counts: Record<string, number>;
}

export interface JobOpeningListParams {
  page?: number;
  page_size?: number;
  search?: string;
  status?: JobOpeningStatus[];
  position_id?: string;
}

// ---------------------------------------------------------------------------
// Job Opening API Functions
// ---------------------------------------------------------------------------

export async function listJobOpenings(
  params: JobOpeningListParams = {},
): Promise<JobOpeningListResponse> {
  const searchParams = new URLSearchParams();
  if (params.page !== undefined) searchParams.set("page", String(params.page));
  if (params.page_size !== undefined) searchParams.set("page_size", String(params.page_size));
  if (params.search) searchParams.set("search", params.search);
  if (params.status && params.status.length > 0) {
    for (const s of params.status) {
      searchParams.append("status", s);
    }
  }
  if (params.position_id) searchParams.set("position_id", params.position_id);
  const query = searchParams.toString();
  const url = `${BASE}/job-openings${query ? `?${query}` : ""}`;
  const res = await fetchWithTimeout(url);
  return handleResponse<JobOpeningListResponse>(res);
}

export async function getJobOpening(id: string): Promise<JobOpeningDetail> {
  const res = await fetchWithTimeout(`${BASE}/job-openings/${id}`);
  return handleResponse<JobOpeningDetail>(res);
}

// ---------------------------------------------------------------------------
// Job Opening metrics types and functions

// --- Job Opening lifecycle (create / update / open / close / cancel) ---
// BE: POST /api/recruitment/job-openings, PUT /:id, POST /:id/open|close|cancel

export interface JobOpeningCreateInput {
  title: string;
  position_id: string;
  target_headcount: number;
  description?: string;
  status?: JobOpeningStatus;
}

export async function createJobOpening(data: JobOpeningCreateInput): Promise<JobOpeningDetail> {
  const res = await fetchWithTimeout(`${BASE}/job-openings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<JobOpeningDetail>(res);
}

export interface JobOpeningUpdateInput {
  title?: string;
  description?: string;
  target_headcount?: number;
}

export async function updateJobOpening(id: string, data: JobOpeningUpdateInput): Promise<JobOpeningDetail> {
  const res = await fetchWithTimeout(`${BASE}/job-openings/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<JobOpeningDetail>(res);
}

export async function openJobOpening(id: string): Promise<JobOpeningDetail> {
  const res = await fetchWithTimeout(`${BASE}/job-openings/${id}/open`, { method: "POST" });
  return handleResponse<JobOpeningDetail>(res);
}

export async function closeJobOpening(id: string): Promise<JobOpeningDetail> {
  const res = await fetchWithTimeout(`${BASE}/job-openings/${id}/close`, { method: "POST" });
  return handleResponse<JobOpeningDetail>(res);
}

export async function cancelJobOpening(id: string): Promise<JobOpeningDetail> {
  const res = await fetchWithTimeout(`${BASE}/job-openings/${id}/cancel`, { method: "POST" });
  return handleResponse<JobOpeningDetail>(res);
}

// ---------------------------------------------------------------------------

export interface JobOpeningMetrics {
  total_job_openings: number;
  draft_count: number;
  open_count: number;
  closed_count: number;
  cancelled_count: number;
}

/**
 * Get summary metrics for Job Opening lifecycle states.
 */
export async function getJobOpeningMetrics(): Promise<JobOpeningMetrics> {
  const res = await fetchWithTimeout(`${BASE}/job-openings/metrics`);
  return handleResponse<JobOpeningMetrics>(res);
}

// ---------------------------------------------------------------------------
// Outbound Email Types and API Functions
// ---------------------------------------------------------------------------

// `OutboundEmailStatus` was declared twice (here and `gmail.ts:356`) with
// identical values — canonical source is `gmail.ts` (#363), since the backend
// enum it mirrors lives in `gmail/domain/enums.py`.

export interface OutboundEmailResponse {
  id: string;
  candidate_id: string | null;
  subject: string;
  recipient_email: string;
  sender_email: string | null;
  status: OutboundEmailStatus;
  gmail_message_id: string | null;
  gmail_thread_id: string | null;
  error_message: string | null;
  retry_count: number;
  max_retries: number;
  last_retry_at: string | null;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface OutboundEmailListResponse {
  items: OutboundEmailResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface OutboundEmailRetryResponse {
  id: string;
  status: OutboundEmailStatus;
  message: string;
}

/**
 * Get the status of an outbound email.
 */
export async function getOutboundEmail(
  outboundId: string,
): Promise<OutboundEmailResponse> {
  const res = await fetchWithTimeout(`${API_BASE_URL}/api/outbound-emails/${outboundId}`);
  return handleResponse<OutboundEmailResponse>(res);
}

/**
 * List outbound emails for a candidate.
 */
export async function listOutboundEmails(
  params: { candidate_id?: string; page?: number; page_size?: number } = {},
): Promise<OutboundEmailListResponse> {
  const searchParams = new URLSearchParams();
  if (params.candidate_id) searchParams.set("candidate_id", params.candidate_id);
  if (params.page) searchParams.set("page", String(params.page));
  if (params.page_size) searchParams.set("page_size", String(params.page_size));
  const query = searchParams.toString();
  const url = `${API_BASE_URL}/api/outbound-emails${query ? `?${query}` : ""}`;
  const res = await fetchWithTimeout(url);
  return handleResponse<OutboundEmailListResponse>(res);
}

/**
 * Send (process) a pending outbound email.
 */
export async function sendOutboundEmail(
  outboundId: string,
): Promise<OutboundEmailResponse> {
  const res = await fetchWithTimeout(
    `${API_BASE_URL}/api/outbound-emails/${outboundId}/send`,
    { method: "POST" },
  );
  return handleResponse<OutboundEmailResponse>(res);
}

/**
 * Retry a failed outbound email.
 */
export async function retryOutboundEmail(
  outboundId: string,
): Promise<OutboundEmailRetryResponse> {
  const res = await fetchWithTimeout(
    `${API_BASE_URL}/api/outbound-emails/${outboundId}/retry`,
    { method: "POST" },
  );
  return handleResponse<OutboundEmailRetryResponse>(res);
}

// ---------------------------------------------------------------------------
// Interview API Functions (GH #148 / #154 / #155)
// ---------------------------------------------------------------------------

/**
 * Create a new interview for a candidate with calendar event.
 * POST /api/recruitment/candidates/{candidateId}/create-interview
 */
export async function createInterview(
  candidateId: string,
  data: CreateInterviewRequest,
): Promise<InterviewResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/candidates/${candidateId}/create-interview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
  return handleResponse<InterviewResponse>(res);
}

/**
 * Complete an interview.
 * POST /api/recruitment/candidates/{candidateId}/interviews/{interviewId}/complete
 */
export async function completeInterview(
  candidateId: string,
  interviewId: string,
): Promise<InterviewResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/candidates/${candidateId}/interviews/${interviewId}/complete`,
    { method: "POST" },
  );
  return handleResponse<InterviewResponse>(res);
}

/**
 * Cancel an interview.
 * POST /api/recruitment/candidates/{candidateId}/interviews/{interviewId}/cancel
 */
export async function cancelInterview(
  candidateId: string,
  interviewId: string,
): Promise<InterviewResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/candidates/${candidateId}/interviews/${interviewId}/cancel`,
    { method: "POST" },
  );
  return handleResponse<InterviewResponse>(res);
}

/**
 * Create a replacement interview (after cancellation).
 * POST /api/recruitment/candidates/{candidateId}/interviews/{interviewId}/replacement
 */
export async function createReplacementInterview(
  candidateId: string,
  interviewId: string,
  data: CreateInterviewRequest,
): Promise<InterviewResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/candidates/${candidateId}/interviews/${interviewId}/replacement`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
  return handleResponse<InterviewResponse>(res);
}

// ---------------------------------------------------------------------------
// Calendar Conflict API Functions
// ---------------------------------------------------------------------------

/**
 * List calendar conflicts, optionally filtered.
 * GET /api/recruitment/calendar-conflicts
 */
export async function listCalendarConflicts(
  params: {
    status?: ConflictStatus;
    candidate_id?: string;
  } = {},
): Promise<CalendarConflictListResponse> {
  const searchParams = new URLSearchParams();
  if (params.status) searchParams.set("status", params.status);
  if (params.candidate_id) searchParams.set("candidate_id", params.candidate_id);
  const query = searchParams.toString();
  const url = `${BASE}/calendar-conflicts${query ? `?${query}` : ""}`;
  const res = await fetchWithTimeout(url);
  return handleResponse<CalendarConflictListResponse>(res);
}

/**
 * Get a single calendar conflict by ID.
 * GET /api/recruitment/calendar-conflicts/{conflictId}
 */
export async function getCalendarConflict(
  conflictId: string,
): Promise<CalendarConflict> {
  const res = await fetchWithTimeout(
    `${BASE}/calendar-conflicts/${conflictId}`,
  );
  return handleResponse<CalendarConflict>(res);
}

/**
 * Resolve a calendar conflict.
 * POST /api/recruitment/calendar-conflicts/{conflictId}/resolve
 */
export async function resolveCalendarConflict(
  conflictId: string,
  data: ResolveConflictRequest,
): Promise<CalendarConflict> {
  const res = await fetchWithTimeout(
    `${BASE}/calendar-conflicts/${conflictId}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
  return handleResponse<CalendarConflict>(res);
}

// ---------------------------------------------------------------------------
// Recruitment Inbox Types & API (GH #184)
// ---------------------------------------------------------------------------

export type InboxStatus =
  | "needs_classification"
  | "needs_information"
  | "ready_for_review"
  | "resolved";

export interface InboxEvidence {
  signal: string;
}

export interface InboxSourceHint {
  key: string;
  value: string;
}

export interface CorrectionHistoryEntry {
  previous_intent: string | null;
  corrected_intent: string;
  // Always `item.inbox_status` at correction time (`inbox_service.py:431`),
  // a non-nullable `InboxStatus`-typed column — never a free-form string.
  previous_inbox_status: InboxStatus;
  corrected_by_user_id: string;
  corrected_at: string;
}

    export interface AttachmentMeta {
      name?: string;
      type?: string;
      size?: number;
    }

    export interface InboxItem {
      id: string;
      gmail_message_id: string;
      gmail_thread_id: string;
      sender_name: string;
      sender_email: string;
      subject: string;
      snippet: string;
      has_attachments: boolean;
      attachments_metadata: AttachmentMeta[] | null;
      inbox_status: InboxStatus;
  prediction_intent: string | null;
  confidence_raw: number | null;
  confidence_calibrated: number | null;
  evidence: InboxEvidence[] | null;
  source_hints: InboxSourceHint[] | null;
  corrected_intent: string | null;
  corrected_by_user_id: string | null;
  corrected_at: string | null;
  correction_history: CorrectionHistoryEntry[] | null;
  dismissed: boolean;
  dismissed_at: string | null;
  dismissed_by_user_id: string | null;
  processing_error: string | null;
  retry_count: number;
  is_retry_exhausted: boolean;
  created_at: string;
  updated_at: string;
}

export interface InboxListResponse {
  items: InboxItem[];
  total: number;
  page: number;
  page_size: number;
}

/**
 * List Recruitment Inbox items with optional status filter.
 */
export async function listInbox(
  params: { inbox_status?: InboxStatus; page?: number; page_size?: number } = {},
): Promise<InboxListResponse> {
  const searchParams = new URLSearchParams();
  if (params.inbox_status) searchParams.set("inbox_status", params.inbox_status);
  if (params.page) searchParams.set("page", String(params.page));
  if (params.page_size) searchParams.set("page_size", String(params.page_size));
  const query = searchParams.toString();
  const url = `${BASE}/inbox${query ? `?${query}` : ""}`;
  const res = await fetchWithTimeout(url);
  return handleResponse<InboxListResponse>(res);
}

/**
 * Get a single Recruitment Inbox item with full detail.
 */
export async function getInboxItem(id: string): Promise<InboxItem> {
  const res = await fetchWithTimeout(`${BASE}/inbox/${id}`);
  return handleResponse<InboxItem>(res);
}

/**
 * Correct the routing intent of an inbox item.
 */
export async function correctInboxIntent(
  id: string,
  corrected_intent: string,
): Promise<InboxItem> {
  const res = await fetchWithTimeout(`${BASE}/inbox/${id}/correct-intent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ corrected_intent }),
  });
  return handleResponse<InboxItem>(res);
}

/**
 * Dismiss an inbox item.
 */
export async function dismissInboxItem(id: string): Promise<InboxItem> {
  const res = await fetchWithTimeout(`${BASE}/inbox/${id}/dismiss`, {
    method: "POST",
  });
  return handleResponse<InboxItem>(res);
}


export type ApplicationSource = "direct" | "employee_referral" | "agency";

// Backend `JobApplication.status` (`entities.py:519`) is a raw `str` column
// but every write site sets it from this enum
// (`recruitment/domain/enums.py:95`) — `job_application_service.py:170`,
// `inbox_service.py:284`, `job_application_decision_service.py:178`.
export type JobApplicationStatus = "new" | "dismissed" | "promoted";

export interface SplitApplicantInput {
  name: string;
  email?: string;
  job_opening_id?: string;
}

export interface SplitInboxItemInput {
  source: ApplicationSource;
  applicants: SplitApplicantInput[];
}

export interface JobApplicationInboxResult {
  id: string;
  source_email_message_id: string;
  gmail_message_id: string;
    gmail_thread_id: string;
    intent: "job_application";
    has_cv: boolean;
    source: ApplicationSource;
  applicant_name: string | null;
  applicant_email: string | null;
  sender_name: string;
  sender_email: string;
  job_opening_id: string | null;
  status: JobApplicationStatus;
  message_references: Array<Record<string, unknown>>;
}

export interface SplitInboxItemResult {
  applications: JobApplicationInboxResult[];
}

export type LinkProposalStatus = "pending" | "confirmed" | "rejected";

export interface JobApplicationLinkProposal {
  id: string;
  recruitment_inbox_item_id: string;
  target_job_application_id: string;
  status: LinkProposalStatus;
  proposed_by_user_id: string;
  resolved_by_user_id: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Split one source message into one Job Application per applicant. */
export async function splitInboxItem(
  id: string,
  data: SplitInboxItemInput,
): Promise<SplitInboxItemResult> {
  const res = await fetchWithTimeout(`${BASE}/inbox/${id}/split`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return handleResponse<SplitInboxItemResult>(res);
}

export interface JobApplicationAssignmentResponse {
  id: string;
  job_opening_id: string | null;
  candidate_id: string | null;
  status: JobApplicationStatus;
}

export interface JobApplicationPromoteRequest {
  applicant_name: string;
  applicant_email: string;
  job_opening_id?: string | null;
}

export interface JobApplicationPromoteResponse {
  id: string;
  candidate_id: string;
  candidate_name: string;
  candidate_email: string;
  job_opening_id: string | null;
  status: JobApplicationStatus;
}

export async function assignJobApplication(
  id: string,
  jobOpeningId: string | null,
): Promise<JobApplicationAssignmentResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/job-applications/${id}/assignment`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_opening_id: jobOpeningId }),
    },
  );
  return handleResponse<JobApplicationAssignmentResponse>(res);
}

export async function promoteJobApplication(
  id: string,
  data: JobApplicationPromoteRequest,
): Promise<JobApplicationPromoteResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/job-applications/${id}/promote`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
  return handleResponse<JobApplicationPromoteResponse>(res);
}

/** Create an inert cross-thread link proposal for HR review. */
export async function proposeInboxLink(
  id: string,
  targetJobApplicationId: string,
): Promise<JobApplicationLinkProposal> {
  const res = await fetchWithTimeout(
    `${BASE}/inbox/${id}/link-proposals`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_job_application_id: targetJobApplicationId }),
    },
  );
  return handleResponse<JobApplicationLinkProposal>(res);
}

/** Confirm or reject a pending cross-thread link proposal. */
export async function resolveInboxLinkProposal(
  proposalId: string,
  decision: Exclude<LinkProposalStatus, "pending">,
): Promise<JobApplicationLinkProposal> {
  const res = await fetchWithTimeout(
    `${BASE}/inbox/link-proposals/${proposalId}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    },
  );
  return handleResponse<JobApplicationLinkProposal>(res);
}
