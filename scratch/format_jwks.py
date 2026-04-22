import requests
import json
import base64

def to_base64url(s):
    # Standard Base64 to Base64URL
    s = s.replace('+', '-').replace('/', '_')
    # Remove padding
    while s.endswith('='):
        s = s[:-1]
    return s

url = "https://dev-axwc0ui527kw0c5d.us.auth0.com/.well-known/jwks.json"
resp = requests.get(url)
data = resp.json()

for key in data['keys']:
    key['n'] = to_base64url(key['n'])
    key['e'] = to_base64url(key['e'])
    # kid usually doesn't need change but let's be safe if it has weird chars
    key['kid'] = to_base64url(key['kid'])

print(json.dumps(data))
