/**
 * Every function here must call `/api/hr/organization/ai-config/*`, never
 * `/api/system-admin/*` — the exact ambiguity `lib/api/admin.ts`'s own
 * docstring warns about (#420: 38 HR endpoints once ended up gated by system
 * admin because an HR surface lived in that file). Nothing catches a client
 * function silently pointed at the dead system-admin path except a test that
 * inspects the actual URL each call makes.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';

import * as hrAiConfig from './hr-ai-config';

const json = (payload: unknown) =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

function stubFetch() {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(typeof input === 'string' ? input : input.toString());
      return json({ connected: true, version: '1', items: [] });
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const CALLS: Array<[string, () => Promise<unknown>]> = [
  ['getConfiguration', () => hrAiConfig.getConfiguration()],
  ['getProviderStatus', () => hrAiConfig.getProviderStatus()],
  ['getDataPolicy', () => hrAiConfig.getDataPolicy()],
  ['acceptDataPolicy', () => hrAiConfig.acceptDataPolicy()],
  ['acceptAutomationConsent', () => hrAiConfig.acceptAutomationConsent()],
  ['acceptAssistantConsent', () => hrAiConfig.acceptAssistantConsent()],
  ['setAIPolicyPreset', () => hrAiConfig.setAIPolicyPreset('balanced')],
  ['enableAutomation', () => hrAiConfig.enableAutomation()],
  ['disableAutomation', () => hrAiConfig.disableAutomation()],
  ['enableAssistant', () => hrAiConfig.enableAssistant()],
  ['disableAssistant', () => hrAiConfig.disableAssistant()],
];

describe.each(CALLS)('%s', (_name, call) => {
  it('calls a /api/hr/organization/ai-config path, never /api/system-admin', async () => {
    const calls = stubFetch();

    await call();

    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain('/api/hr/organization/ai-config');
    expect(calls[0]).not.toContain('/api/system-admin');
  });
});
