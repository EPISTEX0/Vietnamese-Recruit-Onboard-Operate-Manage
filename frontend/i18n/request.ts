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

/**
 * The zone every displayed timestamp is rendered in.
 *
 * Must be set explicitly, and must not be left to the runtime. When
 * `getRequestConfig` returns no `timeZone`, next-intl does not fall back to the
 * reader's browser — it fills the gap on the **server** with
 * `Intl.DateTimeFormat().resolvedOptions().timeZone` (`getConfig.js`), then
 * `NextIntlClientProviderServer` passes that resolved value down as an explicit
 * `timeZone` prop. The browser therefore formats in the *server process's* zone,
 * and `frontend/Dockerfile` (`node:22-slim`, no `TZ`, none in `docker-compose.yml`)
 * makes that UTC. Measured against this repo's own `use-intl`: a check-in stored
 * as `2026-08-03T01:00:00Z` — the seed script's own "08:00 giờ VN" — renders as
 * `01:00:00 3/8/2026`. Seven hours off, on every attendance time, payslip
 * publish time, audit timestamp and Gmail received time.
 *
 * That failure is invisible in development, which is the reason it needs this
 * comment rather than a one-line constant: a developer machine in Vietnam
 * resolves to `Asia/Saigon` and renders the correct `08:00:00`, so the bug
 * appears only once the app is in a container.
 *
 * A fixed zone rather than the reader's own is a domain decision, not a
 * workaround. Attendance windows, payroll cut-offs and leave requests are facts
 * about a Vietnamese company's working day; a manager reading the console from
 * Singapore should still see a shift that began at 08:00 rendered as 08:00, not
 * 09:00. Making it per-organisation is a real future need, not this ticket's —
 * see the follow-up issue referenced in `docs/adr/0016-datetime-locale-via-useformatter.md`.
 */
export const APP_TIME_ZONE = 'Asia/Ho_Chi_Minh';

export default getRequestConfig(async ({ requestLocale }) => {
  let locale = await requestLocale;
  if (!locale || !routing.locales.includes(locale as 'vi' | 'en')) {
    locale = routing.defaultLocale;
  }

  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
    formats,
    timeZone: APP_TIME_ZONE,
  };
});
