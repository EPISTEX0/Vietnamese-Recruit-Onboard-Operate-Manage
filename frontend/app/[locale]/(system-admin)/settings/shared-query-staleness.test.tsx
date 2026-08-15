/**
 * @vitest-environment jsdom
 *
 * Two console surfaces reading the same `queryKey` must agree on how long that
 * answer stays fresh.
 *
 * `staleTime` in React Query v5 is per *observer*, not per key. Two components
 * on `['admin-users']` therefore share one cache entry while each keeps its own
 * opinion about whether that entry is stale, and the shorter opinion wins on
 * mount: it refetches on top of an answer the other surface just put there.
 * Nothing fails and nothing warns; the only symptom is that the number of
 * requests depends on which page the admin opened first (#310).
 *
 * The divergence is not "declared vs zero" — `lib/query-client.ts` gives the
 * whole app a 5-minute default, so a console surface that declares nothing gets
 * *five minutes* while its twin on `/settings` says thirty seconds. #310 reads
 * it the other way round and calls the undeclared surface `staleTime: 0`; that
 * is wrong, and it matters, because it means the page that refetches too eagerly
 * is `/settings` rather than the section — the same defect pointing the other
 * way. Which is exactly why this file mounts the real `createQueryClient()`
 * rather than a bare `new QueryClient()`: against a bare client every
 * undeclared surface looks like `0` and the test would police a failure mode
 * production does not have.
 *
 * Written as renders rather than as an assertion about an options object,
 * because the options object is not the thing that has to hold: a test reading
 * `staleTime` off a literal would stay green the day a page stops handing it to
 * `useQuery`, and would say nothing about what the surface does to the network.
 * So each case drives component → `lib/api/admin` → `fetch` → React Query, with
 * only `fetch` faked.
 *
 * Each case asserts both directions, and both are load-bearing against a
 * different mistake. The stale half fails when a surface's window is *longer*
 * than the shared one (the live defect: an inherited five minutes). The fresh
 * half fails when it is *shorter* (a surface that declares `0`, or the app
 * default being lowered underneath the console). Together they pin the window
 * rather than one side of it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, cleanup } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { QueryClientProvider, type QueryClient, type QueryKey } from '@tanstack/react-query';

import messages from '@/messages/vi.json';
import { createQueryClient } from '@/lib/query-client';

// `createNavigation(routing)` reaches for `next/navigation` at import time,
// which resolves only inside the Next build. Same stub as `page.test.ts`.
vi.mock('@/i18n/navigation', () => ({
  Link: ({ children }: { children?: ReactNode }) => <>{children}</>,
  redirect: vi.fn(),
  usePathname: vi.fn(),
  useRouter: vi.fn(),
  getPathname: vi.fn(),
}));

// Only decides which roster row wears the "bạn" badge. Left real it would fire
// a query of its own into the stubbed `fetch`.
vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ user: null }),
}));

const { default: SystemOverviewPage } = await import('./page');
const { default: UsersRolesPage } = await import('./users/page');
const { default: AIConfigPage } = await import('./ai/page');
const { default: SystemHealthPage } = await import('./health/page');
const { default: OAuthConfigPage } = await import('./oauth/page');

/** One cache entry, and every surface that observes it. */
type SharedQuery = {
  key: QueryKey;
  /** The endpoint path this key is fetched from, to tell its requests apart. */
  path: string;
  /** A last-good answer, shaped as the endpoint returns it. */
  seed: unknown;
  surfaces: { name: string; Page: () => ReactNode }[];
};

