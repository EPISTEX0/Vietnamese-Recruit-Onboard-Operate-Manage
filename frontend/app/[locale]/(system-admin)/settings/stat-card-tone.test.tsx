/**
 * @vitest-environment jsdom
 *
 * The four status cards on Tổng quan hệ thống must take their icon tint from
 * the canonical semantic palette in `shared-ui`, not from class strings written
 * out at the call site.
 *
 * This file exists because #308's refactor has no other witness. `StatCard`
 * used to take `iconBg`/`iconFg` — two bare class strings that always travelled
 * as a pair — and swapping them for a `tone` is the kind of change that looks
 * identical in a screenshot: the colours stay the same family, only the shade
 * moves (`text-*-600` at the call site vs `text-*-700` in `BADGE_TONE_PARTS`),
 * and that drift is exactly the defect the ticket names. Nothing in the suite
 * rendered this page before, so a revert to hand-written strings — or a card
 * quietly wired to the wrong tone — would have shipped unnoticed.
 *
 * So the assertion is deliberately *not* a hardcoded `'bg-sky-50 text-sky-700'`.
 * It reads `BADGE_TONE_PARTS` and demands the rendered chip match it. That way
 * the test states the property under refactor ("the chip wears whatever the
 * canonical table says for its tone") rather than a snapshot of today's hex.
 * The table's own literal values are pinned separately, in
 * `components/shared-ui.test.ts` — the two together are what make a silent
 * change impossible: this file catches a card that stops going through the
 * table, that file catches the table being redefined underneath it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import messages from '@/messages/vi.json';
import { BADGE_TONE_PARTS, type BadgeTone } from '@/components/shared-ui';

// `createNavigation(routing)` reaches for `next/navigation` at import time,
// which resolves only inside the Next build. Same stub as `page.test.ts`.
vi.mock('@/i18n/navigation', () => ({
  Link: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  redirect: vi.fn(),
  usePathname: vi.fn(),
  useRouter: vi.fn(),
  getPathname: vi.fn(),
}));

const { default: SystemOverviewPage } = await import('./page');

const to = messages.settings.systemOverview;

/** The four cards, in the order the page lays them out. */
const CARDS: { name: string; kicker: string; tone: BadgeTone }[] = [
  { name: 'AI', kicker: to.cardAiKicker, tone: 'indigo' },
  { name: 'Runtime', kicker: to.cardRuntimeKicker, tone: 'emerald' },
  { name: 'Tài khoản', kicker: to.cardAccountsKicker, tone: 'sky' },
  { name: 'Nhật ký', kicker: to.cardAuditKicker, tone: 'amber' },
];

let queryClient: QueryClient;

beforeEach(() => {
  // Every query fails, and that is the point: the icon chip sits above the
  // loading/error/value branch, so its tint must not depend on how the five
  // queries resolved. Failing them all keeps the render deterministic without
  // having to satisfy five response schemas.
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 500 })));
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="vi" messages={messages}>
        <SystemOverviewPage />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

/**
 * The tinted square holding the card's icon, and the icon inside it.
 *
 * The two parts of a tone land on two different elements — the background on
 * the chip, the foreground on the `<svg>`, which is how the card was already
 * built — so both have to be reached to check the pair arrived intact.
 *
 * Anchored on the kicker because that is the one string the card owns that no
 * other element on the page repeats; the chip is its sibling. Structural, so a
 * reshuffle of the card header breaks it loudly rather than silently asserting
 * about some other element — which is what the missing-icon throw below is for.
 */
function iconChipFor(kicker: string): { chip: HTMLElement; icon: Element } {
  const header = screen.getByText(kicker).parentElement;
  if (!header) throw new Error(`kicker "${kicker}" has no parent`);
  const chip = header.firstElementChild;
  if (!(chip instanceof HTMLElement)) {
    throw new Error(`kicker "${kicker}" has no icon chip beside it`);
  }
  const icon = chip.querySelector('svg');
  // Guards every assertion below against passing for the wrong reason: without
  // this, a card header that lost its icon would still hand back *an* element
  // and the class check would fail confusingly instead of naming the cause.
  if (!icon) throw new Error(`the element beside kicker "${kicker}" holds no icon`);
  return { chip, icon };
}

const classesOf = (el: Element) => el.getAttribute('class')?.split(/\s+/) ?? [];

describe('Tổng quan hệ thống status cards', () => {
  it.each(CARDS)('tints the $name card from the canonical $tone tone', ({ kicker, tone }) => {
    renderPage();

    const { bg, fg } = BADGE_TONE_PARTS[tone];
    const { chip, icon } = iconChipFor(kicker);

    expect(classesOf(chip)).toContain(bg);
    expect(classesOf(icon)).toContain(fg);
  });

  it('gives each card its own tone', () => {
    // A `tone` prop makes it cheap to paste the same one four times, and the
    // row stops being scannable at a glance the moment two cards look alike.
    //
    // Read off the render, not off `CARDS`: comparing this file's own literals
    // would be true no matter what `page.tsx` does.
    renderPage();

    const tints = CARDS.map(({ kicker }) => {
      const { chip, icon } = iconChipFor(kicker);
      return [
        classesOf(chip).find((cls) => cls.startsWith('bg-')),
        classesOf(icon).find((cls) => cls.startsWith('text-')),
      ].join(' ');
    });

    expect(new Set(tints).size).toBe(CARDS.length);
  });
});
