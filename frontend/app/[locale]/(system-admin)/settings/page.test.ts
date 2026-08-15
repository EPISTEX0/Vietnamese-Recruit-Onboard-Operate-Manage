import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect, vi } from 'vitest';

import { buildSetupGuide, SETUP_TASK_IDS } from '@/lib/system-admin/setup-guide';

/**
 * Guards the seam that nothing else in the suite touches: `/settings` has to
 * resolve to *something*, and everywhere it can send you has to exist too.
 *
 * `homePathForRole('system_admin') === '/settings'` is asserted three times
 * (`lib/auth/roles.test.ts`, `lib/auth/session.test.ts` twice), but all three
 * assert the *string*. Delete `page.tsx` and they stay green — so does
 * `next build`, because a missing page is simply a route that is not emitted,
 * not an error. Every system admin would then land on a 404 at login with no
 * gate saying a word.
 *
 * So this file *imports* the page module rather than reading it off disk: if
 * the module stops existing, resolution fails and the suite goes red, which is
 * the whole point of it being here. Proven by mutation in both directions, not
 * by assertion.
 *
 * This replaces the redirect assertions #301 put here. `/settings` no longer
 * redirects to `/settings/ai` — it renders Tổng quan hệ thống — so those died
 * on purpose. The property they protected did not: the second describe below
 * carries it over from "the redirect target exists" to "every destination the
 * homepage can navigate to exists", which is the same guarantee against a 404
 * one hop later.
 *
 * Not a component test. It never renders a React tree; it checks that a module
 * resolves and that the routes its view-model names are real.
 */

// `createNavigation(routing)` runs at import time and reaches for
// `next/navigation`, which resolves only inside the Next build. Stubbing the
// module keeps the import of `./page` itself real — the page still has to
// exist, parse, and resolve everything else it pulls in, which is the property
// this file is here to hold.
vi.mock('@/i18n/navigation', () => ({
  Link: () => null,
  redirect: vi.fn(),
  usePathname: vi.fn(),
  useRouter: vi.fn(),
  getPathname: vi.fn(),
}));

import SystemOverviewPage from './page';

// `import.meta.dirname`, not `fileURLToPath(import.meta.url)`: under the
// suite's jsdom environment `import.meta.url` is an http:// URL, and
// `fileURLToPath` rejects it.
const SETTINGS_DIR = import.meta.dirname;

/** `/settings/ai` -> `<settings>/ai/page.tsx`. */
function routeFileFor(href: string): string {
  const segments = href.split('/').filter(Boolean);
  if (segments[0] !== 'settings') {
    throw new Error(`"${href}" is not under the console's base path`);
  }
  return join(SETTINGS_DIR, ...segments.slice(1), 'page.tsx');
}

describe('/settings index route', () => {
  it('resolves to a page module', () => {
    expect(typeof SystemOverviewPage).toBe('function');
  });
});

describe('where the Quick-Start Guide can send the admin', () => {
  // Actions do not depend on how the queries resolved, so any set of sources
  // yields the full list of destinations.
  const guide = buildSetupGuide({
    oauthConfig: { status: 'pending' },
    aiConfiguration: { status: 'pending' },
    users: { status: 'pending' },
  });
  const destinations = guide.tasks
    .map((task) => task.action?.href)
    .filter((href): href is string => href != null);

  it('names at least one destination', () => {
    // Without this the loop below passes vacuously the day every task loses
    // its action — and a checklist that navigates nowhere ships unnoticed.
    expect(destinations.length).toBeGreaterThan(0);
  });

  it.each(destinations)('has a real route behind %s', (href) => {
    // The redirect check this replaces existed so the admin would not hit a 404
    // one hop after landing. Same hazard here: a task row is a link, and a link
    // to an unemitted route is a 404 the build will not complain about.
    expect(existsSync(routeFileFor(href))).toBe(true);
  });

  it('sends the Google OAuth task to the section that configures OAuth', () => {
    // Pinned at the route level, not just in the pure module, because the
    // hazard this task carries is a specific one: every other console section
    // handles something adjacent to OAuth without being able to configure it —
    // `/settings/domains` is the allowed email-domain list, `/settings/users`
    // is accounts. The existence check above would happily pass on either,
    // while telling a freshly-installed admin to go configure OAuth somewhere
    // that cannot. Only `/settings/oauth` is the right answer (#307).
    const oauth = guide.tasks.find((task) => task.id === 'googleOAuth');

    expect(SETUP_TASK_IDS).toContain('googleOAuth');
    expect(oauth?.action).toEqual({ href: '/settings/oauth' });
  });
});
