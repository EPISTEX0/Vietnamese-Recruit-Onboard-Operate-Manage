'use client';

import { useQuery } from "@tanstack/react-query";
import type { CurrentUser } from "@/lib/api/auth";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/types";
import { homePathForRole, isUserRole, type UserRole } from "@/lib/auth/roles";

/**
 * Fetch current user profile from BE via GET /api/auth/me.
 *
 * IMPORTANT (BUG-1 fix): BE trả về **flat `UserResponse`** (`{id, email, name,
 * role, must_change_password, gmail_grant_valid, calendar_grant_valid,
 * employee_id, …}`) — KHÔNG wrap trong `user`. Endpoint này khác với
 * `/login`, `/setup`, `/change-password` (các endpoint đó trả
 * `AuthSessionResponse = { user, must_change_password, setup_complete }`,
 * được `lib/api/auth.ts` giữ nguyên).
 *
 * Trả về `CurrentUser` flat khi 200; trả về `null` khi 401/403 (chưa authed).
 * Các lỗi khác (mạng, 5xx) đẩy lên React Query để retry.
 */
async function fetchCurrentUser(): Promise<CurrentUser | null> {
  try {
    return await apiFetch<CurrentUser>("/api/auth/me");
  } catch (error) {
    if (error instanceof ApiError && (error.statusCode === 401 || error.statusCode === 403)) {
      return null;
    }
    throw error;
  }
}

/**
 * React Query hook that fetches and caches the current user session.
 *
 * - Trả về `user` (CurrentUser flat, khớp BE `/api/auth/me`), `role`,
 *   `isLoading`, `isAuthenticated`, `mustChangePassword`, `setupComplete`.
 * - Khi `/api/auth/me` 401/403 → `user` = null (unauthenticated).
 * - Refetch on window focus via React Query defaults.
 *
 * `isAdmin` cố tình KHÔNG còn tồn tại: nó gộp `system_admin` và `hr` — hai
 * role có quyền tách rời — thành một boolean. Call site cần quyền thì đọc
 * `role` và so với danh sách role mình cho phép (xem `useAuthGuard`).
 */
export function useSession() {
  const { data, isLoading, error, refetch } = useQuery<CurrentUser | null>({
    queryKey: ["session"],
    queryFn: fetchCurrentUser,
    retry: (failureCount, error) => {
      // Don't retry auth failures (đã được map thành null, nhưng phòng hờ
      // khi queryFn throw ApiError 401/403 do logic_gateway).
      if (error instanceof ApiError && (error.statusCode === 401 || error.statusCode === 403)) {
        return false;
      }
      return failureCount < 2;
    },
    staleTime: 30 * 1000,
  });

  // `data` là flat CurrentUser khi authed; `null` khi 401/403; `undefined`
  // khi đang loading hoặc lỗi tạm thời chưa retry xong.
  const user: CurrentUser | null = data ?? null;
  const isAuthenticated = !!data && !error;
  // Một role BE trả về mà FE không biết là dữ liệu không tin được — coi như
  // chưa có role thay vì đoán, để guard đẩy về /login chứ không cấp nhầm.
  const role: UserRole | null = isUserRole(user?.role) ? user.role : null;
  const mustChangePassword = user?.must_change_password ?? false;
  // /api/auth/me trả 200 ⇔ đã có user (và org) ⇒ setup hoàn tất.
  const setupComplete = !!data;

  return {
    user,
    role,
    isLoading,
    isAuthenticated,
    mustChangePassword,
    setupComplete,
    error,
    refetch,
  };
}

/**
 * Higher-level hook that redirects based on auth state and role.
 * Use in page components and route-group layouts that need auth guards.
 *
 * Options:
 * - requireAuth: redirect to /login if not authenticated
 * - allowRoles: the roles this surface belongs to. A signed-in user holding
 *   any other role is sent to their own home (`homePathForRole`) — never
 *   silently allowed through. The list is explicit at every call site so a
 *   surface can't inherit access it never asked for.
 * - redirectIfAuthenticated: send an already-signed-in user to their home
 *
 * `allowRoles` is passed as a module-level constant at every call site (not an
 * inline literal), because the array lands in the effect's dependency list and
 * a fresh literal on each render would re-run the redirect effect every time.
 */
export function useAuthGuard(options: {
  requireAuth?: boolean;
  allowRoles?: readonly UserRole[];
  redirectIfAuthenticated?: boolean;
} = {}) {
  const { user, role, isLoading, isAuthenticated } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;

    if (options.redirectIfAuthenticated && isAuthenticated && role) {
      router.replace(homePathForRole(role));
      return;
    }

    if (options.requireAuth && !isAuthenticated) {
      router.replace("/login");
      return;
    }

    if (options.allowRoles && isAuthenticated) {
      // Authenticated but the BE handed back a role this build doesn't know:
      // no home to send them to, so treat it as a broken session.
      if (!role) {
        router.replace("/login");
        return;
      }
      if (!options.allowRoles.includes(role)) {
        router.replace(homePathForRole(role));
        return;
      }
    }
  }, [
    isLoading,
    isAuthenticated,
    role,
    router,
    options.requireAuth,
    options.allowRoles,
    options.redirectIfAuthenticated,
  ]);

  return { user, role, isLoading, isAuthenticated };
}