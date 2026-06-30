# Auth0 Configuration & Flow Testing – Implementation Plan

This document outlines the implementation plan to build a modularized unit testing setup for all Auth0-related initialization and flow logic in the `z1sandbox-website` application.

All test suites will be situated under the `src/test/auth0/` directory and split by feature area.

---

## 1. What Kind of Test Is This?

These are **Isolated Component Tests (Component Integration Tests)** running in a virtualized browser environment.
- **Isolated:** They do not connect to live Auth0 servers.
- **Mock-driven verification:** We mock `@auth0/auth0-react` and `react-router-dom` to assert that the component logic resolves configurations and handles flows correctly.

---

## 2. Testing Stack & Components Used

We leverage the existing testing environment configured in the project:

| Component / Tool | Role in this Test |
| :--- | :--- |
| **Vitest** | The test runner and compiler. It executes the test suite, provides the `describe`, `it`, and `expect` APIs, and manages dependency mocking. |
| **JSDOM** | A headless, pure-JavaScript implementation of web standards for Node.js. It simulates a browser environment, enabling browser globals such as `window.location` and `window.history` to exist. |
| **React Testing Library (RTL)** | Provides the `render` utility to mount `<Auth0ProviderWithHistory>` in JSDOM, simulating how it mounts inside the real browser. |
| **Vitest Mocks (`vi.mock`)** | Used to mock `@auth0/auth0-react` as a spy to intercept props and verify configurations, and `react-router-dom` to spy on navigation redirects. |

---

## 2.1. Vitest Built-In Functions & Purpose

Below is a list of the core functions provided by Vitest that we use in our Auth0 test files, along with what they do:

### 1. Organizing & Defining Tests
*   `describe("Group Name", () => { ... })`
    *   **Purpose:** Groups related tests together. For example, we group all configuration tests under one `describe` block, and login flow tests under another.
*   `it("should do something", () => { ... })` (or `test`)
    *   **Purpose:** Defines a single test case. This is where we write the actual steps and checks for the test.

### 2. Assertions (Verifications)
*   `expect(value)`
    *   **Purpose:** Starts an assertion check to verify if code behaves correctly.
    *   **Common Matchers used:**
        *   `expect(x).toBe(y)`: Asserts that `x` is exactly equal to `y`.
        *   `expect(mockFunc).toHaveBeenCalledTimes(n)`: Asserts that a mock spy was called exactly `n` times.
        *   `expect(mockFunc).toHaveBeenCalledWith(args)`: Asserts that a mock was called with specific arguments.

### 3. Mocking & Spying (`vi` Utilities)
*   `vi.mock("module-name", () => { ... })`
    *   **Purpose:** Intercepts imports of a package (like `@auth0/auth0-react`) and redirects them to a dummy/simulated version, preventing actual network requests or side effects.
*   `vi.fn()`
    *   **Purpose:** Creates a blank "spy" function. It records when it is called, how many times, and what arguments were passed to it.
*   `vi.stubEnv("VARIABLE_NAME", "value")`
    *   **Purpose:** Overrides an environment variable (like `import.meta.env`) during testing, enabling us to simulate missing or custom configuration values.

### 4. Lifecycle Hooks & Cleanups
*   `beforeEach(() => { ... })`
    *   **Purpose:** Runs a block of code automatically **before each individual test** in the file. (Used to clear mock history).
*   `afterEach(() => { ... })`
    *   **Purpose:** Runs a block of code automatically **after each individual test** finishes. (Used to reset environment stubs and mocked modules).
*   `vi.unstubAllEnvs()`
    *   **Purpose:** Undoes all changes made by `vi.stubEnv` and restores original environment variables.
*   `vi.restoreAllMocks()`
    *   **Purpose:** Resets mocked imports and restores spy functions back to their original behavior.

---

## 3. Implementation Plan & Folder Structure

We will place all modularized test files inside `src/test/auth0/`:

```text
src/test/auth0/
├── implementationPlan.md   # This document
├── prompts.md              # Requirements prompt document
├── config.test.tsx         # [NEW] Tests client initialization and fallbacks
└── loginFlow.test.tsx      # [NEW] Tests redirect, error propagation, checkSession
```

To test the actual code written in the codebase, we will perform the following changes:

### A. Export `Auth0ProviderWithHistory` from `App.tsx`
We will export `Auth0ProviderWithHistory` from `/home/berrybytes/Desktop/Kamal/01-Sandbox/z1sandbox-website/src/App.tsx` so it can be imported and tested:
```typescript
export const Auth0ProviderWithHistory = ({ children }: { children: React.ReactNode }) => { ... }
```

### B. Create Modularized Test Suites

#### 1. [NEW] `src/test/auth0/config.test.tsx`
Focuses strictly on initialization parameters:
- **Test 1: Valid client initialization**
  - Verify `Auth0Provider` receives correct `domain`, `clientId`, and `audience` from environment variables.
- **Test 2: Required Env Vars Missing**
  - Verify behavior when `VITE_AUTH0_DOMAIN` or `VITE_AUTH0_CLIENT_ID` are missing/empty. Ensure the app logs/displays a configuration error or fails gracefully without throwing unhandled runtime exceptions.
- **Test 3: Fallbacks to defaults**
  - Assert that scope defaults to `"openid profile email"` and redirect URI defaults to `window.location.origin` if not overridden.

#### 2. [NEW] `src/test/auth0/loginFlow.test.tsx`
Focuses strictly on interaction and login lifecycles:
- **Test 1: Successful login redirect**
  - Verify redirect callback calls routing system to redirect to `/dashboard` or the state-supplied redirect route. Assert authentication triggers the correct URL params (response_type, scope, redirect_uri).
- **Test 2: Failed login attempt**
  - Verify that if authentication fails (e.g. invalid credentials or consent denied), the error is correctly handled/logged or passed down to user-facing feedback components.
- **Test 3: Silent authentication (checkSession / getAccessTokenSilently)**
  - Verify token checks handle both when a valid active session exists (returns token) and when it does not (returns error/null).

---

## 4. Verification Plan

### Automated Tests
Run the entire Auth0 suite locally:
```bash
npx vitest run src/test/auth0/
```

To check coverage for just the Auth0 test suite:
```bash
npx vitest run src/test/auth0/ --coverage
```
