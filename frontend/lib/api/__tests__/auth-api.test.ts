/**
 * @vitest-environment jsdom
 *
 * Tests for the GH #299 Forgot/Reset password API functions:
 * forgotPassword, getResetTokenInfo, resetPassword.
 *
 * Validates correct endpoint URLs, HTTP method, request bodies, and
 * error handling for each function.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  forgotPassword,
  getResetTokenInfo,
  resetPassword,
  AuthApiError,
} from "../auth";

function mockFetch(response: Partial<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
      ...response,
    }),
  );
}

function mockFetchError(status: number, errorCode: string, message: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status,
      json: async () => ({ error: { code: errorCode, message } }),
    }),
  );
}

describe("forgotPassword", () => {
  afterEach(() => vi.restoreAllMocks());

  it("POSTs to /api/auth/forgot-password with the email body", async () => {
    mockFetch({
      json: async () => ({
        message: "Nếu email tồn tại trong hệ thống, chúng tôi đã gửi hướng dẫn khôi phục mật khẩu vào hòm thư của bạn.",
      }),
    });
    const result = await forgotPassword("hr@example.com");

    expect(result.message).toContain("hướng dẫn khôi phục mật khẩu");
    const fetchCall = (vi.mocked(fetch) as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(fetchCall[0]).toContain("/api/auth/forgot-password");
    expect(fetchCall[1]?.method).toBe("POST");
    expect(fetchCall[1]?.headers?.["Content-Type"]).toBe("application/json");
    expect(JSON.parse(fetchCall[1]?.body)).toEqual({ email: "hr@example.com" });
  });

  it("throws AuthApiError with AUTH_RATE_LIMITED code on 429", async () => {
    mockFetchError(429, "AUTH_RATE_LIMITED", "Quá nhiều lần đăng nhập. Vui lòng thử lại sau.");
    await expect(forgotPassword("hr@example.com")).rejects.toThrow(AuthApiError);
    await expect(forgotPassword("hr@example.com")).rejects.toMatchObject({
      code: "AUTH_RATE_LIMITED",
    });
  });
});

describe("getResetTokenInfo", () => {
  afterEach(() => vi.restoreAllMocks());

  it("GETs /api/auth/reset-password-token-info with the token query param", async () => {
    mockFetch({ json: async () => ({ valid: true }) });
    const result = await getResetTokenInfo("abc-123");

    expect(result.valid).toBe(true);
    const fetchCall = (vi.mocked(fetch) as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(fetchCall[0]).toContain("/api/auth/reset-password-token-info");
    expect(fetchCall[0]).toContain("token=abc-123");
    expect(fetchCall[1]?.method).toBeUndefined();
  });

  it("reports invalid token as valid: false (not an error)", async () => {
    mockFetch({ json: async () => ({ valid: false }) });
    const result = await getResetTokenInfo("used-token");
    expect(result.valid).toBe(false);
  });
});

describe("resetPassword", () => {
  afterEach(() => vi.restoreAllMocks());

  it("POSTs to /api/auth/reset-password with token and new_password", async () => {
    mockFetch({ json: async () => ({ message: "Mật khẩu đã được đặt lại thành công." }) });
    const result = await resetPassword("abc-123", "N3w-Secure-Pass!");

    expect(result.message).toContain("đặt lại thành công");
    const fetchCall = (vi.mocked(fetch) as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(fetchCall[0]).toContain("/api/auth/reset-password");
    expect(fetchCall[1]?.method).toBe("POST");
    expect(JSON.parse(fetchCall[1]?.body)).toEqual({
      token: "abc-123",
      new_password: "N3w-Secure-Pass!",
    });
  });

  it("throws AuthApiError with AUTH_INVALID_RESET_TOKEN on expired token", async () => {
    mockFetchError(
      400,
      "AUTH_INVALID_RESET_TOKEN",
      "Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn",
    );
    await expect(resetPassword("expired-token", "N3w-Secure-Pass!")).rejects.toThrow(AuthApiError);
    await expect(resetPassword("expired-token", "N3w-Secure-Pass!")).rejects.toMatchObject({
      code: "AUTH_INVALID_RESET_TOKEN",
    });
  });
});
