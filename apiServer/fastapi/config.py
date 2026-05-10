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
    """
    return {
        "private_key": os.environ.get("JWT_PRIVATE_KEY", ""),
        "public_jwks": os.environ.get("JWT_PUBLIC_JWKS", ""),
        "algorithm": os.environ.get("JWT_ALGORITHM", "RS256"),
        "expiration_minutes": int(os.environ.get("JWT_EXPIRATION_MINUTES", "60")),
        "issuer": os.environ.get("JWT_ISSUER", "code-inspector"),
        "auth0_domain": os.environ.get("AUTH0_DOMAIN", ""),
        "auth0_audience": os.environ.get("AUTH0_AUDIENCE", "code-inspector-api"),
    }
