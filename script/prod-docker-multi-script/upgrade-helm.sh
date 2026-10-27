#!/usr/bin/env bash
# ==============================================================================
# upgrade-helm.sh – Safe Helm Chart-Only Upgrade for Primary & Secondary Hubs
# ==============================================================================

set -euo pipefail

TARGET_HUB="${1:-all}"  # "primaryhub", "secondaryhub", or "all"

# Clear positional parameters before sourcing globals.sh so its CLI parser does not conflict
set --

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

source "${LIB_DIR}/globals.sh"

echo -e "\n${CYAN}${BOLD}=== Safe Helm Upgrade (Metrics & Configs) ===${NC}"
echo -e "Chart path: ${BOLD}${CODE_INSPECTOR_DIR}${NC}"
echo -e "Target:     ${BOLD}${TARGET_HUB}${NC}\n"

upgrade_primary() {
  log_step "Upgrading Helm release on PrimaryHub..."
  ensure_node_ssm_access "$PRIMARYHUB_HOST"
  ensure_cluster_ssh_tunnel "primaryhub" "$PRIMARYHUB_HOST"

  if ! kubectl --kubeconfig "$KUBECONFIG" --context kind-primaryhub cluster-info >/dev/null 2>&1; then
    log_error "Cannot reach kind-primaryhub. Please verify AWS credentials or SSM tunnel."
    return 1
  fi

  log_info "Running helm upgrade for codeinspector on kind-primaryhub..."
  helm upgrade codeinspector "${CODE_INSPECTOR_DIR}" \
    --skip-crds \
    --kubeconfig "$KUBECONFIG" \
    --kube-context kind-primaryhub \
    --namespace opensandbox-system \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
    --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
    --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
    --timeout 5m

  log_success "PrimaryHub Helm upgrade complete!"
}

upgrade_secondary() {
  log_step "Upgrading Helm release on SecondaryHub..."
  ensure_node_ssm_access "$SECONDARYHUB_HOST"
  ensure_cluster_ssh_tunnel "secondaryhub" "$SECONDARYHUB_HOST"

  if ! kubectl --kubeconfig "$KUBECONFIG" --context kind-secondaryhub cluster-info >/dev/null 2>&1; then
    log_warn "Cannot reach kind-secondaryhub. Skipping SecondaryHub."
    return 0
  fi

  log_info "Running helm upgrade for codeinspector on kind-secondaryhub..."
  helm upgrade codeinspector "${CODE_INSPECTOR_DIR}" \
    --skip-crds \
    --kubeconfig "$KUBECONFIG" \
    --kube-context kind-secondaryhub \
    --namespace opensandbox-system \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --values "${CODE_INSPECTOR_DIR}/values-secondary.yaml" \
    --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
    --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
    --set apiServer.valkey.replication.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.valkey.replication.primaryPort=30379 \
    --set apiServer.cnpg.replication.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.cnpg.replication.primaryPort=30432 \
    --set apiServer.failoverController.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.failoverController.primaryPort=30432 \
    --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
    --timeout 5m

  log_success "SecondaryHub Helm upgrade complete!"
}

case "$TARGET_HUB" in
  primary|primaryhub)
    upgrade_primary
    ;;
  secondary|secondaryhub)
    upgrade_secondary
    ;;
  all)
    upgrade_primary
    upgrade_secondary
    ;;
  *)
    echo "Unknown target: $TARGET_HUB (valid options: primaryhub, secondaryhub, all)"
    exit 1
    ;;
esac

echo -e "\n${GREEN}${BOLD}✔ Safe Helm upgrade completed successfully without modifying cluster infrastructure!${NC}\n"
