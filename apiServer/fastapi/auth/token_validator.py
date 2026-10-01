from __future__ import annotations

import asyncio
import datetime
import json
import os
import time

import httpx
import jwt
from config import jwt_config
from core import state
from fastapi import HTTPException, Request, status
from observability.metrics import api_key_requests_total, auth_failures_total
from ratelimit import check_rate_limit, is_key_rate_limited

# Cache for remote JWKS (Auth0)
jwks_cache = {"last_updated": 0, "jwks": None}


async def get_remote_jwks(url: str):
    """
    Fetches and caches the remote JWKS (e.g., from Auth0).
    """
    now = time.time()
    if jwks_cache["jwks"] and (now - jwks_cache["last_updated"] < 3600):
        return jwks_cache["jwks"]

    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        r.raise_for_status()
        jwks_data = r.json()
        jwks = jwt.PyJWKSet.from_dict(jwks_data)
        jwks_cache["jwks"] = jwks
        jwks_cache["last_updated"] = now
        return jwks


async def get_active_developer_keys(state, user_id: str) -> list[str]:
    if not user_id:
        return []

    # Check Redis cache first to bypass PostgreSQL completely
    if state.use_redis and state.redis_client:
        try:
            cached_keys = state.redis_client.get(f"user_keys:{user_id}")
            if cached_keys is not None:
                return json.loads(cached_keys)
        except Exception as e:
            print(f"[Identity Bridge] Redis cache read error: {e}")

    try:
        conn = state.get_db_conn()
        cursor = conn.cursor()
        query = """
            SELECT id, expires_at FROM api_keys
            WHERE LOWER(user_id) = LOWER(%s) AND is_revoked = 0
        """
        cursor.execute(query, (user_id,))
        rows = cursor.fetchall()
        conn.close()

        active_keys = []
        now_utc = datetime.datetime.now(datetime.UTC)
        for row in rows:
            k_id, exp_str = row[0], row[1]
            try:
                from datetime import datetime as dt
                from datetime import timezone

                exp_dt = dt.fromisoformat(exp_str.replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if exp_dt > now_utc:
                    active_keys.append(k_id)
            except Exception:
                if exp_str > now_utc.isoformat():
                    active_keys.append(k_id)

        # Cache in Redis for 60 seconds
        if state.use_redis and state.redis_client:
            try:
                state.redis_client.set(
                    f"user_keys:{user_id}", json.dumps(active_keys), ex=60
                )
            except Exception as e:
                print(f"[Identity Bridge] Redis cache write error: {e}")

        return active_keys
    except Exception as e:
        print(f"[Identity Bridge] Error resolving developer keys from database: {e}")
        return []


async def validate_token(request: Request):
    """
    Decodes and validates the RS256 JWT produced by the Edge Gateway's
    cookie transformation.
    """
    # 0. Drop browser CORS/OPTIONS preflight requests immediately before evaluating limits
    if request.method == "OPTIONS":
        return {}

    path = request.url.path

    # Bypass validation for public documentation, spec, status, health, and report routes
    if (
        path.endswith(
            (
                "/docs",
                "/redoc",
                "/openapi.json",
                "/report",
                "/health",
            )
        )
        or path == "/status"
    ):
        # We only rate limit the "View Documentation" HTML page actions (docs, redoc) - NOT openapi.json spec!
        if path.endswith(("/docs", "/redoc")):
            jti = None
            user_id = None
            auth_header = request.headers.get("authorization") or request.headers.get(
                "Authorization"
            )
            raw_token = None
            if auth_header:
                raw_token = (
                    auth_header.replace("Bearer ", "", 1)
                    if auth_header.startswith("Bearer ")
                    else auth_header
                )
            if not raw_token:
                raw_token = request.cookies.get(
                    "execution_token"
                ) or request.cookies.get("inspector_auth")

            if raw_token:
                try:
                    payload = jwt.decode(raw_token, options={"verify_signature": False})
                    jti = payload.get("jti")
                    user_id = payload.get("sub")
                except Exception:
                    pass

            # Identity Bridge for Docs: Rotate active developer API keys to scale the rate limits
            if user_id:
                try:
                    active_keys = await get_active_developer_keys(state, user_id)
                    if active_keys:
                        selected_jti = None
                        for candidate_jti in active_keys:
                            limited = is_key_rate_limited(state, candidate_jti)
                            if not limited:
                                selected_jti = candidate_jti
                                break
                        jti = selected_jti if selected_jti else active_keys[0]
                        print(
                            f"[Identity Bridge Docs] Mapped documentation view for {user_id} to key: {jti}"
                        )
                except Exception as e:
                    print(f"[Identity Bridge Docs] Error resolving developer keys: {e}")

            if not jti and request.client:
                jti = f"ip:{request.client.host}"

            if jti:
                print(
                    f"[Rate Limit] Enforcing docs rate limit for client key/IP: {jti} on path: {path}"
                )
                await check_rate_limit(state, jti)

        return {}

    # 1. Path-Aware Enforcement: Decide if we allow Cookie Fallbacks
    is_execution_route = (
        path.startswith("/v1/run")
        or path.startswith("/v1/scan-jobs")
        or path == "/v1/repo-scan"
        or "/scan-jobs" in path
        or "/repo-scan" in path
        or (
            "/api/z1sandbox/" in path
            and "/docs" not in path
            and "/openapi.json" not in path
        )
    )

    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    query_token = request.query_params.get("token")
    raw_token = None
    source = "header"

    if auth_header:
        raw_token = (
            auth_header.replace("Bearer ", "", 1)
            if auth_header.startswith("Bearer ")
            else auth_header
        )
    elif query_token:
        raw_token = query_token
        source = "query_parameter"
    elif not is_execution_route:
        exec_cookie = request.cookies.get("execution_token")
        auth0_cookie = request.cookies.get("inspector_auth")

        if exec_cookie:
            raw_token = exec_cookie
            source = "execution_cookie"
        elif auth0_cookie:
            raw_token = auth0_cookie
            source = "management_cookie"

    if not raw_token:
        error_msg = (
            "Execution required an explicit API Key in the Authorization header. Please use the 'Authorize' padlock."
            if is_execution_route
            else "Authentication required (API Key or Session missing)"
        )
        print(
            f"[DEBUG SECURITY] REJECTION: No credentials found for {path} (Is Execution: {is_execution_route})"
        )
        auth_failures_total.labels(reason="missing_token").inc()
        raise HTTPException(status_code=401, detail=error_msg)

    token = raw_token
    conf = jwt_config()

    allow_mock = os.environ.get("ALLOW_MOCK_KEYS", "false").lower() == "true"
    if token.startswith("z1_") and allow_mock:
        print(f"[DEBUG SECURITY] Bypassing verification for local mock token: {token}")
        return {
            "sub": "google-oauth2|105722096444109041480",
            "email": "local-dev@example.com",
            "jti": token,
            "iss": "01 Sandbox",
            "aud": "code-inspector-api",
        }

    print(f"[DEBUG SECURITY] Validating {source} token: {token[:10]}...{token[-10:]}")

    try:
        # Get unverified info to determine issuer
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified_payload.get("iss")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        if not kid:
            auth_failures_total.labels(reason="missing_kid").inc()
            raise HTTPException(status_code=401, detail="Missing 'kid' in token header")

        # Determine which JWKS to use
        if (
            issuer
            and issuer.startswith("https://")
            and conf["auth0_domain"]
            and conf["auth0_domain"] in issuer
        ):
            print(f"[DEBUG SECURITY] Detected Auth0 token from issuer: {issuer}")
            target_jwks = await get_remote_jwks(
                f"{issuer.rstrip('/')}/.well-known/jwks.json"
            )
            target_audience = conf["auth0_audience"]
            target_issuer = issuer
        else:
            print(f"[DEBUG SECURITY] Detected Internal token from issuer: {issuer}")
            jwks_data = json.loads(conf["public_jwks"])
            target_jwks = jwt.PyJWKSet.from_dict(jwks_data)
            target_audience = "code-inspector-api"
            target_issuer = conf["issuer"]

        # Get matching key
        signing_key = None
        for key in target_jwks.keys:
            if key.key_id == kid:
                signing_key = key
                break

        if not signing_key:
            auth_failures_total.labels(reason="signing_key_not_found").inc()
            raise HTTPException(
                status_code=401,
                detail=f"Invalid token: signing key with kid '{kid}' not found in JWKS",
            )

        # Decode and verify signature
        try:
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[conf["algorithm"]],
                audience=target_audience,
                issuer=target_issuer,
            )
        except Exception as e:
            print(f"[DEBUG SECURITY] JWT Decode ERROR: {str(e)}")
            auth_failures_total.labels(reason="invalid_token_format").inc()
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

        # --- IDENTITY BRIDGE & ACTIVE KEY ROTATION ---
        jti = payload.get("jti")
        user_id = payload.get("sub")

        is_management_route = any(
            request.url.path.startswith(p)
            for p in [
                "/v1/api-keys",
                "/v1/generate-api",
                "/v1/revoke-api-key",
                "/v1/backends",
                "/v1/subscriptions",
                "/v1/health",
                "/v1/queue",
                "/v1/jobs",
                "/v1/repo-scan/jobs",
                "/v1/repo-scan/precheck",
            ]
        )

        if user_id:
            if issuer != conf["issuer"] and not jti:
                if is_management_route:
                    print(
                        f"[Security] Allowing management operation for Auth0 user: {user_id}"
                    )
                    return payload

                print(
                    f"[Identity Bridge] Mapping active developer key pool for User: {user_id}..."
                )
                active_keys = await get_active_developer_keys(state, user_id)
                print(
                    f"[Identity Bridge] Resolved {len(active_keys)} active keys for User {user_id}"
                )

                if not active_keys:
                    print(
                        f"[Identity Bridge] WARNING: No active/non-expired Developer Key found for {user_id}"
                    )
                    auth_failures_total.labels(reason="no_active_developer_keys").inc()
                    raise HTTPException(
                        status_code=403,
                        detail="No active or non-expired Developer API Key found. Please create a NEW API Key to enable sandbox operations.",
                    )

                selected_jti = None
                for candidate_jti in active_keys:
                    limited = is_key_rate_limited(state, candidate_jti)
                    print(
                        f"  -> Key {candidate_jti} rate limit check: limited={limited}"
                    )
                    if not limited:
                        selected_jti = candidate_jti
                        break

                if selected_jti:
                    jti = selected_jti
                    print(
                        f"[Identity Bridge] SUCCESS: Auth0/API-Key mapped to active non-limited Key ID: {jti}"
                    )
                else:
                    jti = active_keys[0]
                    print(
                        f"[Identity Bridge] WARNING: All active developer keys are rate limited. Falling back to key ID: {jti}"
                    )

        if not jti:
            auth_failures_total.labels(reason="missing_jti").inc()
            raise HTTPException(
                status_code=401, detail="Invalid token: Missing JTI/Key ID"
            )

        # --- DISTRIBUTED VALIDATION (Redis -> Postgres) ---
        is_valid = False
        backend_val = None

        if state.use_redis:
            is_valid = state.redis_client.sismember("active_api_keys", jti)

        if not is_valid or (issuer != conf["issuer"]):
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            conn = state.get_db_conn()
            cursor = conn.cursor()
            query = "SELECT is_revoked, expires_at, backend FROM api_keys WHERE id = %s"
            cursor.execute(query, (jti,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                auth_failures_total.labels(reason="deactivated_key").inc()
                raise HTTPException(
                    status_code=401, detail="API Key has been deactivated or deleted"
                )

            if row[0] == 1:
                auth_failures_total.labels(reason="revoked_key").inc()
                raise HTTPException(status_code=401, detail="API Key has been revoked")

            if row[1] < now_iso:
                if state.use_redis:
                    state.redis_client.srem("active_api_keys", jti)
                auth_failures_total.labels(reason="expired_key").inc()
                raise HTTPException(status_code=401, detail="API Key has expired")

            backend_val = row[2]
            if state.use_redis:
                state.redis_client.sadd("active_api_keys", jti)
            is_valid = True

        # Mutate payload to bridge Identity context for downstream endpoints/guards
        payload["jti"] = jti
        if backend_val:
            payload["backend"] = backend_val
        elif "backend" not in payload:
            conn = state.get_db_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT backend FROM api_keys WHERE id = %s", (jti,))
            row = cursor.fetchone()
            conn.close()
            if row:
                payload["backend"] = row[0]

        # SUCCESS CASE: Record API key invocation rate
        api_key_requests_total.labels(api_key_id=jti).inc()
        print(f"[DEBUG SECURITY] SUCCESS: Session Verified (Key ID: {jti})")

        # Enforce click-triggered rate limiting on monitored actions
        path = request.url.path
        is_documentation = path.endswith("/docs")
        is_scan_job = path.endswith("/scan-jobs") and request.method == "POST"
        is_repo_scan = path.endswith("/repo-scan") and request.method == "POST"

        if is_documentation or is_scan_job or is_repo_scan:
            print(f"[Rate Limit] Enforcing window counter for action on path: {path}")
            await check_rate_limit(state, jti)

        # Update last_used_at in background
        asyncio.create_task(update_last_used(jti))

        # Session Identity Lockdown
        auth_header_raw = request.headers.get("authorization") or request.headers.get(
            "Authorization"
        )
        auth0_cookie = request.cookies.get("inspector_auth")

        if auth_header_raw and auth0_cookie:
            apikey_sub = payload.get("sub")
            try:
                cookie_payload = jwt.decode(
                    auth0_cookie, options={"verify_signature": False}
                )
                cookie_sub = cookie_payload.get("sub")

                if cookie_sub and apikey_sub and cookie_sub != apikey_sub:
                    print(
                        f"[SECURITY ALERT] IDENTITY MISMATCH: User {cookie_sub} attempted to use API Key belonging to User {apikey_sub}"
                    )
                    auth_failures_total.labels(reason="identity_mismatch").inc()
                    raise HTTPException(
                        status_code=403,
                        detail="Identity Lockdown: You cannot use an API key that belongs to another user.",
                    )
            except Exception as e:
                if isinstance(e, HTTPException):
                    raise e
                pass

        return payload

    except jwt.ExpiredSignatureError:
        auth_failures_total.labels(reason="token_expired_signature").inc()
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        auth_failures_total.labels(reason="invalid_token_signature").inc()
        hint = "Ensure Auth0 is returning an RS256 JWT, not an opaque token. Check that the API Audience is registered."
        if token == "undefined" or not token:
            hint = "Token was passed as empty or undefined. Please re-login on the dashboard."
        elif "." not in token:
            hint = "Received an opaque token (missing JWT segments). Check Auth0 API Audience configuration."

        token_snippet = f"{token[:10]}..." if len(token) > 10 else token
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token format ({str(e)}). Token snippet: '{token_snippet}'. {hint}",
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        auth_failures_total.labels(reason="unknown_auth_error").inc()
        raise HTTPException(status_code=401, detail=f"Authorization failed: {str(e)}")


async def update_last_used(jti: str):
    """Updates the last_used_at timestamp in the central database."""
    try:
        conn = state.get_db_conn()
        cursor = conn.cursor()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        query = "UPDATE api_keys SET last_used_at = %s WHERE id = %s"
        cursor.execute(query, (now, jti))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[background] Error updating last_used_at: {str(e)}")
