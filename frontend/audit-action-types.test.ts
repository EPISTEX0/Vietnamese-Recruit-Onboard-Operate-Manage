/**
 * The invariant, in one sentence: `AuditActionType`
 * (`backend/src/modules/identity/domain/entities.py`) is the single source of
 * truth for what `audit_logs.action_type` can hold, and every frontend list
 * that reads from or writes to it must be provably in the right relationship
 * with it — not merely eyeballed equal.
 *
 * #331 found five parallel lists with zero constraints between them: the
 * backend enum (34), a hand-written TS union (15, missing 19 real values), a
 * filter-dropdown builder (44, ten of which no code path ever writes — an
 * admin who picks one of those ten always sees an empty result and reasonably
 * concludes "nobody did that", which is exactly the lie the System Admin
 * Console exists to prevent), a label map (45), and two i18n namespaces (44
 * each). `backend/scripts/gen_audit_action_types.py` closes the union gap by
 * generation: `AUDIT_ACTION_TYPES` in `lib/audit-action-types.generated.ts` is
 * derived from the enum by importing it, not by hand, so it cannot drift.
 * `backend/tests/modules/identity/test_audit_action_types_freshness.py` is
 * what catches the file going stale relative to the enum.
 *
 * This file is the other half: it checks the three lists that generation does
 * *not* replace, each against `AUDIT_ACTION_TYPES` rather than against each
 * other, because pairwise cross-checks between five lists is the wrong shape
 * — it is the enum, transitively, that every one of them must answer to.
 *
 * Two of the three relationships are **equality**, one is **superset**, and
 * the difference is deliberate, not an oversight:
 *
 * - `AUDIT_ACTION_GROUPS` (the filter dropdown) must equal `AUDIT_ACTION_TYPES`
 *   exactly. A group option pointing at a value nothing ever writes is #331's
 *   core defect restated: it can only ever filter to an empty list. A missing
 *   option is the same defect in the other direction — a real value with no
 *   way to filter for it.
 * - `AUDIT_ACTION_LABELS` must be a **superset**. It renders history rows, not
 *   a menu of live choices, and `audit_logs.action_type` is `varchar(50)`, not
 *   a Postgres enum — a row already in the database can carry a value the
 *   current enum no longer has. Deleting its label would not clean anything
 *   up; it would make an old row render a raw code instead of Vietnamese
 *   text. So the eleven keys `AUDIT_ACTION_TYPES` does not cover are kept,
 *   under one named, closed exception list (`LEGACY_AUDIT_ACTION_LABELS`,
 *   `components/shared-ui.tsx`) — the same shape `datetime-locale.test.ts`
 *   uses for `formatVND`. Closed means an *unnamed* stray key is still an
 *   error: only the eleven named ones are allowed to be present without also
 *   being in the enum.
 * - `messages/vi.json` and `messages/en.json`'s `audit` namespaces must each be
 *   a superset too, for the same reason `AUDIT_ACTION_LABELS` is — plus they
 *   must agree with each other key-for-key, since one locale silently missing
 *   a key the other has is its own, separate lie (a Vietnamese admin sees a
 *   label, an English-reading admin sees a raw code, for the same row).
 *
 * Every check here reads `AUDIT_ACTION_GROUPS`, `AUDIT_ACTION_LABELS`,
 * `LEGACY_AUDIT_ACTION_LABELS`, and `messages/*.json` as live, type-checked
 * imports (the last via `resolveJsonModule`, the same pattern
 * `knowledge-base/status-labels-i18n.test.tsx` uses) rather than by scanning
 * source text — unlike `tailwind-color-shades.test.ts` and
 * `datetime-locale.test.ts`, nothing here needs to recover structured data
 * from a file `tsc` does not otherwise parse. That sidesteps the extractor
 * risk this file's sibling guards have to design around explicitly: a
 * mis-shapen import fails at `tsc --noEmit`, before this suite runs at all.
 */
import { describe, expect, it } from 'vitest';

import { AUDIT_ACTION_TYPES } from '@/lib/audit-action-types.generated';
import { AUDIT_ACTION_GROUPS, AUDIT_ACTION_LABELS, LEGACY_AUDIT_ACTION_LABELS } from '@/components/shared-ui';
import enMessages from '@/messages/en.json';
import viMessages from '@/messages/vi.json';

const ENUM_VALUES = new Set<string>(AUDIT_ACTION_TYPES);

const AUDIT_NAMESPACES = { vi: viMessages.audit, en: enMessages.audit } as const;

describe('AUDIT_ACTION_GROUPS (filter dropdown) equals AUDIT_ACTION_TYPES exactly', () => {
  const groupValues = AUDIT_ACTION_GROUPS.flatMap((group) => group.items.map((item) => item.value));

  it('has no duplicate value across groups', () => {
    const duplicates = groupValues.filter((value, index) => groupValues.indexOf(value) !== index);
    expect(duplicates).toEqual([]);
  });

  it('has no option pointing at a value the enum does not have', () => {
    const phantom = groupValues.filter((value) => !ENUM_VALUES.has(value));
    expect(phantom).toEqual([]);
  });

  it('has an option for every value the enum has', () => {
    const groupValueSet = new Set(groupValues);
    const missing = AUDIT_ACTION_TYPES.filter((value) => !groupValueSet.has(value));
    expect(missing).toEqual([]);
  });
});

describe('AUDIT_ACTION_LABELS covers every value AUDIT_ACTION_TYPES has', () => {
  it('has a label for every real action type', () => {
    const missing = AUDIT_ACTION_TYPES.filter((value) => !(value in AUDIT_ACTION_LABELS));
    expect(missing).toEqual([]);
  });

  it('the only keys not in the enum are the named legacy exceptions', () => {
    const extra = Object.keys(AUDIT_ACTION_LABELS)
      .filter((key) => !ENUM_VALUES.has(key))
      .sort();
    expect(extra).toEqual(Object.keys(LEGACY_AUDIT_ACTION_LABELS).sort());
  });
});

describe('i18n `audit` namespace covers every value AUDIT_ACTION_TYPES has', () => {
  it.each([['vi'], ['en']] as const)('%s.json has a key for every real action type', (locale) => {
    const audit = AUDIT_NAMESPACES[locale];
    const missing = AUDIT_ACTION_TYPES.filter((value) => !(value in audit));
    expect(missing).toEqual([]);
  });

  it('vi.json and en.json declare the exact same audit keys', () => {
    const viKeys = Object.keys(AUDIT_NAMESPACES.vi).sort();
    const enKeys = Object.keys(AUDIT_NAMESPACES.en).sort();
    expect(viKeys).toEqual(enKeys);
  });
});

describe('the AUDIT_ACTION_TYPES floor this suite depends on', () => {
  // A regression in the generator that silently emptied AUDIT_ACTION_TYPES
  // would make every `.filter(...)` above vacuously pass (an empty "missing"
  // list either way). This pins the list to a size and a known member so that
  // "no offenders found" keeps meaning "looked, found none" rather than
  // "had nothing to look at".
  it('is not empty and contains a known real action type', () => {
    expect(AUDIT_ACTION_TYPES.length).toBeGreaterThanOrEqual(30);
    expect(AUDIT_ACTION_TYPES).toContain('whitelist_add');
  });
});
