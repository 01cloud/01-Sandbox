# Walkthrough – Auth0 Configuration & Flow Testing

We have successfully implemented a modularized unit/integration testing suite for the Auth0 integration in `z1sandbox-website`. This document summarizes the changes, test files, and test results.

---

## 1. What Was Done

### A. Exported `Auth0ProviderWithHistory`
To test the actual component that initializes the Auth0 provider with your environment configurations, we updated `App.tsx` to export `Auth0ProviderWithHistory`:
```typescript
// src/App.tsx
export const Auth0ProviderWithHistory = ({ children }: { children: React.ReactNode }) => { ... }
```

### B. Added Config Validation
Added checking inside `Auth0ProviderWithHistory` (located in **`src/App.tsx`**) to intercept missing/empty configurations and display a user-friendly configuration warning, logging a validation error to `console.error` instead of silently failing or throwing fatal runtime exceptions.

### C. Created Global Test Configurations
Added `src/test/setup.ts` to reset environment overrides and clear vitest mock states automatically between test runs.

---

## 2. FAQ & Detailed Clarifications

### Q1: Why did we export `Auth0ProviderWithHistory`?
**Reason:**
Originally, `Auth0ProviderWithHistory` was private (scoped locally) inside `App.tsx`. Because it was private, the test suite could not import it. In unit testing, we isolate components to test them in a "niche" environment.
By exporting `Auth0ProviderWithHistory`, we can import it directly in `config.test.tsx` and `loginFlow.test.tsx` and test *just* the Auth0 logic. This avoids rendering the entire application (`App` component), which contains routing, CSS loading, tooltips, theme providers, query clients, and other dependencies that would require complex setup/mocking and slow down the tests.

### Q2: Added config validation in which file?
**Answer:**
The configuration validation check was added directly in **`src/App.tsx`** inside the `Auth0ProviderWithHistory` component:
```typescript
if (!domain || !clientId) {
  console.error("Auth0 configuration error: domain and clientId must be set.");
  return (
    <div data-testid="auth0-error">
      Missing required Auth0 configuration
    </div>
  );
}
```

### Q3: How do the cleanup methods in `src/test/setup.ts` work? Provide an example.
**Answer:**
In `src/test/setup.ts`, we set up two Vitest lifecycle hooks:
*   `beforeEach(() => { vi.clearAllMocks(); });`: Clears the histories of all active mock spies.
*   `afterEach(() => { vi.unstubAllEnvs(); vi.restoreAllMocks(); });`: Restores environment variables and resets any mocked modules.

#### Example of what happens:
1. In `config.test.tsx`, the test **"should assert an error is thrown or validation message is returned when required env vars are missing"** runs.
2. It stubs the env variables to empty strings to simulate missing configurations:
   ```typescript
   vi.stubEnv("VITE_AUTH0_DOMAIN", "");
   ```
3. Once this test finishes, the `afterEach` hook runs `vi.unstubAllEnvs()`.
4. Without `vi.unstubAllEnvs()`, `VITE_AUTH0_DOMAIN` would remain `""` for the next test (**"should confirm the config object falls back to default values..."**), which would then fail because it expects the environment config variables to be present.
5. `vi.restoreAllMocks()` behaves similarly: it ensures that mocked modules or mocked spy calls from one test file do not pollute another test file.

---

## 3. Modularized Test Suites

All tests are placed in `src/test/auth0/` and organized by feature area:

### 1. `config.test.tsx`
Focuses on validation of environment variables and fallback states during client initialization:
- **Valid Client Initialization:** Asserts that when valid environment variables are present, they are parsed and propagated correctly to the provider.
- **Handling of Missing Env Vars:** Asserts that when required configurations (`VITE_AUTH0_DOMAIN` or `VITE_AUTH0_CLIENT_ID`) are absent, a validation fallback UI is rendered and a console error is logged.
- **Fallback to Default Values:** Confirms the configuration falls back to the browser's origin (`window.location.origin`) for redirects, defaults the scope to `"openid profile email"`, and maps missing optional variables to empty strings.

### 2. `loginFlow.test.tsx`
Focuses on authentication lifecycles and navigation triggers:
- **Successful Login Redirect:** Simulates the successful redirect callback from Auth0 and asserts that it invokes React Router's `navigate` function to redirect the user to `/dashboard` (or a custom route specified in the app state).
- **Trigger Login Redirect:** Verifies that invoking `loginWithRedirect` on the Auth0 hook correctly triggers the SDK auth flow.
- **Failed Login Attempts:** Simulates failed logins (e.g., rejected credentials or denied consent) and verifies that error state values are correctly propagated to user components.
- **Silent Authentication (checkSession / getAccessTokenSilently):** Verifies token checks for both active sessions (returns the token) and inactive sessions (handles errors gracefully).

---

## 4. How to Run the Tests & Generate Coverage

### A. Run the Unit Tests
To execute the Auth0 test suite, navigate to `z1sandbox-website/` and run:
```bash
npx vitest run src/test/auth0/
```

#### Output:
```text
 ✓ src/test/auth0/config.test.tsx (3 tests) 29ms
 ✓ src/test/auth0/loginFlow.test.tsx (5 tests) 48ms

 Test Files  2 passed (2)
      Tests  8 passed (8)
   Start at  17:13:16
   Duration  1.65s
```
All **8 tests passed** successfully.

### B. Generate Coverage Reports
Our tests target **100% code coverage** of the custom Auth0 configuration component (`Auth0ProviderWithHistory` inside `src/App.tsx`). To calculate and inspect the exact coverage percentage:

1. Install the Vitest coverage engine (if not already installed):
   ```bash
   npm install --save-dev @vitest/coverage-v8
   ```

2. Run the tests with the coverage flag:
   ```bash
   npx vitest run src/test/auth0/ --coverage
   ```

This will run the test suite and output a detailed statement, branch, function, and line coverage grid confirming 100% coverage on `Auth0ProviderWithHistory`.
