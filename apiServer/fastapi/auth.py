import time
import json
import jwt
import httpx
import datetime
from fastapi import Request, HTTPException
from config import jwt_config
from state import state

# Cache for remote JWKS (Auth0)
jwks_cache = {
    "last_updated": 0,
    "jwks": None
}

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

async def validate_token(request: Request):
    """
    Decodes and validates the RS256 JWT produced by the Edge Gateway's 
    cookie transformation.
    """
    path = request.url.path
    is_execution_route = path.startswith("/v1/run") or ("/api/z1sandbox/" in path and "/docs" not in path and "/openapi.json" not in path)
    
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    raw_token = None
    source = "header"

    if auth_header:
        raw_token = auth_header.replace("Bearer ", "", 1) if auth_header.startswith("Bearer ") else auth_header
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
        error_msg = "Execution required an explicit API Key in the Authorization header." if is_execution_route else "Authentication required"
        raise HTTPException(status_code=401, detail=error_msg)
    
    token = raw_token
    conf = jwt_config()
    
    try:
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified_payload.get("iss")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        
        if not kid:
            raise HTTPException(status_code=401, detail="Missing 'kid' in token header")
        
        if issuer and issuer.startswith("https://") and conf["auth0_domain"] and conf["auth0_domain"] in issuer:
            target_jwks = await get_remote_jwks(f"{issuer.rstrip('/')}/.well-known/jwks.json")
            target_audience = conf["auth0_audience"]
            target_issuer = issuer
        else:
            jwks_data = json.loads(conf["public_jwks"])
            target_jwks = jwt.PyJWKSet.from_dict(jwks_data)
            target_audience = "code-inspector-api"
            target_issuer = conf["issuer"]
        
        signing_key = None
        for key in target_jwks.keys:
            if key.key_id == kid:
                signing_key = key
                break
        
        try:
            payload = jwt.decode(
                token, 
                signing_key.key, 
                algorithms=[conf["algorithm"]],
                audience=target_audience,
                issuer=target_issuer
            )
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
        
        jti = payload.get("jti")
        user_id = payload.get("sub")
        
        is_management_route = any(request.url.path.startswith(p) for p in ["/v1/api-keys", "/v1/generate-api", "/v1/revoke-api-key", "/v1/history"])
        
        if issuer != conf["issuer"]:
            if is_management_route:
                return payload
            
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            conn = state.get_db_conn()
            cursor = conn.cursor()
            query = """
                SELECT id FROM api_keys 
                WHERE user_id = %s AND is_revoked = 0 AND expires_at > %s
                ORDER BY created_at DESC LIMIT 1
            """ if state.use_postgres else "SELECT id FROM api_keys WHERE user_id = ? AND is_revoked = 0 AND expires_at > ? ORDER BY created_at DESC LIMIT 1"
            cursor.execute(query, (user_id, now_iso))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                jti = row[0]
            else:
                raise HTTPException(status_code=403, detail="No active or non-expired Developer API Key found.")

        if not jti:
            raise HTTPException(status_code=401, detail="Invalid token: Missing JTI/Key ID")

        is_valid = False
        if state.use_redis:
            is_valid = state.redis_client.sismember("active_api_keys", jti)
        
        if not is_valid:
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            conn = state.get_db_conn()
            cursor = conn.cursor()
            query = "SELECT is_revoked, expires_at FROM api_keys WHERE id = %s" if state.use_postgres else "SELECT is_revoked, expires_at FROM api_keys WHERE id = ?"
            cursor.execute(query, (jti,))
            row = cursor.fetchone()
            conn.close()
            
            if not row or row[0] == 1 or row[1] < now_iso:
                raise HTTPException(status_code=401, detail="API Key is invalid, revoked or expired")
            
            if state.use_redis:
                state.redis_client.sadd("active_api_keys", jti)
            is_valid = True
        
        # Identity Lockdown
        auth0_cookie = request.cookies.get("inspector_auth")
        if auth_header and auth0_cookie:
            apikey_sub = payload.get("sub")
            try:
                cookie_payload = jwt.decode(auth0_cookie, options={"verify_signature": False})
                cookie_sub = cookie_payload.get("sub")
                if cookie_sub and apikey_sub and cookie_sub != apikey_sub:
                    raise HTTPException(status_code=403, detail="Identity Lockdown: User mismatch")
            except Exception as e:
                if isinstance(e, HTTPException): raise e
                pass

        return payload
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=401, detail=f"Authorization failed: {str(e)}")
