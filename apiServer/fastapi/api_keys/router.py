from __future__ import annotations

import datetime
import re
import uuid
from typing import Callable

import bleach
import jwt
from config import jwt_config
from fastapi import APIRouter, Depends, HTTPException, status
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
    async def list_user_api_keys(payload: dict = Depends(validate_token)):
        """Retrieves all active and revoked keys for the authenticated user from central store."""
        user_id = payload.get("sub")
        conn = state.get_db_conn()

        # Handle dict behavior difference between sqlite3 and psycopg2
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT * FROM api_keys WHERE LOWER(user_id) = LOWER(%s) AND expires_at > %s"
        cursor.execute(query, (user_id, now_iso))
        rows = cursor.fetchall()
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
        req: APIKeyCreateRequest, payload: dict = Depends(validate_token)
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
        if req.ttl_hours == -1:
            expires_at = now + datetime.timedelta(
                days=365 * 100
            )  # Effectively never expires
            status_msg = (
                f"Key '{sanitized_name}' generated successfully. Valid indefinitely."
            )
        elif req.ttl_hours < 1:
            expires_at = now + datetime.timedelta(hours=req.ttl_hours)
            minutes = int(req.ttl_hours * 60)
            status_msg = f"Key '{sanitized_name}' generated successfully. Valid for {minutes} minute(s)."
        else:
            expires_at = now + datetime.timedelta(hours=req.ttl_hours)
            status_msg = f"Key '{sanitized_name}' generated successfully. Valid for {req.ttl_hours} hour(s)."

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
