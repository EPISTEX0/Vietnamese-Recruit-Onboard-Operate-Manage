/**
 * The three roles of the deployment, and the routing rules that follow from
 * them. Single source of truth for the FE — every other module imports
 * `UserRole` from here rather than re-declaring a union.
 *
 * The roles mirror the BE enum (`src/modules/identity/domain/entities.py`):
 *
 * - `system_admin` — owns `/api/system-admin/*`: OAuth client credentials,
 *   LLM API key, allowed domains, audit log, user management, runtime
 *   health. No HR business surface.
 * - `hr` — owns every business surface: employees, onboarding, knowledge base,
 *   documents, recruitment, Gmail, HR assistant, payslips, employee requests,
 *   attendance, and the organization Google connection. No system setup.
 * - `user` — employee self-service only.
 *
 * There is deliberately no `isAdmin`/`admin` concept. Merging the two staff
 * roles behind one boolean is what let 38 HR endpoints end up gated by system
 * admin on the BE, and what left every staff account stranded on `/employee`
 * on the FE. Call sites must name the role they mean.
 */

export const USER_ROLES = ["system_admin", "hr", "user"] as const;

export type UserRole = (typeof USER_ROLES)[number];

/**
 * Roles a system admin can provision via `POST /api/system-admin/users`.
 *
 * `user` is absent on purpose: self-service accounts are created by HR against
 * an Employee record (`POST /api/employees/{id}/account`), not minted standalone.
 */
export const STAFF_ROLES = ["hr", "system_admin"] as const;

export type StaffRole = (typeof STAFF_ROLES)[number];

/**
 * Landing route for each role after login, first-run setup, or a forced
 * password change.
 *
 * System admin and HR do NOT share a landing page — the system admin console
 * (`/settings`) has no HR business on it, and the HR dashboard has no system
 * setup on it.
 */
const ROLE_HOME_PATH: Record<UserRole, string> = {
  system_admin: "/settings",
  hr: "/dashboard",
  user: "/employee",
};

/** Narrow an untrusted value (e.g. a BE payload field) to a known role. */
export function isUserRole(value: unknown): value is UserRole {
  return typeof value === "string" && (USER_ROLES as readonly string[]).includes(value);
}

/** The route this role should land on. Total over the union — no fallback path. */
export function homePathForRole(role: UserRole): string {
  return ROLE_HOME_PATH[role];
}

/**
 * Whether `role` is one of `allowed`.
 *
 * Takes an explicit allow-list rather than a boolean so a call site can never
 * grant a surface to a role it did not name.
 */
export function hasRole(
  role: UserRole | null | undefined,
  allowed: readonly UserRole[],
): boolean {
  return role != null && allowed.includes(role);
}
