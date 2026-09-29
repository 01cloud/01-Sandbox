#!/usr/bin/env bash
# ==============================================================================
# lib/phase_09_namespaces.sh – Create application namespaces on both hubs
# ==============================================================================

_check_phase_09() {
  local hub ns
  for hub in primaryhub secondaryhub; do
    for ns in opensandbox-system metallb-system agentgateway-system; do
      kubectl --context "kind-${hub}" get namespace "$ns" >/dev/null 2>&1 || return 1
    done
  done
  return 0
}

_do_phase_09_create_namespaces() {
  for hub in primaryhub secondaryhub; do
    for ns in opensandbox-system metallb-system agentgateway-system; do
      kubectl --context "kind-${hub}" create namespace "$ns" \
        --dry-run=client -o yaml | kubectl --context "kind-${hub}" apply -f -
    done
  done

  log_success "Namespaces ready on both hubs."
}

phase_09_create_namespaces() {
  run_phase "09" "Creating Application Namespaces on Hub Clusters" _check_phase_09 _do_phase_09_create_namespaces
}
