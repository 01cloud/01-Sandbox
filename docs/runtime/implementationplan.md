# Implementation Plan: Modular Language-Specific Scanner Images & Dynamic Routing

This document outlines the technical design, base image selection, architecture, step-by-step implementation plan, and **complete code changes** to replace the monolithic **10 GB `code-interpreter` image** with lightweight, language-specific scanner images dynamically assigned during repository scanning.

---

## 1. Executive Summary & Architectural Insights

### A. Dynamic Language-Specific Image Assignment
Currently, every sandbox pod pulls a monolithic 10 GB container image (`code-interpreter`) that pre-packages 4 Java JDKs, 5 Python versions, 3 Node versions, 3 Go versions, Ruby, Maven, Rust, C/C++, and 10+ scanner binaries.

**Proposed Architecture**:
Instead of a single 10 GB mono-image, we decompose `code-interpreter` into modular, domain-specific micro-images:
- `01sandbox-scanner-python`: Python 3.12 + `semgrep`, `bandit`, `pylint` (~250 MB)
- `01sandbox-scanner-go`: Go 1.24 + `gosec`, `staticcheck`, `golangci-lint` (~180 MB)
- `01sandbox-scanner-java`: OpenJDK 21 + `pmd`, Maven (~350 MB)
- `01sandbox-scanner-node`: Node.js 22 + `eslint`, TypeScript (~200 MB)
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

## 2. Code Changes & File Implementation Specifications

### A. Modular Dockerfiles (`code-interpreter/dockerfiles/`)

#### 1. `code-interpreter/dockerfiles/Dockerfile.python`
```dockerfile
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-pip python3-venv \
    libseccomp2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python static analysis tools
RUN pip3 install --break-system-packages --no-cache-dir \
    semgrep \
    bandit \
    yamllint \
    pylint

# Setup workspace & copy scanner orchestrator
RUN mkdir -p /opt/opensandbox/src /workspace /reports
COPY src/ /opt/opensandbox/src/
COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh
RUN chmod +x /opt/opensandbox/code-interpreter.sh

WORKDIR /workspace
ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]
```

---

#### 2. `code-interpreter/dockerfiles/Dockerfile.go`
```dockerfile
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-pip golang-go \
    && rm -rf /var/lib/apt/lists/*

# Install Go security tools
RUN GOSEC_VERSION="2.19.0" && \
    curl -sfL "https://raw.githubusercontent.com/securego/gosec/master/install.sh" | sh -s -- -b /usr/local/bin v${GOSEC_VERSION}

RUN curl -sSfL https://raw.githubusercontent.com/golangci/golangci-lint/master/install.sh | sh -s -- -b /usr/local/bin v1.57.2

RUN mkdir -p /opt/opensandbox/src /workspace /reports
COPY src/ /opt/opensandbox/src/
COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh
RUN chmod +x /opt/opensandbox/code-interpreter.sh

WORKDIR /workspace
ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]
```

---

#### 3. `code-interpreter/dockerfiles/Dockerfile.java`
```dockerfile
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-pip openjdk-21-jdk maven unzip \
    && rm -rf /var/lib/apt/lists/*

# Install PMD static analyzer
RUN set -eux; \
    PMD_VERSION="7.3.0"; \
    curl -fsSL "https://github.com/pmd/pmd/releases/download/pmd_releases%2F${PMD_VERSION}/pmd-dist-${PMD_VERSION}-bin.zip" -o /tmp/pmd.zip \
    && unzip -q /tmp/pmd.zip -d /opt \
    && ln -s /opt/pmd-bin-${PMD_VERSION}/bin/pmd /usr/local/bin/pmd \
    && rm /tmp/pmd.zip

RUN mkdir -p /opt/opensandbox/src /workspace /reports
COPY src/ /opt/opensandbox/src/
COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh
RUN chmod +x /opt/opensandbox/code-interpreter.sh

WORKDIR /workspace
ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]
```

---

