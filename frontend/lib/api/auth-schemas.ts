/**
 * Zod validation schemas for auth forms (login, setup, change-password).
 *
 * Follows the same pattern as admin-schemas.ts.
 * All error messages are in Vietnamese.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Login Schema
// ---------------------------------------------------------------------------

export const loginSchema = z.object({
  email: z
    .string()
    .min(1, "Vui lòng nhập email")
    .email("Email không đúng định dạng"),
  password: z
    .string()
    .min(1, "Vui lòng nhập mật khẩu"),
});

export type LoginFormData = z.infer<typeof loginSchema>;

// ---------------------------------------------------------------------------
// Setup (First-Run Wizard) Schema
// ---------------------------------------------------------------------------

export const setupSchema = z
  .object({
    organization_name: z
      .string()
      .min(1, "Vui lòng nhập tên tổ chức / công ty"),
    name: z
      .string()
      .min(1, "Vui lòng nhập họ tên"),
    email: z
      .string()
      .min(1, "Vui lòng nhập email")
      .email("Email không đúng định dạng"),
    password: z
      .string()
      .min(12, "Mật khẩu phải từ 12 ký tự trở lên"),
    password_confirmation: z
      .string()
      .min(1, "Vui lòng xác nhận mật khẩu"),
  })
  .refine(
    (data) => data.password === data.password_confirmation,
    {
      message: "Xác nhận mật khẩu không trùng khớp",
      path: ["password_confirmation"],
    },
  );

export type SetupFormData = z.infer<typeof setupSchema>;

// ---------------------------------------------------------------------------
// Change-Password Schema
// ---------------------------------------------------------------------------

export const changePasswordSchema = z
  .object({
    current_password: z
      .string()
      .min(1, "Vui lòng nhập mật khẩu hiện tại"),
    new_password: z
      .string()
      .min(12, "Mật khẩu mới phải từ 12 ký tự trở lên"),
    confirm_password: z
      .string()
      .min(1, "Vui lòng xác nhận mật khẩu mới"),
  })
  .refine(
    (data) => data.new_password === data.confirm_password,
    {
      message: "Xác nhận mật khẩu mới không trùng khớp",
      path: ["confirm_password"],
    },
  )
  .refine(
    (data) => data.new_password !== data.current_password,
    {
      message: "Mật khẩu mới không được trùng với mật khẩu hiện tại",
      path: ["new_password"],
    },
  );

export type ChangePasswordFormData = z.infer<typeof changePasswordSchema>;

// ---------------------------------------------------------------------------
// Forgot-Password Schema
// ---------------------------------------------------------------------------

export const forgotPasswordSchema = z.object({
  email: z
    .string()
    .min(1, "Vui lòng nhập email")
    .email("Email không đúng định dạng"),
});

export type ForgotPasswordFormData = z.infer<typeof forgotPasswordSchema>;

// ---------------------------------------------------------------------------
// Reset-Password Schema
// ---------------------------------------------------------------------------

export const resetPasswordSchema = z
  .object({
    new_password: z
      .string()
      .min(12, "Mật khẩu mới phải từ 12 ký tự trở lên"),
    confirm_password: z
      .string()
      .min(1, "Vui lòng xác nhận mật khẩu mới"),
  })
  .refine(
    (data) => data.new_password === data.confirm_password,
    {
      message: "Xác nhận mật khẩu mới không trùng khớp",
      path: ["confirm_password"],
    },
  );

export type ResetPasswordFormData = z.infer<typeof resetPasswordSchema>;
