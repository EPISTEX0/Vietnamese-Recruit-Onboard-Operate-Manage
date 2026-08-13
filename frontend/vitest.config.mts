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
 * covers plain TypeScript modules (`lib/api/*`, `lib/auth/*`) and hooks, none of
 * which need JSX compilation, the Next request pipeline, or `next/font`. Adding
 * those plugins would buy nothing and pull a second toolchain into CI.
 *
 * Two things do have to match the app build:
 *
 * - The `@/*` alias from `tsconfig.json`. Resolved here by hand rather than via
 *   `vite-tsconfig-paths` because the repo has exactly one alias; a dependency
 *   to read a single line of JSON is not worth it. If `tsconfig.json` grows a
 *   second path mapping, mirror it here or switch to the plugin.
 * - `jsdom`. `lib/api/client.ts` reads `document.cookie` and the guard hooks
 *   render React, so a Node environment would fail on the first import.
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
  resolve: {
    alias: {
      "@": projectRoot,
    },
  },
});
