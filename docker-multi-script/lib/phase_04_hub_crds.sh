#!/usr/bin/env bash
# ==============================================================================
# lib/phase_04_hub_crds.sh – Install CRDs on both hub clusters
# ==============================================================================

_check_phase_04() {
  _check_hub_crds_established kind-primaryhub   || return 1
  _check_hub_crds_established kind-secondaryhub || return 1
  return 0
}

_do_phase_04_install_crds_on_hubs() {
  ensure_sandbox_repo || log_warn "Repository clone pending; local CRDs may be deferred."

  for hub in primaryhub secondaryhub; do
    _install_crds "kind-${hub}"
  done
  log_success "CRDs installed on all hub clusters."
}

phase_04_install_crds_on_hubs() {
  run_phase "04" "Installing CRDs on Hub Clusters" _check_phase_04 _do_phase_04_install_crds_on_hubs
}
