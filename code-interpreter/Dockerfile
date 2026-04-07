# syntax=docker/dockerfile:1
# Copyright 2025 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


FROM opensandbox/code-interpreter-base:latest

ARG TARGETARCH
# Prebuilt binary from https://github.com/AkihiroSuda/clone3-workaround/releases/tag/v1.0.0 (amd64 only).
# The binary is linked against libseccomp (runtime package libseccomp2 on Ubuntu).
RUN set -eux; \
    if [ "${TARGETARCH}" = "amd64" ]; then \
        apt-get update && apt-get install -y --no-install-recommends libseccomp2 && rm -rf /var/lib/apt/lists/*; \
        curl -fsSL -o /usr/local/bin/clone3-workaround \
            https://github.com/AkihiroSuda/clone3-workaround/releases/download/v1.0.0/clone3-workaround.x86_64; \
        chmod 755 /usr/local/bin/clone3-workaround; \
    else \
        echo "Skipping clone3-workaround: upstream provides amd64/x86_64 binary only (TARGETARCH=${TARGETARCH})"; \
    fi

# ── Security Scanning Tools ───────────────────────────────────────────────────
# Install system-level dependencies needed by scanning tools
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        unzip \
        ca-certificates \
        libseccomp2 \
    && rm -rf /var/lib/apt/lists/*

# semgrep — static analysis / SAST (supports Python, JS, Go, Java, etc.)
# yamllint — YAML syntax and style linting
# bandit  — Python-specific security linter
# Python is uv-managed; source env script to activate it, then pass
# --break-system-packages (same pattern the kernel install loops use).
RUN set -eux; \
    . /opt/opensandbox/code-interpreter-env.sh python 3.12; \
    python3 -m pip install --no-cache-dir --break-system-packages \
        semgrep \
        yamllint \
        bandit

# gitleaks — secret / credential leak detection in source code
RUN set -eux; \
    GITLEAKS_VERSION="8.18.4"; \
    if [ "${TARGETARCH}" = "amd64" ]; then \
        GITLEAKS_ARCH="x64"; \
    elif [ "${TARGETARCH}" = "arm64" ]; then \
        GITLEAKS_ARCH="arm64"; \
    else \
        GITLEAKS_ARCH="x64"; \
    fi; \
    curl -fsSL \
        "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${GITLEAKS_ARCH}.tar.gz" \
        | tar -xz -C /usr/local/bin gitleaks; \
    chmod 755 /usr/local/bin/gitleaks

# trivy — vulnerability scanner for OS packages and language dependencies
# Pinned to v0.69.3 (confirmed safe immutable release post supply-chain incident)
RUN set -eux; \
    TRIVY_VERSION="0.69.3"; \
    if [ "${TARGETARCH}" = "amd64" ]; then \
        TRIVY_ARCH="64bit"; \
    elif [ "${TARGETARCH}" = "arm64" ]; then \
        TRIVY_ARCH="ARM64"; \
    else \
        TRIVY_ARCH="64bit"; \
    fi; \
    curl -fsSL \
        "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-${TRIVY_ARCH}.tar.gz" \
        | tar -xz -C /usr/local/bin trivy; \
    chmod 755 /usr/local/bin/trivy

# Verify all scanning tools are accessible
RUN set -eux; \
    . /opt/opensandbox/code-interpreter-env.sh python 3.12; \
    semgrep --version; \
    yamllint --version; \
    bandit --version; \
    gitleaks version; \
    trivy --version
# ─────────────────────────────────────────────────────────────────────────────

# Install Python kernels
RUN set -euo pipefail \
    && echo "Setting up ipykernel for Python 3.10, 3.11, 3.12, 3.13, 3.14" \
    && versions=("3.10" "3.11" "3.12" "3.13" "3.14") \
    && for version in "${versions[@]}"; do \
        echo "Setting up ipykernel for Python $version" \
        && . /opt/opensandbox/code-interpreter-env.sh python $version \
        && python3 --version \
        && python3 -m pip install ipykernel jupyter bash_kernel --break-system-packages; \
    done \
    && echo "Setting up ipykernel complete"

# Install Java kernel
RUN set -euo pipefail \
    && echo "Setting up IJava kernel" \
    && curl -L https://github.com/SpencerPark/IJava/releases/download/v1.3.0/ijava-1.3.0.zip -o /tmp/ijava.zip \
    && mkdir -p /opt/ijava \
    && unzip -o /tmp/ijava.zip -d /opt/ijava \
    && rm -rf /tmp/ijava.zip \
    && echo "Setting up IJava kernel done"

# Install tslab for Node.js versions
RUN set -euo pipefail \
    && echo "Setting up tslab kernel" \
    && versions=("18" "20" "22") \
    && for version in "${versions[@]}"; do \
        echo "Setting up tslab for Node $version" \
        && . /opt/opensandbox/code-interpreter-env.sh node $version \
        && node --version \
        && npm --version \
        && npm install -g tslab; \
    done \
    && echo "Setting up tslab kernel done"

# Install Go tooling for gonb
RUN set -euo pipefail \
    && echo "Setting up gonb" \
    && versions=("1.25" "1.24" "1.23") \
    && for version in "${versions[@]}"; do \
        echo "Setting up gonb for Go $version" \
        && . /opt/opensandbox/code-interpreter-env.sh go $version \
        && go version \
        && go install github.com/janpfeifer/gonb@latest \
        && go install golang.org/x/tools/cmd/goimports@latest \
        && go install golang.org/x/tools/gopls@latest; \
    done \
    && echo "Setting up gonb done"

##

ARG JUPYTER_TOKEN=opensandboxcodeinterpreterjupyter
ENV JUPYTER_HOST=http://127.0.0.1:44771 \
    JUPYTER_PORT=44771 \
    JUPYTER_TOKEN=${JUPYTER_TOKEN} \
    PYTHON_VERSION=3.14 \
    NODE_VERSION=22 \
    GO_VERSION=1.25 \
    JAVA_VERSION=21

COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh
COPY scripts/jupyter_notebook_config.py /root/.jupyter/
RUN chmod +x /opt/opensandbox/code-interpreter.sh

WORKDIR /workspace
ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]