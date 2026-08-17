/**
 * The invariant, in one sentence: every `queryKey` that two or more `useQuery`
 * observers declare must agree on *effective* `staleTime` — the window either
 * one actually honours, not merely the literal each happens to write.
 *
 * `shared-query-staleness.test.tsx` checks this by rendering named pairs of
 * pages against a real `QueryClient` and counting network requests — it is
 * the file that pays the cost of proving the *behaviour* matches, for the
 * surfaces someone thought to list by hand. It went green while
 * `['google-calendars']` (`recruitment/interviews/page.tsx` vs
 * `recruitment/candidates/[id]/page.tsx`) disagreed, because nobody had added
 * that pair to its table (#340). This file is the other half: it does not
 * know in advance which keys are shared — it reads every `useQuery` call in
 * the package and derives the set itself, so a new page sharing an existing
 * key is covered the day it is written rather than the day someone remembers
 * to list it. The two files are not redundant: one measures behaviour on a
 * hand-picked set, the other measures coverage on the derived one. Keep both.
 *
 * "Effective", not "declared", is the whole difficulty. React Query's own
 * default only applies when an observer's own `useQuery` call is silent on
 * `staleTime` — and this app hands every observer a `QueryClient` built by
 * `createQueryClient()` (`lib/query-client.ts`), whose `defaultOptions.queries`
 * sets `staleTime: 300 * 1000`. An observer that declares nothing is on a
 * **five-minute** window, not a zero one. Treating "not written" as `0` is
 * backwards in exactly the way that matters: it says the *longer*-window
 * surface is the safe one and the *shorter* one is the outlier, when the
 * silent surface is actually the one serving staler data. `DEFAULT_STALE_TIME`
 * below is not typed in as a second copy of `300 * 1000` — it is read out of
 * `lib/query-client.ts` by the same AST walk this file uses everywhere else,
 * so the two cannot quietly drift apart the way a hand-copied constant could.
 *
 * A broken extractor here does not go red — it goes green on nothing, which
 * reads exactly like "no shared keys, all fine". So this file carries the
 * same two defences `test_depends_provider_call_census.py`
 * (`backend/tests/`) does for the equivalent backend risk:
 *
 * - A **known-positive**: synthetic sources below, planting the exact shape
 *   of #340 (a key shared by two observers, one at 60 000 and one inheriting
 *   the 300 000 default), and asserting the census reports it. A census that
 *   silently stopped matching `useQuery` calls, or stopped resolving the
 *   default, would fail these before it could ever look convincingly clean.
 * - A **floor**, measured against the real tree rather than guessed: at the
 *   time of writing this scan finds 69 `useQuery` observers across the
 *   package, 41 of them naming a fully literal `queryKey` it can compare
 *   across call sites (directly or through the one-hop alias below), spanning
 *   27 distinct literal keys, of which 10 are shared by two or more observers
 *   (24 of the 41 belong to one of those 10 groups) — matching, once the
 *   alias hop is in, the count the ticket this guard replaces a hand-kept
 *   table for had already found by eye. The assertions below pin round
 *   numbers under each of those, so a regression that silently narrowed the
 *   scan is caught long before it reaches zero.
 *
 * Known limits, stated rather than implied
 * -----------------------------------------
 *
 * - Only `queryKey` arrays whose every element is a string or number literal
 *   are compared. `['recruitment-candidates', params]` and similar
 *   parameterised keys are real `useQuery` calls this file counts as
 *   *observers*, but their key cannot be compared across call sites — a
 *   second call with the same literal prefix and a different `params` shape
 *   may or may not be the same cache entry at runtime, and nothing here
 *   guesses. Measured: 28 of the 69 observers found have a non-literal
 *   `queryKey` element (or a reference this scan cannot resolve, see below)
 *   and are excluded from key grouping for this reason.
 * - A `queryKey` referenced by name is resolved one hop: `const X = [...]`
 *   declared at the top level of the *same file* and used as `queryKey: X`
 *   is read as if written inline (`oauth/page.tsx`'s `OAUTH_CONFIG_KEY` is
 *   the one real case, and closing this gap is what makes it group with the
 *   `['oauth-config']` literal in `settings/page.tsx` rather than vanish as
 *   two apparently-unrelated keys). A `let`, a re-assignment, an import from
 *   another module, or an array built with anything but a literal are not
 *   followed — they fall back to the same "non-literal, excluded" case above.
 * - `staleTime` is evaluated only as a numeric literal or `*`/`/`/`+`/`-`
 *   between literals (`60 * 1000`, `30_000`), which is every form this
 *   codebase currently writes. A `staleTime` referencing an imported constant
 *   or a function call would be unevaluable; that one observer is reported as
 *   a blind spot and excluded from comparison, but its key group is not — two
 *   *other* observers on the same key still get compared against each other.
 * - An options object built with `{ ...shared, queryKey: [...] }` is
 *   recognised (the spread is a blind spot, not a silent read of only the
 *   named properties), but there are none in this tree today.
 * - `useQuery` is matched by identifier name, not by resolving the import.
 *   Every call site in this package imports it from `@tanstack/react-query`
 *   unaliased (checked by hand); a local function also named `useQuery`
 *   would be misattributed, and there are none today.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const THIS_FILE = fileURLToPath(import.meta.url);
const PACKAGE_ROOT = dirname(THIS_FILE);

const SCANNED_EXTENSIONS = new Set(['.ts', '.tsx']);
const SKIPPED_DIRECTORIES = new Set(['node_modules', '.next', '.git', 'coverage', 'dist', 'out']);
const TEST_FILE = /\.(test|spec)\.(ts|tsx)$/;

function* sourceFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIPPED_DIRECTORIES.has(entry.name)) yield* sourceFiles(full);
    } else if (entry.isFile() && SCANNED_EXTENSIONS.has(extname(entry.name)) && !TEST_FILE.test(entry.name)) {
      yield full;
    }
  }
}

function parseFile(path: string): ts.SourceFile {
  const text = readFileSync(path, 'utf8');
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, kind);
}

/** Evaluate the numeric-literal-arithmetic shapes this codebase writes `staleTime` as. */
function evalNumeric(node: ts.Expression): number | undefined {
  if (ts.isParenthesizedExpression(node)) return evalNumeric(node.expression);
  if (ts.isNumericLiteral(node)) return Number(node.text.replace(/_/g, ''));
  if (ts.isPrefixUnaryExpression(node) && node.operator === ts.SyntaxKind.MinusToken) {
    const value = evalNumeric(node.operand as ts.Expression);
    return value === undefined ? undefined : -value;
  }
  if (ts.isBinaryExpression(node)) {
    const left = evalNumeric(node.left);
    const right = evalNumeric(node.right);
    if (left === undefined || right === undefined) return undefined;
    switch (node.operatorToken.kind) {
      case ts.SyntaxKind.AsteriskToken: return left * right;
      case ts.SyntaxKind.SlashToken: return left / right;
      case ts.SyntaxKind.PlusToken: return left + right;
      case ts.SyntaxKind.MinusToken: return left - right;
      default: return undefined;
    }
  }
  return undefined;
}

