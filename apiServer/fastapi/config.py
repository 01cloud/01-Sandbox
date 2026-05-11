import os
import base64

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
    Returns configuration for JWT signing (Issuer role) with self-healing PEM repair.
    """
    raw_private_key = os.environ.get("JWT_PRIVATE_KEY", "")
    
    def repair_pem(key_str, is_private=True):
        if not key_str: return None
        # 1. Handle literal \n and clean whitespace
        k = key_str.replace("\\n", "\n").replace("\\r", "").strip()
        
        # 2. Base64 fallback (if the entire thing is b64 encoded)
        if "-----BEGIN" not in k:
            try:
                decoded = base64.b64decode(k).decode("utf-8")
                if "-----BEGIN" in decoded:
                    k = decoded
            except:
                pass

        # 3. Ensure correct headers if they are still missing
        if "-----BEGIN" not in k:
            if is_private:
                k = f"-----BEGIN PRIVATE KEY-----\n{k}\n-----END PRIVATE KEY-----"
            else:
                k = f"-----BEGIN PUBLIC KEY-----\n{k}\n-----END PUBLIC KEY-----"
        
        # 4. Final validation check
        if is_private and "PUBLIC" in k:
            print("[config] WARNING: Expected Private Key but found PUBLIC key header!")
        return k

    processed_key = repair_pem(raw_private_key, is_private=True)

    # Strategy: Load from local file if env is empty
    if not processed_key:
        private_pem_path = "private.pem"
        if os.path.exists(private_pem_path):
            with open(private_pem_path, "r") as f:
                processed_key = repair_pem(f.read(), is_private=True)
            print(f"[config] JWT Private Key loaded from local file: {private_pem_path}")

    public_jwks = os.environ.get("JWT_PUBLIC_JWKS", "").strip()
    private_key_obj = None
    public_key_obj = None

    if processed_key:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            private_key_obj = serialization.load_pem_private_key(
                processed_key.encode("utf-8"),
                password=None,
                backend=default_backend()
            )
            print(f"[config] Private Key Object loaded successfully (Type: {type(private_key_obj).__name__})")
        except Exception as e:
            print(f"[config] CRITICAL: Failed to load Private Key Object: {e}")

    # Auto-derive JWKS if missing
    if processed_key and not public_jwks:
        try:
            import json

            def b64_url_encode(data):
                return base64.urlsafe_b64encode(data).decode('utf-8').replace('=', '')

            if private_key_obj:
                public_key_obj = private_key_obj.public_key()
                numbers = public_key_obj.public_numbers()
                n_bytes = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, byteorder='big')
                e_bytes = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, byteorder='big')
                
                jwks_key = {
                    "kty": "RSA",
                    "use": "sig",
                    "kid": "code-inspector-key-01",
                    "alg": "RS256",
                    "n": b64_url_encode(n_bytes),
                    "e": b64_url_encode(e_bytes)
                }
                public_jwks = json.dumps({"keys": [jwks_key]})
                print(f"[config] Public JWKS derived from Private Key")
        except Exception as e:
            print(f"[config] Failed to generate Public JWKS: {e}")

    return {
        "private_key": processed_key,
        "private_key_obj": private_key_obj,
        "public_key_obj": public_key_obj,
        "public_jwks": public_jwks,
        "algorithm": os.environ.get("JWT_ALGORITHM", "RS256"),
        "expiration_minutes": int(os.environ.get("JWT_EXPIRATION_MINUTES", "60")),
        "issuer": os.environ.get("JWT_ISSUER", "01 Sandbox"),
        "auth0_domain": os.environ.get("AUTH0_DOMAIN", ""),
        "auth0_audience": os.environ.get("AUTH0_AUDIENCE", "code-inspector-api"),
    }
