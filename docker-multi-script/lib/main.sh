#!/usr/bin/env bash
# ==============================================================================
# lib/main.sh – Teardown, health verification, and main() orchestrator
#
# Functions:
#   teardown_environment  – delete all KinD clusters, networks, and state
#   run_verification      – end-to-end health checks (OCM, PG, Valkey)
#   main                  – full provisioning sequence (all steps in order)
# ==============================================================================

teardown_environment() {
  log_step "Tearing down Multi-Cluster Platform..."

  ensure_docker_access
  docker rm -f envoy-gateway 2>/dev/null || true

  for cluster in primaryhub secondaryhub spoke1 spoke2; do
    if kind get clusters 2>/dev/null | grep -q "^${cluster}$"; then
      log_info "Deleting KinD cluster '$cluster'..."
      kind delete cluster --name "$cluster" 2>/dev/null || true
    fi
    docker rm -f "${cluster}-control-plane" 2>/dev/null || true
    kubectl config delete-context "kind-${cluster}"  2>/dev/null || true
    kubectl config delete-cluster  "kind-${cluster}" 2>/dev/null || true
    kubectl config unset "users.kind-${cluster}"     2>/dev/null || true
  done

  if docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$"; then
    for cid in $(docker network inspect "$TRANSIT_NET_NAME" \
                   -f '{{range $k, $v := .Containers}}{{$k}} {{end}}' 2>/dev/null || true); do
      docker network disconnect -f "$TRANSIT_NET_NAME" "$cid" 2>/dev/null || true
    done
    docker network rm "$TRANSIT_NET_NAME" 2>/dev/null || true
  fi

  [ -z "$(kind get clusters 2>/dev/null || true)" ] && \
    docker network rm kind 2>/dev/null || true

  rm -rf "$STATE_DIR" /tmp/01sandbox-* /tmp/kind-*.yaml /tmp/spoke*-* 2>/dev/null || true
  docker volume prune -f 2>/dev/null || true
  log_success "Cleanup complete."
}

run_verification() {
  log_step "End-to-End Health Verification"

  echo -e "\n${BOLD}[1a] OCM Managed Clusters – PrimaryHub:${NC}"
  kubectl --context kind-primaryhub get managedclusters 2>/dev/null || true

  echo -e "\n${BOLD}[1b] OCM Managed Clusters – SecondaryHub (Standby):${NC}"
  kubectl --context kind-secondaryhub get managedclusters 2>/dev/null || true

  echo -e "\n${BOLD}[2] PostgreSQL Replication Sender (PrimaryHub):${NC}"
  kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
    -c postgres -- psql -U postgres -d apikeys \
    -c "SELECT client_addr,application_name,state,sync_state FROM pg_stat_replication;" \
    2>/dev/null || true

  echo -e "\n${BOLD}[3] PostgreSQL WAL Receiver (SecondaryHub):${NC}"
  kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
    -c postgres -- psql -U postgres -d apikeys \
    -c "SELECT status,sender_host,sender_port,latest_end_lsn FROM pg_stat_wal_receiver;" \
    2>/dev/null || true

  echo -e "\n${BOLD}[4] Valkey Replication (SecondaryHub):${NC}"
  kubectl --context kind-secondaryhub exec -n opensandbox-system deploy/valkey -- \
    valkey-cli info replication | grep -E "role|master_host|master_port|master_link_status" \
    2>/dev/null || true

  echo -e "\n${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "${GREEN}${BOLD}  MULTI-CLUSTER HEALTH VERIFICATION COMPLETE${NC}"
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}\n"
}

# Final summary banner
_print_summary() {
  echo -e "\n${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "${GREEN}${BOLD}  MULTI-CLUSTER SETUP COMPLETE${NC}"
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "  Transit Network : ${TRANSIT_NET_NAME} (${TRANSIT_SUBNET})"
  echo -e "  WireGuard Mesh  : ${WG_SUBNET_PREFIX}.0/24 (encrypted overlay)"
  echo -e "  Gateway VIP     : https://${WG_VIP}:6443 & :80 (Envoy)"
  echo -e "  PrimaryHub      : ${WG_HUB1_IP} (Master Read-Write)"
  echo -e "  SecondaryHub    : ${WG_HUB2_IP} (Warm Standby)"
  echo -e "  Spoke1          : ${WG_SPOKE1_IP} (workload)"
  echo -e "  Spoke2          : ${WG_SPOKE2_IP} (workload)"
  echo -e "  DB Replication  : PostgreSQL physical WAL streaming (primary → standby)"
  echo -e "  Failover Ctrl   : ocm-failover-controller active on SecondaryHub"
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}\n"
}

main() {
  phase_01_preflight
  phase_02_transit_network_and_wg_keys
  phase_03_create_hub_clusters
  phase_04_install_crds_on_hubs
  phase_05_wireguard_on_hubs
  phase_06_envoy_gateway
  phase_07_verify_root_ca_and_vip
  phase_08_ocm_init
  phase_09_create_namespaces
  phase_10_load_custom_image
  phase_11_primaryhub_deploy
  phase_12_secondaryhub_deploy
  phase_13_create_spoke_clusters
  phase_14_wireguard_on_all_clusters
  phase_15_install_crds_on_spokes
  phase_15b_setup_kata_firecracker
  phase_16_join_spokes_to_ocm
  phase_17_sync_spokes_to_secondaryhub

  run_verification
  _print_summary
}
