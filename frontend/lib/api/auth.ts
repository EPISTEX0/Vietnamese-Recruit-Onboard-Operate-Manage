import { API_BASE_URL } from "./client";
import type { UserRole } from "@/lib/auth/roles";

const BASE = `${API_BASE_URL}/api/auth`;

export type { UserRole };

export interface CurrentUser {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  employee_id?: string | null;
  role: UserRole;
  must_change_password: boolean;
  gmail_grant_valid: boolean;
  calendar_grant_valid: boolean;
  created_at: string;
  last_login: string;
}

/**
 * Session shape trả về bởi POST /api/auth/login, /setup, /change-password
 * (BE wrap `user` + `must_change_password` + `setup_complete`).
 *
 * LƯU Ý: KHÔNG áp dụng cho GET /api/auth/me — /me trả flat `CurrentUser`
 * (xem lib/auth/session.ts). Bug cũ (BUG-1): app từng dùng
 * AuthSessionResponse cho /me → `data.user` undefined →
 * isAuthenticated=false → redirect /login dù /me trả 200 admin thật.
 */
export interface AuthSessionResponse {
  user: CurrentUser;
  must_change_password: boolean;
  setup_complete: boolean;
}

export interface SetupStatusResponse {
  setup_complete: boolean;
}

export interface EmployeeAccountStatusResponse {
  exists: boolean;
  user_id: string | null;
  email: string | null;
  role: UserRole | null;
  must_change_password: boolean | null;
}

export interface EmployeeAccountCreateResponse {
  user: EmployeeAccountStatusResponse;
  temporary_password: string;
}


export class AuthApiError extends Error {
  code?: string;
  fields: Record<string, string>;

  constructor(message: string, code?: string, fields: Record<string, string> = {}) {
    super(message);
    this.name = "AuthApiError";
    this.code = code;
    this.fields = fields;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const details = Array.isArray(payload?.detail) ? payload.detail : [];
    const fields = Object.fromEntries(
      details
        .filter((item: { loc?: unknown; msg?: unknown }) => Array.isArray(item.loc) && item.msg)
        .map((item: { loc: string[]; msg: string }) => [item.loc.at(-1), item.msg]),
    );
    const message =
      payload?.error?.message ??
      (typeof payload?.detail === "string" ? payload.detail : undefined) ??
      payload?.message ??
      (Object.keys(fields).length ? "Vui lòng kiểm tra lại thông tin" : res.statusText);
    throw new AuthApiError(
      typeof message === "string" ? message : JSON.stringify(message),
      payload?.error?.code,
      fields,
    );
  }
  return res.json() as Promise<T>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  return handleResponse<T>(res);
}

export async function getSetupStatus(): Promise<SetupStatusResponse> {
  const res = await fetch(`${BASE}/setup-status`, { credentials: "include" });
  return handleResponse<SetupStatusResponse>(res);
}

export async function login(email: string, password: string): Promise<AuthSessionResponse> {
  return request<AuthSessionResponse>("/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function setupFirstRun(
  organization_name: string,
  name: string,
  email: string,
  password: string,
  password_confirmation: string,
): Promise<AuthSessionResponse> {
  return request<AuthSessionResponse>("/setup", {
    method: "POST",
    body: JSON.stringify({ organization_name, name, email, password, password_confirmation }),
  });
}

export async function changePassword(
  current_password: string,
  new_password: string,
): Promise<AuthSessionResponse> {
  return request<AuthSessionResponse>("/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });
}

// ---------------------------------------------------------------------------
// Forgot / Reset password flow (issue 299)
// ---------------------------------------------------------------------------

export interface ForgotPasswordResponse {
  message: string;
}

export interface ResetTokenInfoResponse {
  valid: boolean;
}

export interface ResetPasswordResponse {
  message: string;
}

/**
 * Request a password reset email. BE always returns the same generic 200
 * message (anti-enumeration) — whether the account exists or not. Can throw
 * AuthApiError with code AUTH_RATE_LIMITED (HTTP 429) when rate-limited.
 */
export async function forgotPassword(
  email: string,
): Promise<ForgotPasswordResponse> {
  return request<ForgotPasswordResponse>("/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/** Check whether a reset token is still redeemable. */
export async function getResetTokenInfo(
  token: string,
): Promise<ResetTokenInfoResponse> {
  return request<ResetTokenInfoResponse>(
    `/reset-password-token-info?token=${encodeURIComponent(token)}`,
  );
}

/** Set a new password using a valid reset token. */
export async function resetPassword(
  token: string,
  new_password: string,
): Promise<ResetPasswordResponse> {
  return request<ResetPasswordResponse>("/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password }),
  });
}
