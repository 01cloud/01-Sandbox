## Configuration & initialization

- Write a test verifying the Auth0 client initializes correctly with valid domain, clientId, and audience values.
- Write a test that asserts an error is thrown (or a clear validation message is returned) when required env vars like AUTH0_DOMAIN or AUTH0_CLIENT_ID are missing.
- Write a test confirming the config object falls back to default values where applicable (e.g., default scope, default redirect URI).

## Login / authentication flow

- Write a test simulating a successful login redirect, checking the generated authorization URL contains the correct response_type, scope, and redirect_uri.
- Write a test for a failed login attempt (e.g., invalid credentials or denied consent) and verify the error is handled/propagated correctly.
- Write a test covering the silent authentication (checkSession) path, both when a valid session exists and when it doesn't.
