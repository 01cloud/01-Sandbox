# Implementation Plan: Modular Language-Specific Scanner Images & Dynamic Routing

This document outlines the technical design, base image selection, architecture, and step-by-step implementation plan to replace the monolithic **10 GB `code-interpreter` image** with lightweight, language-specific scanner images dynamically assigned during repository scanning.

---

## 1. Executive Summary & Architectural Insights

### A. Dynamic Language-Specific Image Assignment
Currently, every sandbox pod pulls a monolithic 10 GB container image (`code-interpreter`) that pre-packages 4 Java JDKs, 5 Python versions, 3 Node versions, 3 Go versions, Ruby, Maven, Rust, C/C++, and 10+ scanner binaries.

**Proposed Architecture**:
Instead of a single 10 GB mono-image, we decompose `code-interpreter` into modular, domain-specific micro-images:
- `01sandbox-scanner-python`: Python runtime + `semgrep`, `bandit`, `pylint` (~250 MB)
- `01sandbox-scanner-go`: Go runtime + `gosec`, `staticcheck`, `golangci-lint` (~180 MB)
- `01sandbox-scanner-java`: OpenJDK runtime + `pmd`, Maven (~350 MB)
- `01sandbox-scanner-node`: Node.js runtime + `eslint`, TypeScript (~200 MB)
- `01sandbox-scanner-k8s`: Minimal runtime + `yamllint`, `kube-linter`, `kubeconform`, `kube-score`, `shellcheck`, `gitleaks`, `trivy` (~150 MB)
- `01sandbox-codeinterpreter-base`: Fallback multi-language image for arbitrary code execution.

During repository scanning, `apiServer` identifies the detected language for each scan task and dynamically passes the corresponding `image` URI to `opensandbox-server` when creating the `BatchSandbox` pod.

---

### B. Base Image Evaluation & Selection Matrix

| Base Image | Size | libc Implementation | Binary / C-Extension Compatibility | Security & Maintenance | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Wolfi (Chainguard)** | **~30 MB** | `glibc` | **High**: Excellent for Python C-extensions & Go static tools | Container-native, zero-CVE focus | **Recommended (Best Security & Compact Size)** |
| **Debian Slim (`debian:bookworm-slim`)** | **~74 MB** | `glibc` | **100%**: Native compatibility with standard Linux tooling | Standard Debian security updates | **Recommended (Safest Direct Migration)** |
| **Alpine Linux** | **~5 MB** | `musl` | **Poor**: `musl` breaks Python C-extensions (`semgrep`, `github-linguist`) | Lightweight | **Not Recommended** (musl compatibility issues) |
| **Google Distroless** | **~20 MB** | `glibc` | **Low**: No shell (`/bin/sh`), breaks orchestrator scripts | Highly minimal | **Not Suitable** (Needs bash/sh orchestrator) |
| **Void Linux** | **~40 MB** | `glibc/musl` | **Medium**: Niche package ecosystem | Community-driven | **Not Recommended** (Non-standard ecosystem) |

**Recommendation**: Use **Wolfi** (or **Debian Slim** for 100% legacy script drop-in compatibility).

---

### C. Impact on `kata-fc` CPU, RAM, & Disk Load

1. **Storage / DevMapper Volume Mount Speed**:
   - In `kata-fc`, creating container rootfs snapshot block devices from an LVM thin pool for a **10 GB image** creates massive disk I/O and `devmapper` lock contention.
   - Reduction from 10 GB to **~150 MB - 300 MB per image** reduces rootfs snapshot size by **~95%**, drastically cutting container provisioning time and host storage I/O wait.
2. **Container RAM & Process Overhead**:
   - The monolithic image currently runs multi-version setup loops (Python 3.10–3.14, Node 18–22, Go 1.23–1.25, Java 8–21 registration in `code-interpreter.sh`).
   - Language-specific micro-images eliminate multi-version initialization routines, saving CPU cycles on pod startup and reducing active process RSS memory.
