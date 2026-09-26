import os
import time

import requests

API_KEY = os.environ.get("API_KEY", "")
API_URL = os.environ.get("API_URL", "http://localhost:8000")

headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
payload = {"language": "python", "code": "print('hello')"}

print("Testing rate limit with Python...")
for i in range(1, 11):
    response = requests.post(f"{API_URL}/v1/scan-jobs", json=payload, headers=headers)
    print(f"Request {i} -> HTTP Status: {response.status_code}")
    if response.status_code == 429:
        print(f"Rate limit hit details: {response.json()}")
