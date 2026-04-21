import os

def opensandbox_base_url() -> str:
    """
    Points directly to the internal Kubernetes service by default.
    Using 'http://opensandbox-server:80' resolves locally inside the cluster.
    """
    return os.environ.get("BACKEND_URL_OPENSANDBOX", "http://opensandbox-server:80")


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
    }
