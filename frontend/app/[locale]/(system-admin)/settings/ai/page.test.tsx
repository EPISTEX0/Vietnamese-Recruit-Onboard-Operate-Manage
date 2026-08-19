/**
 * @vitest-environment jsdom
 *
 * The API key field on this page collapses two states an admin must tell
 * apart: no key has ever been saved, versus a key is saved but the backend
 * cannot decrypt it (#394, the state #384 added `api_key_decrypt_failed`
 * for). Both leave `api_key_masked` null, so the input alone reads
 * identically in both — an admin who cannot tell them apart types a new key
 * over one that only needed the encryption key restored, losing it for good.
 *
 * Written as renders driving the real chain — component → `lib/api/admin` →
 * `fetch` → React Query — with only `fetch` faked, matching `../oauth/page.test.tsx`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import messages from '@/messages/vi.json';
import { formats } from '@/i18n/request';

import AIConfigPage from './page';

const t = messages.settings;

/** Everything the page needs from a well-formed configuration response. */
const BASE_CONFIG = {
  provider: 'openai',
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-4o',
  api_key_masked: null as string | null,
  api_key_decrypt_failed: false,
  configured: false,
  updated_at: null as string | null,
  credential_source: null as string | null,
  deployment_key_available: false,
  data_policy_accepted: true,
  data_policy_accepted_at: '2026-01-01T00:00:00Z',
  data_policy_version: '1',
  automation_enabled: false,
  automation_state: 'not_configured',
  assistant_enabled: false,
  assistant_state: 'not_configured',
  classification_policy: 'stable_only',
  classification_policy_version: '1',
  stable_classifier_version: '1',
  candidate_classifier_version: null,
  candidate_classification_policy: null,
  candidate_classification_policy_version: null,
  rollout_mode: 'stable' as const,
  canary_percentage: 0,
  ai_automation_consent: true,
  ai_assistant_consent: true,
  ai_policy_preset: 'balanced',
  ai_policy_preset_version: '1',
};

/** State 1: no key has ever been saved for this organization. */
const NOT_CONFIGURED = { ...BASE_CONFIG };

/** State 2: a key is saved and the backend can decrypt it. */
const KEY_WORKING = {
  ...BASE_CONFIG,
  api_key_masked: '****abcd',
  configured: true,
  credential_source: 'org_api_key',
};

/** State 3 — the bug: a key is saved but no longer decrypts, e.g. after the
 *  deployment's encryption key was rotated. */
const KEY_DECRYPT_FAILED = {
  ...BASE_CONFIG,
  api_key_masked: null,
  api_key_decrypt_failed: true,
  configured: true,
  credential_source: 'org_api_key',
};

const json = (payload: unknown) =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

function stubFetch(config: typeof BASE_CONFIG) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => json(config)),
  );
}

let queryClient: QueryClient;

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="vi" messages={messages} formats={formats}>
        <AIConfigPage />
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

describe('telling apart "no key yet" from "key present but undecryptable"', () => {
  it('shows no decrypt-failed warning when no key has ever been configured', async () => {
    stubFetch(NOT_CONFIGURED);
    renderPage();

    await screen.findByPlaceholderText(t.apiKeyPlaceholder);
    expect(screen.queryByText(t.apiKeyDecryptFailedTitle)).toBeNull();
  });

  it('shows no decrypt-failed warning when the saved key still decrypts', async () => {
    stubFetch(KEY_WORKING);
    renderPage();

    await screen.findByPlaceholderText(t.apiKeyMasked);
    expect(screen.queryByText(t.apiKeyDecryptFailedTitle)).toBeNull();
  });

  it('warns distinctly, naming the encryption-key cause, when the saved key fails to decrypt', async () => {
    stubFetch(KEY_DECRYPT_FAILED);
    renderPage();

    expect(await screen.findByText(t.apiKeyDecryptFailedTitle)).toBeTruthy();
    expect(screen.getByText(t.apiKeyDecryptFailedDesc)).toBeTruthy();
  });
});

/**
 * The connection badge (header of Section 1, and the "Trạng thái" line right
 * below it) used to be computed from `configured` alone — true the moment a
 * config row exists, even if its key can no longer be decrypted. That made it
 * contradict the amber decrypt-failed banner above (#411, same underlying gap
 * as #394: the backend already exposes the real state, this call site just
 * wasn't reading it).
 */
describe('connection badge reflects real state (#411)', () => {
  it('shows the gray "not connected" badge when no config exists', async () => {
    stubFetch(NOT_CONFIGURED);
    renderPage();

    await screen.findByPlaceholderText(t.apiKeyPlaceholder);
    expect(screen.getAllByText(t.notConnected).length).toBeGreaterThan(0);
    expect(screen.queryByText(t.connected)).toBeNull();
    expect(screen.queryByText(t.connectionUnavailable)).toBeNull();
  });

  it('shows the green "connected" badge when the saved key still decrypts', async () => {
    stubFetch(KEY_WORKING);
    renderPage();

    await screen.findByPlaceholderText(t.apiKeyMasked);
    expect(screen.getAllByText(t.connected).length).toBeGreaterThan(0);
    expect(screen.queryByText(t.notConnected)).toBeNull();
    expect(screen.queryByText(t.connectionUnavailable)).toBeNull();
  });

  it('does NOT show the green "connected" badge when the saved key fails to decrypt', async () => {
    stubFetch(KEY_DECRYPT_FAILED);
    renderPage();

    await screen.findByText(t.apiKeyDecryptFailedTitle);
    expect(screen.queryByText(t.connected)).toBeNull();
    expect(screen.getAllByText(t.connectionUnavailable).length).toBeGreaterThan(0);
  });

  it('shows the same badge label in both the header and the "Trạng thái" line', async () => {
    stubFetch(KEY_DECRYPT_FAILED);
    renderPage();

    await screen.findByText(t.apiKeyDecryptFailedTitle);
    // Both call sites of CONNECTION_STATUS.label in page.tsx: the Section 1
    // header badge, and the "Trạng thái: …" line below it.
    expect(screen.getAllByText(t.connectionUnavailable)).toHaveLength(2);
  });
});