/** `const NAME = [literal, literal, ...]` declarations at a source file's top level. */
function collectLocalArrayAliases(sourceFile: ts.SourceFile): Map<string, ts.ArrayLiteralExpression> {
  const aliases = new Map<string, ts.ArrayLiteralExpression>();
  for (const statement of sourceFile.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (
        ts.isIdentifier(declaration.name) &&
        declaration.initializer &&
        ts.isArrayLiteralExpression(declaration.initializer)
      ) {
        aliases.set(declaration.name.text, declaration.initializer);
      }
    }
  }
  return aliases;
}

/**
 * The literal, JSON-comparable form of a `queryKey` expression, resolving one
 * hop through `aliases` first. `undefined` for anything with a non-literal
 * element, or an identifier `aliases` does not know.
 */
function literalKey(node: ts.Expression, aliases: Map<string, ts.ArrayLiteralExpression>): string | undefined {
  const array = ts.isIdentifier(node) ? aliases.get(node.text) : node;
  if (!array || !ts.isArrayLiteralExpression(array)) return undefined;
  const parts: (string | number)[] = [];
  for (const element of array.elements) {
    if (ts.isStringLiteralLike(element)) parts.push(element.text);
    else if (ts.isNumericLiteral(element)) parts.push(Number(element.text));
    else return undefined;
  }
  return JSON.stringify(parts);
}

/** One `useQuery` call site this scan could read a `queryKey` and effective `staleTime` from. */
type Observer = {
  file: string;
  line: number;
  /** `undefined` when the key has a non-literal element or is an unresolved reference. */
  key: string | undefined;
  /** `undefined` when `staleTime` is declared but not one of the evaluable shapes. */
  effectiveStaleTime: number | undefined;
};

type BlindSpot = { file: string; line: number; reason: string };

