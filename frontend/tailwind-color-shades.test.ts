/**
 * The invariant, in one sentence: every Tailwind colour utility written as a
 * literal in this package names a shade that exists on the scale of the colour
 * it names — so it emits a real CSS rule instead of nothing.
 *
 * Note the shape of that sentence: it is conditional on the colour being one
 * the palette knows. Utilities naming an unknown *colour* are a separate class
 * of the same defect and are not reported here — see the third blind spot at
 * the end of this comment for why.
 *
 * Nothing else in the toolchain checks this. A shade Tailwind does not know is
 * not an error to `tsc` (it is a string), not an error to `eslint` (it is a
 * string), and not an error to `next build` (the page renders, the class lands
 * in the DOM, and matches no rule). It is not even visibly broken: `.border`
 * compiles to width and style but no colour, so an unmatched `border-rose-250`
 * leaves `border-color` at its initial `currentColor` and the box still draws a
 * border — in the text colour instead of the intended tint. That is how #317
 * shipped `border-rose-250` and `border-slate-250` and survived review: both
 * classes read as entirely plausible and both render *something*.
 *
 * Both sides of the comparison are derived, not typed out here:
 *
 * - The palette comes from `tailwindcss/theme.css`, the same file the build
 *   compiles against, plus any `@theme` block in `app/globals.css`. So the
 *   scale this checks against is whatever the installed version says it is, and
 *   a custom colour the design system adds later is accepted the day it is
 *   added rather than the day someone remembers to update this file.
 * - The files come from walking the package. A hand-written list of files or of
 *   known-bad classes would read as exhaustive while covering only what its
 *   author happened to think of — the failure mode this repo already paid for
 *   in #340.
 *
 * Two blind spots, stated rather than left to be rediscovered:
 *
 * 1. Composed class names (`` `bg-${tone}-50` ``) are invisible to any static
 *    scan, including this one. The tree has none today, and `shared-ui.tsx`
 *    already warns against them for the separate reason that Tailwind's own
 *    content scan cannot see them either — but nothing stops one being added,
 *    and this guard would stay green if one were.
 * 2. Arbitrary values (`border-[#abc123]`, `bg-[--brand]`) are valid syntax and
 *    are deliberately not reported. They carry their own colour rather than
 *    naming a palette entry, so there is no shade to check.
 *
 * A third, narrower one: this reports a *known colour with an unknown shade*.
 * An unknown colour name (`bg-brand-500` with no `brand` in the palette) emits
 * no CSS for the same reason, but flagging it would mean deciding which
 * `<word>-<word>-<number>` strings in the tree are meant to be classes at all,
 * and that guess produces false positives. Measured rather than assumed: a
 * variant of this scan that also reported unknown colour names found four
 * hits on the current tree and all four were wrong — `border-r-0` (a border
 * *width*, not a colour) and the three `*-probe-*` strings in
 * `status-pill-tone.test.tsx`, which are a synthetic tone that file introduces
 * precisely because it is not a Tailwind colour. Zero real ones, four false.
 * That ratio is the reason for the narrower invariant.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const THIS_FILE = fileURLToPath(import.meta.url);
const PACKAGE_ROOT = dirname(THIS_FILE);

/** Colour name → the shades the palette defines for it. */
type Palette = Map<string, Set<string>>;

/**
 * The body of every `@theme` block in `css`.
 *
 * Only `@theme` puts a colour into the palette. A `--color-slate-250` declared
 * in a plain `:root` block is an ordinary CSS variable that generates no
 * utility, so reading the whole file would let this guard bless a shade the
 * build still emits nothing for — the exact failure it exists to catch.
 */