3. **Kata MicroVM Memory Footprint Note**:
   - While lightweight base images shrink container image layer RAM and startup CPU load, `kata-fc` MicroVMs still require a base guest Linux kernel memory floor (~128 MB RAM).
   - Lightweight images allow safely setting `sandboxMemory: "256Mi"` without risking OOM crashes caused by heavy mono-image background scripts.

---

## 2. Component Design & Proposed Changes

### A. `code-interpreter` (Modular Dockerfiles & Base Images)

#### 1. `Dockerfile.python`
Create lightweight Python scanner image containing Python 3.12, `semgrep`, `bandit`, `pylint`, and `scanner_orchestrator.py`.

#### 2. `Dockerfile.go`
Create lightweight Go scanner image containing Go 1.24, `gosec`, `staticcheck`, `golangci-lint`, and `scanner_orchestrator.py`.

#### 3. `Dockerfile.java`
Create lightweight Java scanner image containing OpenJDK 21, Maven, `pmd`, and `scanner_orchestrator.py`.

#### 4. `Dockerfile.node`
Create lightweight Node/TS scanner image containing Node 22, `eslint`, `@typescript-eslint`, and `scanner_orchestrator.py`.

#### 5. `Dockerfile.k8s`
Create lightweight K8s/IaC/Shell scanner image containing `yamllint`, `kube-linter`, `kubeconform`, `kube-score`, `shellcheck`, `gitleaks`, `trivy`.

---

### B. `apiServer/fastapi` (Dynamic Image Assignment Router)

#### 1. `config.py`
Add configurable mapping of language scanner images:
```python
SCANNER_IMAGES = {
    "python": os.getenv("SCANNER_IMAGE_PYTHON", "199012118961/01sandbox-scanner-python:dev"),
    "go": os.getenv("SCANNER_IMAGE_GO", "199012118961/01sandbox-scanner-go:dev"),
    "java": os.getenv("SCANNER_IMAGE_JAVA", "199012118961/01sandbox-scanner-java:dev"),
    "javascript": os.getenv("SCANNER_IMAGE_NODE", "199012118961/01sandbox-scanner-node:dev"),
    "yaml": os.getenv("SCANNER_IMAGE_K8S", "199012118961/01sandbox-scanner-k8s:dev"),
    "default": os.getenv("SCANNER_IMAGE_DEFAULT", "199012118961/01sandbox-codeinterpreter:dev"),
}
```

#### 2. `file_scanner.py`
Pass the specific language scanner image in `image` / `metadata.image` payload when requesting sandbox creation from `opensandbox-server`.

---

### C. `opensandbox-server` (Backend Sandbox Provisioner)

#### 1. `batchsandbox_provider.py`
Ensure dynamic `image` passed in request override payload takes precedence over default ConfigMap sandbox image when constructing the `BatchSandbox` CRD pod spec.

---

## 3. Verification & Validation Plan

### Automated Build Verification
1. Build individual language scanner Dockerfiles and verify image sizes:
   ```bash
   docker build -t 01sandbox-scanner-python:dev -f code-interpreter/dockerfiles/Dockerfile.python code-interpreter/
   docker build -t 01sandbox-scanner-go:dev -f code-interpreter/dockerfiles/Dockerfile.go code-interpreter/
   docker images | grep 01sandbox-scanner
   ```
2. Verify image sizes are under ~300 MB each (vs ~10 GB mono-image).

### Manual Verification
1. Submit a multi-language GitHub repository scan via `POST /api/v1/01sbx/scan-jobs`.
2. Inspect Kubernetes pods with `kubectl get pods -n opensandbox-system -o wide` to verify that Python scan tasks pull `01sandbox-scanner-python:dev`, Go scan tasks pull `01sandbox-scanner-go:dev`, etc.
3. Monitor `k9s` host CPU/memory consumption under `kata-fc` runtime to confirm reduced RAM and disk I/O load.
