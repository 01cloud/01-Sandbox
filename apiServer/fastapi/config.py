import os

def backend_mappings() -> dict[str, str]:
    """
    Returns a mapping of backend IDs to their internal cluster URLs.
    Can be extended via a JSON environment variable.
    """
    default_mappings = {
        "z1sandbox": os.environ.get("BACKEND_URL_Z1SANDBOX", "http://opensandbox-server:80"),
        "opensandbox": os.environ.get("BACKEND_URL_OPENSANDBOX", "http://opensandbox-server:80"),
    }
    
    # Allow override/extension via JSON string
    custom_json = os.environ.get("BACKEND_MAPPINGS_JSON")
    if custom_json:
        try:
            import json
            custom_mappings = json.loads(custom_json)
            default_mappings.update(custom_mappings)
        except Exception as e:
            print(f"[config] Failed to parse BACKEND_MAPPINGS_JSON: {e}")
            
    return default_mappings
    
def opensandbox_route_prefix() -> str:
    """
    Returns the versioned prefix for the internal Opensandbox backend.
    Example: /api/v1/01sbx
    """
    return os.environ.get("OPENSANDBOX_ROUTE_PREFIX", "/api/v1/01sbx").rstrip("/")

def opensandbox_base_url(backend_id: str = "opensandbox") -> str:
    """
    Retrieves the internal URL for a specific backend ID.
    """
    return backend_mappings().get(backend_id, "http://opensandbox-server:80")


def opensandbox_headers() -> dict[str, str]:
    """Generates standard HTTP parameter validating authorization."""
    api_key = os.environ.get("OPENSANDBOX_API_KEY", "your-secure-api-key")
    return {
        "Content-Type": "application/json",
        "OPEN-SANDBOX-API-KEY": api_key,
    }


def gateway_secret_config():
    """
    Returns configuration for the Kubernetes Secret used by the agentgateway.
    """
    return {
        "name": os.environ.get("GATEWAY_SECRET_NAME", "apikey"),
        "namespace": os.environ.get("GATEWAY_SECRET_NAMESPACE", "agentgateway-system"),
        "key": os.environ.get("GATEWAY_SECRET_KEY", "api-key"),
    }


def jwt_config():
    """
    Returns configuration for JWT signing (Issuer role).
    Automatically repairs common PEM formatting issues from env vars.
    """
    import base64
    raw_key = os.environ.get("JWT_PRIVATE_KEY", "").strip()
    processed_key = raw_key

    # Strategy 1: Handle literal \n (common in env vars)
    if "\\n" in processed_key:
        processed_key = processed_key.replace("\\n", "\n")
    
    # Strategy 2: Check if it's Base64 encoded (common in K8s secrets)
    if processed_key and not processed_key.startswith("---"):
        try:
            # Try to decode. If it succeeds and contains PEM headers, use it.
            decoded = base64.b64decode(processed_key).decode("utf-8")
            if "---" in decoded:
                processed_key = decoded
        except Exception:
            pass 

    # Strategy 3: Final cleanup
    processed_key = processed_key.strip()

    # Strategy 4: Diagnostic Validation
    if processed_key.startswith("-----BEGIN PUBLIC KEY-----"):
        print("[config] ERROR: JWT_PRIVATE_KEY appears to be a PUBLIC KEY. Signing requires a PRIVATE KEY.")
    elif processed_key and not processed_key.startswith("-----BEGIN"):
         print("[config] WARNING: JWT_PRIVATE_KEY is missing PEM headers (-----BEGIN...). Signing may fail.")

    # Diagnostic (Safe masking)
    key_peek = processed_key[:20].replace("\n", " ") + "..." if processed_key else "EMPTY"
    print(f"[config] JWT Key loaded (peek: {key_peek})")

    return {
        "private_key": processed_key,
        "public_jwks": os.environ.get("JWT_PUBLIC_JWKS", ""),
        "algorithm": os.environ.get("JWT_ALGORITHM", "RS256"),
        "expiration_minutes": int(os.environ.get("JWT_EXPIRATION_MINUTES", "60")),
        "issuer": os.environ.get("JWT_ISSUER", "code-inspector"),
        "auth0_domain": os.environ.get("AUTH0_DOMAIN", ""),
        "auth0_audience": os.environ.get("AUTH0_AUDIENCE", "code-inspector-api"),
    }
