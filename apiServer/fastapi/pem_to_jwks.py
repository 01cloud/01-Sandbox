import base64
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

def b64_url_encode(data):
    return base64.urlsafe_b64encode(data).decode('utf-8').replace('=', '')

with open("public.pem", "rb") as f:
    public_key = serialization.load_pem_public_key(f.read(), backend=default_backend())
    numbers = public_key.public_numbers()
    
    n = numbers.n
    e = numbers.e
    
    n_bytes = n.to_bytes((n.bit_length() + 7) // 8, byteorder='big')
    e_bytes = e.to_bytes((e.bit_length() + 7) // 8, byteorder='big')
    
    key = {
        "kty": "RSA",
        "use": "sig",
        "kid": "code-inspector-key-01",
        "alg": "RS256",
        "n": b64_url_encode(n_bytes),
        "e": b64_url_encode(e_bytes)
    }
    
    print(json.dumps({"keys": [key]}))