#### 4. `code-interpreter/dockerfiles/Dockerfile.node`
```dockerfile
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-pip nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g eslint @typescript-eslint/parser @typescript-eslint/eslint-plugin typescript

RUN mkdir -p /opt/opensandbox/src /workspace /reports
COPY src/ /opt/opensandbox/src/
COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh
RUN chmod +x /opt/opensandbox/code-interpreter.sh

WORKDIR /workspace
ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]
```

---

#### 5. `code-interpreter/dockerfiles/Dockerfile.k8s`
```dockerfile
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-pip shellcheck \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages --no-cache-dir yamllint

# Install trivy, gitleaks, kube-linter, kubeconform, kube-score
RUN set -eux; \
    curl -fsSL https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/gitleaks_8.18.4_linux_x64.tar.gz | tar -xz -C /usr/local/bin gitleaks; \
    curl -fsSL https://github.com/aquasecurity/trivy/releases/download/v0.69.3/trivy_0.69.3_Linux-64bit.tar.gz | tar -xz -C /usr/local/bin trivy; \
    curl -fsSL https://github.com/stackrox/kube-linter/releases/download/v0.8.3/kube-linter-linux.tar.gz | tar -xz -C /usr/local/bin kube-linter; \
    curl -fsSL https://github.com/yannh/kubeconform/releases/download/v0.7.0/kubeconform-linux-amd64.tar.gz | tar -xz -C /usr/local/bin kubeconform; \
    curl -fsSL https://github.com/zegl/kube-score/releases/download/v1.20.0/kube-score_1.20.0_linux_amd64.tar.gz | tar -xz -C /usr/local/bin kube-score; \
    chmod 755 /usr/local/bin/*

RUN mkdir -p /opt/opensandbox/src /workspace /reports
COPY src/ /opt/opensandbox/src/
COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh
RUN chmod +x /opt/opensandbox/code-interpreter.sh

WORKDIR /workspace
ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]
```

---

### B. `apiServer/fastapi/config.py` Code Changes

Add language scanner image resolution mapping in `apiServer/fastapi/config.py`:

```python
# --- Language Scanner Image Registry ---
SCANNER_IMAGES = {
    "python": os.getenv(
        "SCANNER_IMAGE_PYTHON", "199012118961/01sandbox-scanner-python:dev"
    ),
    "go": os.getenv(
        "SCANNER_IMAGE_GO", "199012118961/01sandbox-scanner-go:dev"
    ),
    "java": os.getenv(
        "SCANNER_IMAGE_JAVA", "199012118961/01sandbox-scanner-java:dev"
    ),
    "javascript": os.getenv(
        "SCANNER_IMAGE_NODE", "199012118961/01sandbox-scanner-node:dev"
    ),
    "typescript": os.getenv(
        "SCANNER_IMAGE_NODE", "199012118961/01sandbox-scanner-node:dev"
    ),
    "yaml": os.getenv(
        "SCANNER_IMAGE_K8S", "199012118961/01sandbox-scanner-k8s:dev"
    ),
    "kubernetes yaml": os.getenv(
        "SCANNER_IMAGE_K8S", "199012118961/01sandbox-scanner-k8s:dev"
    ),
    "shell": os.getenv(
        "SCANNER_IMAGE_K8S", "199012118961/01sandbox-scanner-k8s:dev"
    ),
    "default": os.getenv(
        "SCANNER_IMAGE_DEFAULT", "199012118961/01sandbox-codeinterpreter:dev"
    ),
}
```

---

### C. `apiServer/fastapi/scan_repository/file_scanner.py` Code Changes

Modify `_submit_scan_job` in `apiServer/fastapi/scan_repository/file_scanner.py` to dynamically attach target language image:

