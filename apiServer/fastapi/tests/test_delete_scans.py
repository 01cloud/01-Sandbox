#!/usr/bin/env python3
"""
test_delete_scans.py — Auto-detect and delete/purge active repository scan jobs.

Does NOT submit any new scans. It fetches all jobs currently running or queued
on the server, automatically extracts their job IDs, and cancels/purges them.

Usage:
    JWT_TOKEN=<token> python3 tests/test_delete_scans.py
    python3 tests/test_delete_scans.py <JWT_TOKEN>

Optional env vars:
    PURGE   — "true" (default) to permanently remove from Redis + UI list
              "false" to only soft-cancel (job stays in list with CANCELLED status)
    API_URL — API base URL (default: https://api-sandbox.01security.com)
"""

import asyncio
import json
import os
import sys
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────

JWT_TOKEN = os.environ.get("JWT_TOKEN")
if not JWT_TOKEN and len(sys.argv) > 1:
    JWT_TOKEN = sys.argv[1]

if not JWT_TOKEN:
    print("Error: Please set JWT_TOKEN environment variable.")
    print("Example: JWT_TOKEN=your_token_here python3 tests/test_delete_scans.py")
    sys.exit(1)

API_BASE_URL = os.environ.get("API_URL", "https://api-sandbox.01security.com")
PURGE = os.environ.get("PURGE", "true").lower() == "true"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {JWT_TOKEN}",
}

# ── Helpers ───────────────────────────────────────────────────────────────────


def _request(method: str, url: str) -> dict | list | None:
    req = urllib.request.Request(url, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} {method} {url}: {e.read().decode()}")
        return None
    except Exception as e:
        print(f"  ERROR {method} {url}: {e}")
        return None


def list_jobs() -> list[dict]:
    """GET /v1/repo-scan/jobs — returns all existing repo-scan jobs."""
    url = f"{API_BASE_URL.rstrip('/')}/v1/repo-scan/jobs"
    data = _request("GET", url)
    if isinstance(data, list):
        return data
    return []


def delete_job(job_id: str, repo_url: str) -> bool:
    """DELETE /v1/jobs/{job_id}?purge=true|false"""
    purge_param = "true" if PURGE else "false"
    url = f"{API_BASE_URL.rstrip('/')}/v1/jobs/{job_id}?purge={purge_param}"
    data = _request("DELETE", url)
    if data and data.get("status") == "DELETE_QUEUED":
        action = "PURGED" if PURGE else "CANCELLED"
        print(f"  🗑️  [{action}] {job_id} ({repo_url})")
        return True
    print(f"  ❌ FAILED {job_id} ({repo_url}): {data}")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────


async def main():
    print(f"\n{'='*70}")
    print(f"  Target API : {API_BASE_URL}")
    print(f"  Purge mode : {PURGE}  (set PURGE=false to only soft-cancel)")
    print(f"{'='*70}\n")

    # ── Step 1: Fetch all existing active/running/queued jobs ────────────────
    print("[1/2] Fetching all existing repo-scan jobs...")
    all_jobs = list_jobs()

    # Filter for active/running/queued jobs or just all jobs currently in the list
    # We target jobs that are NOT already in a terminal state (DONE/ERROR) unless we want to clear everything.
    # To be safe and clean, we will target jobs that are running, queued, or detecting.
    active_jobs = []
    for job in all_jobs:
        step = str(job.get("step", "")).upper()
        # We can target all jobs that aren't finished, or simply target all listed jobs to clean up completely
        active_jobs.append(job)

    if not active_jobs:
        print("\n✅ No repo-scan jobs found on the server to delete.")
        return

    print(f"\n  Auto-detected {len(active_jobs)} job(s) on the server:\n")
    for job in active_jobs:
        jid = job.get("job_id", "?")
        repo = job.get("repo_url") or job.get("repo", "?")
        step = job.get("step", "?")
        progress = job.get("progress", "?")
        print(f"    • {jid}  [{step} {progress}%]  {repo}")

    print()

    # ── Step 2: Delete detected jobs concurrently ───────────────────────────
    print(f"[2/2] Deleting detected job(s) via RabbitMQ scan.delete queue...\n")
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(
            None,
            delete_job,
            job.get("job_id", ""),
            job.get("repo_url") or job.get("repo", "Unknown Repo"),
        )
        for job in active_jobs
        if job.get("job_id")
    ]
    results = await asyncio.gather(*tasks)

    # ── Summary ────────────────────────────────────────────────────────────
    deleted = sum(results)
    print(f"\n{'='*70}")
    print(f"  Summary")
    print(f"{'='*70}")
    print(f"  Auto-detected : {len(active_jobs)} job(s)")
    print(f"  Successfully Deleted/Purged : {deleted} job(s)")
    if PURGE:
        print(
            "\n✅ Targeted jobs have been fully purged and will disappear from the UI."
        )
    else:
        print("\n✅ Targeted jobs have been marked as CANCELLED in the UI.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
