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

import { describe, expect, it } from 'vitest';

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
