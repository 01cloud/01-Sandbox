#!/usr/bin/env bash
# ==============================================================================
# lib/phase_15_spoke_crds.sh – Install CRDs on spoke clusters
# ==============================================================================

_check_phase_15() {
  _check_hub_crds_established kind-spoke1 || return 1
  _check_hub_crds_established kind-spoke2 || return 1
  return 0
}

_do_phase_15_install_crds_on_spokes() {
  ensure_sandbox_repo || log_warn "Repository clone pending; local CRDs may be deferred."

  for spoke in spoke1 spoke2; do
    _install_crds "kind-${spoke}"
  done
  log_success "CRDs installed on all spoke clusters."
}

phase_15_install_crds_on_spokes() {
  run_phase "15" "Installing CRDs on Spoke Clusters" _check_phase_15 _do_phase_15_install_crds_on_spokes
}
