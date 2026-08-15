import { describe, it, expect } from 'vitest';

import { routing } from '@/i18n/routing';
import { formatRuntimeDetail, formatLatency, formatAuditDetails } from '@/components/shared-ui';

/**
 * The three pure formatters in `shared-ui.tsx` that carry their own built-in
 * strings instead of going through next-intl messages.
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
