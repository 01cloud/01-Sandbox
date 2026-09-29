#!/usr/bin/env bash
# ==============================================================================
# lib/99_main.sh – main() orchestrator: calls all 18 phases in order
#
# Each phase_NN_* function checks its own state first (_check_phase_NN) and
# skips its work if already configured. Use --force to override all checks.
# Comment out or reorder phases here to customise the setup sequence.
# ==============================================================================

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
  phase_16_join_spokes_to_ocm
  phase_17_sync_spokes_to_secondaryhub
  phase_18_verify_and_summary
}
