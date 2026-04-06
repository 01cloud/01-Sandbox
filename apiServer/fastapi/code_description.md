# CodeInspector API (`codeinspectior_api.py`) Functionality Breakdown

This document provides a detailed, line-by-line explanation of the functionalities present in `codeinspectior_api.py`. It is designed to act as a robust API Gateway for the OpenSandbox environment, intelligently proxying traffic instead of hardcoding execution endpoints.

---

## 1. Base Schemas & Models
These Pydantic models validate and structure the data passing through the basic health and diagnostic interfaces of the gateway itself.

*   **`Language (Enum)`**: Strictly defines the execution languages that the gateway officially acknowledges (Python, JavaScript, Bash).
*   **`RunRequest`**: Validates the payload for diagnostic execution requests (verifying the code format, language type, and timeout limits).
*   **`RunResponse`**: Structures the expected response from a code execution command, providing fields for `stdout`, `stderr`, execution time, and standard exit codes.
*   **`SessionResponse` & `StatusResponse`**: Helper models used to standardize the system diagnostics, ensuring health checks and routing sessions have a predictable JSON format.

## 2. Kubernetes Configuration & Environment Config
The API is designed to operate dynamically in a Kubernetes cluster natively via environments.

*   **`opensandbox_base_url()`**: Fetches the cluster-internal DNS address for the backend from memory (`BACKEND_URL_OPENSANDBOX`). Defaults to `http://opensandbox-server:80`. This cleanly isolates the gateway from the backend.
*   **`opensandbox_headers()`**: Dynamically creates the strict authorization headers—injecting the `OPEN-SANDBOX-API-KEY` required to authenticate and interact securely with the OpenSandbox backend. 

## 3. The Backend Abstraction Model
*   **`SandboxBackend (ABC)`**: An abstract interface defining the core contract elements that any sandbox connector must follow (`run`, `open_session`, `health_check`). This makes the API extensible—if you want to add a different backend engine in the future natively, you just inherit from this class.

## 4. `GenericHTTPBackend` Implementation
This is the workhorse class that actively links the Gateway to the OpenSandbox Server.

*   **`__init__`**: Stores the backend's name and REST URL.
*   **`run()`**: A synchronous diagnostic execution method. It utilizes the synchronous `httpx.Client` to dispatch dummy code execution directly to the `/run` endpoint of the backend to verify the backend sandbox engine is completely functional.
*   **`open_session()` / `close_session()`**: Simulates session attachment tracking by assigning simple UUID validations.
*   **`health_check()`**: Reaches out to the upstream `/health` endpoint to ensure the upstream Kubernetes deployment is reachable, returning `True` or `False`.

## 5. Global State & App Instantiation
*   **`AppState`**: A singleton state manager. It initializes the `GenericHTTPBackend` immediately pointing at the internal OpenSandbox Kubernetes address.
*   **`lifespan(app: FastAPI)`**: The startup routine hook that safely validates and logs cluster parameters when the Uvicorn server turns on, printing the destination URL to the logs for transparent infrastructure debugging.
*   **`app = FastAPI(...)`**: Instantiates the FastAPI application application, dictating basic OpenAPI properties for the proxy itself (served at `/docs`).

## 6. General API Interfaces
*   **`@app.get("/health")`**: Evaluates `state.backend.health_check()`. It confirms whether both the gateway itself is alive AND whether the proxy connection to the backend is uninterrupted.
*   **`@app.post("/run")`**: An internal testing endpoint that utilizes the backend's REST bridge directly. Used locally to check standard task completion without utilizing heavy API proxying.

## 7. OpenSandbox Proxy Forwarding (The Core Feature)
This section actively intercepts and forwards API calls.

*   **`/backend/opensandbox/docs`**: Rather than redirecting users to the backend or failing, this dynamically serves a local Swagger UI that operates explicitly on the patched remote OpenAPI specification.
*   **`/backend/opensandbox/openapi.json`**: This function connects to the OpenSandbox internal service, clones its layout specification data JSON, rewrites the root `servers` URL to point implicitly to `/backend/opensandbox` locally, and injects API security context safely. It tricks Swagger UI into securely targeting our gateway logic flawlessly.
*   **`_do_proxy(proxy_path: str, request: Request)`**: The brain of the API Gateway natively. 
    1.  Receives explicit dynamic `{proxy_path}` variables.
    2.  Builds the destination `target_url` mapped to the internal Kubernetes Backend.
    3.  Strips explicit `content-length` or `host` headers to ensure proxy headers do not clash seamlessly.
    4.  Injects the `OPEN-SANDBOX-API-KEY`.
    5.  Asynchronously streams request parameters, HTTP body payloads, and headers logically into the backend URL using `httpx.AsyncClient`.
    6.  Takes the verbatim HTTP response, cleans the encoding headers predictably safely, and mirrors it logically back to the client.

*   **`proxy_get / proxy_post / proxy_put / proxy_delete / proxy_patch`**:
    *   Previously, combining them caused Swagger UI rendering mapping conflicts rendering only `PATCH`. By dynamically isolating `POST`, `PUT`, `GET`, etc., into mutually exclusive routing mechanisms asynchronously utilizing `_do_proxy()`, we force HTTP protocol alignment correctly natively in all interfaces. This is what handles user requests flawlessly securely.
