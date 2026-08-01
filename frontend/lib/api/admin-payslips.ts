/** API client for Admin Payslip management (HR). */

import { apiFetch } from "./client";
const BASE = "/api/hr/payslips";
// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Payslip {
  id: string;
  employee_id: string;
  period_month: string;
  gross_salary: string;
  deductions: string;
  insurance_employee: string;
  taxable_income: string;
  pit_amount: string;
  net_salary: string;
  currency: string;
  status: "draft" | "published";
  published_at: string | null;
  pdf_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface PayslipListResponse {
  payslips: Payslip[];
  total: number;
  page: number;
  page_size: number;
}

export interface CreatePayslipRequest {
  employee_id: string;
  /** YYYY-MM-DD (first of month). BE normalizes day→1. */
  period_month: string;
  gross_salary: string;
  deductions?: string;
  insurance_employee?: string;
  taxable_income?: string;
  pit_amount?: string;
  net_salary: string;
  pdf_url?: string;
}

export interface UpdatePayslipRequest {
  gross_salary?: string;
  deductions?: string;
  insurance_employee?: string;
  taxable_income?: string;
  pit_amount?: string;
  net_salary?: string;
  pdf_url?: string;
}

// ---------------------------------------------------------------------------
// Admin endpoints
// ---------------------------------------------------------------------------

/** List payslips with optional filters. */
export async function fetchPayslips(options?: {
  page?: number;
  page_size?: number;
  employee_id?: string;
  status?: "draft" | "published";
  period_month?: string; // YYYY-MM
}): Promise<PayslipListResponse> {
  const params = new URLSearchParams();
  if (options?.page) params.set("page", String(options.page));
  if (options?.page_size) params.set("page_size", String(options.page_size));
  if (options?.employee_id) params.set("employee_id", options.employee_id);
  if (options?.status) params.set("status", options.status);
  if (options?.period_month) params.set("period_month", options.period_month);

  const qs = params.toString();
  return apiFetch<PayslipListResponse>(qs ? `${BASE}?${qs}` : BASE);
}

/** Get a specific payslip by ID (any status). */
export async function fetchPayslip(id: string): Promise<Payslip> {
  return apiFetch<Payslip>(`${BASE}/${encodeURIComponent(id)}`);
}

/** Create a new draft payslip. */
export async function createPayslip(data: CreatePayslipRequest): Promise<Payslip> {
  return apiFetch<Payslip>(BASE, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

/** Update a draft payslip. Only draft payslips can be updated. */
export async function updatePayslip(id: string, data: UpdatePayslipRequest): Promise<Payslip> {
  return apiFetch<Payslip>(`${BASE}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

/** Publish a draft payslip. Once published it is visible to the Employee. */
export async function publishPayslip(id: string): Promise<Payslip> {
  return apiFetch<Payslip>(`${BASE}/${encodeURIComponent(id)}/publish`, {
    method: "POST",
  });
}

/** Unpublish a published payslip, reverting it to draft. */
export async function unpublishPayslip(id: string): Promise<Payslip> {
  return apiFetch<Payslip>(`${BASE}/${encodeURIComponent(id)}/unpublish`, {
    method: "POST",
  });
}

/** Bulk publish multiple draft payslips. Returns count of published. */
export async function bulkPublishPayslips(ids: string[]): Promise<{ published_count: number }> {
  return apiFetch<{ published_count: number }>(`${BASE}/bulk-publish`, {
    method: "POST",
    body: JSON.stringify({ payslip_ids: ids }),
  });
}

/** Delete a draft payslip. Only draft payslips can be deleted. */
export async function deletePayslip(id: string): Promise<void> {
  await apiFetch<void>(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" });
}