function themeBlocks(css: string): string[] {
  const bodies: string[] = [];
  for (const opening of css.matchAll(/@theme\b[^{]*\{/g)) {
    const start = (opening.index ?? 0) + opening[0].length;
    let end = start;
    for (let depth = 1; depth > 0 && end < css.length; end++) {
      if (css[end] === '{') depth++;
      else if (css[end] === '}') depth--;
    }
    bodies.push(css.slice(start, end - 1));
  }
  return bodies;
}

/**
 * Every `--color-<name>-<shade>` the `@theme` blocks of `css` define, merged
 * into `into`.
 *
 * Reads the CSS rather than importing `tailwindcss/colors`, because the CSS is
 * what `@import "tailwindcss"` actually pulls in; the JS export is a second
 * copy that could in principle disagree with it.
 */
function collectPalette(css: string, into: Palette): Palette {
  for (const [, name, shade] of themeBlocks(css).join('\n').matchAll(/--color-([a-z][a-z0-9-]*?)-(\d+)\s*:/g)) {
    const shades = into.get(name) ?? new Set<string>();
    shades.add(shade);
    into.set(name, shades);
  }
  return into;
}

function loadPalette(): Palette {
  const require = createRequire(import.meta.url);
  const palette = collectPalette(
    readFileSync(require.resolve('tailwindcss/theme.css'), 'utf8'),
    new Map(),
  );
  // Project extensions are added on top. `globals.css` declares no `@theme`
  // today, which is exactly why `250` resolves to nothing; reading it anyway
  // means the guard does not go red the day the design system defines a colour
  // of its own.
  return collectPalette(readFileSync(join(PACKAGE_ROOT, 'app/globals.css'), 'utf8'), palette);
}

/**
 * A `<utility>-<colour>-<shade>` occurrence found in source text.
 *
 * `utility` is captured so a report can show the whole class rather than the
 * colour fragment, which is what makes a failure message searchable.
 */
type ColorUtility = { utility: string; color: string; shade: string; line: number };

/**
 * Every colour utility in `text`, for the colour names `palette` knows.
 *
 * The colour names come from the palette rather than the utility prefixes,
 * because prefixes are open-ended (`bg`, `text`, `border-t`, `divide`, `ring`,
 * `from`, `decoration`, …) while the colour list is closed and already loaded.
 * The leading `(?<![a-z0-9-])` is what keeps a custom property declaration from
 * matching itself: in `--color-rose-250:` every candidate start is preceded by
 * a letter or a dash, so the definition of a colour is never read as a use.
 */
function findColorUtilities(text: string, palette: Palette): ColorUtility[] {
  const names = [...palette.keys()].join('|');
  const pattern = new RegExp(
    String.raw`(?<![a-z0-9-])([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(${names})-(\d+)(?![0-9a-z-])`,
    'g',
  );

  const found: ColorUtility[] = [];
  text.split('\n').forEach((text, index) => {
    for (const [, utility, color, shade] of text.matchAll(pattern)) {
      found.push({ utility: `${utility}-${color}-${shade}`, color, shade, line: index + 1 });
    }
  });
  return found;
}

const SCANNED_EXTENSIONS = new Set(['.ts', '.tsx', '.mts', '.js', '.jsx', '.mjs', '.cjs', '.css']);

/**
 * Directories with no authored source in them. Not a list of what to check —
 * everything else in the package is checked, whether or not it existed when
 * this was written.
 */
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

describe('Tailwind colour utilities', () => {
  const palette = loadPalette();

  // Both halves of the scan can fail open: a palette that failed to resolve
  // knows no colour names and matches nothing, and a walk that returned early
  // reads no files. Either would leave the assertion below green while checking
  // nothing at all, so both are pinned first.
  it('loads a palette from the installed Tailwind', () => {
    expect(palette.size).toBeGreaterThan(20);
    expect([...(palette.get('rose') ?? [])].sort()).toEqual(
      ['100', '200', '300', '400', '50', '500', '600', '700', '800', '900', '950'],
    );
    expect(palette.get('rose')?.has('250')).toBe(false);
  });

  it('counts a colour as defined only where `@theme` defines it', () => {
    expect(collectPalette('@theme { --color-brand-250: #abc123; }', new Map()).get('brand'))
      .toEqual(new Set(['250']));
    // A custom property anywhere else is just a variable — Tailwind compiles no
    // `bg-brand-250` for it, so neither does this guard accept one.
    expect(collectPalette(':root { --color-brand-250: #abc123; }', new Map()).size).toBe(0);
  });

  it('names only shades the palette defines', () => {
    const files = [...sourceFiles(PACKAGE_ROOT)]
      // This file states the counter-examples below as literals; scanning it
      // would report its own fixtures. Excluded by its own path rather than by
      // name, so a rename cannot silently turn the exclusion into a hole.
      .filter((file) => file !== THIS_FILE);
    expect(files.length).toBeGreaterThan(50);

    const offenders = files.flatMap((file) =>
      findColorUtilities(readFileSync(file, 'utf8'), palette)
        .filter(({ color, shade }) => !palette.get(color)?.has(shade))
        .map(({ utility, line }) => `${relative(PACKAGE_ROOT, file)}:${line}  ${utility}`),
    );

    expect(offenders).toEqual([]);
  });

  // The scan above is only worth its runtime if it can still tell a bad shade
  // from a good one after the tree is clean. These fix that on a fixture, so
  // that "no offenders" keeps meaning "looked and found none".
  describe('the scan itself', () => {
    const scan = (text: string) =>
      findColorUtilities(text, palette)
        .filter(({ color, shade }) => !palette.get(color)?.has(shade))
        .map(({ utility }) => utility);

    it('reports a shade off the scale, wherever the utility sits', () => {
      expect(scan('className="p-3 bg-rose-50 border border-rose-250 text-rose-600"')).toEqual([
        'border-rose-250',
      ]);
      expect(scan('"hover:bg-slate-250 focus:ring-indigo-1000 border-t-emerald-450"')).toEqual([
        'bg-slate-250',
        'ring-indigo-1000',
        'border-t-emerald-450',
      ]);
    });

    it('passes shades that exist, including opacity and variant syntax', () => {
      expect(scan('bg-rose-50 text-rose-600/50 hover:border-slate-200 from-sky-950')).toEqual([]);
    });

    it('says nothing about arbitrary values or non-colour numbers', () => {
      expect(scan('border-[#abc123] bg-[--brand] text-[rgb(1,2,3)]')).toEqual([]);
      expect(scan('duration-250 z-250 grid-cols-250 delay-250 max-w-250')).toEqual([]);
    });

    it('does not read a colour definition as a colour use', () => {
      expect(scan('  --color-rose-250: oklch(90% 0.05 10);')).toEqual([]);
      expect(scan('border: 1px solid var(--color-slate-250);')).toEqual([]);
    });
  });
});
