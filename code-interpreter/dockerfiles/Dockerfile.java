FROM alpine:3.22

ENV LANG=C.UTF-8

RUN apk add --no-cache \
    ca-certificates \
    curl \
    git \
    python3 \
    py3-pip \
    openjdk17-jre-headless \
    unzip \
    bash

# Install PMD static analyzer
RUN set -eux; \
    PMD_VERSION="7.3.0"; \
    curl -fsSL "https://github.com/pmd/pmd/releases/download/pmd_releases%2F${PMD_VERSION}/pmd-dist-${PMD_VERSION}-bin.zip" -o /tmp/pmd.zip \
    && unzip -q /tmp/pmd.zip -d /opt \
    && ln -s /opt/pmd-bin-${PMD_VERSION}/bin/pmd /usr/local/bin/pmd \
    && rm -rf /tmp/* /var/tmp/*

# Install gitleaks and trivy
RUN set -eux; \
    curl -fsSL https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/gitleaks_8.18.4_linux_x64.tar.gz | tar -xz -C /usr/local/bin gitleaks; \
    curl -fsSL https://github.com/aquasecurity/trivy/releases/download/v0.69.3/trivy_0.69.3_Linux-64bit.tar.gz | tar -xz -C /usr/local/bin trivy; \
    chmod 755 /usr/local/bin/gitleaks /usr/local/bin/trivy

RUN mkdir -p /opt/opensandbox/src /opt/opensandbox/rules /workspace /reports
COPY src/ /opt/opensandbox/src/
COPY rules/ /opt/opensandbox/rules/
COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh
RUN chmod +x /opt/opensandbox/code-interpreter.sh

WORKDIR /workspace
ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]
