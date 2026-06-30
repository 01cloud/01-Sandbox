// ============================================================================
// Auth0 Login & Authentication Flow Tests
// ============================================================================
// This test file checks that our login redirects, logout procedures, failed
// login attempts, and background session token fetches (silent auth) work correctly.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { BrowserRouter } from "react-router-dom";
import { useAuth0 } from "@auth0/auth0-react";
import { Auth0ProviderWithHistory } from "../../App";

// 1. MOCKING REACT ROUTER REDIRECTS
// We mock 'useNavigate' from 'react-router-dom' so we can capture where the application
// attempts to redirect the user after logging in.
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate, // Use our mock spy instead of the real routing redirector
  };
});

// 2. MOCKING THE AUTH0 SDK HOOKS & FUNCTIONS
// We define spy variables to track if login, logout, or token fetch are called.
let mockOnRedirectCallback: any = null;
const mockLoginWithRedirect = vi.fn();
const mockLogout = vi.fn();
const mockGetAccessTokenSilently = vi.fn();
let currentMockError: any = null; // Used to simulate a login error

vi.mock("@auth0/auth0-react", () => {
  return {
    // When components inside the test call 'useAuth0()', they get this mocked state:
    useAuth0: () => ({
      isAuthenticated: !currentMockError,
      user: currentMockError ? null : { name: "Test User", email: "test@example.com" },
      isLoading: false,
      error: currentMockError,
      loginWithRedirect: mockLoginWithRedirect,
      logout: mockLogout,
      getAccessTokenSilently: mockGetAccessTokenSilently,
    }),
    // When App mounts Auth0Provider, we intercept the callback and render a simple div:
    Auth0Provider: vi.fn(({ children, onRedirectCallback }) => {
      mockOnRedirectCallback = onRedirectCallback; // Save the callback function so we can trigger it in tests
      return <div data-testid="auth0-provider">{children}</div>;
    }),
  };
});

// 3. CREATING A DUMMY COMPONENT FOR TESTING
// To test how components interact with Auth0 hooks (like clicking "Login"),
// we build a very simple temporary component. It has buttons that call the hooks.
const DummyAuthComponent = () => {
  const { loginWithRedirect, logout, getAccessTokenSilently, error } = useAuth0();
  const [token, setToken] = React.useState("");
  const [errorText, setErrorText] = React.useState("");

  const handleGetToken = async () => {
    try {
      const t = await getAccessTokenSilently();
      setToken(t);
    } catch (e: any) {
      setErrorText(e.message || "Failed to get token");
    }
  };

  return (
    <div>
      <button onClick={() => loginWithRedirect({ authorizationParams: { connection: "google-oauth2" } })}>
        Login
      </button>
      <button onClick={() => logout()}>Logout</button>
      <button onClick={handleGetToken}>Get Token</button>
      {token && <span data-testid="token-value">{token}</span>}
      {errorText && <span data-testid="error-value">{errorText}</span>}
      {error && <div data-testid="auth-error-state">{error.message}</div>}
    </div>
  );
};

describe("Auth0 Login & Authentication Flow Tests", () => {
  // Set up mock configurations before each test runs
  beforeEach(() => {
    vi.clearAllMocks();
    currentMockError = null;
    (window as any)._env_ = {
      VITE_AUTH0_DOMAIN: "test-domain.auth0.com",
      VITE_AUTH0_CLIENT_ID: "test-client-id",
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ==========================================================================
  // Test Case 1: Login redirect route checks
  // ==========================================================================
  it("should simulate a successful login redirect, calling navigate to /dashboard or returnTo path", () => {
    render(
      <BrowserRouter>
        <Auth0ProviderWithHistory>
          <div>App Content</div>
        </Auth0ProviderWithHistory>
      </BrowserRouter>
    );

    // Assert that the onRedirectCallback function was set up correctly
    expect(mockOnRedirectCallback).toBeTypeOf("function");

    // Call the redirect callback with an empty state. It should default to redirecting to "/dashboard"
    mockOnRedirectCallback({});
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard");

    // // Call the redirect callback with a custom returnTo path (e.g., if user bookmarked a page)
    // mockOnRedirectCallback({ returnTo: "/custom-settings-page" });
    // expect(mockNavigate).toHaveBeenCalledWith("/custom-settings-page");
  });

  // ==========================================================================
  // Test Case 2: loginWithRedirect arguments check
  // ==========================================================================
  it("should verify loginWithRedirect triggers redirect flow with correct connection parameters", () => {
    render(
      <BrowserRouter>
        <Auth0ProviderWithHistory>
          <DummyAuthComponent />
        </Auth0ProviderWithHistory>
      </BrowserRouter>
    );

    // Simulate clicking the "Login" button in the browser DOM
    const loginBtn = screen.getByText("Login");
    fireEvent.click(loginBtn);

    // Assert that the SDK login function was called with our requested options
    expect(mockLoginWithRedirect).toHaveBeenCalledTimes(1);
    expect(mockLoginWithRedirect).toHaveBeenCalledWith({
      authorizationParams: { connection: "google-oauth2" }
    });
  });

  // ==========================================================================
  // Test Case 3: Error state propagation
  // ==========================================================================
  it("should verify failed login attempts propagate and render error correctly", () => {
    // Inject a simulated connection/login error into our mocked state
    currentMockError = new Error("Invalid credentials");

    render(
      <BrowserRouter>
        <Auth0ProviderWithHistory>
          <DummyAuthComponent />
        </Auth0ProviderWithHistory>
      </BrowserRouter>
    );

    // Assert that our component reads this error state and renders it in the DOM
    expect(screen.getByTestId("auth-error-state")).toBeInTheDocument();
    expect(screen.getByTestId("auth-error-state")).toHaveTextContent("Invalid credentials");
  });

  // ==========================================================================
  // Test Case 4: Silent auth (success path)
  // ==========================================================================
  it("should verify silent authentication (getAccessTokenSilently) succeeds when a valid session exists", async () => {
    // Simulate that the session is valid and returns a dummy JWT token
    mockGetAccessTokenSilently.mockResolvedValue("mocked-jwt-token-12345");

    render(
      <BrowserRouter>
        <Auth0ProviderWithHistory>
          <DummyAuthComponent />
        </Auth0ProviderWithHistory>
      </BrowserRouter>
    );

    // Click the "Get Token" button
    const tokenBtn = screen.getByText("Get Token");
    fireEvent.click(tokenBtn);

    // Wait for the async token fetch to complete and check if it rendered the token value
    await waitFor(() => {
      expect(screen.getByTestId("token-value")).toHaveTextContent("mocked-jwt-token-12345");
    });
    expect(mockGetAccessTokenSilently).toHaveBeenCalledTimes(1);
  });

  // ==========================================================================
  // Test Case 5: Silent auth (failure path)
  // ==========================================================================
  it("should verify silent authentication fails gracefully and throws error when session does not exist", async () => {
    // Simulate that the user has no session, causing the token fetch to fail
    mockGetAccessTokenSilently.mockRejectedValue(new Error("Login required"));

    render(
      <BrowserRouter>
        <Auth0ProviderWithHistory>
          <DummyAuthComponent />
        </Auth0ProviderWithHistory>
      </BrowserRouter>
    );

    // Click "Get Token"
    const tokenBtn = screen.getByText("Get Token");
    fireEvent.click(tokenBtn);

    // Wait and verify that our component caught the error and rendered the error message
    await waitFor(() => {
      expect(screen.getByTestId("error-value")).toHaveTextContent("Login required");
    });
    expect(mockGetAccessTokenSilently).toHaveBeenCalledTimes(1);
  });
});
