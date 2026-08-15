import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect, beforeEach, vi } from 'vitest';

/**
 * Guards the seam that nothing else in the suite touches: `/settings` has to
 * resolve to *something*.
 *
 * `homePathForRole('system_admin') === '/settings'` is asserted three times
 * (`lib/auth/roles.test.ts`, `lib/auth/session.test.ts` twice), but all three
 * assert the *string*. Delete `page.tsx` and they stay green — so does
 * `next build`, because a missing page is simply a route that is not emitted,
 * not an error. Every system admin would then land on a 404 at login with no
 * gate saying a word.
 *
 * So this file imports the page module rather than reading it: if the module
 * stops existing, resolution fails and the suite goes red, which is the whole
 * point of it being here.
 */

const mocks = vi.hoisted(() => ({
  redirect: vi.fn(),
  getLocale: vi.fn(),
}));

// The real `redirect` comes out of `createNavigation(routing)` and throws
// NEXT_REDIRECT inside a request context this test has no business standing up.
// Mocking it keeps the assertion on the argument, which is the part that has to
// stay right: next-intl's locale-aware signature, not `next/navigation`'s.
vi.mock('@/i18n/navigation', () => ({ redirect: mocks.redirect }));
vi.mock('next-intl/server', () => ({ getLocale: mocks.getLocale }));

import SettingsIndexPage from './page';

// `import.meta.dirname`, not `fileURLToPath(import.meta.url)`: under the
// suite's jsdom environment `import.meta.url` is an http:// URL, and
// `fileURLToPath` rejects it.
const SETTINGS_DIR = import.meta.dirname;

describe('/settings index route', () => {
  beforeEach(() => {
    mocks.redirect.mockReset();
    mocks.getLocale.mockReset();
  });

  it('redirects to the AI configuration section, carrying the active locale', async () => {
    mocks.getLocale.mockResolvedValue('vi');

    await SettingsIndexPage();

    expect(mocks.redirect).toHaveBeenCalledWith({ href: '/settings/ai', locale: 'vi' });
  });

  it('carries whichever locale is active, not a hardcoded one', async () => {
    mocks.getLocale.mockResolvedValue('en');

    await SettingsIndexPage();

    expect(mocks.redirect).toHaveBeenCalledWith({ href: '/settings/ai', locale: 'en' });
  });

  it('points at a section route that exists', () => {
    // Without this, the test above passes just as happily when `/settings/ai`
    // is gone — and the admin lands on a 404 one hop later instead of zero.
    expect(existsSync(join(SETTINGS_DIR, 'ai', 'page.tsx'))).toBe(true);
  });
});
