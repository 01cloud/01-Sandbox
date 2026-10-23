#!/usr/bin/env bash
# ==============================================================================
# port-forward-grafana.sh – View Grafana in your local browser
# ==============================================================================
# Opens Grafana (centralized, primaryhub) at http://localhost:3000
# Uses AWS SSM port-forwarding to reach the private subnet EC2 instance.
#
# Login: admin / sandbox-admin
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

source "${LIB_DIR}/globals.sh"

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  01-Sandbox Grafana – Centralized Multi-Cluster Dashboard ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Target:${NC}  primaryhub Grafana (namespace: monitoring)"
echo -e "  ${BOLD}Access:${NC}  SSM → EC2 → kind port-forward → localhost:3000"
echo ""

ensure_node_ssm_access "$PRIMARYHUB_HOST"
ensure_cluster_ssh_tunnel "primaryhub" "$PRIMARYHUB_HOST"

echo -e "${GREEN}${BOLD}✔ SSM tunnel to primaryhub active.${NC}"
echo ""
echo -e "👉 ${BOLD}Open in your browser:${NC} ${YELLOW}${BOLD}http://localhost:3000${NC}"
echo -e "   Login: ${BOLD}admin${NC} / ${BOLD}sandbox-admin${NC}"
echo ""
echo -e "   Pre-loaded dashboards:"
echo -e "     • ${CYAN}01-Sandbox Multi-Cluster Overview${NC} — all 5 clusters in one view"
echo ""
echo -e "Press ${BOLD}Ctrl+C${NC} to stop the tunnel.\n"

kubectl --kubeconfig "$KUBECONFIG" --context kind-primaryhub \
  -n monitoring port-forward svc/grafana 3000:3000
