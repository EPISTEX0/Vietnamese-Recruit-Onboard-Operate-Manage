/**
 * @vitest-environment jsdom
 *
 * `CANDIDATE_STATUS_META[c.status]` is keyed by the six `CandidateStatus`
 * values the frontend union declares, but the value on the wire is whatever
 * the backend enum currently holds — untyped once it crosses `fetch` — so a
 * status the union has not caught up with falls through to the `??` fallback
 * at render time, not to a type error at build time.
 *
 * The fallback object was missing `labelKey` while the render site two lines
 * down reads `tc(meta.labelKey)` unconditionally (#341). Measured, not
 * guessed: `tc(undefined)` does not throw — it renders `"common"`, the
 * namespace name next-intl's default `MISSING_MESSAGE` fallback returns when
 * the key itself is `undefined` (there is no `namespace.key` path to join).
 * So the shipped symptom was a status pill reading the literal word "common"
 * for any candidate in a status the union does not list.
 *
 * The fix — `labelKey: c.status`, matching the fallback five other call
 * sites already use — does not make the pill print the bare status either.
 * With a *defined* key that still names nothing in the catalogue, next-intl's
 * default fallback instead renders the full unresolved path, `"common.<status>"`
 * (measured below, not assumed: the pre-fix "common" case and the post-fix
 * "common.<status>" case are different branches of the same fallback, one
 * keyed on `undefined` and one on a real string). What is asserted is the
 * one property that is actually the fix, and that a coincidence could not
 * fake: the candidate's own status text appears in the pill, which is false
 * pre-fix (the pill reads exactly "common", nothing else) and true post-fix.
 */
import { expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';

import viMessages from '@/messages/vi.json';
import type { CandidateListResponse, CandidateStatus } from '@/lib/api/recruitment';

vi.mock('@/lib/api/recruitment', () => ({
  listCandidates: vi.fn(),
}));

vi.mock('@/i18n/navigation', () => ({
  Link: ({ children }: { children?: ReactNode }) => <>{children}</>,
  redirect: vi.fn(),
  usePathname: vi.fn(),
  useRouter: vi.fn(() => ({ push: vi.fn() })),
  getPathname: vi.fn(),
}));

const api = await import('@/lib/api/recruitment');
const { default: CandidatesPage } = await import('./page');

// Not a real `CandidateStatus`: this is exactly the shape of the defect,
// where the wire carries a value the frontend union has not caught up with.
const UNKNOWN_STATUS = 'pending_review' as CandidateStatus;

const RESPONSE: CandidateListResponse = {
  candidates: [{
    id: 'cand-1',
    name: 'Nguyễn Văn A',
    email: 'a@example.test',
    phone: '',
    skills: [],
    status: UNKNOWN_STATUS,
    confidence_score: 0.8,
    created_at: '2026-01-02T03:04:05Z',
    has_cv: false,
    job_opening_id: null,
    job_opening_title: '',
  }],
  total_count: 1,
  page: 1,
  page_size: 12,
};

it('shows the candidate\'s own status on a status the meta map has no entry for', async () => {
  vi.mocked(api.listCandidates).mockResolvedValue(RESPONSE);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="vi" messages={viMessages}>
        <CandidatesPage />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );

  // Not an exact match on "common.pending_review": the exact path is an
  // implementation detail of next-intl's fallback, but the status text
  // appearing in it at all is exactly what `labelKey: c.status` buys, and
  // exactly what a bare `"common"` (the pre-fix render) does not have.
  expect(await screen.findByText((text) => text.includes(UNKNOWN_STATUS))).toBeTruthy();
});
