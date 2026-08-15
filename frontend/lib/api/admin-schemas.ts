/**
 * Zod validation schemas for admin panel forms.
 *
 * These schemas provide client-side validation for the admin API
 * request payloads before submission.
 */

import { z } from "zod";

import { STAFF_ROLES, USER_ROLES } from "@/lib/auth/roles";

// ---------------------------------------------------------------------------
// Whitelist Add Schema
// ---------------------------------------------------------------------------

/**
 * Validates a whitelist entry value — either a full email address
 * (user@domain.com) or a domain pattern (@domain.com).
 */
export const whitelistAddSchema = z.object({
  value: z
    .string()
    .min(3, "Giá trị phải có ít nhất 3 ký tự")
    .max(255, "Giá trị không được vượt quá 255 ký tự")
    .refine(
      (val) => {
        // Domain pattern: starts with @ followed by a valid domain
        const domainPatternRegex = /^@[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$/;
        // Email: standard email format
        const emailRegex = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$/;
        return domainPatternRegex.test(val) || emailRegex.test(val);
      },
      {
        message: "Phải là email hợp lệ (user@domain.com) hoặc domain (@domain.com)",
      }
    ),
});

export type WhitelistAddFormData = z.infer<typeof whitelistAddSchema>;

// ---------------------------------------------------------------------------
// OAuth Config Update Schema
// ---------------------------------------------------------------------------

/**
 * Whether a redirect URI may be served over plaintext http.
 *
 * Google's own requirement is https *except* for loopback redirect URIs, and
 * this codebase's default is `http://localhost:8000/api/auth/callback`
 * (`identity/infrastructure/config.py`). A flat `startsWith("https://")` check
 * therefore rejects the very value `/settings/oauth` prints one card above its
 * form on every self-hosted deployment that has not been put behind TLS yet.
 *
 * Both arms parse rather than prefix-match, so the two agree on what a scheme
 * and a host are: `HTTPS://vroom.example.com/cb` is the same URL as its
 * lowercase form, and `http://localhost.attacker.example/` and
 * `http://localhost@evil.example/` merely begin with the loopback name while
 * being neither loopback nor safe.
 */
function isAcceptableRedirectUri(value: string): boolean {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (url.protocol === "https:") return true;
  // `[::1]` is what `URL` reports for the IPv6 loopback, brackets included.
  return url.protocol === "http:"
    && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
}

/**
 * Validates OAuth configuration update fields:
 * - client_id must be non-empty
 * - client_secret must be non-empty — the backend does *not* check this
 *   (`oauth_config_manager.py:190-201`), so a blank field would be encrypted
 *   and stored over working credentials
 * - redirect_uri must be a valid https URL, or an http loopback one
 *
 * All three trim first. These values are pasted from the Google Cloud Console,
 * and a trailing newline or a stray space rides along invisibly: the backend
 * strips only for its own emptiness check and stores what it was sent, so
 * `"GOCSPX-secret\n"` is encrypted with the newline and every login afterwards
 * fails `invalid_client` while this screen shows a configuration that looks
 * complete. Trimming also closes the whitespace-only hole in `min(1)`.
 */
export const oauthConfigUpdateSchema = z.object({
  client_id: z
    .string()
    .trim()
    .min(1, "Client ID không được để trống")
    .max(255, "Client ID không được vượt quá 255 ký tự"),
  client_secret: z
    .string()
    .trim()
    .min(1, "Client Secret không được để trống")
    .max(500, "Client Secret không được vượt quá 500 ký tự"),
  redirect_uri: z
    .string()
    .trim()
    .min(1, "Redirect URI không được để trống")
    .max(500, "Redirect URI không được vượt quá 500 ký tự")
    .url("Redirect URI phải là URL hợp lệ")
    .refine(
      isAcceptableRedirectUri,
      { message: "Redirect URI phải bắt đầu bằng https:// (http:// chỉ dùng được với localhost)" }
    ),
});

export type OAuthConfigUpdateFormData = z.infer<typeof oauthConfigUpdateSchema>;

// ---------------------------------------------------------------------------
// Role Update Schema
// ---------------------------------------------------------------------------

/**
 * Validates that the role value is one of the three deployment roles.
 * Mirrors the BE `UserRole` enum — see lib/auth/roles.ts.
 */
export const roleUpdateSchema = z.object({
  role: z.enum(USER_ROLES, {
    message: "Vai trò phải là 'system_admin', 'hr' hoặc 'user'",
  }),
});

export type RoleUpdateFormData = z.infer<typeof roleUpdateSchema>;

// ---------------------------------------------------------------------------
// Staff Account Create Schema
// ---------------------------------------------------------------------------

/**
 * Validates the payload for POST /api/system-admin/users.
 *
 * Only staff roles are offered: `user` accounts are provisioned by HR against
 * an Employee record, not minted standalone here.
 */
export const staffAccountCreateSchema = z.object({
  email: z
    .string()
    .min(1, "Email không được để trống")
    .max(255, "Email không được vượt quá 255 ký tự")
    .email("Email không hợp lệ"),
  name: z
    .string()
    .trim()
    .min(1, "Tên không được để trống")
    .max(255, "Tên không được vượt quá 255 ký tự"),
  role: z.enum(STAFF_ROLES, {
    message: "Vai trò phải là 'hr' hoặc 'system_admin'",
  }),
});

export type StaffAccountCreateFormData = z.infer<typeof staffAccountCreateSchema>;


// ---------------------------------------------------------------------------
// Domain Add Schema
// ---------------------------------------------------------------------------

/**
 * Validates a domain string for the Organization allowed domains list.
 * Must be a bare domain without protocol or @ prefix (e.g. company.vn).
 */
export const domainAddSchema = z.object({
  domain: z
    .string()
    .min(3, "Domain phải có ít nhất 3 ký tự")
    .max(255, "Domain không được vượt quá 255 ký tự")
    .regex(
      /^[a-z0-9]([a-z0-9-]*[a-z0-9])?\.[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$/,
      "Domain không hợp lệ (ví dụ: company.vn)"
    ),
});

export type DomainAddFormData = z.infer<typeof domainAddSchema>;
