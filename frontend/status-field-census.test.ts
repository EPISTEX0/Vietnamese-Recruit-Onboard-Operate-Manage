/**
 * The invariant, in one sentence: no `status`/`*_status` field declared in
 * `frontend/lib/api/` types is a bare `string` unless it is named in
 * `ALLOWLIST` below with a reason (#363).
 *
 * ## Why this, not just "narrow the 11/13 the ticket found"
 *
 * #363's own ticket body counted 11 such fields by `rg -n 'status: string|
 * processing_status\??: string'` and said so explicitly: that count is a
 * floor, not a target — the regex is blind to a field lifted out into its
 * own multi-line declaration or nested inside an inline object type (exactly
 * how `ProcessAttachmentsResponse.cv_documents[].processing_status` in
 * `gmail.ts` was declared). This file replaces the regex with a real
 * TypeScript AST walk over every `PropertySignature` in `lib/api/`, so a
 * future field shaped either way is still caught the day it is written.
 *
 * At the time of writing, re-running the *ticket's own* `rg` command finds
 * 13, not 11 — two fields (`knowledge-base.ts`, `payslips.ts`) were already
 * present when the ticket's census was taken and simply missed by hand. The
 * AST walk here finds the same 13, confirming the two extractors agree once
 * the same tree is scanned; the number is not re-derived from the ticket.
 *
 * ## What counts as a violation
 *
 * A `PropertySignature` whose name ends in `status` (case-insensitive,
 * covering `processing_status`, `previous_inbox_status`, etc.) and whose
 * type annotation — stripped of any `| null` / `| undefined` — is the plain
 * `string` keyword. A union of string *literals* (`"a" | "b"`) is not a
 * violation: that is exactly the narrowed shape this guard exists to push
 * fields toward.
 *
 * ## Guarding against a xanh-rỗng extractor
 *
 * `describe('the extractor itself')` below plants a known violation (bare
 * `status: string`) and a known-clean shape (a narrowed literal union) in a
 * synthetic source and asserts the walk tells them apart. Without this, an
 * extractor whose node-kind check silently stopped matching would leave the
 * real-tree assertion green on an empty violation list — which reads
 * exactly like "problem solved" instead of "extractor broken".
 *
 * ## `ALLOWLIST`
 *
 * Empty right now: every field the walk can find has been narrowed (#363).
 * If a future field genuinely must stay `string` (backend itself returns
 * free-form text — the KB module's `status` comment case is the nearest
 * precedent, see `backend/tests/modules/recruitment/test_status_meta_freshness.py`),
 * register it here with the reason inline, not just for the guard's sake.
 * `test_allowlist_entries_are_still_real` keeps stale entries from
 * accumulating: an entry naming a field the walk no longer finds as a bare
 * `string` fails loudly rather than silently over-excusing nothing.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const THIS_FILE = fileURLToPath(import.meta.url);
const PACKAGE_ROOT = dirname(THIS_FILE);
const LIB_API_ROOT = join(PACKAGE_ROOT, 'lib', 'api');

const STATUS_NAME = /status$/i;

/** `key: path#TypeMember` used to register an allowlist entry against a violation. */
type Violation = { file: string; line: number; key: string };

/** Is `t` the `null` or `undefined` member of a union — as `null` parses to `LiteralType(NullKeyword)`, not a bare keyword. */
function isNullishMember(t: ts.TypeNode): boolean {
  if (t.kind === ts.SyntaxKind.UndefinedKeyword) return true;
  if (ts.isLiteralTypeNode(t) && t.literal.kind === ts.SyntaxKind.NullKeyword) return true;
  return false;
}

/** Does `typeNode`, once `| null` / `| undefined` members are stripped, reduce to plain `string`? */
function isBareStringType(typeNode: ts.TypeNode | undefined): boolean {
  if (!typeNode) return false;
  if (typeNode.kind === ts.SyntaxKind.StringKeyword) return true;
  if (ts.isParenthesizedTypeNode(typeNode)) return isBareStringType(typeNode.type);
  if (ts.isUnionTypeNode(typeNode)) {
    const nonNullish = typeNode.types.filter((t) => !isNullishMember(t));
    // A union that still has >1 non-nullish member here is a union of
    // several real types (e.g. `string | number`) or of string literals —
    // neither is "bare string", so only a single stripped-down StringKeyword
    // counts.
    return nonNullish.length === 1 && isBareStringType(nonNullish[0]);
  }
  return false;
}

