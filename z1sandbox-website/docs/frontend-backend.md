# Frontend-Backend Communication Architecture

This document describes the decoupled architecture of the Z1 Sandbox platform, where the frontend and backend are hosted on separate servers and communicate across networks.

## Architecture Overview

The system consists of two primary components test:
1.  **Frontend (z1sandbox-website)**: A Vite-based React application.
2.  **Backend (apiServer)**: A FastAPI-based service hosted via Helm/Kubernetes and exposed via a public URL (e.g., ngrok).

### 1. Decoupling Logic
To achieve separation, the frontend no longer assumes the backend is hosted on the same origin. It uses a dynamic base URL for all API interactions.

- **Configuration**: The base URL is managed via the `VITE_API_BASE_URL` environment variable.
- **Dynamic Discovery**: The frontend fetches its service definitions (like Z1 Sandbox and OpenSandbox) from a `backends.json` file. This allows adding new backend services without rebuilding the frontend.

## Local Development (Vite Proxy)

During development on `localhost:8080`, browsers enforce strict CORS (Cross-Origin Resource Sharing) policies. To bypass these without compromising security:

1.  **Vite Proxy**: `vite.config.ts` is configured to intercept relative paths (`/v1`, `/api`) and forward them to the remote backend.
    ```typescript
    proxy: {
      '/v1': {
        target: 'https://contests-name-publishers-off.trycloudflare.com',
        changeOrigin: true,
        headers: { 'ngrok-skip-browser-warning': 'true' }
      }
    }
    ```
2.  **ngrok Compatibility**: Added the `ngrok-skip-browser-warning` header to bypass ngrok's manual confirmation page for API requests.

## Runtime Environment Injection

The platform uses a "Build Once, Run Anywhere" strategy. Environment variables are injected at runtime instead of build time.

1.  **`env-config.js`**: A script loaded in `index.html` that populates `window._env_`.
2.  **`docker-entrypoint.sh`**: In production, this script generates `env-config.js` from the container's environment variables before starting the web server.
3.  **Local Fallback**: For development, a placeholder `public/env-config.js` is used to prevent 404 errors.

## Authentication (Auth0 SPA Flow)

Authentication is handled via the Auth0 Single Page Application (SPA) flow.

1.  **`Auth0ProviderWithHistory`**: The Auth0 provider is wrapped inside the React Router context. This allows using `useNavigate` for smooth, non-reloading redirects after login.
2.  **Identity Bridging**: The frontend sends an Auth0 JWT in the `Authorization` header. The backend validates this token and bridges it to persistent developer API keys.

## Service Discovery (`backends.json`)

The applications list in the dashboard is populated by fetching `/config/backends.json`.
- **Base URLs**: Each backend service defined in this file is automatically prefixed with the `VITE_API_BASE_URL` if a relative path is provided.
- **Independence**: The frontend remains agnostic of where each specific backend service lives, as long as it is reachable via the configured base URL.

## Summary of Connectivity

| Environment | Frontend URL | Backend URL | Method |
| :--- | :--- | :--- | :--- |
| **Development** | `localhost:8080` | `ngrok-url` | Vite Proxy (Server-to-Server) |
| **Production** | `your-domain.com` | `api.your-domain.com` | Direct CORS-enabled API calls |
