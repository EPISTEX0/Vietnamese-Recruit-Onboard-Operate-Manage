/**
 * @vitest-environment jsdom
 *
 * `useAuthGuard` is the only thing standing between a signed-in account and a
 * surface that does not belong to its role, so it is tested against the real
 * `useSession` → `isUserRole` → `homePathForRole` chain. Only the two edges are
 * faked: the network (`apiFetch`) and the router.
 *
 * The cases below are decisions, not renders — each one asks "given this
 * `/api/auth/me` payload, where does the guard send the user?".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ApiError } from "@/lib/api/types";

const { mockReplace, mockApiFetch, mockRouter } = vi.hoisted(() => {
  const replace = vi.fn();
  return {
    mockReplace: replace,
    mockApiFetch: vi.fn(),
    // One stable object for the whole file. A fresh router per render would
    // change the guard effect's dependency identity and re-run it on every
    // render, which would quietly turn "redirected once" assertions into
    // "redirected however many times React happened to render".
    mockRouter: { replace, push: vi.fn(), refresh: vi.fn() },
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
}));

vi.mock("@/lib/api/client", () => ({
  apiFetch: mockApiFetch,
  API_BASE_URL: "http://api.test",
}));

// Imported after the mocks so `session.ts` binds the fakes.
const { useAuthGuard, useSession } = await import("./session");

// `allowRoles` lands in the guard's effect dependency list, so call sites pass
// stable references. These mirror that.
const HR_ONLY = ["hr"] as const;
const SYSTEM_ADMIN_ONLY = ["system_admin"] as const;
const STAFF = ["hr", "system_admin"] as const;

/** A `/api/auth/me` 200 body with the given role. */
function meResponse(role: string | null | undefined) {
  return {
    id: "u-1",
    email: "someone@example.com",
    name: "Someone",
    role,
    must_change_password: false,
  };
}

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

function newClient() {
  return new QueryClient({
    // `retryDelay: 0`, not `retry: false` — `useSession` sets its own `retry`
    // predicate, which wins over the default, so the retries happen either way.
    // Zeroing the backoff keeps the real retry behaviour under test without
    // spending React Query's exponential delay on every 5xx case.
    defaultOptions: { queries: { retry: false, retryDelay: 0, gcTime: 0 } },
  });
}

/** Render the guard and wait for the session query to settle. */
async function renderGuard(options: Parameters<typeof useAuthGuard>[0]) {
  const view = renderHook(() => useAuthGuard(options), {
    wrapper: wrapperFor(newClient()),
  });
  await waitFor(() => expect(view.result.current.isLoading).toBe(false));
  return view;
}

beforeEach(() => {
  mockReplace.mockReset();
  mockApiFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSession — auth failures are not errors", () => {
  /**
   * The guard can only tell "signed out" from "the request blew up" if
   * `useSession` maps both auth statuses to a null user. `useAuthGuard` does not
   * re-expose `error`, so this has to be asserted one level down: `user` and
   * `isAuthenticated` look identical in both cases, and only `error` separates
   * a clean signed-out state from a query stuck in its error state (which
   * callers render as a crash screen instead of a login redirect).
   */
  it.each([401, 403])("maps a %i to a signed-out session, not a query error", async (status) => {
    mockApiFetch.mockRejectedValue(new ApiError(status, "AUTH", "không có quyền"));

    const { result } = renderHook(() => useSession(), { wrapper: wrapperFor(newClient()) });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.error).toBeNull();
    expect(result.current.user).toBeNull();
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.role).toBeNull();
  });

  it("lets a genuine server error surface instead of faking a signed-out session", async () => {
    mockApiFetch.mockRejectedValue(new ApiError(500, "INTERNAL", "lỗi máy chủ"));

    const { result } = renderHook(() => useSession(), { wrapper: wrapperFor(newClient()) });
    await waitFor(() => expect(result.current.error).not.toBeNull());

    // A 5xx is not evidence that the visitor is signed out. Swallowing it would
    // log people out on a backend hiccup.
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });
});

describe("useAuthGuard — unauthenticated", () => {
  it("sends an unauthenticated visitor to /login when the surface requires auth", async () => {
    mockApiFetch.mockRejectedValue(new ApiError(401, "UNAUTHENTICATED", "chưa đăng nhập"));

    const { result } = await renderGuard({ requireAuth: true, allowRoles: HR_ONLY });

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.role).toBeNull();
    expect(mockReplace).toHaveBeenCalledWith("/login");
  });

  it("does not redirect an unauthenticated visitor off a public surface", async () => {
    mockApiFetch.mockRejectedValue(new ApiError(401, "UNAUTHENTICATED", "chưa đăng nhập"));

    await renderGuard({ redirectIfAuthenticated: true });

    expect(mockReplace).not.toHaveBeenCalled();
  });
});

