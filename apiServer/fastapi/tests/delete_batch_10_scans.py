import asyncio
import json
import os
import sys
import urllib.request

# Fetch token from environment variable, command line, or default fallback
JWT_TOKEN = os.environ.get("JWT_TOKEN")
if not JWT_TOKEN and len(sys.argv) > 1:
    if sys.argv[1].startswith("eyJ"):
        JWT_TOKEN = sys.argv[1]

if not JWT_TOKEN:
    JWT_TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImNvZGUtaW5zcGVjdG9yLWtleS0wMSIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJnb29nbGUtb2F1dGgyfDEwOTQwMzkxOTIxOTUxNjg5NjYyMiIsImlhdCI6MTc4Mjk5MTYyNSwiZXhwIjo0OTM2NTkxNjI1LCJpc3MiOiIwMSBTYW5kYm94IiwiYXVkIjoiY29kZS1pbnNwZWN0b3ItYXBpIiwianRpIjoiMDczYjNjZjktYjlmOC00Y2M1LTg4MGQtZGM4OTU3MGUzYmIzIiwiYmFja2VuZCI6IloxX1NBTkRCT1gifQ.iS5GwT_fF2ecuSPWEWY99QYRvLXA_CecGqex4h_10PSnzKYeDqZg4X4qTWKd1gXg_9mQpoJOx1yZX3j6X77fpk-IynXvZmAPoKu7tob2eljCfwRsD8JRDqqBcejSXX-O9sp_Vkq4z58lnSgS0Z1hVKdCwgJ58N1ZmoOTwWTx843XU6isIlu0u4Mcxr0uN8RB_us3G-TohCZOBHvb7Tu8LLgwD18fgQW6Kkb9gBDGdHoVmJc6M2P0H7AoRLsBOnereCQluSSXYFuzZYXIAv9a7M4vtPYLZ_HPWSv41ar5GlYa_qOwwH-tHUX96d18aZ7tglDrX6Bn2DPgccM20FulMQ"

API_BASE_URL = os.environ.get("API_URL", "http://localhost:8000")

# The 10 target GitHub repositories
TARGET_REPOS = {
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
}


def fetch_active_jobs():
    url = f"{API_BASE_URL.rstrip('/')}/v1/repo-scan/jobs"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {JWT_TOKEN}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error fetching jobs: {e}")
        return []


def delete_job(job_id):
    url = f"{API_BASE_URL.rstrip('/')}/v1/jobs/{job_id}?purge=true"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {JWT_TOKEN}",
        },
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[DELETED] Job ID: {job_id} -> {data.get('status')}")
            return job_id, True
    except Exception as e:
        print(f"[FAILED DELETION] Job ID: {job_id}: {e}")
        return job_id, False


async def main():
    # If job IDs are passed explicitly as command line arguments
    explicit_job_ids = [arg for arg in sys.argv[1:] if not arg.startswith("eyJ")]

    job_ids_to_delete = []

    if explicit_job_ids:
        print(f"Deleting {len(explicit_job_ids)} explicitly provided Job IDs...")
        job_ids_to_delete = explicit_job_ids
    else:
        print(
            f"Fetching active scan jobs from {API_BASE_URL} to identify target repository scans..."
        )
        all_jobs = fetch_active_jobs()
        if not all_jobs:
            print("No active jobs found or error occurred.")
            return

        for job in all_jobs:
            meta = job.get("metadata", {})
            repo_url = meta.get("repo_url")
            if repo_url in TARGET_REPOS:
                job_ids_to_delete.append(job.get("job_id"))

        if not job_ids_to_delete:
            print(
                "No matching jobs found in the active jobs list for the 10 target repositories."
            )
            return

        print(f"Found {len(job_ids_to_delete)} matching jobs to delete.")

    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, delete_job, jid) for jid in job_ids_to_delete]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
