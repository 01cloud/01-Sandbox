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
    JWT_TOKEN = ""

API_BASE_URL = os.environ.get("API_URL", "https://api-sandbox.01security.com")

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
