#!/bin/bash
# Disable Semgrep phone home telemetry and version check to prevent hanging in network-restricted sandboxes
export SEMGREP_SEND_TELEMETRY=off
export SEMGREP_DISABLE_VERSION_CHECK=true
export SEMGREP_SKIP_VERSION_CHECK=true
export SEMGREP_ENABLE_VERSION_CHECK=0

# Ensure global binary paths are at the front of the PATH
export PATH="/usr/local/bin:/usr/bin:/bin:/root/.local/bin:$PATH"

# Set up symlinks for SCAN_DIR and SCAN_REPORT if they are custom and not at standard /workspace and /reports paths.
# This prevents Kata Containers/Firecracker subPath volume mounting issues by mounting the PVC root instead of subPath.
if [ -n "$SCAN_DIR" ] && [ "$SCAN_DIR" != "/workspace" ]; then
    rm -rf /workspace
    mkdir -p "$(dirname "$SCAN_DIR")"
    ln -sf "$SCAN_DIR" /workspace
fi

if [ -n "$SCAN_REPORT" ] && [ "$(dirname "$SCAN_REPORT")" != "/reports" ]; then
    rm -rf /reports
    mkdir -p "$(dirname "$SCAN_REPORT")"
    ln -sf "$(dirname "$SCAN_REPORT")" /reports
fi



# ── Automated Security Scanning ───────────────────────────────────────────────
run_security_scans() {
    echo "============================================="
    echo "  Automated Security Scanning — /workspace"
    echo "---------------------------------------------"

    # Conditional trigger: Only scan if there are files in /workspace
    if [ ! "$(ls -A /workspace 2>/dev/null)" ]; then
        echo " [SKIP]    No files found in /workspace. Skipping automated scans."
        echo "---------------------------------------------"
        echo "============================================="
        return 0
    fi

    if [ -f /opt/opensandbox/src/scanner_orchestrator.py ]; then
        python3 /opt/opensandbox/src/scanner_orchestrator.py
    else
        echo " [ERROR]   Scanner orchestrator not found at /opt/opensandbox/src/scanner_orchestrator.py"
    fi

    echo "---------------------------------------------"
    echo "  Security scans complete."
    echo "============================================="
}

# Execute security scans automatically on startup
echo "[SANDBOX] Initializing scan environment..." >> /reports/process.log
run_security_scans 2>&1 | tee -a /reports/process.log
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

# jupyter notebook --ip=127.0.0.1 --port="${JUPYTER_PORT:-44771}" --allow-root --no-browser --NotebookApp.token="${JUPYTER_TOKEN:-opensandboxcodeinterpreterjupyter}" >/opt/opensandbox/jupyter.log