describe("useAuthGuard — role routing", () => {
  it("lets a role through the surface it owns", async () => {
    mockApiFetch.mockResolvedValue(meResponse("hr"));

    const { result } = await renderGuard({ requireAuth: true, allowRoles: HR_ONLY });

    expect(result.current.role).toBe("hr");
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("bounces a system admin off an HR surface to the system admin home", async () => {
    mockApiFetch.mockResolvedValue(meResponse("system_admin"));

    await renderGuard({ requireAuth: true, allowRoles: HR_ONLY });

    // Not /login, and not silently allowed through: their own console.
    expect(mockReplace).toHaveBeenCalledWith("/settings");
  });

  it("bounces HR off the system admin console to the HR dashboard", async () => {
    mockApiFetch.mockResolvedValue(meResponse("hr"));

    await renderGuard({ requireAuth: true, allowRoles: SYSTEM_ADMIN_ONLY });

    expect(mockReplace).toHaveBeenCalledWith("/dashboard");
  });

  it("bounces an employee off a staff surface to employee self-service", async () => {
    mockApiFetch.mockResolvedValue(meResponse("user"));

    await renderGuard({ requireAuth: true, allowRoles: STAFF });

    expect(mockReplace).toHaveBeenCalledWith("/employee");
  });

  it("admits both staff roles to a surface that names them both", async () => {
    for (const role of ["hr", "system_admin"] as const) {
      mockReplace.mockReset();
      mockApiFetch.mockResolvedValue(meResponse(role));

      const { unmount } = await renderGuard({ requireAuth: true, allowRoles: STAFF });

      expect(mockReplace, `${role} was bounced off a staff surface`).not.toHaveBeenCalled();
      // Unmount inside the loop: `cleanup()` only runs in `afterEach`, so
      // without this the first iteration's hook is still mounted and its
      // effects can fire against the second iteration's assertions.
      unmount();
    }
  });

  it("sends an already signed-in user away from a public surface to their own home", async () => {
    mockApiFetch.mockResolvedValue(meResponse("system_admin"));

    await renderGuard({ redirectIfAuthenticated: true });

    expect(mockReplace).toHaveBeenCalledWith("/settings");
  });
});

describe("useAuthGuard — broken session", () => {
  it("does not guess a home for a role this build does not know", async () => {
    // BE drifted ahead of the FE (or the payload was tampered with). There is
    // no correct landing page, so the guard must not invent one.
    mockApiFetch.mockResolvedValue(meResponse("admin"));

    const { result } = await renderGuard({ requireAuth: true, allowRoles: HR_ONLY });

    expect(result.current.role).toBeNull();
    expect(mockReplace).toHaveBeenCalledWith("/login");
    expect(mockReplace).toHaveBeenCalledTimes(1);
  });

  it("treats a null role the same as an unknown one", async () => {
    mockApiFetch.mockResolvedValue(meResponse(null));

    const { result } = await renderGuard({ requireAuth: true, allowRoles: HR_ONLY });

    expect(result.current.role).toBeNull();
    expect(mockReplace).toHaveBeenCalledWith("/login");
  });

  it("treats a missing role field the same as an unknown one", async () => {
    mockApiFetch.mockResolvedValue(meResponse(undefined));

    await renderGuard({ requireAuth: true, allowRoles: HR_ONLY });

    expect(mockReplace).toHaveBeenCalledWith("/login");
  });

  it("does not send a broken session to a home page even with no allowRoles", async () => {
    // `redirectIfAuthenticated` is the one path that could route on a role it
    // never validated. With no usable role there is nowhere to go.
    mockApiFetch.mockResolvedValue(meResponse("admin"));

    await renderGuard({ redirectIfAuthenticated: true });

    expect(mockReplace).not.toHaveBeenCalled();
  });
});

describe("useAuthGuard — before the session resolves", () => {
  it("redirects nobody while /api/auth/me is still in flight", async () => {
    // Redirecting on a not-yet-known role would log every user out on a slow
    // network.
    mockApiFetch.mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(
      () => useAuthGuard({ requireAuth: true, allowRoles: HR_ONLY }),
      { wrapper: wrapperFor(newClient()) },
    );

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(result.current.isLoading).toBe(true);
    expect(mockReplace).not.toHaveBeenCalled();
  });
});
