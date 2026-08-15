import { fileURLToPath } from "node:url";
import { defaultExclude, defineConfig } from "vitest/config";

/**
 * Vitest config for the frontend.
 *
 * `.mts`, not `.ts`: the repo has no `"type": "module"`, so Vite's native config
 * loader reads a `.ts` config as CommonJS and warns on the ESM syntax below.
 * Flipping the whole package to ESM to satisfy one file would change how
 * `next.config.ts` and the PostCSS config are loaded — the extension is the
 * narrower fix. `tsconfig.json` has a matching `.mts` glob in `include` so this
 * file stays typechecked.
 *
 * Deliberately minimal — no Next plugin, no `@vitejs/plugin-react`. The suite
 * covers plain TypeScript modules (`lib/api/*`, `lib/auth/*`) and hooks, plus
 * one route module imported only to prove it resolves. None of that needs the
 * Next request pipeline or `next/font`, and adding those plugins would pull a
 * second toolchain into CI for no gain.
 *
 * Three things do have to match the app build:
 *
 * - The `@/*` alias from `tsconfig.json`. Resolved here by hand rather than via
 *   `vite-tsconfig-paths` because the repo has exactly one alias; a dependency
 *   to read a single line of JSON is not worth it. If `tsconfig.json` grows a
 *   second path mapping, mirror it here or switch to the plugin.
 * - `jsdom`. `lib/api/client.ts` reads `document.cookie` and the guard hooks
 *   render React, so a Node environment would fail on the first import.
 * - JSX compilation. `tsconfig.json` sets `"jsx": "preserve"` because Next does
 *   its own transform, and esbuild honours that — so a `.tsx` file reaches
 *   Vite's import analysis as raw JSX and fails to parse. That matters for
 *   exactly one file: `settings/page.test.ts` imports the console's homepage
 *   route to prove it still resolves, and that route is now a component rather
 *   than the JSX-free redirect it used to be. Overriding the mode here is a
 *   one-line answer to that; `@vitejs/plugin-react` would be a whole toolchain
 *   for the same result, and none of these tests render anything.
 */
const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  test: {
    environment: "jsdom",
    // No `globals: true` — every test file imports `describe`/`it`/`expect`
    // explicitly, which is what the four pre-existing test files already do and
    // what keeps `tsc --noEmit` honest without a global type shim.
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.{test,spec}.{ts,tsx}"],
    // Extend rather than replace: Vitest's `defaultExclude` already covers
    // `**/node_modules/**` at any depth. Listing only `node_modules/**` would
    // miss nested trees and start collecting dependencies' own test files.
    exclude: [...defaultExclude, ".next/**"],
  },
  // Vite 8 transforms with Oxc, and Oxc honours `tsconfig.json`'s
  // `"jsx": "preserve"` unless told otherwise.
  oxc: {
    jsx: { runtime: "automatic" },
  },
  resolve: {
    alias: {
      "@": projectRoot,
    },
  },
});