```python
# File: apiServer/fastapi/scan_repository/file_scanner.py

from config import SCANNER_IMAGES, opensandbox_base_url, opensandbox_headers, opensandbox_route_prefix

async def _submit_scan_job(
    files_dict: dict[str, str],
    tools: Optional[list[str]] = None,
    parent_job_id: Optional[str] = None,
    runtime: Optional[str] = None,
    language: Optional[str] = None,  # Added target language parameter
) -> dict:
    ...
    # Resolve dynamic scanner image based on target language
    target_lang = (language or "").strip().lower()
    selected_image = SCANNER_IMAGES.get(target_lang, SCANNER_IMAGES["default"])

    payload: dict = {
        "files": files_dict,
        "timeout": 900,
        "metadata": {
            "job_id": child_job_id,
            "image": selected_image,  # Pass target image in metadata
        },
    }
    if runtime:
        payload["metadata"]["runtime"] = runtime
    ...
```

---

### D. `opensandbox-server` Backend Dynamic Image Override Code Changes

Modify `create_workload` in `opensandbox-server/docker-build/src/services/k8s/batchsandbox_provider.py`:

```python
# File: opensandbox-server/docker-build/src/services/k8s/batchsandbox_provider.py

# Check if a custom sandbox image is specified in request extensions/metadata
if extensions and "image" in extensions:
    custom_image = extensions["image"]
    logger.info("[DYNAMIC IMAGE] Overriding container image to %s", custom_image)
    pod_spec["containers"][0]["image"] = custom_image
elif extensions and "sandboxImage" in extensions:
    custom_image = extensions["sandboxImage"]
    logger.info("[DYNAMIC IMAGE] Overriding container image to %s", custom_image)
    pod_spec["containers"][0]["image"] = custom_image
```

---

### E. Necessity and Functions of Startup Scripts in the Dockerfile

Every language scanner Dockerfile requires **`code-interpreter.sh`** and **`code-interpreter-env.sh`**. Below is their exact technical necessity and function:

#### 1. `code-interpreter.sh` (Container `ENTRYPOINT` & Startup Orchestrator)
- **Why Required**: Configured as `ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]`. Without this script, the container pod will not execute volume path normalization, tool health checks, or automated security scans upon boot.
- **Key Functions**:
  1. **Volume Path Normalization for Kata-FC**: Symlinks custom `$SCAN_DIR` to `/workspace` and `$SCAN_REPORT` to `/reports`. In Kata Containers / Firecracker MicroVMs, `subPath` volume mounts often fail or cause lock issues; symlinking raw root PVC mounts ensures 100% Kata compatibility.
  2. **`clone3` Syscall Workaround**: Checks `EXECD_CLONE3_COMPAT` and re-executes container startup under `/usr/local/bin/clone3-workaround` on host kernels/runtimes that do not support the `clone3` syscall.
  3. **Tool Startup Health Check**: Verifies that all 9+ security tools (`semgrep`, `bandit`, `gitleaks`, `trivy`, `shellcheck`, etc.) exist on `$PATH` before scanning.
  4. **Automated Scanning Trigger**: Invokes `run_security_scans()`, executing `python3 /opt/opensandbox/src/scanner_orchestrator.py` against `/workspace` files and logging output to `/reports/process.log`.

#### 2. `code-interpreter-env.sh` (Environment & Language Version Switcher)
- **Why Required**: Provides a unified interface (`source code-interpreter-env.sh <language> <version>`) for switching `$PATH`, `$JAVA_HOME`, and `$GOROOT` environment variables across toolchains.
- **Key Functions**:
  1. **Build-Time Tooling Setup**: Used during `docker build` to source the target Python/Node/Go/Java version environment before installing packages via `pip`, `npm`, or `go install`.
  2. **Runtime Version Switching**: Allows `code-interpreter.sh` or scan tasks to dynamically switch language versions (e.g. Python 3.12 vs 3.10) on the fly inside the container.
  3. **Sub-Shell Environment Persistence**: Appends updated `PATH` exports to `/root/.bashrc` and `$EXECD_ENVS` so child process trees (like `scanner_orchestrator.py`) automatically inherit the correct binary paths.

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
