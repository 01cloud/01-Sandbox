import base64
import json

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

private_key_pem = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC4cJ4r0fjpgQ8j
Q93fNhi4Nmmy6aCqHcqw2EI4wynn6S9R56yLpbkF5OHKiynfeArbbpPUC5xcl7oy
cQm6Lm7F4CB7pKaNMmoB3i30Qw3LBeq62CdDD2DiYJ7J9kMHdHZIOddO/H0cqlIV
y1KmknQZh6qaSJG3mUtNa4edif0ItEfwe2Bz3I/XTBxG5vovU6Q0wLxwCKriLlJF
yoyLj74SMsgMDYKSW4qqdkoFiLHtoZ7r7n1ccbYBE3LzsXCvojyQQGOH+vattl9V
jngAj4ic2Pyoim1gWgz89HN43DVUPY1i57ZLpkCV8Fqcr2R6I2ADguXtO9v/ByF+
/awzEqZ3AgMBAAECggEAPUofJYym3GFouF1LE2uUl1JtmWiNXTp9KnsStm4UNN6G
PP9xlQ5DB7RhW78W8Q2g/f4m3aXmFdbNpwltJBNd+B9ca/nw5qbEF07PkoXdxYN3
1xMRKPWpSlC1M4PEDmwjCik+ZR7+LFJk6J0iH/w23Gz6wj4vPgWQwm9jJ3S9QvCk
fDm983jYXCAmG/kzJXS8BUIVtvoUXiDE2RdWxfaK9fqw5XOWlsK8gqLEkvG2HK2Y
9UADhsWbAVlAmHE5rAjIwoG2+8ZrkoamV75jhM2KG78Gsp4/IHi34J5G4u/xFmCw
wO4iEuKuojYBK1i98gH9Z9ZRRRRLF0/a2qfmRApqwQKBgQDwHZu1hkR8xZ19UtDJ
prcj0bQXEa8U3GNM1o1H/Sj6TQOpb6Ge9LusiL2FOAcTFbpWHeRU6obDwOi44YQm
Drp9qjiHy3FPCzAiy43MhWHDqdmDIROOMGNVntNr/Y3+MrcJIUBdigguYeFAnMa3
8V6n3l25WR1qmnvxFt5ERMEbEQKBgQDEpCIa0j4alLJ5SgANtSoDnp9WMrHBj4ZK
RfomAGLB1heBWefzYwWmodABgkVmKAFceQQnYLf6s/lVQ4zJo1hNVIujP37HAnFB
YKPWcYD127u2utKQTZCJshTPU1o0hIFIu9MWQhctNK+ukRV+BdGWDsRY2t2TsswZ
IDho5UJZBwKBgQCys+Z57+ay0cRHTEZVfb1ZbC3S6XdbWDaTLi7RwAkqV/z8sjcf
/VZbrx0Vs8AYJRicFP0lcbJAqCbLyhM228lR00jlT7URterqSoJLD43WOGfInupV
7Y9QXrdM7NUrtYThx5yGwT8bff9NviBrL7lZyDYKxtcMytKpeHKNbnolcQKBgF/T
YG9Tn0ISznp+zlHfxA6pgRpfb/JUf+u3+DQGohm1vBSj/H3F9p4CYrwpgKpMuoUW
0ChkYKPCirG7TmOAv2pH1hiCu8Q9c3WZo53ACElHgE2G80+xDMudRbjW9LF9FQed
yDsjj+nOsAJQ33lfWohWv+ZRGgN88SsZYYP0nQWHAoGBAIigNpk8Mrx0aQWJQ97H
2d/EFgneIQKYzrLeLzt8+WCed/mAesDGL21TI7blZYtSq9Gba6M8VnQhnjk9yul9
RM6PSdDPTe6f01TBU71sMsjPtAxCrsonn/ISzHatpJ9vAUmmHXK3/blplEc7Fhkl
kYVZfPw/fIDkPCKJx7E8mCmM
-----END PRIVATE KEY-----"""

private_key_obj = serialization.load_pem_private_key(
    private_key_pem.encode("utf-8"), password=None, backend=default_backend()
)


def b64_url_encode(data):
    return base64.urlsafe_b64encode(data).decode("utf-8").replace("=", "")


public_key_obj = private_key_obj.public_key()
numbers = public_key_obj.public_numbers()
n_bytes = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, byteorder="big")
e_bytes = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, byteorder="big")

jwks_key = {
    "kty": "RSA",
    "use": "sig",
    "kid": "code-inspector-key-01",
    "alg": "RS256",
    "n": b64_url_encode(n_bytes),
    "e": b64_url_encode(e_bytes),
}
print(json.dumps({"keys": [jwks_key]}))
