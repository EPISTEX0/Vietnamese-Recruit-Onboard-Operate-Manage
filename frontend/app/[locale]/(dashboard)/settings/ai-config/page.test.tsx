/**
 * @vitest-environment jsdom
 *
 * HR's AI configuration page (#420) — moved off System Admin's
 * `(system-admin)/settings/ai` (see that page's own test file, which used to
 * cover this same #414 state-pill contract before the capability toggles
 * moved here).
 *
 * Written as renders driving the real chain — component → `lib/api/hr-ai-config`
 * → `fetch` → React Query — with only `fetch` faked, matching the System Admin
 * console's own convention (`../oauth/page.test.tsx` there).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import messages from '@/messages/vi.json';
import { formats } from '@/i18n/request';

import HRAIConfigPage from './page';

const t = messages.settings;

/** Everything the page needs from a well-formed HR configuration response. */
const BASE_CONFIG = {
  provider_configured: true,
  updated_at: null as string | null,
  data_policy_accepted: true,
  data_policy_accepted_at: '2026-01-01T00:00:00Z' as string | null,
  data_policy_version: '1' as string | null,
  automation_enabled: false,
  automation_state: 'not_configured',
  assistant_enabled: false,
  assistant_state: 'not_configured',
  ai_automation_consent: true,
  ai_assistant_consent: true,
  ai_policy_preset: 'balanced',
  ai_policy_preset_version: '1',
};

/** A capability toggle is on but its credential can't be resolved — the case
 *  `_compute_state` reports as `unavailable` (mirrors #414's fixture). */
const AUTOMATION_UNAVAILABLE = {
  ...BASE_CONFIG,
  automation_enabled: true,
  automation_state: 'unavailable',
};

const NOT_ACCEPTED = {
  ...BASE_CONFIG,
  data_policy_accepted: false,
  data_policy_accepted_at: null,
  data_policy_version: null,
};

const json = (payload: unknown) =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

function stubFetch(options: {
  config?: typeof BASE_CONFIG;
  connected?: boolean;
  policy?: { version: string; items: unknown[] };
}) {
  const config = options.config ?? BASE_CONFIG;
  const connected = options.connected ?? true;
  const policy = options.policy ?? { version: '1', items: [{ category: 'CV', data_types: 'text', purpose: 'sàng lọc', retention: '1 năm' }] };

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.endsWith('/provider-status')) return json({ connected });
      if (url.endsWith('/data-policy')) return json(policy);
      return json(config);
    }),
  );
}

let queryClient: QueryClient;

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="vi" messages={messages} formats={formats}>
        <HRAIConfigPage />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('provider status card is credential-free (#420)', () => {
  it('shows the connected badge and no "contact System Admin" message when a provider is connected', async () => {
    stubFetch({ connected: true });
    renderPage();

    await screen.findByText(t.providerStatusTitle);
    expect(screen.getByText(t.connected)).toBeTruthy();
    expect(screen.queryByText(t.contactSystemAdminForAI)).toBeNull();
  });

  it('shows "contact System Admin" when no provider is connected', async () => {
    stubFetch({ connected: false });
    renderPage();

    await screen.findByText(t.providerStatusTitle);
    expect(screen.getByText(t.notConnected)).toBeTruthy();
    expect(screen.getByText(t.contactSystemAdminForAI)).toBeTruthy();
  });
});

describe('data policy section (#420)', () => {
  it('hides the data-policy section once already accepted', async () => {
    stubFetch({ config: BASE_CONFIG });
    renderPage();

    await screen.findByText(t.providerStatusTitle);
    expect(screen.queryByText(t.dataPolicyRequired)).toBeNull();
  });

  it('shows the data-policy section and its acceptance button when not yet accepted', async () => {
    stubFetch({ config: NOT_ACCEPTED });
    renderPage();

    expect(await screen.findByText(t.dataPolicyRequired)).toBeTruthy();
    expect(screen.getByText(t.acceptAndActivate)).toBeTruthy();
  });
});

/**
 * `stateLabel()` maps `automation_state`/`assistant_state` — sourced from
 * backend `AICapabilityState` — to a pill label. #414's original test lived
 * on the System Admin page before the toggle switches moved here; this is
 * the same contract, same fixture.
 */
describe('capability state pill covers every backend AICapabilityState (#414)', () => {
  it('shows the translated label, not the raw state string, when a capability is unavailable', async () => {
    stubFetch({ config: AUTOMATION_UNAVAILABLE });
    renderPage();

    const titleEl = await screen.findByText(t.featureEmailClassify);
    const pillContainer = titleEl.parentElement as HTMLElement;
    expect(within(pillContainer).getByText(t.unavailable)).toBeTruthy();
    expect(screen.queryByText('unavailable')).toBeNull();
  });
});
