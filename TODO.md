# 01-Sandbox — Improvement Backlog

> All items below are planned improvements identified after the RabbitMQ implementation.
> Items are grouped by category and ordered by priority within each group.

---

## 🔴 RabbitMQ Queue Enhancements

- [ ] **Dead Letter Queue (DLQ)** — Route failed/timed-out scan messages to a `scan.failed` queue instead of silently dropping them. Enables manual inspection, retry, and alerting on failures.
- [ ] **Job Retry with Backoff** — Automatically re-queue a failed scan up to 3 times with increasing delays (5s → 30s → 2min) before marking the job `ERROR`.
- [ ] **Job Cancellation** — Allow users to cancel a queued or in-flight scan via `DELETE /v1/jobs/{job_id}`, removing the message from the queue before a worker picks it up.
- [ ] **Job Priority Levels** — Tag submissions as `high` or `low` priority so paid/urgent scans jump ahead in the queue over free-tier jobs.
- [ ] **Scan TTL (Time-to-Live)** — Auto-expire queued jobs that haven't been picked up within X minutes (e.g. 30 min), marking them `EXPIRED` instead of hanging forever.
- [ ] **Per-User Queue Limit** — Reject publish if a specific user already has 5+ jobs in the queue, preventing any single user from monopolizing the queue.
- [ ] **RabbitMQ Metrics Endpoint** — Expose a `/v1/queue/stats` API endpoint returning live queue depths, consumer counts, and throughput — viewable from the dashboard.
- [ ] **Bulk Scan Type** — New `scan.bulk` queue for scanning all repos in a GitHub organization at once, submitted in batches.
- [ ] **Webhook Notifications** — When a scan reaches `DONE` or `ERROR`, trigger an HTTP callback to a user-configured URL (e.g. Slack, CI/CD pipeline).
- [ ] **Scheduled Scans** — Allow users to schedule a repo scan at a specific time (e.g. nightly), published to the queue via a cron job instead of an HTTP request.

---

## 🔴 Reliability & Stability

- [ ] **Persistent Job Storage (PostgreSQL)** — Store job history in PostgreSQL so scan records survive pod/Redis restarts. Currently jobs live only in memory + Redis.
- [ ] **Sandbox Timeout Enforcement** — Add a global scan timeout (e.g. 10 min) that auto-aborts hanging scans and marks them `ERROR` if the sandbox never becomes ready.
- [ ] **Health Checks per Dependency** — Extend the `/health` endpoint to report the status of each component individually: `postgres`, `redis`, `rabbitmq`, `opensandbox-server`.

---

## 🟡 Security

- [ ] **API Key Expiry Notifications** — Alert users (email or dashboard banner) before their API key expires so they are not suddenly locked out.
- [ ] **Audit Logging** — Log every authenticated action (who submitted what scan, when, from which IP) to PostgreSQL for compliance and abuse detection.
- [ ] **Per-User Queue Isolation** — Enforce per-user job limits at the queue intake level so one user cannot submit 20 jobs and starve all other users.

---

## 🟠 Performance

- [ ] **Scan Result Caching** — If the same GitHub repo is scanned twice within 24 hours, return the cached result from Redis instead of re-running the full pipeline.
- [ ] **Incremental / Delta Scanning** — Track the last scanned commit hash per repo. On re-scan, only process files changed since that commit — significant time savings for large repos.
- [ ] **Parallel Language Scanning** — Languages inside `file_scanner.py` are currently scanned sequentially. Run each language's scanner concurrently using `asyncio.gather()`.

---

## 🟢 Developer Experience & Observability

- [ ] **Structured JSON Logging** — Replace all `print()` statements across the codebase with structured JSON logs `{"level": "INFO", "component": "RabbitMQ", "job_id": "..."}` — makes logs searchable in Grafana Loki or similar tools.
- [ ] **OpenTelemetry Tracing** — Add distributed tracing to track the full lifecycle of a scan (HTTP submit → RabbitMQ publish → consumer pickup → sandbox → result) as a single trace in Jaeger or Tempo.
- [ ] **Admin Dashboard API** — Add operator-only endpoints: force-drain a queue, inspect raw job state, manually re-queue a failed job, view per-user submission stats.

---

## 🔵 New Features

- [ ] **GitHub Webhook Integration** — Auto-trigger a scan on every `push` event to a registered repo, without requiring a manual API call.
- [ ] **Scan Comparison / Diff** — Compare two scan results of the same repo across different commits and highlight which vulnerabilities appeared or were resolved.
- [ ] **SARIF Export** — Export scan results in the industry-standard SARIF format for upload to GitHub Security tab or import into SonarQube/Snyk.
