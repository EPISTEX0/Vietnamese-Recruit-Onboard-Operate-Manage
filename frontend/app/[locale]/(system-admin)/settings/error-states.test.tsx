/**
 * @vitest-environment jsdom
 *
 * The three console list routes must tell "empty" and "broken" apart.
 *
 * These are the surfaces where the two states carry opposite meanings. Two of
 * them — Người dùng & Vai trò, Tên miền Workspace được phép nối — describe who
 * may reach the deployment or connect the shared Google account. Drawing
 * "Danh sách trống" over a failed `GET /users` tells the admin nobody holds
 * access, when the truth is the system could not say who does; the natural
 * next move is to grant access that already exists, or to conclude the
 * allowlist was wiped. #305 exists for that single sentence.
 *
 * Written as renders rather than as assertions about a helper: the defect being
 * fixed is not a wrong branch, it is a *missing* one. A unit test over some
 * `resolveListState()` would stay green the day a page stops calling it, which
 * is exactly the regression that has to fail here. So each case drives the real
 * chain — component → `lib/api/admin` → `fetch` → React Query — and only
 * `fetch` is faked.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import {
  onlineManager,
  QueryClient,
  QueryClientProvider,
  type QueryKey,
} from '@tanstack/react-query';

import messages from '@/messages/vi.json';
import { formats } from '@/i18n/request';

// `useSession` only decides which row wears the "bạn" badge; it has nothing to
// do with the three states under test, and left real it would fire its own
// query into the stubbed `fetch` and drag its retry policy into every case.
vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ user: null }),
}));

const { default: UsersRolesPage } = await import('./users/page');
const { default: EmailDomainsPage } = await import('./domains/page');
const { default: AuditLogPage } = await import('./audit/page');

const t = messages.settings;

/** The sentence the backend puts in a 500 body, echoed by `apiErrorText`. */
const FAILURE_DETAIL = 'Lỗi máy chủ khi đọc dữ liệu';

type Surface = {
  /** Route folder, so a failure names the page. */
  name: string;
  Page: () => ReactNode;
  queryKey: QueryKey;
  /** A 200 body with no records — the genuinely-empty state. */
  emptyPayload: unknown;
  /** A 200 body carrying `staleMarker` — the last good answer React Query keeps. */
  populatedPayload: unknown;
  /**
   * What the page says when the list is genuinely empty. This exact string must
   * never appear while the query is in error.
   */
  emptyText: string;
  /** Row content only the populated payload can produce. */
  staleMarker: string;
};

const SURFACES: Surface[] = [
  {
    name: 'users',
    Page: UsersRolesPage,
    queryKey: ['admin-users'],
    emptyPayload: [],
    populatedPayload: [{
      id: 'u1',
      email: 'admin@vroom.test',
      name: 'Quản trị viên cũ',
      avatar_url: null,
      role: 'system_admin',
      is_active: true,
      created_at: '2026-01-02T03:04:05Z',
      last_login: '2026-01-02T03:04:05Z',
    }],
    emptyText: t.noUsers,
    // The name, not the email: the email shares its `<p>` with the "· Đã tạo
    // …" suffix, and an exact matcher would never fire on it — leaving the
    // stale-data case below silently inert on this surface.
    staleMarker: 'Quản trị viên cũ',
  },
  {
    name: 'domains',
    Page: EmailDomainsPage,
    queryKey: ['org-domains'],
    emptyPayload: { allowed_domains: [] },
    populatedPayload: { allowed_domains: ['vroom.test'] },
    emptyText: t.noDomains,
    staleMarker: '@vroom.test',
  },
  {
    name: 'audit',
    Page: AuditLogPage,
    // Mirrors the page's first render: `{ page, page_size }` with no filters.
    queryKey: ['audit-logs', { page: 1, page_size: 15 }],
    emptyPayload: { items: [], total: 0, page: 1, page_size: 15 },
    populatedPayload: {
      items: [{
        id: 'a1',
        admin_email: 'nguoi-sua@vroom.test',
        action_type: 'whitelist_add',
        details: {},
        created_at: '2026-01-02T03:04:05Z',
      }],
      total: 1,
      page: 1,
      page_size: 15,
    },
    emptyText: t.noActivityYet,
    staleMarker: 'nguoi-sua@vroom.test',
  },
];

