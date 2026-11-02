# Kubernetes-Native Automated Secret Rotation for Push-Based Multi-Cluster Prometheus

This document outlines the **100% Kubernetes-native, zero-downtime secret rotation architecture** for Push-Based Multi-Cluster Prometheus, replacing static passwords with an automated, scheduled rotation lifecycle without any cloud Lambda dependencies.

---

## 1. Architectural Principles

In a distributed multi-cluster observability architecture (PrimaryHub in Virginia, Spokes in Frankfurt, Mumbai, etc.), rotating credentials cannot be done instantaneously across distributed nodes.

### The Dual-Credential Overlap Pattern (Zero Downtime)
Prometheus `--web.config.file=/etc/prometheus-web/web.yml` natively supports multiple entries under `basic_auth_users`. By utilizing an overlapping grace period, Prometheus accepts **both the current and new credentials simultaneously**:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        PRIMARYHUB KUBERNETES CLUSTER                                   │
│                                                                                        │
│   ┌───────────────────────────┐      1. Generates New Pass + Bcrypt Hash               │
│   │   Secret Rotator CronJob  │ ───────────────────────────────────────┐               │
│   │ (Runs every 30d / on-call)│                                        │               │
│   └─────────────┬─────────────┘                                        ▼               │
│                 │ 2. Updates Dual-Auth                 ┌─────────────────────────────┐ │
│                 │    Secret [A + B]                    │ Secret:                     │ │
│                 ├────────────────────────────────────► │ prometheus-web-config       │ │
│                 │                                      │ (Contains: web.yml)         │ │
│                 │                                      └──────────────┬──────────────┘ │
│                 │ 3. Hot-Reload                                       │                │
│                 ▼    POST /-/reload                                   ▼                │
│   ┌───────────────────────────┐                        ┌─────────────────────────────┐ │
│   │    PrimaryHub Prometheus  │ ◄──────────────────────┤ /etc/prometheus-web/web.yml │ │
│   │    (Accepts both A & B)   │                        └─────────────────────────────┘ │
│   └───────────────────────────┘                                                        │
│                 │                                                                      │
│                 │ 4. Distribute Secret B to Edge Spokes via OCM ManifestWork           │
│                 ▼                                                                      │
└─────────────────┼──────────────────────────────────────────────────────────────────────┘
                  │
      Over WireGuard Mesh (10.99.0.0/24)
                  │
                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        EDGE SPOKE CLUSTERS (Spoke 1, Spoke 2, ...)                     │
│                                                                                        │
│   5. klusterlet-work-agent applies Secret: prometheus-remote-write-auth                │
│   6. Reloader triggers POST /-/reload on edge scraper pod (< 10ms)                     │
│   7. Edge scraper switches to Secret B (Zero 401 errors, zero sample loss)             │
│                                                                                        │
│   8. After Grace Period (600s): Rotator revokes Secret A on PrimaryHub                 │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Components Implemented

### 2.1 Multi-User Web Config (`secret-web-config.yaml`)
Path: [`codeInspector/charts/prometheus/templates/secret-web-config.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/secret-web-config.yaml)
* Renders multiple active users in `basic_auth_users` dictionary.
* Supports both manual `values.yaml` users and automated dynamic rotation.

### 2.2 Kubernetes-Native Rotator CronJob (`rotator-cronjob.yaml`)
Path: [`codeInspector/charts/prometheus/templates/rotator-cronjob.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/rotator-cronjob.yaml)
* **RBAC**: `ServiceAccount`, `ClusterRole`, and `ClusterRoleBinding` granting scoped access to Secrets, ConfigMaps, and OCM `manifestworks`.
* **Zero Dependency Generation**: Uses Python standard library `crypt.crypt(..., crypt.mksalt(crypt.METHOD_BLOWFISH))` to generate native Bcrypt `$2b$` hashes without requiring external pip packages.
* **InitContainer Pattern**: Copies `kubectl` from `bitnami/kubectl:latest` into a shared `emptyDir` volume, executed by a lightweight `python:3.11-alpine` container.

### 2.3 Values Configuration (`values.yaml`)
Path: [`codeInspector/charts/prometheus/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/values.yaml)
```yaml
rotator:
  enabled: true
  schedule: "0 0 1 * *" # Monthly rotation at midnight UTC
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  gracePeriodSeconds: 600 # 10-minute overlap window
  image:
    kubectl: "docker.io/bitnami/kubectl:latest"
    python: "docker.io/library/python:3.11-alpine"
```

---

## 3. Live EC2 Cluster Validation Results

The dual-authentication hot-reload was tested live on the production PrimaryHub EC2 instance (`i-034a312a42a2d2267` in `us-east-1`):

### Test 1: Hot-Reload Benchmark
```bash
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- --header="Authorization: Basic <AUTH_HEADER>" --post-data='' http://localhost:9090/-/reload
```
**Live Log Output:**
```text
ts=2026-11-02T10:04:30.256Z level=info msg="Loading configuration file" filename=/etc/prometheus/prometheus.yml
ts=2026-11-02T10:04:30.264Z level=info msg="Completed loading of configuration file" filename=/etc/prometheus/prometheus.yml totalDuration=7.802221ms
```
* **Reload Duration:** Exactly **7.8 milliseconds** with zero downtime and zero container restart.

### Test 2: Dual-Authentication Verification (Live Run)
```bash
# 1. Test Original Credential:
HTTP/1.1 200 OK (Accepted)

# 2. Test New Rotating Credential:
HTTP/1.1 200 OK (Accepted)

# 3. Test Invalid Credential:
HTTP/1.1 401 Unauthorized (Blocked at gate)
```

### Test 3: Remote Write (`/api/v1/write`) Authorization
```bash
# Valid rotating credential sent to /api/v1/write:
HTTP/1.1 415 Unsupported Media Type  (Passed auth gate; reached TSDB remote write handler)

# Invalid credential sent to /api/v1/write:
HTTP/1.1 401 Unauthorized            (Rejected before TSDB)
```

---

## 4. Operational Runbook

### How to Trigger an Immediate Manual Rotation
```bash
kubectl -n opensandbox-system create job \
  --from=cronjob/prometheus-secret-rotator \
  manual-rotation-$(date +%s)
```

### How to Check Rotation Job Logs
```bash
kubectl -n opensandbox-system logs -l component=secret-rotator --tail=100 -f
```

### How to Inspect Active Credentials in PrimaryHub
```bash
kubectl -n opensandbox-system get secret prometheus-web-config \
  -o jsonpath='{.data.web\.yml}' | base64 -d
```
