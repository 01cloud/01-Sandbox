#!/usr/bin/env bash
# ==============================================================================
# lib/07_verification.sh – End-to-end health checks
# ==============================================================================

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
