/** API client for Employee Request (Leave & Overtime) endpoints. */

import { apiFetch } from "./client";

const BASE = "/api/employee-requests";

// ---------------------------------------------------------------------------
// Types — ESS
// ---------------------------------------------------------------------------

export interface EmployeeRequestListItem {
  id: string;
  employee_id: string;
  request_type: "leave" | "overtime";
  status: "submitted" | "approved" | "rejected" | "cancelled";
  submitted_at: string | null;
  updated_at: string | null;
  /** Overtime fields */
  work_date: string | null;
  start_time: string | null;
  end_time: string | null;
  duration_minutes: number | null;
  /** Leave fields */
  leave_type: string | null;
  start_date: string | null;
  end_date: string | null;
  /** Common */
  reason: string | null;
  project_or_task: string | null;
  cancellation_reason: string | null;
  /** Review fields */
  review_reason: string | null;
  reviewed_at: string | null;
}

export interface EmployeeRequestListResponse {
  requests: EmployeeRequestListItem[];
}

export interface CreateLeaveData {
  leave_type: "annual" | "sick" | "unpaid" | "other";
  start_date: string;
  end_date: string;
  reason: string;
}

export interface LeaveResponse {
  id: string;
  employee_id: string;
  status: "submitted" | "approved" | "rejected" | "cancelled";
  leave_type: string | null;
  start_date: string | null;
  end_date: string | null;
  reason: string | null;
  submitted_at: string | null;
  updated_at: string | null;
  cancellation_reason: string | null;
  review_reason: string | null;
  reviewed_at: string | null;
}

export interface LeaveCreateResponse {
  message: string;
  request: LeaveResponse;
}

export interface LeaveCancelResponse {
  message: string;
  request: LeaveResponse;
}

export interface CreateOvertimeData {
  work_date: string;
  start_time: string;
  end_time: string;
  reason: string;
  project_or_task?: string | null;
}

export interface OvertimeResponse {
  id: string;
  employee_id: string;
  status: "submitted" | "approved" | "rejected" | "cancelled";
  work_date: string | null;
  start_time: string | null;
  end_time: string | null;
  duration_minutes: number | null;
  reason: string | null;
  project_or_task: string | null;
  submitted_at: string | null;
  updated_at: string | null;
  cancellation_reason: string | null;
  review_reason: string | null;
  reviewed_at: string | null;
}

export interface OvertimeCreateResponse {
  message: string;
  request: OvertimeResponse;
}

export interface OvertimeCancelResponse {
  message: string;
  request: OvertimeResponse;
}

// ---------------------------------------------------------------------------
// ESS — List
// ---------------------------------------------------------------------------

/** Fetch all requests for the current employee (leave + overtime merged). */
export async function fetchMyRequests(): Promise<EmployeeRequestListResponse> {
  return apiFetch<EmployeeRequestListResponse>(`${BASE}/me`);
}

export interface LeaveBalance {
  annual_entitlement_days: number;
  approved_days_used: number;
  pending_days: number;
  remaining_days: number;
  as_of: string;
}

/** Fetch the current employee's annual leave balance. */
export async function fetchLeaveBalance(): Promise<LeaveBalance> {
  return apiFetch<LeaveBalance>(`${BASE}/me/leave/balance`);
}

// ---------------------------------------------------------------------------
// ESS — Leave
// ---------------------------------------------------------------------------

/** Create a new leave request. */
export async function createLeave(data: CreateLeaveData): Promise<LeaveCreateResponse> {
  return apiFetch<LeaveCreateResponse>(`${BASE}/me/leave`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

/** Cancel own submitted leave request. */
export async function cancelLeave(
  requestId: string,
  cancellationReason?: string | null,
): Promise<LeaveCancelResponse> {
  return apiFetch<LeaveCancelResponse>(
    `${BASE}/me/leave/${encodeURIComponent(requestId)}/cancel`,
    {
      method: "POST",
      body: JSON.stringify({ cancellation_reason: cancellationReason ?? null }),
    },
  );
}

// ---------------------------------------------------------------------------
// ESS — Overtime
// ---------------------------------------------------------------------------

/** Create a new overtime request. */
export async function createOvertime(data: CreateOvertimeData): Promise<OvertimeCreateResponse> {
  return apiFetch<OvertimeCreateResponse>(`${BASE}/me/overtime`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

/** Cancel own submitted overtime request. */
export async function cancelOvertime(
  requestId: string,
  cancellationReason?: string | null,
): Promise<OvertimeCancelResponse> {
  return apiFetch<OvertimeCancelResponse>(
    `${BASE}/me/overtime/${encodeURIComponent(requestId)}/cancel`,
    {
      method: "POST",
      body: JSON.stringify({ cancellation_reason: cancellationReason ?? null }),
    },
  );
}

// ---------------------------------------------------------------------------
// Admin / HR review
// ---------------------------------------------------------------------------
const ADMIN_BASE = "/api/hr/employee-requests";
export interface AdminEmployeeRequestItem {
  id: string;
  employee_id: string;
  employee_name: string;
  request_type: "leave" | "overtime";
  status: "submitted" | "approved" | "rejected" | "cancelled";
  submitted_at: string | null;
  updated_at: string | null;
  reason: string | null;
  /** Overtime fields */
  work_date: string | null;
  start_time: string | null;
  end_time: string | null;
  duration_minutes: number | null;
  /** Leave fields */
  leave_type: string | null;
  start_date: string | null;
  end_date: string | null;
  /** Cancellation */
  cancellation_reason: string | null;
  /** Review fields */
  review_reason: string | null;
  reviewed_at: string | null;
  reviewed_by_user_id: string | null;
}

export interface AdminReviewQueueResponse {
  requests: AdminEmployeeRequestItem[];
}

export interface ReviewResponse {
  message: string;
  request: AdminEmployeeRequestItem;
}

export interface ReviewQueueFilters {
  request_type?: "leave" | "overtime";
  status?: "submitted" | "approved" | "rejected" | "cancelled";
  date_from?: string;
  date_to?: string;
  employee_id?: string;
}

/** Fetch submitted requests for HR review queue (admin only), with optional filters. */
export async function fetchSubmittedRequests(
  filters?: ReviewQueueFilters,
): Promise<AdminReviewQueueResponse> {
  const sp = new URLSearchParams();
  if (filters?.request_type) sp.set("request_type", filters.request_type);
  if (filters?.status) sp.set("status", filters.status);
  if (filters?.date_from) sp.set("date_from", filters.date_from);
  if (filters?.date_to) sp.set("date_to", filters.date_to);
  if (filters?.employee_id) sp.set("employee_id", filters.employee_id);
  const qs = sp.toString();
  return apiFetch<AdminReviewQueueResponse>(qs ? `${ADMIN_BASE}?${qs}` : ADMIN_BASE);
}

/** Approve a submitted employee request (admin only). */
export async function approveRequest(
  requestId: string,
  reviewReason?: string | null,
): Promise<ReviewResponse> {
  return apiFetch<ReviewResponse>(
    `${ADMIN_BASE}/${encodeURIComponent(requestId)}/approve`,
    {
      method: "POST",
      body: JSON.stringify({ review_reason: reviewReason ?? null }),
    },
  );
}

/** Reject a submitted employee request (admin only). Reason required. */
export async function rejectRequest(
  requestId: string,
  decisionReason: string,
): Promise<ReviewResponse> {
  return apiFetch<ReviewResponse>(
    `${ADMIN_BASE}/${encodeURIComponent(requestId)}/reject`,
    {
      method: "POST",
      body: JSON.stringify({ decision_reason: decisionReason }),
    },
  );
}