/** A `fetch` that answers every call the same way. */
function stubFetch(respond: () => Response) {
  const fake = vi.fn(async () => respond());
  vi.stubGlobal('fetch', fake);
  return fake;
}

const ok = (payload: unknown) => () =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

const serverError = () => () =>
  new Response(JSON.stringify({ detail: { message: FAILURE_DETAIL } }), {
    status: 500,
    headers: { 'Content-Type': 'application/json' },
  });

let queryClient: QueryClient;

function renderPage(Page: () => ReactNode) {
  return render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="vi" messages={messages} formats={formats}>
        <Page />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient = new QueryClient({
    // A retrying query would leave the page on its loading branch for the whole
    // test and every assertion below would time out rather than fail honestly.
    defaultOptions: { queries: { retry: false } },
  });
});

afterEach(() => {
  // `onlineManager` is module-global, so a case that goes offline would leave
  // every later query paused and every later assertion timing out.
  onlineManager.setOnline(true);
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe.each(SURFACES)('/settings/$name', (surface) => {
  it('shows the retryable error box, never the empty-state sentence, when the query fails', async () => {
    stubFetch(serverError());

    renderPage(surface.Page);

    // `ErrorBox` is the console's only error affordance carrying a retry
    // button (ADR-0014), so the button standing next to the reason is what
    // distinguishes it from the mutation-error strips these pages also draw.
    expect(await screen.findByText(FAILURE_DETAIL)).toBeTruthy();
    expect(screen.getByRole('button', { name: t.retry })).toBeTruthy();
    expect(screen.queryByText(surface.emptyText)).toBeNull();
  });

  it('still shows the empty-state sentence when the query succeeds with no records', async () => {
    stubFetch(ok(surface.emptyPayload));

    renderPage(surface.Page);

    // The other direction of the fix: turning every empty list into an error
    // would be the same defect wearing the opposite sign.
    expect(await screen.findByText(surface.emptyText)).toBeTruthy();
    expect(screen.queryByRole('button', { name: t.retry })).toBeNull();
  });

  it('does not claim the list is empty while the query is paused offline', async () => {
    // The third way `data` comes back undefined, and the one `isLoading` misses:
    // React Query pauses rather than fires when the browser is offline, leaving
    // `status: 'pending'` with `fetchStatus: 'paused'`. `isLoading` is
    // `isPending && isFetching`, so it reads false — and a branch keyed on it
    // falls straight past both the loading and the error arm into "trống",
    // which is the very sentence #305 exists to prevent. `settings/page.tsx`
    // hit this first and its `StatCard` docstring names it.
    onlineManager.setOnline(false);
    const fetchSpy = stubFetch(ok(surface.emptyPayload));

    renderPage(surface.Page);

    await waitFor(() => {
      expect(screen.queryByText(surface.emptyText)).toBeNull();
    });
    // Guards the assertion above against passing for the wrong reason: it must
    // be the paused branch being read, not a request that quietly succeeded.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('replaces the last good answer with the error box when a refetch fails', async () => {
    // React Query keeps `data` alongside `error` when a background refetch
    // fails. A branch that reads `data` before `error` therefore keeps drawing
    // rows that are no longer known to be true — on an access-control surface,
    // a list of who *used to* hold access presented as who holds it now.
    // `updatedAt` in the past marks the seeded answer stale so mounting
    // refetches; `audit` sets `staleTime: 30_000` and would not otherwise.
    queryClient.setQueryData(surface.queryKey, surface.populatedPayload, { updatedAt: 1 });
    stubFetch(serverError());

    renderPage(surface.Page);

    expect(await screen.findByText(FAILURE_DETAIL)).toBeTruthy();
    await waitFor(() => {
      expect(screen.queryByText(surface.staleMarker)).toBeNull();
    });
    expect(screen.queryByText(surface.emptyText)).toBeNull();
  });
});
