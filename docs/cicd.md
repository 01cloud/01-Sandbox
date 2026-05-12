# 🛡️ CI/CD Pipeline Architecture

This document describes the automated CI/CD pipeline for the 01-Sandbox project. The pipeline is designed to manage a mono-repo containing multiple services with independent build cycles and synchronized deployments.

## 🚀 Overview

The pipeline (defined in `.github/workflows/main.yml`) automates the lifecycle from code commit to production deployment on the RKE2 cluster. It uses **Semantic Versioning** and **Path-Based Filtering** to ensure efficiency and reliability.

---

## 🛠️ Pipeline Stages

### 1. Versioning Engine
- **Tool:** `paulhatch/semantic-version`
- **Logic:** Calculates the next sequential version (e.g., `v1.0.5`) based on existing Git tags.
- **Persistence:** Each successful deployment creates a new Git tag, providing a base for the next increment.

### 2. Intelligent Change Detection (Filter)
- **Tool:** `dorny/paths-filter`
- **Purpose:** Analyzes the commit to determine which services were modified.
- **Components Tracked:**
  - `apiServer/**` -> `api`
  - `code-interpreter/**` -> `scanner`
  - `opensandbox-server/**` -> `server`
  - `opensandbox-controller/**` -> `controller`
  - `codeInspector/**` -> `infra`

### 3. Parallel Service Builds
- **Execution:** Build jobs run only if their respective path filter is `true`.
- **Tagging:** Every image is pushed to Docker Hub with:
  - `latest`: For development tracking.
  - `${VERSION}`: Unique semantic version for production stability.

### 4. Atomic Cluster Deployment
- **Mechanism:** Helm upgrade with dynamic overrides.
- **Self-Healing:** 
  - Automatically clears stuck Helm locks (`pending-upgrade` secrets) before starting.
  - Uses `--rollback-on-failure` to revert to the last stable state if deployment fails.
- **Dynamic Injections:** Only overrides image tags for components that were actually rebuilt in the current run.

### 5. State Synchronization (Git-Back & Remote)
- **Repo Update:** Uses `sed` to patch `values.yaml` with the new version tags. Handles both quoted and unquoted YAML formats.
- **Commit Logic:** Automatically commits changes with `[skip ci]` to prevent build loops.
- **Tagging:** Pushes a new Git tag to mark the release.
- **Remote Sync:** SSHes into the production server (`148.113.4.247`) and runs `git pull` to keep the local workspace in sync.

---

## 🔑 Required Secrets

The following secrets must be configured in the GitHub Repository:

| Secret | Description |
|--------|-------------|
| `DOCKERHUB_USERNAME` | Registry username |
| `DOCKERHUB_TOKEN` | Registry access token |
| `KUBECONFIG` | RKE2 Cluster configuration (Auto-patched for public IP) |
| `REMOTE_USER` | SSH user for the production server |
| `REMOTE_PASSWORD` | SSH password/key for the production server |

---

## 🛡️ Best Practices
- **Atomic Releases:** Always use the unique semantic version for production rollouts.
- **Path Isolation:** Keep service-specific logic inside their respective folders to leverage the filtering engine.
- **Manual Overrides:** Use Helm `--set` flags in the pipeline instead of manually editing `values.yaml` image tags.
