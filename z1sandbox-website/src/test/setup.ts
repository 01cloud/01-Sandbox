// ============================================================================
// Global Test Setup Configuration for Vitest
// ============================================================================

// Import Jest DOM matchers globally.
// This registers custom matchers (like .toBeInTheDocument(), .toHaveTextContent(), etc.)
// from React Testing Library so they are available in every test file without needing
// individual imports.
import "@testing-library/jest-dom";
import { vi, beforeEach, afterEach } from "vitest";

/**
 * Setup hook executed BEFORE EACH individual test run.
 *
 * We call `vi.clearAllMocks()` here to clear the call history, arguments, and return
 * values of any active spies or mock functions (e.g., `mockNavigate` or `mockLoginWithRedirect`).
 * This prevents mock call counts from carrying over and polluting subsequent tests.
 */
beforeEach(() => {
  vi.clearAllMocks();
});

/**
 * Teardown hook executed AFTER EACH individual test run.
 *
 * We call:
 * 1. `vi.unstubAllEnvs()` to restore any environment variables that were stubbed
 *    during the test (e.g., VITE_AUTH0_DOMAIN) back to their original configuration values.
 * 2. `vi.restoreAllMocks()` to restore any mocked modules (created with vi.mock)
 *    and mocked implementations back to their original behavior.
 */
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});
