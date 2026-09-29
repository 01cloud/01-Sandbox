#!/usr/bin/env bash
# ==============================================================================
# lib/phase_14_spoke_wireguard.sh – WireGuard on spokes + hub re-apply
#
# After the spokes exist their public keys are known, so the hub wg0.conf
# files must be re-written to include the spoke [Peer] entries.
# ==============================================================================

_check_phase_14() {
  _check_wg_active spoke1-control-plane       "$WG_SPOKE1_IP" || return 1
  _check_wg_active spoke2-control-plane       "$WG_SPOKE2_IP" || return 1
  _check_wg_active primaryhub-control-plane   "$WG_HUB1_IP"   || return 1
  _check_wg_active secondaryhub-control-plane "$WG_HUB2_IP"   || return 1

  # Check that hub configs already include the spoke peers
  docker exec primaryhub-control-plane grep -q "${WG_PUB[spoke1]}" /etc/wireguard/wg0.conf 2>/dev/null || return 1
  docker exec primaryhub-control-plane grep -q "${WG_PUB[spoke2]}" /etc/wireguard/wg0.conf 2>/dev/null || return 1

  return 0
}

_do_phase_14_wireguard_on_all_clusters() {
  _setup_wireguard "spoke1-control-plane" "spoke1" "$WG_SPOKE1_IP"
  _setup_wireguard "spoke2-control-plane" "spoke2" "$WG_SPOKE2_IP"

  # Re-apply on hubs so their wg0.conf now includes the spoke [Peer] entries
  log_info "Re-applying WireGuard on hubs (spoke peers now included)..."
  _setup_wireguard "primaryhub-control-plane"   "primaryhub"   "$WG_HUB1_IP"
  _setup_wireguard "secondaryhub-control-plane" "secondaryhub" "$WG_HUB2_IP"

  log_success "WireGuard overlay peer-complete on all 4 clusters."
}

phase_14_wireguard_on_all_clusters() {
  run_phase "14" "WireGuard on All Clusters (Spokes + Hub Re-apply)" _check_phase_14 _do_phase_14_wireguard_on_all_clusters
}
