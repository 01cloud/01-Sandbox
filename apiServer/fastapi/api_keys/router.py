from __future__ import annotations

import datetime
import re
import uuid
from typing import Callable

import bleach
import jwt
from config import jwt_config
from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg2.extras import RealDictCursor

from .models import (
    APIKeyCreateRequest,
    APIKeyListResponse,
    APIKeyRecord,
    GenerateAPIResponse,
)


def get_api_keys_router(state, validate_token: Callable) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/generate-api",
        response_model=GenerateAPIResponse,
        tags=["Security"],
        dependencies=[Depends(validate_token)],
    )
    async def generate_api(user_id: str = "default-user"):
        """
        Generates a secure JWT for multi-user authentication.
        The token is signed with a shared secret and verified by the agentgateway.
        """
        conf = jwt_config()
        private_key = conf["private_key"]
        algorithm = conf["algorithm"]
        expires_delta = conf["expiration_minutes"]
        issuer = conf["issuer"]

        try:
            now = datetime.datetime.now(datetime.UTC)
            payload = {
                "sub": user_id,
                "iat": now,
                "exp": now + datetime.timedelta(minutes=expires_delta),
                "iss": issuer,
                "aud": "code-inspector-api",
            }

            token = jwt.encode(
                payload,
                private_key,
                algorithm=algorithm,
                headers={"kid": "code-inspector-key-01"},
            )

            return GenerateAPIResponse(
                api_key=token,
                api_key_id=payload.get("jti", "legacy"),
                status=f"JWT generated successfully for {user_id}. Valid for {expires_delta} minutes.",
            )

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate JWT: {str(e)}",
            )

    @router.get(
        "/v1/api-keys",
        response_model=APIKeyListResponse,
        tags=["Security"],
        dependencies=[Depends(validate_token)],
    )
    async def list_user_api_keys(
        request: Request, payload: dict = Depends(validate_token)
    ):
        """Retrieves all active and revoked keys for the authenticated user from central store."""
        user_id = payload.get("sub")
        conn = state.get_db_conn()

        # Handle dict behavior difference between sqlite3 and psycopg2
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT * FROM api_keys WHERE LOWER(user_id) = LOWER(%s) AND expires_at > %s"
        cursor.execute(query, (user_id, now_iso))
        rows = [dict(r) for r in cursor.fetchall()]

        # Self-healing: if any key is missing the user's email, resolve it and update the DB
        needs_email_update = any(not r.get("user_email") for r in rows)
        resolved_email = None

        if needs_email_update:
            # 1. Try to get from token claims
            for k, v in payload.items():
                if k == "email" or k.endswith("/email"):
                    resolved_email = v
                    break

            # 2. Try to get from /userinfo fallback
            if (
                not resolved_email
                and payload.get("iss")
                and "auth0" in payload.get("iss")
            ):
                try:
                    auth_header = request.headers.get(
                        "Authorization"
                    ) or request.headers.get("authorization")
                    if auth_header:
                        import httpx

                        async with httpx.AsyncClient() as client:
                            r = await client.get(
                                f"{payload['iss'].rstrip('/')}/userinfo",
                                headers={"Authorization": auth_header},
                                timeout=5.0,
                            )
                            if r.status_code == 200:
                                userinfo = r.json()
                                resolved_email = userinfo.get("email")
                                print(
                                    f"[Security] Self-healed email={resolved_email} from /userinfo for list_user_api_keys"
                                )
                except Exception as e:
                    print(f"[Security] Failed /userinfo self-healing fetch: {e}")

            if resolved_email:
                try:
                    update_query = "UPDATE api_keys SET user_email = %s WHERE LOWER(user_id) = LOWER(%s) AND (user_email IS NULL OR user_email = '')"
                    cursor.execute(update_query, (resolved_email, user_id))
                    conn.commit()
                    # Update local rows to reflect the healed email
                    for r in rows:
                        if not r.get("user_email"):
                            r["user_email"] = resolved_email
                except Exception as update_err:
                    print(
                        f"[Security] Failed to self-heal user_email in DB: {update_err}"
                    )
                    conn.rollback()

        conn.close()

        keys = []
        for row in rows:
            keys.append(
                APIKeyRecord(
                    id=row["id"],
                    name=row["name"],
                    backend=row["backend"],
                    user_id=row["user_id"],
                    user_email=row.get("user_email"),
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    last_used_at=row["last_used_at"],
                    is_revoked=bool(row["is_revoked"]),
                    prefix=row["prefix"],
                )
            )
        return APIKeyListResponse(keys=keys)

    @router.post(
        "/v1/api-keys",
        response_model=GenerateAPIResponse,
        tags=["Security"],
        dependencies=[Depends(validate_token)],
    )
    async def create_api_key(
        req: APIKeyCreateRequest,
        request: Request,
        payload: dict = Depends(validate_token),
    ):
        """
        Generates a new signed API key (JWT) and persists metadata for revocation/management.
        One-time reveal implementation.
        """
        user_id = payload.get("sub")

        # 1. Strip all HTML/Script tags using bleach
        clean_name = bleach.clean(req.name, tags=[], strip=True).strip()

        # 2. Strict Whitelist Sanitization: Allow only alphanumeric, spaces, dashes, and underscores
        sanitized_name = re.sub(r"[^a-zA-Z0-9\s\-_]", "", clean_name).strip()

        if not sanitized_name:
            sanitized_name = "Untitled Key"

        # Check quota (Max 5 keys per user)
        conn = state.get_db_conn()
        cursor = conn.cursor()
        query_count = "SELECT COUNT(*) FROM api_keys WHERE LOWER(user_id) = LOWER(%s)"
        cursor.execute(query_count, (user_id,))
        count = cursor.fetchone()[0]
        conn.close()

        if count >= 5:
            raise HTTPException(
                status_code=403,
                detail="API Key limit reached (Max 5). Please delete an existing key to create a new one.",
            )

        conf = jwt_config()
        jti = str(uuid.uuid4())
        now = datetime.datetime.now(datetime.UTC)

        # Convert ttl_seconds to ttl_hours if provided
        ttl_hours = req.ttl_hours
        if req.ttl_seconds is not None:
            if req.ttl_seconds == -1:
                ttl_hours = -1
            else:
                ttl_hours = req.ttl_seconds / 3600.0

        if ttl_hours == -1:
            expires_at = now + datetime.timedelta(
                days=365 * 100
            )  # Effectively never expires
            status_msg = (
                f"Key '{sanitized_name}' generated successfully. Valid indefinitely."
            )
        elif ttl_hours < 1:
            expires_at = now + datetime.timedelta(hours=ttl_hours)
            minutes = int(ttl_hours * 60)
            status_msg = f"Key '{sanitized_name}' generated successfully. Valid for {minutes} minute(s)."
        else:
            expires_at = now + datetime.timedelta(hours=ttl_hours)
            status_msg = f"Key '{sanitized_name}' generated successfully. Valid for {ttl_hours} hour(s)."

        token_payload = {
            "sub": user_id,
            "iat": now,
            "exp": expires_at,
            "iss": conf["issuer"],
            "aud": "code-inspector-api",
            "jti": jti,
            "backend": req.backend.value,
        }

        try:
            # Use pre-loaded object if available for better reliability with RS256
            signing_key = conf.get("private_key_obj") or conf["private_key"]
            token = jwt.encode(
                token_payload,
                signing_key,
                algorithm=conf["algorithm"],
                headers={"kid": "code-inspector-key-01"},
            )
        except Exception as e:
            print(f"[Security] CRITICAL: JWT Encoding Failed: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Authentication setup failed: {str(e)}"
            )

        # Persist metadata with User identity
        # Priority: Explicit request field -> Token claim -> Dynamic Namespaced/Custom claims containing email
        auth0_email = None
        if isinstance(payload, dict):
            for k, v in payload.items():
                if k == "email" or k.endswith("/email"):
                    auth0_email = v
                    break

        user_email = req.user_email or auth0_email

        # Fallback to Auth0 Userinfo endpoint if email is still not resolved
        if (
            not user_email
            and payload
            and payload.get("iss")
            and "auth0" in payload.get("iss")
        ):
            try:
                auth_header = request.headers.get(
                    "Authorization"
                ) or request.headers.get("authorization")
                if auth_header:
                    import httpx

                    async with httpx.AsyncClient() as client:
                        r = await client.get(
                            f"{payload['iss'].rstrip('/')}/userinfo",
                            headers={"Authorization": auth_header},
                            timeout=5.0,
                        )
                        if r.status_code == 200:
                            userinfo = r.json()
                            user_email = userinfo.get("email")
                            print(
                                f"[Security] Resolved user_email={user_email} from Auth0 /userinfo"
                            )
            except Exception as e:
                print(f"[Security] Failed to fetch /userinfo fallback: {e}")

        conn = state.get_db_conn()
        cursor = conn.cursor()
        query = """
            INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """

        cursor.execute(
            query,
            (
                jti,
                sanitized_name,
                req.backend.value,
                user_id,
                user_email,
                now.isoformat(),
                expires_at.isoformat(),
                f"ci_{jti[:8]}",
            ),
        )
        conn.commit()
        conn.close()

        # Sync to Redis for instant cluster-wide activation
        if state.use_redis:
            state.redis_client.sadd("active_api_keys", jti)

        return GenerateAPIResponse(api_key=token, api_key_id=jti, status=status_msg)

    @router.delete(
        "/v1/api-keys/{jti}",
        tags=["Security"],
        dependencies=[Depends(validate_token)],
    )
    async def delete_api_key(jti: str, payload: dict = Depends(validate_token)):
        """Deletes/Revokes an API key instantly from global registry and cache."""
        user_id = payload.get("sub")
        conn = state.get_db_conn()
        cursor = conn.cursor()

        query = "DELETE FROM api_keys WHERE id = %s AND user_id = %s"
        cursor.execute(query, (jti, user_id))
        rows_deleted = cursor.rowcount
        conn.commit()
        conn.close()

        if rows_deleted == 0:
            raise HTTPException(status_code=404, detail="Key not found or unauthorized")

        # Instant revocation across all pods via shared Redis
        if state.use_redis:
            state.redis_client.srem("active_api_keys", jti)
            print(f"[DEBUG SECURITY] Key {jti} removed from shared Redis allowlist")

        return {
            "status": "success",
            "message": f"Key {jti} has been permanently destroyed across the cluster.",
        }

    return router