/** Every `useQuery(...)` call in `sourceFile`, plus anything this scan could not read through. */
function extractObservers(
  sourceFile: ts.SourceFile,
  relPath: string,
  appDefaultStaleTime: number,
): { observers: Observer[]; blindSpots: BlindSpot[] } {
  const observers: Observer[] = [];
  const blindSpots: BlindSpot[] = [];
  const aliases = collectLocalArrayAliases(sourceFile);

  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'useQuery') {
      const line = sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1;
      const arg = node.arguments[0];

      if (!arg || !ts.isObjectLiteralExpression(arg)) {
        blindSpots.push({ file: relPath, line, reason: 'useQuery call whose first argument is not an object literal' });
      } else {
        let queryKeyNode: ts.Expression | undefined;
        let staleTimeNode: ts.Expression | undefined;
        let hasSpread = false;
        for (const prop of arg.properties) {
          if (ts.isSpreadAssignment(prop)) { hasSpread = true; continue; }
          if (!ts.isPropertyAssignment(prop) || !ts.isIdentifier(prop.name)) continue;
          if (prop.name.text === 'queryKey') queryKeyNode = prop.initializer;
          if (prop.name.text === 'staleTime') staleTimeNode = prop.initializer;
        }

        if (hasSpread) {
          blindSpots.push({
            file: relPath,
            line,
            reason: 'useQuery options object spreads another object, which may set queryKey/staleTime this scan cannot see',
          });
        } else if (!queryKeyNode) {
          blindSpots.push({ file: relPath, line, reason: 'useQuery call has no queryKey property this scan can see' });
        } else {
          const key = literalKey(queryKeyNode, aliases);

          let effectiveStaleTime: number | undefined;
          if (!staleTimeNode) {
            effectiveStaleTime = appDefaultStaleTime;
          } else {
            effectiveStaleTime = evalNumeric(staleTimeNode);
            if (effectiveStaleTime === undefined) {
              blindSpots.push({
                file: relPath,
                line,
                reason: `staleTime is not a literal or literal arithmetic this scan can evaluate: \`${staleTimeNode.getText()}\``,
              });
            }
          }

          observers.push({ file: relPath, line, key, effectiveStaleTime });
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return { observers, blindSpots };
}

/** The `defaultOptions.queries.staleTime` `createQueryClient()` gives every observer that declares none. */
function extractAppDefaultStaleTime(sourceFile: ts.SourceFile): number {
  const found: number[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isPropertyAssignment(node) && ts.isIdentifier(node.name) && node.name.text === 'staleTime') {
      const value = evalNumeric(node.initializer);
      if (value !== undefined) found.push(value);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  if (found.length !== 1) {
    throw new Error(
      `expected exactly one evaluable top-level staleTime in lib/query-client.ts, found ${found.length}: [${found.join(', ')}]. ` +
      'The app-wide default this census relies on could not be read with confidence.',
    );
  }
  return found[0];
}

type Finding = { key: string; observers: Observer[] };

/** Every literal key shared by two or more observers whose effective staleTime disagrees. */
function findStalenessMismatches(observers: Observer[]): Finding[] {
  const byKey = new Map<string, Observer[]>();
  for (const observer of observers) {
    if (observer.key === undefined) continue;
    const list = byKey.get(observer.key) ?? [];
    list.push(observer);
    byKey.set(observer.key, list);
  }

  const findings: Finding[] = [];
  for (const [key, list] of byKey) {
    // An unknown effective value (a blind-spot staleTime) makes that one
    // observer's agreement unprovable in either direction — it is reported as
    // a blind spot already, and left out here rather than guessed at as
    // matching or mismatching. The *other* observers on the same key are not
    // withheld with it: if they already disagree with each other, that is
    // provable without knowing the unresolved one at all.
    const known = list.filter((o): o is Observer & { effectiveStaleTime: number } => o.effectiveStaleTime !== undefined);
    if (known.length < 2) continue;
    const distinctValues = new Set(known.map((o) => o.effectiveStaleTime));
    if (distinctValues.size > 1) findings.push({ key, observers: known });
  }
  return findings.sort((a, b) => a.key.localeCompare(b.key));
}

function describeFinding(finding: Finding): string {
  const rows = finding.observers
    .map((o) => `${o.file}:${o.line} staleTime=${o.effectiveStaleTime}`)
    .join(', ');
  return `${finding.key} -> ${rows}`;
}

type CensusResult = {
  filesScanned: number;
  appDefaultStaleTime: number;
  observers: Observer[];
  blindSpots: BlindSpot[];
  findings: Finding[];
};

function censusRepository(): CensusResult {
  const appDefaultStaleTime = extractAppDefaultStaleTime(parseFile(join(PACKAGE_ROOT, 'lib/query-client.ts')));

  const observers: Observer[] = [];
  const blindSpots: BlindSpot[] = [];
  let filesScanned = 0;
  for (const file of sourceFiles(PACKAGE_ROOT)) {
    filesScanned++;
    const sourceFile = parseFile(file);
    const { observers: found, blindSpots: spots } = extractObservers(
      sourceFile,
      relative(PACKAGE_ROOT, file),
      appDefaultStaleTime,
    );
    observers.push(...found);
    blindSpots.push(...spots);
  }

  return { filesScanned, appDefaultStaleTime, observers, blindSpots, findings: findStalenessMismatches(observers) };
}

/** Parse literal source for the self-tests, without touching disk. */
function analyze(source: string, appDefaultStaleTime = 300_000) {
  const sourceFile = ts.createSourceFile('fixture.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  return extractObservers(sourceFile, 'fixture.tsx', appDefaultStaleTime);
}

describe('the app-wide default this census relies on', () => {
  it('reads staleTime: 300 * 1000 out of lib/query-client.ts, not a copy of the number', () => {
    const value = extractAppDefaultStaleTime(parseFile(join(PACKAGE_ROOT, 'lib/query-client.ts')));
    expect(value).toBe(300_000);
  });

  it('refuses to guess when the shape it expects is not there', () => {
    const empty = ts.createSourceFile('x.ts', 'export const x = 1;', ts.ScriptTarget.Latest, true);
    expect(() => extractAppDefaultStaleTime(empty)).toThrow(/expected exactly one/);
  });
});

describe('the census itself', () => {
  it('reports two observers on the same key disagreeing on staleTime', () => {
    const { observers, blindSpots } = analyze(`
      useQuery({ queryKey: ['google-calendars'], queryFn: a, staleTime: 60 * 1000 });
      useQuery({ queryKey: ['google-calendars'], queryFn: b, staleTime: 5 * 60 * 1000 });
    `);
    expect(blindSpots).toEqual([]);
    const findings = findStalenessMismatches(observers);
    expect(findings).toHaveLength(1);
    expect(findings[0].key).toBe('["google-calendars"]');
    expect(findings[0].observers.map((o) => o.effectiveStaleTime).sort((a, b) => (a ?? 0) - (b ?? 0)))
      .toEqual([60_000, 300_000]);
  });

  it('stays quiet when both observers declare the same staleTime', () => {
    const { observers } = analyze(`
      useQuery({ queryKey: ['x'], queryFn: a, staleTime: 60_000 });
      useQuery({ queryKey: ['x'], queryFn: b, staleTime: 60_000 });
    `);
    expect(findStalenessMismatches(observers)).toEqual([]);
  });

  it('treats an undeclared staleTime as the app default, not zero — the #310 direction', () => {
    const { observers } = analyze(`
      useQuery({ queryKey: ['x'], queryFn: a });
      useQuery({ queryKey: ['x'], queryFn: b, staleTime: 300_000 });
    `, 300_000);
    // Agrees: the undeclared observer inherits exactly 300_000, the same as
    // the other's explicit value. A census reading "undeclared" as 0 would
    // report a mismatch here that does not exist.
    expect(findStalenessMismatches(observers)).toEqual([]);
    expect(observers.find((o) => o.line === 2)?.effectiveStaleTime).toBe(300_000);
  });

  it('reports a mismatch between an undeclared observer and a shorter explicit one', () => {
    const { observers } = analyze(`
      useQuery({ queryKey: ['x'], queryFn: a });
      useQuery({ queryKey: ['x'], queryFn: b, staleTime: 60_000 });
    `, 300_000);
    const findings = findStalenessMismatches(observers);
    expect(findings).toHaveLength(1);
    // The undeclared observer's effective value is the app default, not 0.
    expect(findings[0].observers.map((o) => o.effectiveStaleTime).sort((a, b) => (a ?? 0) - (b ?? 0))).toEqual([60_000, 300_000]);
  });

  it('does not report a key only one observer uses', () => {
    const { observers } = analyze(`useQuery({ queryKey: ['only-one'], queryFn: a, staleTime: 1 });`);
    expect(findStalenessMismatches(observers)).toEqual([]);
  });

  it('resolves a same-file const array alias to the literal it holds', () => {
    const { observers, blindSpots } = analyze(`
      const KEY = ['aliased'];
      useQuery({ queryKey: KEY, queryFn: a, staleTime: 60_000 });
      useQuery({ queryKey: ['aliased'], queryFn: b, staleTime: 300_000 });
    `);
    expect(blindSpots).toEqual([]);
    const findings = findStalenessMismatches(observers);
    expect(findings).toHaveLength(1);
    expect(findings[0].key).toBe('["aliased"]');
  });

  it('excludes a queryKey with a dynamic element from grouping, rather than guessing', () => {
    const { observers } = analyze(`
      useQuery({ queryKey: ['jobs', id], queryFn: a, staleTime: 60_000 });
      useQuery({ queryKey: ['jobs', otherId], queryFn: b, staleTime: 300_000 });
    `);
    expect(observers.every((o) => o.key === undefined)).toBe(true);
    expect(findStalenessMismatches(observers)).toEqual([]);
  });

  it('flags a spread options object as a blind spot rather than reading past it', () => {
    const { blindSpots } = analyze(`useQuery({ ...shared, queryKey: ['x'], staleTime: 60_000 });`);
    expect(blindSpots).toHaveLength(1);
    expect(blindSpots[0].reason).toMatch(/spread/);
  });

  it('flags an unevaluable staleTime as a blind spot and excludes only that one observer from findings', () => {
    const { observers, blindSpots } = analyze(`
      useQuery({ queryKey: ['x'], queryFn: a, staleTime: SOME_CONSTANT });
      useQuery({ queryKey: ['x'], queryFn: b, staleTime: 60_000 });
    `);
    expect(blindSpots).toHaveLength(1);
    expect(blindSpots[0].reason).toMatch(/SOME_CONSTANT/);
    // With only one other known observer on this key, agreement is
    // unprovable rather than false — the blind spot above is what surfaces it.
    expect(findStalenessMismatches(observers)).toEqual([]);
  });

  it('still reports a mismatch between two known observers when a third on the same key is unresolvable', () => {
    const { observers, blindSpots } = analyze(`
      useQuery({ queryKey: ['x'], queryFn: a, staleTime: SOME_CONSTANT });
      useQuery({ queryKey: ['x'], queryFn: b, staleTime: 60_000 });
      useQuery({ queryKey: ['x'], queryFn: c, staleTime: 300_000 });
    `);
    expect(blindSpots).toHaveLength(1);
    // b and c disagree regardless of what a's unresolved staleTime turns out
    // to be — withholding the whole group because of a would hide a
    // mismatch this scan already has enough information to prove.
    const findings = findStalenessMismatches(observers);
    expect(findings).toHaveLength(1);
    expect(findings[0].observers).toHaveLength(2);
    expect(findings[0].observers.map((o) => o.effectiveStaleTime).sort((a, b) => (a ?? 0) - (b ?? 0))).toEqual([60_000, 300_000]);
  });
});

describe('the real repository', () => {
  const census = censusRepository();

  it('scans the whole package rather than a narrowed subset', () => {
    expect(census.filesScanned).toBeGreaterThan(90);
  });

  // Both floors below are measured, not guessed (see header comment), and set
  // a round number under the real count so a regression that silently
  // narrowed the scan is caught long before it reaches zero.
  it('finds a healthy number of useQuery observers and literal keys', () => {
    // Measured 69 / 41 / 27 at the time of writing (see header comment).
    expect(census.observers.length).toBeGreaterThanOrEqual(60);
    const literalKeyObservers = census.observers.filter((o) => o.key !== undefined);
    expect(literalKeyObservers.length).toBeGreaterThanOrEqual(36);
    const distinctKeys = new Set(literalKeyObservers.map((o) => o.key));
    expect(distinctKeys.size).toBeGreaterThanOrEqual(22);
  });

  it('finds a healthy number of keys actually shared by two or more observers', () => {
    // Measured 10 keys / 24 observers at the time of writing.
    const byKey = new Map<string, number>();
    for (const o of census.observers) {
      if (o.key === undefined) continue;
      byKey.set(o.key, (byKey.get(o.key) ?? 0) + 1);
    }
    const shared = [...byKey.values()].filter((count) => count >= 2);
    expect(shared.length).toBeGreaterThanOrEqual(9);
    expect(shared.reduce((sum, count) => sum + count, 0)).toBeGreaterThanOrEqual(22);
  });

  it('has no call site this scan could not read through', () => {
    expect(census.blindSpots).toEqual([]);
  });

  it('has every queryKey shared by two or more observers agreeing on effective staleTime', () => {
    expect(census.findings.map(describeFinding)).toEqual([]);
  });
});
