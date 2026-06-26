import asyncio
import json
import os
import sys
import urllib.request

JWT_TOKEN = os.environ.get("JWT_TOKEN")
if not JWT_TOKEN and len(sys.argv) > 1:
    JWT_TOKEN = sys.argv[1]

if not JWT_TOKEN:
    print(
        "Error: Please set JWT_TOKEN environment variable or pass it as the first argument."
    )
    sys.exit(1)

API_BASE_URL = os.environ.get("API_URL", "https://api-sandbox.01security.com")

URLS = [
    "https://github.com/tiangolo/fastapi",
    "https://github.com/firecracker-microvm/firecracker",
    "https://github.com/ytdl-org/youtube-dl",
    "https://github.com/tiangolo/fastapi",
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
            print(f"Submitted {repo_url} -> job_id: {data.get('job_id')}")
            return data.get("job_id")
    except Exception as e:
        print(f"Failed {repo_url}: {e}")
        return None


async def main():
    print(f"Submitting concurrent scans to {API_BASE_URL}...")
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, submit_scan, url) for url in URLS]
    job_ids = await asyncio.gather(*tasks)
    print(f"All submitted. Job IDs: {job_ids}")


if __name__ == "__main__":
    asyncio.run(main())
