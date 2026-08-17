/**
 * The invariant, in one sentence: no source file may format a date/time for
 * display using a locale that is not the reader's actual active locale — not
 * as a literal argument, and not as a parameter default nobody overrides.
 *
 * #313 was one bug wearing two shapes, and a plain `rg "vi-VN"` only ever
 * found the first one:
 *
 * 1. **Literal in the call.** `new Date(x).toLocaleString('vi-VN', ...)`.
 *    Grep-visible — the string `'vi-VN'` sits right there in the call.
 * 2. **Silent default parameter.** `function f(x, locale = 'vi-VN') {}`,
 *    called from 42 sites that never mention `'vi-VN'` at all. This shape
 *    is what a string search cannot see: the literal exists exactly once,
 *    at the declaration, and every call site that matters is silent about
 *    it. It was also the larger half — 27 of the 43 datetime call sites
 *    fixed by #313 were shape 2, not shape 1.
 *
 * So this guard checks both shapes, and — like `tailwind-color-shades.test.ts`,
 * which it is deliberately modelled on — derives its file list and its
 * targets from the tree rather than from a hand-written list of "the files
 * #313 touched". A hand-written list reads as exhaustive while covering only
 * what its author remembered, which is the exact failure mode that let shape
 * 2 reach 27 call sites before anyone counted them.
 *
 * ## Why `toLocaleString` needs a receiver check and the other two don't
 *
 * `toLocaleDateString`/`toLocaleTimeString` exist only on `Date` — a literal
 * locale there is unambiguously this bug. `toLocaleString` exists on both
 * `Date` *and* `Number`, and `payroll/payslips/page.tsx` calls it on a number
 * for currency formatting (`calcNet.toLocaleString("vi-VN")`) — a real,
 * deliberately out-of-scope use of the same method name (#313's own
 * boundary: thousands-separator formatting is a different decision from
 * date/time formatting). So a `toLocaleString` literal is only flagged when
 * its receiver is a `Date` — either written inline (`new Date(x).toLocaleString(...)`)
 * or traced through a same-file variable assigned from an expression
 * containing `new Date(`. That receiver check is exactly what keeps the
 * payslips currency calls off this guard's report without naming that file.
 *
 * ## Why the default-parameter check excludes exactly one function by name
 *
 * `formatVND` still declares `locale = 'vi-VN'` after #313 — deliberately:
 * currency formatting was ruled out of scope in the same ticket that fixed
 * the datetime helpers, for the same reason as above (a different decision).
 * Scoping the check to "helpers whose body calls a datetime Intl API" would
 * dodge this by construction, but `formatAuditDetails` — squarely in scope,
 * and one of the 27 — never calls `toLocaleString` either; it branches on
 * `isVietnamese(locale)` to pick between two hardcoded string tables. There
 * is no body-shape signal that reliably separates "this is a datetime
 * helper" from "this is a currency helper" here. So the exclusion is named
 * explicitly, the way `docs/adr/0006-*.md`'s own blind-spot notes are named
 * explicitly, rather than built to look automatic.
 *
 * ## Blind spots, stated rather than left to be rediscovered
 *
 * - **Arrow functions.** The default-parameter check matches `function NAME(...)`
 *   declarations; `const f = (locale = 'vi-VN') => {}` would not be flagged.
 *   Every helper in this codebase today uses `function` syntax.
 * - **Multi-line signatures/calls.** Both checks scan line by line (matching
 *   `tailwind-color-shades.test.ts`'s own approach), so a call or parameter
 *   list split across lines is invisible to this guard. Every real instance
 *   #313 fixed was single-line.
 * - **Cross-file variable tracing.** The `toLocaleString` receiver trace only
 *   follows `const`/`let` assignments within the *same file* as the call.
 *   A `Date` passed in from another module and reassigned locally would not
 *   be traced.
 * - **Non-`Date`, non-`Number` locale-shaped bugs** — e.g. `Intl.DateTimeFormat`
 *   constructed with a literal locale directly — are a related but distinct
 *   shape this guard does not scan for. None exist in the tree today.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createFormatter } from 'use-intl';
import { describe, expect, it, vi } from 'vitest';

/**
 * `next-intl/server` has to be stubbed to import `i18n/request.ts` at all.
 *
 * The package ships two builds behind an export condition, and Vitest has no
 * `react-server` condition, so it resolves to the client one — whose
 * `getRequestConfig` is a stub that throws `"not supported in Client
 * Components"` on call. That is the resolution being wrong, not the module.
 *
 * The replacement is not an approximation. The real server implementation
 * (`server/react-server/getRequestConfig.js` under `next-intl/dist/esm`) is
 * the identity function in full:
 *
 *     function getRequestConfig(createRequestConfig) {
 *       return createRequestConfig;
 *     }
 *
 * It exists to carry a type signature, not behaviour, so returning the callback
 * unchanged is what production does. `i18n/request.ts`'s own logic — the locale
 * validation and the returned config — is the real module throughout.
 */
