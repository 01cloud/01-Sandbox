#!/usr/bin/env bash
# ==============================================================================
# port-forward-prometheus.sh – View Prometheus in your local browser
# ==============================================================================
# Usage:
#   ./port-forward-prometheus.sh              # defaults to primaryhub
#   ./port-forward-prometheus.sh primaryhub
#   ./port-forward-prometheus.sh secondaryhub
#   ./port-forward-prometheus.sh spoke1
#   ./port-forward-prometheus.sh spoke2
#   ./port-forward-prometheus.sh spoke3
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

source "${LIB_DIR}/globals.sh"

TARGET="${1:-primaryhub}"

# ── Map cluster name to SSM target and host ────────────────────────────────────
case "$TARGET" in
  primaryhub)
    SSM_HOST="$PRIMARYHUB_HOST"
    ;;
  secondaryhub)
    SSM_HOST="$SECONDARYHUB_HOST"
    ;;
  spoke1)
    SSM_HOST="$SPOKE1_HOST"
    ;;
  spoke2)
    SSM_HOST="$SPOKE2_HOST"
    ;;
  spoke3)
    # spoke3 uses SPOKE3_HOST if defined, otherwise falls back gracefully
    SSM_HOST="${SPOKE3_HOST:-}"
    if [ -z "$SSM_HOST" ]; then
      echo -e "\033[0;31m[ERROR]\033[0m SPOKE3_HOST is not set in prod.env. Add it and re-run."
      exit 1
    fi
    ;;
  *)
    echo "Usage: $0 [primaryhub|secondaryhub|spoke1|spoke2|spoke3]"
    exit 1
    ;;
esac

echo -e "\n\033[0;36m\033[1m=== Connecting to Prometheus on ${TARGET} ===\033[0m"

ensure_node_ssm_access "$SSM_HOST"
ensure_cluster_ssh_tunnel "$TARGET" "$SSM_HOST"

echo -e "\n\033[0;32m\033[1m✔ Connected to ${TARGET}!\033[0m"
echo -e "Starting port-forward on \033[1mhttp://localhost:9090\033[0m..."
echo -e "\n👉 \033[0;34m\033[1mOpen this URL in your web browser:\033[0m \033[1;33mhttp://localhost:9090\033[0m"
echo -e "   Cluster: \033[1m${TARGET}\033[0m"
echo -e "Press Ctrl+C anytime to stop.\n"

kubectl --kubeconfig "$KUBECONFIG" --context "kind-${TARGET}" \
  -n monitoring port-forward svc/prometheus-scraper 9090:9090
