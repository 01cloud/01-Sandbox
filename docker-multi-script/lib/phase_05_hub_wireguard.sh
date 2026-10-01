#!/usr/bin/env bash
# ==============================================================================
# lib/phase_05_hub_wireguard.sh – Bring up WireGuard overlay on hub clusters
# ==============================================================================

_check_phase_05() {
  _check_wg_active primaryhub-control-plane   "$WG_HUB1_IP" || return 1
  _check_wg_active secondaryhub-control-plane "$WG_HUB2_IP" || return 1
  return 0
}

_do_phase_05_wireguard_on_hubs() {
  _setup_wireguard "primaryhub-control-plane"   "primaryhub"   "$WG_HUB1_IP"
  _setup_wireguard "secondaryhub-control-plane" "secondaryhub" "$WG_HUB2_IP"

  log_success "WireGuard overlay active on hub clusters."
}

phase_05_wireguard_on_hubs() {
  run_phase "05" "Bringing Up WireGuard Overlay on Hub Clusters" _check_phase_05 _do_phase_05_wireguard_on_hubs
}
