/** API client for Employee-owned Payslip endpoints (read-only, published only). */

import { apiFetch } from "./client";

const BASE = "/api/payslips";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// Backend `Payslip.status` (`payslip/domain/entities.py:83`) is typed
// `PayslipStatus` on both the entity and `PayslipResponse` (`api/schemas.py:27`).
export type PayslipStatus = "draft" | "published";

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
  status: PayslipStatus;
  published_at: string | null;
  pdf_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface PayslipListResponse {
  payslips: Payslip[];
}

// ---------------------------------------------------------------------------
// Employee-owned endpoints (only published payslips are visible here)
// ---------------------------------------------------------------------------

/** Fetch all published payslips for the current employee. */
export async function fetchMyPayslips(): Promise<PayslipListResponse> {
  return apiFetch<PayslipListResponse>(`${BASE}/me`);
}

/** Fetch a single published payslip owned by the current employee. */
export async function fetchMyPayslip(id: string): Promise<Payslip> {
  return apiFetch<Payslip>(`${BASE}/me/${encodeURIComponent(id)}`);
}