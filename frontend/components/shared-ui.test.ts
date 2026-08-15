import { describe, it, expect } from 'vitest';

import { routing } from '@/i18n/routing';
import {
  formatRuntimeDetail,
  formatLatency,
  formatAuditDetails,
  BADGE_TONE_PARTS,
  badgeToneClass,
} from '@/components/shared-ui';

/**
 * The pure, render-free parts of `shared-ui.tsx`: three formatters that carry
 * their own built-in strings instead of going through next-intl messages, and
 * the semantic tone table `DESIGN.md` names as its source of truth.
 *
 * These are plain functions — no render, no React tree. Testing them does not
 * open component testing (#302 AC: "Không viết component test").
 *
 * Why they need a test at all: each one took a `locale` parameter and compared
 * it against the literal `'vi-VN'`. No locale by that name exists. `routing`
 * declares `['vi', 'en']` and every call site passes `useLocale()`, so the
 * comparison was false at all four call sites — `settings/health`,
 * `settings/audit`, the console homepage, and `recruitment/interviews` — and a
 * Vietnamese-default product served "Just now", "Fast" and English audit labels
 * to its Vietnamese users. Ten dead branches, no test to notice.
 *
 * So the assertions below are all keyed off `routing`, not off a hardcoded
 * `'vi'`. That is deliberate: the bug was a mismatch between what the router
 * produces and what these functions accept, and a test that hardcodes its own
 * locale string reproduces exactly the assumption that failed. If the routing
 * locales are ever renamed, these tests must move with them or say so.
 */

/** The locale the app actually runs in by default — the value `useLocale()` yields. */
const VI = routing.defaultLocale;
const EN = routing.locales.find((locale) => locale !== VI)!;

describe('the locales these formatters are actually called with', () => {
  it('is what routing declares, not a regional tag', () => {
    // The anchor for every test below. If this fails, the routing locales were
    // renamed and the formatters' language matching has to be re-checked
    // against the new spelling rather than silently falling through.
    expect(routing.defaultLocale).toBe('vi');
    expect(routing.locales).toEqual(['vi', 'en']);
  });
});

describe('formatRuntimeDetail', () => {
  /** `Date.now()` is read inside the function, so build the input relative to it. */
  const beatSecondsAgo = (seconds: number) => `last beat: ${Date.now() / 1000 - seconds}`;

  it('renders relative time in Vietnamese for the Vietnamese locale', () => {
    expect(formatRuntimeDetail(beatSecondsAgo(5), VI)).toBe('Vừa xong');
    expect(formatRuntimeDetail(beatSecondsAgo(630), VI)).toBe('10 phút trước');
    expect(formatRuntimeDetail(beatSecondsAgo(7500), VI)).toBe('2 giờ trước');
    expect(formatRuntimeDetail(beatSecondsAgo(190_000), VI)).toBe('2 ngày trước');
  });

  it('renders relative time in English for the English locale', () => {
    // The other half of the guard: the fix must not be "always Vietnamese".
    expect(formatRuntimeDetail(beatSecondsAgo(5), EN)).toBe('Just now');
    expect(formatRuntimeDetail(beatSecondsAgo(630), EN)).toBe('10 min ago');
    expect(formatRuntimeDetail(beatSecondsAgo(7500), EN)).toBe('2 hr ago');
    expect(formatRuntimeDetail(beatSecondsAgo(190_000), EN)).toBe('2 day ago');
  });

  it('translates the dead-heartbeat detail', () => {
    expect(formatRuntimeDetail('no heartbeat', VI)).toBe('Không hoạt động');
    expect(formatRuntimeDetail('no heartbeat', EN)).toBe('Inactive');
  });

  it('accepts a regional Vietnamese tag as Vietnamese too', () => {
    // Nothing passes `vi-VN` today, but the parameter defaults to it, so it has
    // to mean Vietnamese rather than fall out the English side.
    expect(formatRuntimeDetail('no heartbeat', 'vi-VN')).toBe('Không hoạt động');
  });

  it('leaves details it does not recognise alone', () => {
    expect(formatRuntimeDetail('pool: 4/8', VI)).toBe('pool: 4/8');
    expect(formatRuntimeDetail(null, VI)).toBe('');
  });
});

