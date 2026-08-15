/**
 * @vitest-environment jsdom
 *
 * `StatusPill` must take its colours from the canonical semantic palette in
 * `shared-ui`, not from a tone table of its own. This file is the witness for
 * #316, which merged the pill's inline table into `BADGE_TONE_PARTS`; the
 * doc comment on that table records why the split was a defect.
 *
 * The assertions are deliberately driven off `Object.keys(BADGE_TONE_PARTS)`
 * rather than a list written here: they say "the pill wears every tone the
 * canonical table publishes, spelled the way the table spells it". That is the
 * property the merge establishes, and it is what dies if someone re-adds an
 * inline table — any second table is a copy that has to be kept in step, and
 * the first tone added to the canonical one without it fails here.
 *
 * Same pairing as `settings/stat-card-tone.test.tsx`: this file catches a
 * consumer that stops going through the table, and `components/shared-ui.test.ts`
 * pins the table's own literal values so both sides cannot move together.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';

import { BADGE_TONE_PARTS, StatusPill, type BadgeTone } from '@/components/shared-ui';

const TONES = Object.keys(BADGE_TONE_PARTS) as BadgeTone[];

const classesOf = (el: Element) => el.getAttribute('class')?.split(/\s+/) ?? [];

/**
 * Render one pill and hand back the `<span>` carrying the tone classes.
 *
 * Reached through this render's own `container` rather than a `screen` query,
 * because the last case below renders every tone inside a single test and a
 * document-wide lookup would find all seven.
 */
function pillWith(props: { tone?: BadgeTone }): Element {
  const { container } = render(<StatusPill status="any" label="Trạng thái" {...props} />);
  const pill = container.firstElementChild;
  if (!pill) throw new Error('StatusPill rendered nothing');
  return pill;
}

/** The tone classes on a pill, ignoring its layout and typography classes. */
const tintOf = (pill: Element) =>
  new Set(classesOf(pill).filter((cls) => /^(bg|text|border)-[a-z]+-\d{2,3}$/.test(cls)));

describe('StatusPill tones', () => {
  it.each(TONES)('wears the canonical %s tone and nothing else', (tone) => {
    // Exact, not `toContain`: the looser check would pass a pill that wore the
    // right three classes *and* kept a stray `text-slate-600` from an old table
    // alongside them.
    const { bg, fg, border } = BADGE_TONE_PARTS[tone];

    expect(tintOf(pillWith({ tone }))).toEqual(new Set([bg, fg, border]));
  });

  it('defaults to the canonical slate, not a shade of its own', () => {
    // The default is where the two tables disagreed most quietly: both called
    // it `slate` and rendered a different `text-slate-*`.
    const { bg, fg, border } = BADGE_TONE_PARTS.slate;

    expect(tintOf(pillWith({}))).toEqual(new Set([bg, fg, border]));
  });

  it('reads the table at render time rather than a copy of its values', () => {
    // The cases above all die if a re-added inline table *drifts* from the
    // canonical one. None of them die if someone pastes an exact copy — and an
    // exact copy is precisely how #316's defect started: two tables that agreed
    // on the day they were written and disagreed in three places by the time
    // anyone looked.
    //
    // So this one forces the seam. A tone that exists only at runtime cannot
    // appear in any literal in `shared-ui.tsx`; the pill can only render it by
    // actually looking in `BADGE_TONE_PARTS`. Deliberately not a Tailwind
    // colour, so nothing is asked of the CSS build.
    const probe = 'probe' as BadgeTone;
    const parts = { bg: 'bg-probe-50', fg: 'text-probe-700', border: 'border-probe-200' };

    BADGE_TONE_PARTS[probe] = parts;
    try {
      const classes = classesOf(pillWith({ tone: probe }));

      expect(classes).toContain(parts.bg);
      expect(classes).toContain(parts.fg);
      expect(classes).toContain(parts.border);
    } finally {
      // Shared module state: leaving the probe behind would add an eighth tone
      // to every other file in the run.
      delete BADGE_TONE_PARTS[probe];
    }
  });
});
