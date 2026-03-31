ARG KIND_VERSION=v1.32.0
FROM kindest/node:${KIND_VERSION}

# Detect arch and install gVisor binaries
RUN set -e; \
    ARCH=$(uname -m); \
    case "$ARCH" in \
      x86_64)  GVISOR_ARCH="x86_64" ;; \
      aarch64) GVISOR_ARCH="aarch64" ;; \
      *) echo "Unsupported arch: $ARCH" && exit 1 ;; \
    esac; \
    BASE_URL="https://storage.googleapis.com/gvisor/releases/release/latest/${GVISOR_ARCH}"; \
    curl -fsSL --retry 5 -o /usr/local/bin/runsc "${BASE_URL}/runsc"; \
    curl -fsSL --retry 5 -o /usr/local/bin/containerd-shim-runsc-v1 "${BASE_URL}/containerd-shim-runsc-v1"; \
    chmod 0755 /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1; \
    /usr/local/bin/runsc --version

