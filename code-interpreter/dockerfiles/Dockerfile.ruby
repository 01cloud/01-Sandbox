FROM alpine:3.22

ENV LANG=C.UTF-8

RUN apk add --no-cache \
    ca-certificates \
    curl \
    git \
    ruby \
    python3 \
    py3-pip \
    bash

# Install Ruby security tools (rubocop & brakeman SAST scanners) and cleanup build headers
RUN apk add --no-cache --virtual .build-deps build-base ruby-dev \
    && gem install rubocop rubocop-performance brakeman --no-document \
    && apk del .build-deps \
    && rm -rf /root/.gem /tmp/* /var/tmp/* /usr/lib/ruby/gems/*/cache

# Install semgrep for Ruby SAST scanning
RUN pip3 install --break-system-packages --no-cache-dir semgrep \
    && rm -rf /root/.cache/pip

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
