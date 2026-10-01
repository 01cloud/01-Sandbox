#!/usr/bin/env bash
# ==============================================================================
# lib/phase_10_image.sh – Build and load the custom opensandbox-server image
# ==============================================================================

_check_phase_10() {
  local img="01community/01sandbox-opensandbox-server:v0.7.10-ocm"
  docker image inspect "$img" >/dev/null 2>&1 || return 1

  local hub
  for hub in primaryhub secondaryhub; do
    docker exec "${hub}-control-plane" crictl images 2>/dev/null | grep -q "01sandbox-opensandbox-server" || return 1
  done
  return 0
}

_do_phase_10_load_custom_image() {
  ensure_sandbox_repo

  if [ ! -d "${OPENSANDBOX_BUILD_DIR}" ] || [ ! -f "${OPENSANDBOX_BUILD_DIR}/Dockerfile" ]; then
    log_error "Dockerfile not found at detected build path: ${OPENSANDBOX_BUILD_DIR}"
    log_error "Failed to locate opensandbox-server/docker-build context."
    exit 1
  fi

  local img="01community/01sandbox-opensandbox-server:v0.7.10-ocm"
  log_info "Building $img from: ${OPENSANDBOX_BUILD_DIR}..."
  docker build -t "$img" "${OPENSANDBOX_BUILD_DIR}"
  log_success "Built $img successfully with OCM workload provider support."

  for hub in primaryhub secondaryhub; do
    log_info "Loading $img into $hub..."
    kind load docker-image "$img" --name "$hub"
    kubectl --context "kind-${hub}" rollout restart deployment/opensandbox-server -n opensandbox-system 2>/dev/null || true
  done

  log_success "Custom image ready on both hubs."
}

phase_10_load_custom_image() {
  run_phase "10" "Building & Loading Custom opensandbox-server Image" _check_phase_10 _do_phase_10_load_custom_image
}
