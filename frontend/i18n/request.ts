import { getRequestConfig } from 'next-intl/server';
import { routing } from './routing';

/**
 * Named `dateTime` presets for `useFormatter().dateTime(value, name)`.
 *
 * Centralised because five call sites across the app repeated the same
 * option object literally (#313) — Tailwind-style utility duplication, but
 * for `Intl.DateTimeFormatOptions` instead of class names. Adding a shape
 * here makes it reusable; call sites that need one field of their own (e.g.
 * a per-row `timeZone`) pass it as the third argument, which `useFormatter`
 * merges on top of the named preset rather than replacing it.
 *
 * - `full` replicates the no-options default of `Date.prototype.toLocaleString`
 *   (numeric date + time, including seconds) — `Intl.DateTimeFormat` with no
 *   options defaults to date-only instead, so this has to be spelled out.
 * - No `date`-only preset exists because `Intl.DateTimeFormat` with no
 *   options already renders date-only — the default *is* that preset.
 */
export const formats = {
  dateTime: {
    full: {
      year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: 'numeric', second: 'numeric',
    },
    short: { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' },
    shortWithYear: {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    },
    time: { hour: '2-digit', minute: '2-digit' },
  },
} as const;

export default getRequestConfig(async ({ requestLocale }) => {
  let locale = await requestLocale;
  if (!locale || !routing.locales.includes(locale as 'vi' | 'en')) {
    locale = routing.defaultLocale;
  }

  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
    formats,
  };
});
