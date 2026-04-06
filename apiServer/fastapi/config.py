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