describe('formatLatency', () => {
  it('labels latency in Vietnamese for the Vietnamese locale', () => {
    expect(formatLatency(50, VI)).toBe('Nhanh');
    expect(formatLatency(300, VI)).toBe('Bình thường');
    expect(formatLatency(900, VI)).toBe('Chậm');
  });

  it('labels latency in English for the English locale', () => {
    expect(formatLatency(50, EN)).toBe('Fast');
    expect(formatLatency(300, EN)).toBe('Normal');
    expect(formatLatency(900, EN)).toBe('Slow');
  });

  it('accepts a regional Vietnamese tag as Vietnamese too', () => {
    expect(formatLatency(50, 'vi-VN')).toBe('Nhanh');
  });

  it('renders nothing when there is no measurement', () => {
    expect(formatLatency(null, VI)).toBe('');
  });
});

describe('formatAuditDetails', () => {
  it('translates field labels for the Vietnamese locale', () => {
    // `fieldMap` — the key becomes the label.
    expect(formatAuditDetails({ target_user_email: 'a@b.vn' }, VI)).toBe('Người dùng: a@b.vn');
    expect(formatAuditDetails({ provider: 'openai' }, VI)).toBe('Nhà cung cấp: openai');
  });

  it('translates field labels for the English locale', () => {
    expect(formatAuditDetails({ target_user_email: 'a@b.vn' }, EN)).toBe('User: a@b.vn');
    expect(formatAuditDetails({ provider: 'openai' }, EN)).toBe('Provider: openai');
  });

  it('translates values for the Vietnamese locale', () => {
    // `valueMap` — the *value* is translated, separately from the label.
    expect(formatAuditDetails({ action: 'update' }, VI)).toBe('Hành động: Cập nhật');
    expect(formatAuditDetails({ entry_type: 'domain_pattern' }, VI)).toBe('Loại: Tên miền');
  });

  it('translates values for the English locale', () => {
    expect(formatAuditDetails({ action: 'update' }, EN)).toBe('Action: Update');
    expect(formatAuditDetails({ entry_type: 'domain_pattern' }, EN)).toBe('Type: Domain Pattern');
  });

  it('translates label and value together in one entry', () => {
    expect(formatAuditDetails({ old_role: 'user' }, VI)).toBe('Quyền cũ: Nhân viên');
    expect(formatAuditDetails({ old_role: 'user' }, EN)).toBe('Old Role: Employee');
  });

  it('accepts a regional Vietnamese tag as Vietnamese too', () => {
    expect(formatAuditDetails({ action: 'update' }, 'vi-VN')).toBe('Hành động: Cập nhật');
  });

  it('defaults to Vietnamese when no locale is passed', () => {
    // `recruitment/interviews/page.tsx:145` calls it with one argument.
    expect(formatAuditDetails({ action: 'update' })).toBe('Hành động: Cập nhật');
  });

  it('never renders raw JSON for an empty or absent payload', () => {
    expect(formatAuditDetails(null, VI)).toBe('—');
    expect(formatAuditDetails({}, VI)).toBe('—');
  });
});

/**
 * `BADGE_TONE_PARTS` — the canonical semantic palette.
 *
 * Pinned as literals, unlike everything else in this file, because this table
 * is what other assertions are allowed to be relative to. `settings/stat-card-tone.test.tsx`
 * asserts the console's status cards match *whatever this table says*; that is
 * the right shape for a wiring test, but it means the table itself could be
 * rewritten with both sides moving together and nothing would notice. These
 * cases are the fixed end of that pair.
 *
 * The values are the ones `Badge` has shipped with. #308 split the single
 * joined string into three parts so a caller needing only `bg` + `fg` could
 * stop writing its own; `badgeToneClass` is asserted to rebuild the original
 * string exactly, so the split stayed a refactor.
 */
