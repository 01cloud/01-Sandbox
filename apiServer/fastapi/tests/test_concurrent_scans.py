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

URLS = [
    "https://github.com/agentgateway/agentgateway",
    "https://github.com/firecracker-microvm/firecracker",
    "https://github.com/ytdl-org/youtube-dl",
    "https://github.com/tiangolo/fastapi",
]


def submit_scan(repo_url):
    url = "http://localhost:30080/v1/repo-scan"
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
            print(f'Submitted {repo_url} -> job_id: {data.get("job_id")}')
            return data.get("job_id")
    except Exception as e:
        print(f"Failed {repo_url}: {e}")
        return None


async def main():
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, submit_scan, url) for url in URLS]
    job_ids = await asyncio.gather(*tasks)
    print(f"All submitted. Job IDs: {job_ids}")


if __name__ == "__main__":
    asyncio.run(main())
