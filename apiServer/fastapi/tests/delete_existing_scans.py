#!/usr/bin/env python3
"""
delete_existing_scans.py — Target and delete specific repo-scan jobs from the server.

Can delete by:
  1. Specific Job ID (UUID)
  2. Substring of the Repository URL (e.g., "fastapi" or "youtube-dl")
  3. Interactive selection (if run without target arguments, it lists all jobs and prompts you)
  4. All jobs (using the --all flag)

Usage:
    JWT_TOKEN=<token> python3 delete_existing_scans.py <job_id_or_repo_name>
    JWT_TOKEN=<token> python3 delete_existing_scans.py --all
    JWT_TOKEN=<token> python3 delete_existing_scans.py  # Interactive mode

Optional env vars:
    PURGE   — "true" (default) to permanently remove from Redis + UI list
              "false" to only soft-cancel (job stays in list with CANCELLED status)
    API_URL — API base URL (default: https://api-sandbox.01security.com)
"""

import asyncio
import json
import os
import re
import sys
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────

JWT_TOKEN = os.environ.get("JWT_TOKEN")
# Check if first argument is token (if not set in env) or if it's the target
target_arg = None
if len(sys.argv) > 1:
    arg1 = sys.argv[1]
    # Simple heuristic: if it looks like a JWT token (long, contains dots), it's the token
    if len(arg1) > 50 and "." in arg1:
        JWT_TOKEN = arg1
        if len(sys.argv) > 2:
            target_arg = sys.argv[2]
    else:
        target_arg = arg1

if not JWT_TOKEN:
    print("Error: Please set JWT_TOKEN environment variable.")
    print("Example: JWT_TOKEN=eyJ... python3 delete_existing_scans.py <target>")
    sys.exit(1)

API_BASE_URL = os.environ.get("API_URL", "http://localhost:8000")
PURGE = os.environ.get("PURGE", "true").lower() == "true"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {JWT_TOKEN}",
}

UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

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
        print(
            f"  🗑️  [{action}] Job {job_id} ({repo_url}) successfully queued for deletion."
        )
        return True
    print(f"  ❌ FAILED to delete job {job_id} ({repo_url}): {data}")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────


async def main():
    print(f"\n{'='*70}")
    print(f"  Target API : {API_BASE_URL}")
    print(f"  Purge mode : {PURGE}  (set PURGE=false to only soft-cancel)")
    print(f"{'='*70}\n")

    # Fetch existing jobs first
    print("Fetching existing repo-scan jobs from server...")
    jobs = list_jobs()

    if not jobs:
        print("No active or historical repo-scan jobs found on the server.")
        return

    # Process based on arguments or interactive mode
    target = target_arg
    to_delete = []

    if target == "--all":
        print(f"Target is '--all'. Preparing to delete all {len(jobs)} jobs.")
        to_delete = jobs
    elif target:
        # Check if it's a specific UUID
        if UUID_REGEX.match(target):
            # Find the job matching this ID
            matched = [j for j in jobs if j.get("job_id") == target]
            if matched:
                to_delete = matched
            else:
                # Add it anyway even if not listed in GET /jobs
                to_delete = [{"job_id": target, "repo_url": "Direct ID input"}]
            print(f"Targeting specific Job ID: {target}")
        else:
            # Substring match on repo URL or owner/repo
            print(f"Searching jobs matching name/substring: '{target}'...")
            for job in jobs:
                repo = str(job.get("repo_url") or job.get("repo") or "").lower()
                if target.lower() in repo:
                    to_delete.append(job)

            if not to_delete:
                print(f"❌ No jobs matched the substring '{target}'.")
                print("Available jobs:")
                for job in jobs:
                    print(
                        f"  - {job.get('job_id')} : {job.get('repo_url') or job.get('repo')}"
                    )
                return
    else:
        # Interactive Mode
        print(f"\nFound {len(jobs)} existing job(s) on the server:")
        for idx, job in enumerate(jobs):
            jid = job.get("job_id", "?")
            repo = job.get("repo_url") or job.get("repo", "?")
            step = job.get("step", "?")
            progress = job.get("progress", "?")
            print(f"  [{idx + 1}] {jid}  [{step} {progress}%]  {repo}")

        print("\nSelect jobs to delete:")
        print("  - Enter a number (e.g., '1' or '2')")
        print("  - Enter a repo substring (e.g., 'fastapi' or 'youtube-dl')")
        print("  - Enter a specific Job UUID")
        print("  - Enter 'all' to delete everything")
        print("  - Press Enter to cancel")

        try:
            choice = input("\nYour choice: ").strip()
        except KeyboardInterrupt:
            print("\nCancelled.")
            return

        if not choice:
            print("No selection made. Exiting.")
            return

        if choice.lower() == "all":
            to_delete = jobs
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(jobs):
                to_delete = [jobs[idx]]
            else:
                print("Invalid selection index.")
                return
        elif UUID_REGEX.match(choice):
            matched = [j for j in jobs if j.get("job_id") == choice]
            if matched:
                to_delete = matched
            else:
                to_delete = [{"job_id": choice, "repo_url": "Direct ID input"}]
        else:
            # Substring match
            for job in jobs:
                repo = str(job.get("repo_url") or job.get("repo") or "").lower()
                if choice.lower() in repo:
                    to_delete.append(job)
            if not to_delete:
                print(f"No jobs matched '{choice}'.")
                return

    # Perform the deletion
    if not to_delete:
        print("No jobs selected for deletion.")
        return

    print(f"\nProceeding to delete/purge {len(to_delete)} job(s) concurrently:\n")
    for job in to_delete:
        repo = job.get("repo_url") or job.get("repo") or "Unknown Repo"
        print(f"  • {job.get('job_id')} ({repo})")

    print()

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(
            None,
            delete_job,
            job.get("job_id", ""),
            job.get("repo_url") or job.get("repo", "Unknown Repo"),
        )
        for job in to_delete
    ]
    results = await asyncio.gather(*tasks)

    deleted_count = sum(1 for r in results if r)
    print(f"\n{'='*70}")
    print(f"  Summary:")
    print(
        f"  Successfully deleted/purged {deleted_count} of {len(to_delete)} targeted job(s)."
    )
    if PURGE:
        print("  Targeted jobs have been fully purged and will disappear from the UI.")
    else:
        print("  Targeted jobs have been soft-cancelled (marked CANCELLED).")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