const SHARED_QUERIES: SharedQuery[] = [
  {
    key: ['admin-users'],
    path: '/api/system-admin/users',
    seed: [{
      id: 'u1',
      email: 'admin@vroom.test',
      name: 'Quản trị viên',
      avatar_url: null,
      role: 'system_admin',
      is_active: true,
      created_at: '2026-01-02T03:04:05Z',
      last_login: '2026-01-02T03:04:05Z',
    }],
    surfaces: [
      { name: '/settings', Page: SystemOverviewPage },
      { name: '/settings/users', Page: UsersRolesPage },
    ],
  },
  {
    key: ['ai-config'],
    path: '/api/system-admin/organization/ai-config',
    // `data_policy_accepted` keeps `/settings/ai`'s second query disabled, so
    // the only request either surface can make for this key is the one counted.
    seed: {
      provider: 'openai',
      base_url: null,
      model: 'gpt-4o',
      api_key_masked: 'sk-…abcd',
      configured: true,
      updated_at: '2026-01-02T03:04:05Z',
      credential_source: 'organization',
      deployment_key_available: false,
      data_policy_accepted: true,
      data_policy_accepted_at: '2026-01-02T03:04:05Z',
      data_policy_version: '1',
      automation_enabled: false,
      automation_state: 'disabled',
      assistant_enabled: false,
      assistant_state: 'disabled',
      classification_policy: 'default',
      classification_policy_version: '1',
      stable_classifier_version: '1',
      candidate_classifier_version: null,
      candidate_classification_policy: null,
    },
    surfaces: [
      { name: '/settings', Page: SystemOverviewPage },
      { name: '/settings/ai', Page: AIConfigPage },
    ],
  },
  // The two below agree today, and only because #302 and #307 said so by hand.
  // They are here for the same reason the fixed pair is: nothing but this table
  // notices when one of them stops agreeing.
  {
    key: ['runtime-health'],
    path: '/api/system-admin/runtime/health',
    seed: {
      status: 'healthy',
      services: [{ name: 'database', status: 'healthy', latency_ms: 3, detail: null }],
    },
    surfaces: [
      { name: '/settings', Page: SystemOverviewPage },
      { name: '/settings/health', Page: SystemHealthPage },
    ],
  },
  {
    key: ['oauth-config'],
    path: '/api/system-admin/oauth/config',
    seed: {
      client_id: '1234.apps.googleusercontent.com',
      client_secret_masked: '****cd12',
      redirect_uri: 'https://vroom.test/api/auth/callback',
      updated_at: '2026-01-02T03:04:05Z',
      source: 'database',
    },
    surfaces: [
      { name: '/settings', Page: SystemOverviewPage },
      { name: '/settings/oauth', Page: OAuthConfigPage },
    ],
  },
];

/**
 * The freshness window every console surface on a shared key honours, in ms.
 *
 * Stated here rather than read off the pages, so "the two agree" cannot be
 * satisfied by both drifting to some other number together — including to the
 * app-wide default in `lib/query-client.ts`, which is ten times longer and is
 * what a surface silently gets when it declares nothing.
 */
const CONSOLE_STALE_TIME = 30_000;

let fetchSpy: ReturnType<typeof vi.fn>;
let queryClient: QueryClient | undefined;

beforeEach(() => {
  // Every *other* query on the page fails, which keeps the render deterministic
  // without having to satisfy five response schemas. The query under test never
  // reaches `fetch` in the fresh half, so its body would go unread anyway.
  fetchSpy = vi.fn(async () => new Response('{}', { status: 500 }));
  vi.stubGlobal('fetch', fetchSpy);
});

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient?.clear();
  queryClient = undefined;
});

/**
 * Mount `Page` over a cache entry written `ageMs` ago and count the requests it
 * made for that entry.
 *
 * The client is the app's own, because its `staleTime` default is half of what
 * is under test — a surface that declares nothing inherits it, and that is the
 * defect. Only `retry` is overridden: the real policy would keep the *other*
 * queries on the page firing after the count is taken, which is a property of
 * the harness rather than of the console.
 *
 * Synchronous on purpose: React Query starts a mount refetch inside the
 * subscribe effect and RTL's `render` flushes effects before returning, so the
 * request is already recorded here. "No request" therefore needs no waiting to
 * be a real observation rather than a snapshot of the first tick.
 */
function requestsOnMount(Page: () => ReactNode, shared: SharedQuery, ageMs: number): number {
  queryClient = createQueryClient();
  queryClient.setQueryDefaults([], { retry: false });
  queryClient.setQueryData(shared.key, shared.seed, { updatedAt: Date.now() - ageMs });

  fetchSpy.mockClear();
  render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="vi" messages={messages}>
        <Page />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );

  const count = fetchSpy.mock.calls
    .filter(([url]) => String(url).split('?')[0].endsWith(shared.path))
    .length;

  cleanup();
  queryClient.clear();
  return count;
}

describe.each(SHARED_QUERIES)('surfaces sharing $key', (shared) => {
  it.each(shared.surfaces)('$name honours the console freshness window', ({ Page }) => {
    // Inside the window: the answer the other surface just fetched is good
    // enough, and nothing goes back out.
    expect(requestsOnMount(Page, shared, CONSOLE_STALE_TIME / 2)).toBe(0);

    // Past it: the surface does read this key, which is what makes the count
    // above mean "declined to refetch" rather than "never asked". This is also
    // the half a surface fails by inheriting the app's five-minute default.
    expect(requestsOnMount(Page, shared, CONSOLE_STALE_TIME * 2)).toBe(1);
  });
});
