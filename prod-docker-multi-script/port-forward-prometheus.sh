#!/usr/bin/env bash
# ==============================================================================
# port-forward-prometheus.sh – View Prometheus in your local browser
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

source "${LIB_DIR}/globals.sh"

echo -e "\n${CYAN}${BOLD}=== Connecting to Prometheus on PrimaryHub ===${NC}"

ensure_node_ssm_access "$PRIMARYHUB_HOST"
ensure_cluster_ssh_tunnel "primaryhub" "$PRIMARYHUB_HOST"

echo -e "\n${GREEN}${BOLD}✔ Connected to PrimaryHub!${NC}"
echo -e "Starting port-forward on ${BOLD}http://localhost:9090${NC}..."
echo -e "\n👉 ${BLUE}${BOLD}Open this URL in your web browser:${NC} ${YELLOW}${BOLD}http://localhost:9090${NC}"
echo -e "Press Ctrl+C anytime to stop.\n"

kubectl --kubeconfig "$KUBECONFIG" --context kind-primaryhub -n opensandbox-system port-forward svc/prometheus-service 9090:9090