/** Every `status`-named `PropertySignature` typed as bare `string` in `sourceFile`. */
function findViolations(sourceFile: ts.SourceFile, relPath: string): Violation[] {
  const violations: Violation[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isPropertySignature(node) && node.name && ts.isIdentifier(node.name)) {
      const name = node.name.text;
      if (STATUS_NAME.test(name) && isBareStringType(node.type)) {
        const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
        violations.push({ file: relPath, line, key: `${relPath}#${name}@${line}` });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return violations;
}

function parseFile(path: string): ts.SourceFile {
  const text = readFileSync(path, 'utf8');
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, kind);
}

function censusLibApi(): Violation[] {
  const violations: Violation[] = [];
  for (const entry of readdirSync(LIB_API_ROOT, { withFileTypes: true })) {
    if (!entry.isFile()) continue;
    if (!['.ts', '.tsx'].includes(extname(entry.name))) continue;
    if (/\.(test|spec)\.tsx?$/.test(entry.name)) continue;
    const path = join(LIB_API_ROOT, entry.name);
    violations.push(...findViolations(parseFile(path), `lib/api/${entry.name}`));
  }
  return violations;
}

/**
 * Fields intentionally left as bare `string`, with the reason inline.
 * Empty at the time of writing (#363) — see header comment.
 */
const ALLOWLIST: Record<string, string> = {};

describe('the extractor itself', () => {
  it('flags a bare `status: string` field', () => {
    const source = ts.createSourceFile(
      'fixture.ts',
      'interface Foo { status: string; }',
      ts.ScriptTarget.Latest,
      true,
    );
    const violations = findViolations(source, 'fixture.ts');
    expect(violations).toHaveLength(1);
    expect(violations[0].key).toBe('fixture.ts#status@1');
  });

  it('flags a nested inline `processing_status: string`, the shape a line-based regex misses', () => {
    const source = ts.createSourceFile(
      'fixture.ts',
      'interface Foo { items?: Array<{ processing_status: string }>; }',
      ts.ScriptTarget.Latest,
      true,
    );
    expect(findViolations(source, 'fixture.ts')).toHaveLength(1);
  });

  it('flags a bare `status?: string | null` field (nullable does not exempt it)', () => {
    const source = ts.createSourceFile(
      'fixture.ts',
      'interface Foo { status?: string | null; }',
      ts.ScriptTarget.Latest,
      true,
    );
    expect(findViolations(source, 'fixture.ts')).toHaveLength(1);
  });

  it('stays quiet on a narrowed string-literal union', () => {
    const source = ts.createSourceFile(
      'fixture.ts',
      'type S = "a" | "b"; interface Foo { status: S; }',
      ts.ScriptTarget.Latest,
      true,
    );
    expect(findViolations(source, 'fixture.ts')).toEqual([]);
  });

  it('stays quiet on a field that merely contains "status" but does not end with it', () => {
    const source = ts.createSourceFile(
      'fixture.ts',
      'interface Foo { status_history: string; }',
      ts.ScriptTarget.Latest,
      true,
    );
    expect(findViolations(source, 'fixture.ts')).toEqual([]);
  });

  it('stays quiet on `status: string | number` — not reducible to plain string', () => {
    const source = ts.createSourceFile(
      'fixture.ts',
      'interface Foo { status: string | number; }',
      ts.ScriptTarget.Latest,
      true,
    );
    expect(findViolations(source, 'fixture.ts')).toEqual([]);
  });
});

describe('lib/api/', () => {
  const violations = censusLibApi();
  const unexcused = violations.filter((v) => !(v.key in ALLOWLIST));

  it('has no status field declared as bare string outside ALLOWLIST', () => {
    expect(unexcused.map((v) => `${v.file}:${v.line}`)).toEqual([]);
  });

  it('keeps ALLOWLIST honest: every entry still names a real violation', () => {
    const found = new Set(violations.map((v) => v.key));
    const stale = Object.keys(ALLOWLIST).filter((key) => !found.has(key));
    expect(stale).toEqual([]);
  });
});
