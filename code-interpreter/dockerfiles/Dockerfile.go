# Stage 1: Build tools using Go compiler
FROM alpine:3.22 AS builder

RUN apk add --no-cache ca-certificates curl git go bash

RUN go install github.com/securego/gosec/v2/cmd/gosec@v2.22.1 && \
    go install honnef.co/go/tools/cmd/staticcheck@v0.6.0 && \
    cp /root/go/bin/* /usr/local/bin/

RUN curl -sSfL https://raw.githubusercontent.com/golangci/golangci-lint/master/install.sh | sh -s -- -b /usr/local/bin v1.64.6

RUN set -eux; \
    curl -fsSL https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/gitleaks_8.18.4_linux_x64.tar.gz | tar -xz -C /usr/local/bin gitleaks; \
    curl -fsSL https://github.com/aquasecurity/trivy/releases/download/v0.69.3/trivy_0.69.3_Linux-64bit.tar.gz | tar -xz -C /usr/local/bin trivy; \
    chmod 755 /usr/local/bin/*

# Stage 2: Ultra-lightweight runtime image
FROM alpine:3.22

ENV LANG=C.UTF-8

RUN apk add --no-cache \
    ca-certificates \
    curl \
    git \
    python3 \
    py3-pip \
    go \
    bash

# Install semgrep SAST engine for Go security vulnerability scanning
RUN pip3 install --break-system-packages --no-cache-dir semgrep \
    && find /usr/lib/python* -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true \
    && find /usr/lib/python* -name '*.pyc' -delete \
    && rm -rf /root/.cache /var/cache/apk/*

COPY --from=builder /usr/local/bin/gosec /usr/local/bin/gosec
COPY --from=builder /usr/local/bin/golangci-lint /usr/local/bin/golangci-lint
COPY --from=builder /usr/local/bin/staticcheck /usr/local/bin/staticcheck
COPY --from=builder /usr/local/bin/gitleaks /usr/local/bin/gitleaks
COPY --from=builder /usr/local/bin/trivy /usr/local/bin/trivy

RUN mkdir -p /opt/opensandbox/src /opt/opensandbox/rules /workspace /reports
COPY src/ /opt/opensandbox/src/
COPY rules/ /opt/opensandbox/rules/
COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh
RUN chmod +x /opt/opensandbox/code-interpreter.sh

WORKDIR /workspace
ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]
