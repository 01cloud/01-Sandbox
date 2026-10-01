#!/usr/bin/env bash
# ==============================================================================
# lib/phase_13_spokes.sh – Create spoke clusters (spoke1 + spoke2)
# ==============================================================================

_check_phase_13() {
  kind get clusters 2>/dev/null | grep -q '^spoke1$' || return 1
  kind get clusters 2>/dev/null | grep -q '^spoke2$' || return 1
  kubectl --context kind-spoke1 get node spoke1-control-plane >/dev/null 2>&1 || return 1
  kubectl --context kind-spoke2 get node spoke2-control-plane >/dev/null 2>&1 || return 1
  return 0
}

_do_phase_13_create_spoke_clusters() {
  _create_kind_cluster "spoke1" "10.246.0.0/16" "10.98.0.0/16"  "$SPOKE1_TRANSIT_IP" "false"
  _create_kind_cluster "spoke2" "10.247.0.0/16" "10.100.0.0/16" "$SPOKE2_TRANSIT_IP" "false"

  log_success "Spoke clusters created."
}

phase_13_create_spoke_clusters() {
  run_phase "13" "Creating Spoke Clusters" _check_phase_13 _do_phase_13_create_spoke_clusters
}
