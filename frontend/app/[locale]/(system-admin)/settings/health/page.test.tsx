/**
 * @vitest-environment jsdom
 *
 * The classification rollout panel added to this page (#422) is the only
 * escape hatch for a bad AI classification rollout, and its telemetry is
 * built entirely from `*_recall_proxy` fields — measured from recent
 * operational logs, not the real recall ADR-0005's 98% guardrail is defined
 * against. Three defects this panel must not have, each cheap to introduce
 * by accident:
 *
 * 1. Drawing the proxy under the bare word "Recall" — a reader would take it
 *    for the real, eval-set recall and judge it against the 98% threshold.
 * 2. Hiding `sample_size` away from the number it qualifies — a 97% proxy
 *    over 3 emails and over 3,000 means something very different.
 * 3. Rendering `no_cv_recall_proxy: null` as "0.0%" — nullable because the
 *    window can contain zero no-CV emails; 0.0% claims the classifier is
 *    missing every one of them when it has simply seen none.
 *
 * A fourth: the rollback button must call the real rollback endpoint, not
 * silently no-op or hit the wrong route — it is the one action this panel
 * exists to offer in a crisis.
 *
 * Written as renders driving the real chain — component → `lib/api/admin` →
 * `fetch` → React Query — matching `../ai/page.test.tsx`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import messages from '@/messages/vi.json';
import { formats } from '@/i18n/request';

import SystemHealthPage from './page';

const t = messages.settings;

const RUNTIME_HEALTH = { status: 'healthy', services: [] };

const AI_CONFIG = {
  provider: 'openai',
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-4o',
  api_key_masked: '****abcd',
  api_key_decrypt_failed: false,
  configured: true,
  updated_at: '2026-01-01T00:00:00Z',
  credential_source: 'org_api_key',
  deployment_key_available: false,
  data_policy_accepted: true,
  data_policy_accepted_at: '2026-01-01T00:00:00Z',
  data_policy_version: '1',
  automation_enabled: true,
  automation_state: 'ready',
  assistant_enabled: false,
  assistant_state: 'not_configured',
  classification_policy: 'recall_first',
  classification_policy_version: '3',
  stable_classifier_version: 'v3',
  candidate_classifier_version: 'v4-canary',
  candidate_classification_policy: 'recall_first',
  candidate_classification_policy_version: '4',
  rollout_mode: 'canary' as const,
  canary_percentage: 20,
  ai_automation_consent: true,
  ai_assistant_consent: true,
  ai_policy_preset: 'balanced',
  ai_policy_preset_version: '1',
};

/** A telemetry payload with `no_cv_recall_proxy` present (the ordinary case). */
const TELEMETRY_WITH_NO_CV_DATA = {
  sample_size: 3247,
  job_application_recall_proxy: 0.974,
  stable_recall_proxy: 0.991,
  no_cv_recall_proxy: 0.87,
  correction_rate: 0.02,
  review_rate: 0.05,
  needs_classification_rate: 0.01,
  p95_latency_ms: 820,
  provider_error_rate: 0.001,
  duplicate_count: 4,
  retry_failure_rate: 0.0,
  total_prompt_tokens: 100000,
  total_completion_tokens: 20000,
  estimated_cost_usd: 1.23,
};

/** State the null-handling test exists for: zero no-CV emails in the window. */
const TELEMETRY_NO_CV_NULL = {
  ...TELEMETRY_WITH_NO_CV_DATA,
  sample_size: 3,
  no_cv_recall_proxy: null as number | null,
};

const json = (payload: unknown) =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

function stubFetch(telemetry: unknown, config: unknown = AI_CONFIG) {
  const fake = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/classification-rollout/telemetry')) return json(telemetry);
    if (url.includes('/classification-rollout/rollback')) return json(config);
    if (url.includes('/runtime/health')) return json(RUNTIME_HEALTH);
    if (url.includes('/organization/ai-config')) return json(config);
    throw new Error(`unstubbed fetch: ${url}`);
  });
  vi.stubGlobal('fetch', fake);
  return fake;
}

let queryClient: QueryClient;

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="vi" messages={messages} formats={formats}>
        <SystemHealthPage />
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

describe('classification rollout telemetry (#422)', () => {
  it('labels the recall figure "Recall ước lượng (proxy)", never bare "Recall"', async () => {
    stubFetch(TELEMETRY_WITH_NO_CV_DATA);
    renderPage();

    expect(await screen.findByText(t.rolloutRecallProxy)).toBeTruthy();
    // The disclaimer sentence must also be present, not just the label.
    expect(screen.getByText(t.rolloutRecallProxyNote)).toBeTruthy();
  });

  it('shows sample_size next to the recall proxy value', async () => {
    stubFetch(TELEMETRY_WITH_NO_CV_DATA);
    renderPage();

    const label = await screen.findByText(t.rolloutRecallProxy);
    // Same header row as the label — not just present somewhere on the page.
    expect(label.closest('div')?.textContent).toContain('3247');
  });

  it('renders no_cv_recall_proxy as a percentage when the window has no-CV data', async () => {
    stubFetch(TELEMETRY_WITH_NO_CV_DATA);
    renderPage();

    await screen.findByText(t.rolloutNoCvRecallProxy);
    expect(screen.getByText('87.0%')).toBeTruthy();
    expect(screen.queryByText(t.rolloutNoCvRecallProxyNoData)).toBeNull();
  });

  it('does NOT render "0.0%" when no_cv_recall_proxy is null — shows the no-data message instead', async () => {
    stubFetch(TELEMETRY_NO_CV_NULL);
    renderPage();

    await screen.findByText(t.rolloutNoCvRecallProxy);
    expect(screen.getByText(t.rolloutNoCvRecallProxyNoData)).toBeTruthy();
    expect(screen.queryByText('0.0%')).toBeNull();
  });
});

describe('rollback button (#422)', () => {
  it('calls the rollback endpoint (not configure/guardrails) when confirmed', async () => {
    const fetchSpy = stubFetch(TELEMETRY_WITH_NO_CV_DATA);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderPage();

    const button = await screen.findByRole('button', { name: t.rolloutRollback });
    fireEvent.click(button);

    await waitFor(() => {
      const rollbackCalls = fetchSpy.mock.calls.filter(([input, init]) => {
        const url = String(input);
        return (
          url.includes('/classification-rollout/rollback') &&
          (init as RequestInit | undefined)?.method === 'POST'
        );
      });
      expect(rollbackCalls.length).toBeGreaterThan(0);
    });
    expect(await screen.findByText(t.rolloutRollbackSuccess)).toBeTruthy();
  });

  it('does not call rollback when the confirm dialog is dismissed', async () => {
    const fetchSpy = stubFetch(TELEMETRY_WITH_NO_CV_DATA);
    vi.spyOn(window, 'confirm').mockReturnValue(false);

    renderPage();

    const button = await screen.findByRole('button', { name: t.rolloutRollback });
    fireEvent.click(button);

    expect(
      fetchSpy.mock.calls.some(([input]) => String(input).includes('/classification-rollout/rollback')),
    ).toBe(false);
  });
});
