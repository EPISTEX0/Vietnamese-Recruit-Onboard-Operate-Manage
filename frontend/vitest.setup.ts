import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

/**
 * React Testing Library only auto-registers its `afterEach` cleanup when Vitest
 * runs with `globals: true`. This suite does not, so unmounting is wired up
 * here — without it a `renderHook` from one test keeps running effects while
 * the next test asserts, and the router spy sees redirects it never triggered.
 */
afterEach(() => {
  cleanup();
});
