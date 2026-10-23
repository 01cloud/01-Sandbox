#!/usr/bin/env bash
# ==============================================================================
# apply-monitoring.sh — Deploy Prometheus (all clusters) + Grafana (primaryhub)
# ==============================================================================
# Usage:
#   ./manifests/monitoring/apply-monitoring.sh           # deploy all
#   ./manifests/monitoring/apply-monitoring.sh primaryhub
#   ./manifests/monitoring/apply-monitoring.sh spokes
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
log_info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Template substitution helper ───────────────────────────────────────────────
# render_and_apply <manifest_file> <cluster_name> <cluster_role> <kubectl_context>
render_and_apply() {
  local manifest="$1" cluster_name="$2" cluster_role="$3" ctx="$4"
  log_info "Applying ${manifest##*/} → context: ${ctx} (cluster=${cluster_name}, role=${cluster_role})"
  sed \
    -e "s/__CLUSTER_NAME__/${cluster_name}/g" \
    -e "s/__CLUSTER_ROLE__/${cluster_role}/g" \
    "${manifest}" | kubectl --context "${ctx}" apply -f -
}

# ── Hub clusters ───────────────────────────────────────────────────────────────
deploy_hubs() {
  local hubs=("primaryhub" "secondaryhub")
  local roles=("hub" "hub")

  for i in "${!hubs[@]}"; do
    local name="${hubs[$i]}" role="${roles[$i]}" ctx="kind-${hubs[$i]}"
    log_info "─── Deploying monitoring to ${name} ───────────────────────────────"

    if ! kubectl --context "${ctx}" cluster-info &>/dev/null; then
      log_warn "Context ${ctx} not reachable – skipping."
      continue
    fi

    render_and_apply "${SCRIPT_DIR}/03-prometheus-scraper.yaml" "${name}" "${role}" "${ctx}"

    # Apply CNPG + OCM metrics helper services on hubs
    kubectl --context "${ctx}" apply -f "${SCRIPT_DIR}/01-cnpg-metrics.yaml" || true
    kubectl --context "${ctx}" apply -f "${SCRIPT_DIR}/02-ocm-metrics.yaml" || true

    log_success "${name}: Prometheus scraper deployed."
  done
}

# ── Grafana (primaryhub only) ──────────────────────────────────────────────────
deploy_grafana() {
  local ctx="kind-primaryhub"
  log_info "─── Deploying centralized Grafana to primaryhub ──────────────────────"

  if ! kubectl --context "${ctx}" cluster-info &>/dev/null; then
    log_warn "primaryhub context not reachable – skipping Grafana."
    return
  fi

  kubectl --context "${ctx}" apply -f "${SCRIPT_DIR}/05-grafana.yaml"
  log_success "Grafana deployed on primaryhub (namespace: monitoring)."
  echo ""
  echo -e "  👉 Access: ${BOLD}kubectl --context kind-primaryhub port-forward svc/grafana 3000:3000 -n monitoring${NC}"
  echo -e "     Then open: ${YELLOW}http://localhost:3000${NC}  (admin / sandbox-admin)"
}

# ── Spoke clusters ─────────────────────────────────────────────────────────────
deploy_spokes() {
  # Dynamic discovery: all 'kind-spoke*' contexts in kubeconfig
  local spokes
  mapfile -t spokes < <(kubectl config get-contexts -o name 2>/dev/null | grep 'kind-spoke' | sort)

  if [ "${#spokes[@]}" -eq 0 ]; then
    log_warn "No kind-spoke* contexts found in kubeconfig. Skipping spoke deployment."
    return
  fi

  for ctx in "${spokes[@]}"; do
    local name="${ctx#kind-}"   # strip "kind-" prefix → spoke1, spoke2, …
    log_info "─── Deploying monitoring to ${name} ───────────────────────────────"

    if ! kubectl --context "${ctx}" cluster-info &>/dev/null; then
      log_warn "Context ${ctx} not reachable – skipping."
      continue
    fi

    render_and_apply "${SCRIPT_DIR}/04-prometheus-spoke.yaml" "${name}" "spoke" "${ctx}"

    # Apply OCM agent-side metrics services on spokes
    kubectl --context "${ctx}" apply -f "${SCRIPT_DIR}/02-ocm-metrics.yaml" 2>/dev/null | grep -v 'namespace.*not found' || true

    log_success "${name}: Prometheus scraper deployed."
  done
}

# ── Verify NodePort reachability (informational) ────────────────────────────────
check_nodeport_reachability() {
  log_info "─── Verifying NodePort 30900 reachability over WireGuard ───────────────"
  local wg_ips=("10.99.0.2" "10.99.0.3" "10.99.0.4" "10.99.0.5")
  local names=("secondaryhub" "spoke1" "spoke2" "spoke3")

  for i in "${!wg_ips[@]}"; do
    local ip="${wg_ips[$i]}" name="${names[$i]}"
    if curl -sf --max-time 3 "http://${ip}:30900/-/healthy" &>/dev/null; then
      log_success "${name} (${ip}:30900): reachable ✔"
    else
      log_warn "${name} (${ip}:30900): not reachable yet (Prometheus may still be starting, or WireGuard tunnel not up)"
    fi
  done
}

# ── Main ───────────────────────────────────────────────────────────────────────
TARGET="${1:-all}"
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║   01-Sandbox Monitoring Stack Deployment                     ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

case "$TARGET" in
  all)
    deploy_hubs
    deploy_spokes
    deploy_grafana
    echo ""
    check_nodeport_reachability
    ;;
  hubs)
    deploy_hubs
    deploy_grafana
    ;;
  primaryhub)
    render_and_apply "${SCRIPT_DIR}/03-prometheus-scraper.yaml" "primaryhub" "hub" "kind-primaryhub"
    kubectl --context kind-primaryhub apply -f "${SCRIPT_DIR}/01-cnpg-metrics.yaml" || true
    kubectl --context kind-primaryhub apply -f "${SCRIPT_DIR}/02-ocm-metrics.yaml" || true
    deploy_grafana
    ;;
  secondaryhub)
    render_and_apply "${SCRIPT_DIR}/03-prometheus-scraper.yaml" "secondaryhub" "hub" "kind-secondaryhub"
    kubectl --context kind-secondaryhub apply -f "${SCRIPT_DIR}/01-cnpg-metrics.yaml" || true
    kubectl --context kind-secondaryhub apply -f "${SCRIPT_DIR}/02-ocm-metrics.yaml" || true
    ;;
  spokes)
    deploy_spokes
    ;;
  spoke[0-9]*)
    # Direct: ./apply-monitoring.sh spoke3
    local ctx="kind-${TARGET}"
    render_and_apply "${SCRIPT_DIR}/04-prometheus-spoke.yaml" "${TARGET}" "spoke" "${ctx}"
    kubectl --context "${ctx}" apply -f "${SCRIPT_DIR}/02-ocm-metrics.yaml" 2>/dev/null || true
    ;;
  grafana)
    deploy_grafana
    ;;
  *)
    echo "Usage: $0 [all|hubs|primaryhub|secondaryhub|spokes|spoke1|spoke2|spoke3|grafana]"
    exit 1
    ;;
esac

echo ""
log_success "Monitoring deployment complete."
echo ""
