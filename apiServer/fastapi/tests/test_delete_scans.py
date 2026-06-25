#!/usr/bin/env python3
"""
test_delete_scans.py — Submit concurrent repo scans and then delete them mid-flight
to verify that:
  1. Deletion is queued via RabbitMQ (scan.delete queue)
  2. The sandbox pods for each language are terminated via the cleanup_child_jobs flow

Usage:
    JWT_TOKEN=<token> python3 test_delete_scans.py
    python3 test_delete_scans.py <JWT_TOKEN>
    JWT_TOKEN=<token> API_URL=https://api-sandbox.01security.com python3 test_delete_scans.py

Optional env vars:
    DELETE_DELAY_SECS  — seconds to wait after submitting before deleting (default: 5)
                         Set higher for slower repos, lower to delete before provisioning.
    PURGE              — if set to "true", permanently purges job records from Redis/PVC too
"""

import asyncio
import json
import os
import sys
import time
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────

JWT_TOKEN = os.environ.get("JWT_TOKEN")
if not JWT_TOKEN and len(sys.argv) > 1:
    JWT_TOKEN = sys.argv[1]

if not JWT_TOKEN:
    print(
        "Error: Please set JWT_TOKEN environment variable or pass it as the first argument."
    )
    sys.exit(1)

API_BASE_URL = os.environ.get("API_URL", "https://api-sandbox.01security.com")
DELETE_DELAY_SECS = int(os.environ.get("DELETE_DELAY_SECS", "5"))
PURGE = os.environ.get("PURGE", "false").lower() == "true"

URLS = [
    "https://github.com/tiangolo/fastapi",
    "https://github.com/firecracker-microvm/firecracker",
    "https://github.com/ytdl-org/youtube-dl",
    "https://github.com/tiangolo/fastapi",
]

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {JWT_TOKEN}",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _request(method: str, url: str, body: dict | None = None) -> dict | None:
    """Send a synchronous HTTP request and return the parsed JSON body."""
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
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


def submit_scan(repo_url: str) -> str | None:
    """POST /v1/repo-scan and return the job_id."""
    url = f"{API_BASE_URL.rstrip('/')}/v1/repo-scan"
    data = _request("POST", url, {"repo_url": repo_url})
    if data:
        job_id = data.get("job_id")
        print(f"  ✅ Submitted {repo_url} → job_id: {job_id}")
        return job_id
    print(f"  ❌ Failed to submit {repo_url}")
    return None


def delete_scan(job_id: str) -> bool:
    """DELETE /v1/jobs/{job_id} — published to RabbitMQ scan.delete queue."""
    purge_param = "true" if PURGE else "false"
    url = f"{API_BASE_URL.rstrip('/')}/v1/jobs/{job_id}?purge={purge_param}"
    data = _request("DELETE", url)
    if data and data.get("status") == "DELETE_QUEUED":
        print(f"  🗑️  Deleted job {job_id} → status: DELETE_QUEUED (via RabbitMQ)")
        return True
    print(f"  ❌ Failed to delete job {job_id}: {data}")
    return False


def get_job_status(job_id: str) -> str:
    """GET /v1/repo-scan/{job_id}/status snapshot (non-streaming)."""
    url = f"{API_BASE_URL.rstrip('/')}/v1/jobs"
    data = _request("GET", url)
    if data:
        for job in data:
            if job.get("job_id") == job_id:
                return job.get("step", "UNKNOWN")
    return "NOT_FOUND"


# ── Main ──────────────────────────────────────────────────────────────────────


async def main():
    print(f"\n{'='*60}")
    print(f"  Target API : {API_BASE_URL}")
    print(f"  Delete delay: {DELETE_DELAY_SECS}s after submission")
    print(f"  Purge mode  : {PURGE}")
    print(f"{'='*60}\n")

    # ── Step 1: Submit all scans concurrently ──────────────────────────────
    print(f"[1/3] Submitting {len(URLS)} concurrent repo scans...\n")
    loop = asyncio.get_event_loop()
    submit_tasks = [loop.run_in_executor(None, submit_scan, url) for url in URLS]
    job_ids = await asyncio.gather(*submit_tasks)

    valid_jobs = {jid: url for jid, url in zip(job_ids, URLS) if jid}
    if not valid_jobs:
        print("\n❌ No jobs were submitted successfully. Exiting.")
        sys.exit(1)

    print(f"\n  Submitted {len(valid_jobs)}/{len(URLS)} job(s) successfully.")

    # ── Step 2: Wait for sandbox pods to spin up ───────────────────────────
    print(
        f"\n[2/3] Waiting {DELETE_DELAY_SECS}s for language sandbox pods to provision..."
    )
    print("      (Increase DELETE_DELAY_SECS env var to wait longer before deleting)\n")
    for remaining in range(DELETE_DELAY_SECS, 0, -1):
        print(f"  Deleting in {remaining}s...", end="\r")
        time.sleep(1)
    print()

    # ── Step 3: Delete all submitted jobs ─────────────────────────────────
    print(
        f"\n[3/3] Deleting {len(valid_jobs)} job(s) via RabbitMQ scan.delete queue...\n"
    )
    delete_tasks = [loop.run_in_executor(None, delete_scan, jid) for jid in valid_jobs]
    results = await asyncio.gather(*delete_tasks)

    # ── Summary ────────────────────────────────────────────────────────────
    deleted = sum(results)
    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Submitted : {len(valid_jobs)} job(s)")
    print(f"  Deleted   : {deleted} job(s)")
    print(f"\n  Job IDs:")
    for jid, url in valid_jobs.items():
        status = (
            "🗑️  DELETE_QUEUED" if results[list(valid_jobs).index(jid)] else "❌ FAILED"
        )
        print(f"    [{status}] {jid}  ({url})")

    print(
        f"\n✅ Check RabbitMQ management UI → 'scan.delete' queue for queued deletions."
    )
    print(
        f"✅ Check 'kubectl get pods -n <namespace>' to confirm sandbox pod termination."
    )
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
