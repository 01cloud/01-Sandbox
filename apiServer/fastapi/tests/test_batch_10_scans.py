import asyncio
import json
import os
import sys
import urllib.request

# Fetch token from environment variable, command line, or default fallback
JWT_TOKEN = os.environ.get("JWT_TOKEN")
if not JWT_TOKEN and len(sys.argv) > 1:
    JWT_TOKEN = sys.argv[1]

if not JWT_TOKEN:
    JWT_TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImNvZGUtaW5zcGVjdG9yLWtleS0wMSIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJnb29nbGUtb2F1dGgyfDEwOTQwMzkxOTIxOTUxNjg5NjYyMiIsImlhdCI6MTc4Mjk5MTYyNSwiZXhwIjo0OTM2NTkxNjI1LCJpc3MiOiIwMSBTYW5kYm94IiwiYXVkIjoiY29kZS1pbnNwZWN0b3ItYXBpIiwianRpIjoiMDczYjNjZjktYjlmOC00Y2M1LTg4MGQtZGM4OTU3MGUzYmIzIiwiYmFja2VuZCI6IloxX1NBTkRCT1gifQ.iS5GwT_fF2ecuSPWEWY99QYRvLXA_CecGqex4h_10PSnzKYeDqZg4X4qTWKd1gXg_9mQpoJOx1yZX3j6X77fpk-IynXvZmAPoKu7tob2eljCfwRsD8JRDqqBcejSXX-O9sp_Vkq4z58lnSgS0Z1hVKdCwgJ58N1ZmoOTwWTx843XU6isIlu0u4Mcxr0uN8RB_us3G-TohCZOBHvb7Tu8LLgwD18fgQW6Kkb9gBDGdHoVmJc6M2P0H7AoRLsBOnereCQluSSXYFuzZYXIAv9a7M4vtPYLZ_HPWSv41ar5GlYa_qOwwH-tHUX96d18aZ7tglDrX6Bn2DPgccM20FulMQ"

API_BASE_URL = os.environ.get("API_URL", "http://localhost:8000")

# 10 popular GitHub repositories for batch testing
URLS = [
    "https://github.com/tiangolo/fastapi",
    "https://github.com/psf/requests",
    "https://github.com/pallets/flask",
    "https://github.com/django/django",
    "https://github.com/encode/django-rest-framework",
    "https://github.com/pytest-dev/pytest",
    "https://github.com/docker/docker-py",
    "https://github.com/pypa/pip",
    "https://github.com/python-tortoise/tortoise-orm",
    "https://github.com/encode/httpx",
]


def submit_scan(repo_url):
    url = f"{API_BASE_URL.rstrip('/')}/v1/repo-scan"
    payload = {"repo_url": repo_url}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {JWT_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[SUBMITTED] {repo_url} -> Job ID: {data.get('job_id')}")
            return repo_url, data.get("job_id")
    except Exception as e:
        print(f"[FAILED] {repo_url}: {e}")
        return repo_url, None


async def main():
    print(f"Submitting 10 concurrent scans to {API_BASE_URL}...")
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, submit_scan, url) for url in URLS]
    results = await asyncio.gather(*tasks)

    print("\n--- Summary of Submissions ---")
    for repo, job_id in results:
        status = "Success" if job_id else "Failed"
        print(f"- {repo}: {status} (Job ID: {job_id})")


if __name__ == "__main__":
    asyncio.run(main())