vi.mock('next-intl/server', () => ({
  getRequestConfig: <T,>(createRequestConfig: T): T => createRequestConfig,
}));

import getRequestConfig, { APP_TIME_ZONE, formats } from '@/i18n/request';

const THIS_FILE = fileURLToPath(import.meta.url);
const PACKAGE_ROOT = dirname(THIS_FILE);

const SCANNED_EXTENSIONS = new Set(['.ts', '.tsx']);

/** Directories with no authored source in them — see `tailwind-color-shades.test.ts`. */
const SKIPPED_DIRECTORIES = new Set(['node_modules', '.next', '.git', 'coverage', 'dist', 'out']);

function* sourceFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIPPED_DIRECTORIES.has(entry.name)) yield* sourceFiles(full);
    } else if (entry.isFile() && SCANNED_EXTENSIONS.has(extname(entry.name))) {
      yield full;
    }
  }
}

type Offense = { line: number; snippet: string };

/** Every `<name>` a `const`/`let` in `text` binds to an expression containing `new Date(`. */
function tracedDateVariableNames(text: string): Set<string> {
  const names = new Set<string>();
  for (const m of text.matchAll(/\b(?:const|let)\s+(\w+)\s*=[^;\n]*\bnew Date\(/g)) {
    names.add(m[1]);
  }
  return names;
}

/**
 * Every `.toLocaleDateString('literal')` / `.toLocaleTimeString('literal')`
 * (always a `Date` method), plus every `.toLocaleString('literal')` whose
 * receiver is, or traces to, a `new Date(...)`.
 */
function findLiteralLocaleDateCalls(text: string): Offense[] {
  const dateVars = tracedDateVariableNames(text);
  const receiver = dateVars.size
    ? String.raw`(?:new Date\([^)]*\)|\b(?:${[...dateVars].join('|')})\b)`
    : String.raw`new Date\([^)]*\)`;
  const patterns = [
    /\.toLocale(?:Date|Time)String\(\s*(['"`])[^'"`]*\1/g,
    new RegExp(String.raw`${receiver}\.toLocaleString\(\s*(['"\`])[^'"\`]*\1`, 'g'),
  ];

  const found: Offense[] = [];
  text.split('\n').forEach((line, i) => {
    for (const pattern of patterns) {
      for (const m of line.matchAll(pattern)) {
        found.push({ line: i + 1, snippet: m[0] });
      }
    }
  });
  return found;
}

type DefaultParamOffense = Offense & { functionName: string };

/** Every `function NAME(..., locale = 'literal', ...)` declaration in `text`. */
function findLocaleDefaultParams(text: string): DefaultParamOffense[] {
  const pattern = /function\s+(\w+)\s*\([^)]*\blocale\s*(?::\s*string)?\s*=\s*(['"])[a-zA-Z-]+\2[^)]*\)/g;
  const found: DefaultParamOffense[] = [];
  text.split('\n').forEach((line, i) => {
    for (const m of line.matchAll(pattern)) {
      found.push({ line: i + 1, snippet: m[0], functionName: m[1] });
    }
  });
  return found;
}

/** Named, not inferred — see the header comment for why this can't be structural. */
const CURRENCY_FORMATTER_EXCEPTIONS = new Set(['formatVND']);

describe('datetime locale literals', () => {
  const files = [...sourceFiles(PACKAGE_ROOT)].filter((file) => file !== THIS_FILE);

  it('walked more than a handful of files', () => {
    // Guards the assertion below against a walk that silently returned early
    // and would otherwise read as "looked and found none" — see the parallel
    // note in `tailwind-color-shades.test.ts`.
    expect(files.length).toBeGreaterThan(50);
  });

  it('names no hardcoded locale in a Date-formatting call, and no silent locale default', () => {
    const offenders = files.flatMap((file) => {
      const text = readFileSync(file, 'utf8');
      const literalCalls = findLiteralLocaleDateCalls(text)
        .map(({ line, snippet }) => `${relative(PACKAGE_ROOT, file)}:${line}  ${snippet}`);
      const defaultParams = findLocaleDefaultParams(text)
        .filter(({ functionName }) => !CURRENCY_FORMATTER_EXCEPTIONS.has(functionName))
        .map(({ line, snippet }) => `${relative(PACKAGE_ROOT, file)}:${line}  ${snippet}`);
      return [...literalCalls, ...defaultParams];
    });

    expect(offenders).toEqual([]);
  });

  // The scans above are only worth their runtime if they can still tell a bad
  // shape from a good one. These fix that on fixtures reproducing the exact
  // shapes #313 found and fixed, so "no offenders" keeps meaning "looked and
  // found none" rather than "the pattern stopped matching anything".
  describe('the scan itself', () => {
    describe('literal locale in a Date-formatting call', () => {
      const scan = (text: string) => findLiteralLocaleDateCalls(text).map((o) => o.snippet);

      it('reports the exact shapes #313 fixed', () => {
        // `settings/users/page.tsx:75` before the fix. The match stops at the
        // literal's closing quote — it only has to prove a hardcoded locale
        // reached the call, not capture the rest of the argument list.
        expect(scan(`new Date(u.created_at).toLocaleDateString('vi-VN')`))
          .toEqual([`.toLocaleDateString('vi-VN'`]);
        // `AiChat.tsx:49` before the fix.
        expect(scan(`new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })`))
          .toEqual([`.toLocaleTimeString('vi-VN'`]);
        // `settings/page.tsx:197` before the fix — `toLocaleString` on an inline
        // `new Date(...)`, so the receiver is part of the match too.
        expect(scan(`new Date(log.created_at).toLocaleString('vi-VN', { hour: '2-digit' })`))
          .toEqual([`new Date(log.created_at).toLocaleString('vi-VN'`]);
      });

      it('traces a `toLocaleString` receiver through a same-file `new Date(` assignment', () => {
        // `settings/oauth/page.tsx`'s `formatUpdatedAt` before the fix: the
        // receiver is a variable, not an inline `new Date(...)`.
        const text = [
          `const parsed = new Date(updatedAt);`,
          `return parsed.toLocaleString('vi-VN', { year: 'numeric' });`,
        ].join('\n');
        expect(scan(text)).toEqual([`parsed.toLocaleString('vi-VN'`]);
      });

      it('does not flag a literal `toLocaleString` locale on a non-Date receiver', () => {
        // `payroll/payslips/page.tsx` — currency formatting, explicitly out of
        // scope for #313 (a different decision, not an oversight).
        expect(scan(`calcNet.toLocaleString("vi-VN")`)).toEqual([]);
      });

      it('does not flag the fixed shape — a variable locale, or a named preset', () => {
        expect(scan(`new Date(iso).toLocaleString(locale)`)).toEqual([]);
        expect(scan(`format.dateTime(new Date(iso), 'full')`)).toEqual([]);
        expect(scan(`format.dateTime(new Date(iso))`)).toEqual([]);
      });
    });

    describe('locale parameter defaulted to a literal', () => {
      const scan = (text: string) =>
        findLocaleDefaultParams(text)
          .filter(({ functionName }) => !CURRENCY_FORMATTER_EXCEPTIONS.has(functionName))
          .map((o) => o.functionName);

      it('reports the exact shape #313 fixed — 27 call sites relied on this default', () => {
        expect(scan(`export function formatDateTime(iso: string | null | undefined, locale = 'vi-VN'): string {`))
          .toEqual(['formatDateTime']);
        expect(scan(`export function formatAuditDetails(details: unknown, locale = 'vi-VN'): string {`))
          .toEqual(['formatAuditDetails']);
      });

      it('does not flag a required parameter — the fixed shape', () => {
        expect(scan(`export function formatRuntimeDetail(detail: string | null, locale: string): string {`))
          .toEqual([]);
      });

      it('does not flag a plain variable named `locale`', () => {
        expect(scan(`const locale = useLocale();`)).toEqual([]);
        expect(scan(`let locale = await requestLocale;`)).toEqual([]);
      });

      it('excludes exactly the named currency exception, not defaults in general', () => {
        expect(scan(`export function formatVND(value: string | number | null | undefined, locale = 'vi-VN'): string {`))
          .toEqual([]);
        // Same shape, different name: still reported. Proves the exclusion is
        // by name, not by some incidental feature of the signature.
        expect(
          findLocaleDefaultParams(
            `export function formatVNDButNotReally(value: number, locale = 'vi-VN'): string {`,
          ).map((o) => o.functionName),
        ).toEqual(['formatVNDButNotReally']);
      });
    });
  });
});

/**
 * The second half of #313's invariant, and the half the scans above cannot see.
 *
 * Replacing `toLocaleString('vi-VN')` with `format.dateTime(d, 'full')` moved
 * the failure mode rather than removing it. The scans above are pure source
 * greps: they prove no file *names* a locale. They say nothing about whether
 * the named preset `'full'` actually resolves at runtime — and if it does not,
 * `use-intl` does not throw. It reports `MISSING_FORMAT` to `onError` (a
 * `console.error` by default) and returns the fallback, which for `dateTime`
 * is `String(value)`:
 *
 *     dateTime: (value, formatOrName, options) =>
 *       resolve(..., () => String(value))
 *
 * `String(new Date())` is `'Mon Aug 17 2026 09:49:27 GMT+0700 (Indochina Time)'`
 * — raw, English, and locale-blind, i.e. a worse version of the exact bug #313
 * was filed to fix, delivered silently to production with only a console line.
 *
 * That failure needs exactly two things to go wrong, neither of which any
 * other test in this suite touches:
 *
 * 1. **`i18n/request.ts` stops returning `formats`.** Measured, not assumed:
 *    deleting the `formats,` line from the config leaves all 222 tests green
 *    and `tsc --noEmit` clean. The six component tests that render datetimes
 *    pass `formats={formats}` to `NextIntlClientProvider` themselves, so they
 *    keep working while the app path breaks — they test the provider, never
 *    the config that feeds it in production.
 * 2. **A call site names a preset that was never defined** (typo, rename, or a
 *    preset deleted while a caller still asks for it). Nothing typechecks the
 *    second argument of `format.dateTime` against `formats.dateTime`'s keys.
 *
 * On the production path itself: `app/layout.tsx` — the app's only layout that
 * renders `NextIntlClientProvider`, and the only `layout.tsx` in the tree —
 * renders it *without* a `formats` prop, which is correct and not a third
 * failure mode. It does not declare `'use client'`, so React resolves
 * `next-intl` through its `react-server` export condition
 * (`next-intl/package.json`) to `index.react-server.js`, which re-exports
 * `NextIntlClientProvider` as `NextIntlClientProviderServer`. That component
 * fills the gap from the request config:
 *
 *     formats: formats === undefined ? await getFormats() : formats
 *
 * and `getFormats()` is `(await getConfig()).formats` — the very object
 * asserted below. So the config is the single source for every server-rendered
 * datetime in the app, which is what makes assertion 1 load-bearing.
 *
 * The same provider resolves `timeZone` by the same route, with one difference
 * that matters: an absent `timeZone` is not left absent. `getConfig` substitutes
 * the server process's own zone, which the container makes UTC. That is asserted
 * separately below, and explained on `APP_TIME_ZONE` in `i18n/request.ts`.
 */
describe('datetime format presets resolve', () => {
  /**
   * Every preset name passed as `format.dateTime(value, 'name')` in the tree.
   *
   * Extracted by walking parentheses rather than by one regex: the first
   * argument is almost always `new Date(...)`, whose own `)` ends any
   * `\([^)]*\)` pattern early, and several call sites put two `format.dateTime`
   * calls plus unrelated quoted strings on a single line. A non-greedy regex
   * across such a line happily pairs one call's opening with another's
   * argument, which would invent preset names that no call site asks for.
   */
  function findPresetNames(text: string): string[] {
    const names: string[] = [];
    const CALL = 'format.dateTime(';

    for (let start = text.indexOf(CALL); start !== -1; start = text.indexOf(CALL, start + 1)) {
      const argsStart = start + CALL.length;
      let depth = 1;
      let quote: string | null = null;
      let i = argsStart;
      const args: string[] = [];
      let current = '';

      for (; i < text.length && depth > 0; i++) {
        const ch = text[i];
        if (quote) {
          if (ch === '\\') { current += ch + (text[i + 1] ?? ''); i++; continue; }
          if (ch === quote) quote = null;
          current += ch;
          continue;
        }
        if (ch === '"' || ch === "'" || ch === '`') { quote = ch; current += ch; continue; }
        if (ch === '(' || ch === '[' || ch === '{') depth++;
        else if (ch === ')' || ch === ']' || ch === '}') {
          depth--;
          if (depth === 0) break;
        }
        if (ch === ',' && depth === 1) { args.push(current.trim()); current = ''; continue; }
        current += ch;
      }
      args.push(current.trim());

      const second = args[1];
      if (second) {
        const literal = /^(['"])(\w+)\1$/.exec(second);
        if (literal) names.push(literal[2]);
      }
    }
    return names;
  }

  const files = [...sourceFiles(PACKAGE_ROOT)].filter((file) => file !== THIS_FILE);

  it('defines every preset the app asks for, with none asked for that is undefined', () => {
    const defined = new Set(Object.keys(formats.dateTime));

    const used = new Map<string, string[]>();
    for (const file of files) {
      for (const name of findPresetNames(readFileSync(file, 'utf8'))) {
        const where = used.get(name) ?? [];
        where.push(relative(PACKAGE_ROOT, file));
        used.set(name, where);
      }
    }

    // Proves the extractor found the call sites at all, so an empty `undefined`
    // list below means "checked them" rather than "matched nothing".
    expect([...used.keys()].sort()).toEqual(['full', 'short', 'shortWithYear', 'time']);

    const undefinedPresets = [...used.entries()]
      .filter(([name]) => !defined.has(name))
      .map(([name, where]) => `${name} — used in ${[...new Set(where)].join(', ')}`);
    expect(undefinedPresets).toEqual([]);
  });

  it('ships those presets in the request config, not just in the module', async () => {
    // The mutation this kills: dropping `formats,` from the returned object.
    // Importing the `formats` const alone would not catch it — the const would
    // still exist and still be well-formed while no request ever received it.
    const config = await getRequestConfig({
      requestLocale: Promise.resolve('en'),
      locale: 'en',
    } as never);

    expect(config.formats).toBe(formats);
  });

  it('pins an explicit time zone, so the container clock cannot become the app clock', async () => {
    // Dropping `timeZone` does not leave datetimes on the reader's own zone —
    // `getConfig` substitutes the server process's zone and the provider ships
    // it to the browser as an explicit prop. In the production image
    // (`node:22-slim`, no `TZ`) that is UTC. See `APP_TIME_ZONE`'s own comment.
    const config = await getRequestConfig({
      requestLocale: Promise.resolve('vi'),
      locale: 'vi',
    } as never);

    expect(config.timeZone).toBe(APP_TIME_ZONE);
    // Named, not just non-empty: `'UTC'` is a perfectly valid IANA zone, so a
    // test that only asked for truthiness would pass on the exact regression.
    expect(APP_TIME_ZONE).toBe('Asia/Ho_Chi_Minh');
  });

  it('renders a stored instant at its Vietnamese wall-clock time', () => {
    // The seed script's own example: 08:00 in Vietnam, stored as 01:00Z. This
    // is the assertion that fails if `APP_TIME_ZONE` is dropped or changed —
    // and it fails identically whatever zone the machine running it is in,
    // which is the property the dev-machine blind spot needs.
    const checkIn = new Date('2026-08-03T01:00:00Z');
    const format = createFormatter({
      locale: 'vi',
      formats,
      timeZone: APP_TIME_ZONE,
      onError: () => {},
    });

    expect(format.dateTime(checkIn, 'full')).toBe('08:00:00 3/8/2026');
    // The regression this pins, spelled out: the container default renders the
    // same instant seven hours early, on a date that can also be the day before.
    const asContainerWould = createFormatter({
      locale: 'vi',
      formats,
      timeZone: 'UTC',
      onError: () => {},
    });
    expect(asContainerWould.dateTime(checkIn, 'full')).toBe('01:00:00 3/8/2026');
  });

  it('degrades to an unlocalized `String(date)` when a preset is missing', () => {
    // The stake, executed rather than described: this is what the two
    // assertions above are protecting against, and it is why a missing preset
    // cannot be left to a console warning nobody reads in production.
    const when = new Date(Date.UTC(2026, 7, 9, 3, 4, 5));
    const withPresets = createFormatter({
      locale: 'en',
      formats,
      timeZone: 'UTC',
      onError: () => {},
    });
    const withoutPresets = createFormatter({
      locale: 'en',
      timeZone: 'UTC',
      onError: () => {},
    });

    expect(withPresets.dateTime(when, 'full')).toBe('8/9/2026, 3:04:05 AM');
    expect(withoutPresets.dateTime(when, 'full')).toBe(String(when));
    // ...and `String(when)` is neither localized nor even stable across the two
    // locales this app ships, which is the whole of #313 in one expression.
    expect(withoutPresets.dateTime(when, 'full')).toMatch(/^\w{3} \w{3} \d{2} 2026/);
  });
});