describe('BADGE_TONE_PARTS', () => {
  it('names exactly the seven tones the design system publishes', () => {
    // Not six, not eight: `DESIGN.md` documents a meaning per tone, and a tone
    // added here without one is a colour nobody can look up. `violet` is the
    // seventh — #316 folded `StatusPill`'s private table into this one rather
    // than dropping the tone, because `interview_scheduled` has to stay
    // distinguishable from `reviewing` (`indigo`) where the two statuses sit
    // side by side in `recruitment/interviews`.
    expect(Object.keys(BADGE_TONE_PARTS).sort()).toEqual(
      ['amber', 'emerald', 'indigo', 'rose', 'sky', 'slate', 'violet'],
    );
  });

  it('carries the class strings the badge renders', () => {
    // Slate's background is the odd one and is meant to be: `bg-*-100` where
    // every other tone takes `bg-*-50`, because `slate-50` is the page
    // background and a chip in it would not read as a chip on a white card.
    //
    // Its foreground is not an exception. This table shipped `text-slate-600`
    // while `StatusPill`'s table said `700`; #316 settled on `700`, so `slate`
    // now matches the other six on the axis where nothing justified a split.
    // That is a visible change to every slate `Badge`.
    expect(badgeToneClass('slate')).toBe('bg-slate-100 text-slate-700 border-slate-200');
    expect(badgeToneClass('indigo')).toBe('bg-indigo-50 text-indigo-700 border-indigo-200');
    expect(badgeToneClass('emerald')).toBe('bg-emerald-50 text-emerald-700 border-emerald-200');
    expect(badgeToneClass('amber')).toBe('bg-amber-50 text-amber-700 border-amber-200');
    expect(badgeToneClass('rose')).toBe('bg-rose-50 text-rose-700 border-rose-200');
    expect(badgeToneClass('sky')).toBe('bg-sky-50 text-sky-700 border-sky-200');
    expect(badgeToneClass('violet')).toBe('bg-violet-50 text-violet-700 border-violet-200');
  });

  it('gives every tone but slate the same shape', () => {
    // States the rule the `slate` exception is an exception *to*, so a future
    // tone added as `bg-*-100`/`text-*-600` fails here instead of quietly
    // becoming a second precedent.
    for (const [tone, parts] of Object.entries(BADGE_TONE_PARTS)) {
      if (tone === 'slate') continue;

      expect(parts.bg, tone).toMatch(/-50$/);
      expect(parts.fg, tone).toMatch(/-700$/);
      expect(parts.border, tone).toMatch(/-200$/);
    }
  });

  it('spells every part as a whole Tailwind class', () => {
    // Tailwind v4 scans source text for complete class names. A part stored as
    // a bare shade — `'emerald-50'`, or a value meant to be interpolated —
    // would typecheck, render into `class`, and produce no CSS whatsoever.
    for (const [tone, parts] of Object.entries(BADGE_TONE_PARTS)) {
      expect(parts.bg, tone).toMatch(/^bg-[a-z]+-\d{2,3}$/);
      expect(parts.fg, tone).toMatch(/^text-[a-z]+-\d{2,3}$/);
      expect(parts.border, tone).toMatch(/^border-[a-z]+-\d{2,3}$/);
    }
  });

  it('keeps all three parts of a tone on the same colour', () => {
    // The failure this catches is a copy-paste when a tone is added: a row
    // reading `bg-sky-50 text-slate-600 border-sky-200` looks plausible in
    // review and renders unreadable text.
    for (const [tone, parts] of Object.entries(BADGE_TONE_PARTS)) {
      const families = [parts.bg, parts.fg, parts.border].map((cls) => cls.split('-')[1]);

      expect(new Set(families), tone).toEqual(new Set([families[0]]));
    }
  });
});
