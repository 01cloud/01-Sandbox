FROM debian:bookworm-slim
# FROM alpine:3.22

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
