# CI/CD Pipeline Implementation Plan

This document outlines the implementation of an automated CI/CD pipeline for the `01-Sandbox` project. The pipeline handles building Docker images for specific components, automatic semantic versioning, and validated Helm deployments to an RKE2 cluster on a remote VM.

## User Review Required

> [!IMPORTANT]
> **Secrets Configuration**: You will need to configure the following secrets in your GitHub repository:
> - `DOCKERHUB_USERNAME`: Your Docker Hub username.
> - `DOCKERHUB_TOKEN`: A Docker Hub Personal Access Token.
> - `KUBECONFIG`: The contents of your RKE2 cluster's kubeconfig file (ensure the server URL uses the public IP).

> [!NOTE]
> **Versioning Strategy**: This plan uses "Conventional Commits" to automate semantic versioning. Commits starting with `feat:` will trigger a minor version bump, `fix:` a patch bump, and `feat!:` or `fix!:` (or breaking change footer) a major bump.

## Proposed Changes

### CI/CD Workflow Component

The core of the automation will be a GitHub Actions workflow that conditionally executes jobs based on modified paths.

#### [NEW] `.github/workflows/main.yml`
Create a unified workflow to handle all CI/CD logic.

- **Triggers**:
    - Push to `main` branch.
    - Path filters to ensure builds only trigger when relevant files change.
- **Job: Detect Changes**:
    - Uses `dorny/paths-filter` to identify which service (apiServer, code-interpreter, etc.) was modified.
- **Job: Build and Push**:
    - Authenticates with Docker Hub.
    - Builds and pushes images for the detected changed components.
    - Tags images with the Git SHA and `latest`.
- **Job: Release**:
    - Uses `google-github-actions/release-please-action` to automate versioning and CHANGELOG generation.
    - Creates a new GitHub Release when merged to `main`.
- **Job: Deploy**:
    - Triggers if `codeInspector/values.yaml` is updated or a new release is created.
    - **Validation**: Runs `helm lint` and `helm template` to verify the chart.
    - **Deployment**: Uses `helm upgrade --install` with the `--atomic` flag for automatic rollback on failure.
    - Connects to the RKE2 VM using the `KUBECONFIG` secret.

### Deployment Configuration

#### [MODIFY] `codeInspector/values.yaml`
Ensure the `values.yaml` is prepared for dynamic image tagging if necessary, though the pipeline will initially focus on deploying the configuration as defined.

## Verification Plan

### Automated Tests
1. **Linting**:
   - `helm lint ./codeInspector` will be run as part of the pipeline.
2. **Dry Run**:
   - `helm install --dry-run --debug ./codeInspector` to verify manifest generation.

### Manual Verification
1. **Trigger Build**: Push a change to `apiServer/` and verify only that image is built and pushed.
2. **Trigger Deployment**: Modify a value in `codeInspector/values.yaml` and verify the deployment starts on the RKE2 cluster.
3. **Rollback Test**: Introduce a breaking change in `values.yaml` (e.g., invalid image tag) and verify Helm automatically rolls back to the previous version.
4. **Versioning**: Verify a GitHub Release is created with a new semantic version (e.g., `v1.0.1`) after a `fix:` commit.

## Rollback Strategy
The pipeline uses Helm's native atomic deployment feature:
`helm upgrade --install --atomic --timeout 5m ...`
If the new pods fail to reach a "Ready" state within 5 minutes, Helm will automatically revert the cluster to its previous state.
