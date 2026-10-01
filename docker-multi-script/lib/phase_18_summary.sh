#!/usr/bin/env bash
# ==============================================================================
# lib/phase_18_summary.sh – Final verification and setup summary banner
# ==============================================================================

phase_18_verify_and_summary() {
  run_verification

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
  echo -e "  DB Replication  : PostgreSQL physical WAL streaming (primary -> standby)"
  echo -e "  Failover Ctrl   : ocm-failover-controller active on SecondaryHub"
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}\n"
}
