// ============================================================================
// Auth0 Configuration & Initialization Tests
// ============================================================================
// This test file checks that our Auth0 integration gets set up correctly
// using the domain, clientId, and other credentials from our environment.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { BrowserRouter } from "react-router-dom";
import { Auth0ProviderWithHistory } from "../../App";

// 1. MOCKING THE AUTH0 LIBRARY
// We don't want our tests to connect to the actual Auth0 servers over the internet.
// Instead, we use `vi.fn` to create a "dummy" version of the Auth0Provider.
// This dummy provider records what properties (props) were passed to it,
// so we can verify if they are correct.
const mockAuth0Provider = vi.fn(({ children }) => (
  <div data-testid="auth0-provider">{children}</div>
));

// We tell Vitest: "Whenever the app imports '@auth0/auth0-react', use this mock object."
vi.mock("@auth0/auth0-react", async () => {
  const actual = await vi.importActual<typeof import("@auth0/auth0-react")>("@auth0/auth0-react");
  return {
    ...actual,
    // Replace the real Auth0Provider with our mock tracker function
    Auth0Provider: (props: any) => mockAuth0Provider(props),
  };
});

describe("Auth0 Configuration & Initialization Tests", () => {
  // Store the original window environment so we can restore it after the tests finish
  const originalWindowEnv = (window as any)._env_;

  // This runs BEFORE every test. It clears previous records of how our mocks were called.
  beforeEach(() => {
    vi.clearAllMocks();
    (window as any)._env_ = undefined;

    // Suppress console.error logs so they don't clutter the test results window
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  // This runs AFTER every test. It restores everything back to its original state.
  afterEach(() => {
    (window as any)._env_ = originalWindowEnv;
    vi.unstubAllEnvs(); // Reset any environment variables we changed
    vi.restoreAllMocks();
  });

  // ==========================================================================
  // Test Case 1: Valid initialization
  // ==========================================================================
  it("should initialize the Auth0 client correctly with valid domain, clientId, and audience values from window._env_", () => {
    // Simulate setting environment variables in the browser's window object
    (window as any)._env_ = {
      VITE_AUTH0_DOMAIN: "test-window-domain.auth0.com",
      VITE_AUTH0_CLIENT_ID: "test-window-client-id",
      VITE_AUTH0_AUDIENCE: "test-window-audience",
    };

    // Render the component (wrapped in a Router because useNavigate is used inside it)
    render(
      <BrowserRouter>
        <Auth0ProviderWithHistory>
          <div>App Content</div>
        </Auth0ProviderWithHistory>
      </BrowserRouter>
    );

    // Verify that our mock Auth0Provider was called exactly once
    expect(mockAuth0Provider).toHaveBeenCalledTimes(1);

    // Inspect the properties (props) that were passed to the Auth0Provider component
    const passedProps = mockAuth0Provider.mock.calls[0][0];

    // Assert that the credentials match the environment variables we set above
    expect(passedProps.domain).toBe("test-window-domain.auth0.com");
    expect(passedProps.clientId).toBe("test-window-client-id");
    expect(passedProps.authorizationParams.audience).toBe("test-window-audience");
  });

  // ==========================================================================
  // Test Case 2: Config missing validation
  // ==========================================================================
  it("should assert an error is thrown or validation message is returned when required env vars are missing", () => {
    // Clear out window env config
    (window as any)._env_ = {
      VITE_AUTH0_DOMAIN: "",
      VITE_AUTH0_CLIENT_ID: "",
    };

    // Clear out meta environment variables using vi.stubEnv
    vi.stubEnv("VITE_AUTH0_DOMAIN", "");
    vi.stubEnv("VITE_AUTH0_CLIENT_ID", "");

    // Render the component
    render(
      <BrowserRouter>
        <Auth0ProviderWithHistory>
          <div>App Content</div>
        </Auth0ProviderWithHistory>
      </BrowserRouter>
    );

    // Verify that our configuration error message is rendered in the virtual browser DOM
    expect(screen.getByTestId("auth0-error")).toBeInTheDocument();
    expect(screen.getByText(/Missing required Auth0 configuration/i)).toBeInTheDocument();

    // Verify that console.error was indeed triggered for debugging
    expect(console.error).toHaveBeenCalled();

    // Verify that the mock Auth0Provider was NEVER initialized since configs were invalid
    expect(mockAuth0Provider).not.toHaveBeenCalled();
  });

  // ==========================================================================
  // Test Case 3: Default config fallbacks
  // ==========================================================================
  it("should confirm the config object falls back to default values where applicable (e.g. default scope, default redirect URI)", () => {
    // Provide only the required parameters
    (window as any)._env_ = {
      VITE_AUTH0_DOMAIN: "fallback-test-domain.auth0.com",
      VITE_AUTH0_CLIENT_ID: "fallback-test-client-id",
    };

    render(
      <BrowserRouter>
        <Auth0ProviderWithHistory>
          <div>App Content</div>
        </Auth0ProviderWithHistory>
      </BrowserRouter>
    );

    // Verify our mock provider was called
    expect(mockAuth0Provider).toHaveBeenCalledTimes(1);
    const passedProps = mockAuth0Provider.mock.calls[0][0];

    // Assert that fallback default settings were applied
    expect(passedProps.authorizationParams.redirect_uri).toBe(window.location.origin);
    expect(passedProps.authorizationParams.scope).toBe("openid profile email");
    expect(passedProps.authorizationParams.audience).toBe(""); // defaults to empty string
  });
});
