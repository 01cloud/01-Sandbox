#!/usr/bin/env bash
# ==============================================================================
# lib/phase_08_ocm.sh – Initialize OCM on PrimaryHub and SecondaryHub
# ==============================================================================

_check_phase_08() {
  local ctx avail
  for ctx in kind-primaryhub kind-secondaryhub; do
    kubectl --context "$ctx" get crd managedclusters.cluster.open-cluster-management.io >/dev/null 2>&1 || return 1
    avail=$(kubectl --context "$ctx" -n open-cluster-management-hub get deployment \
      cluster-manager-registration-webhook -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0)
    [ "${avail:-0}" -ge 1 ] || return 1
  done
  return 0
}

_do_phase_08_ocm_init() {
  for hub in primaryhub secondaryhub; do
    log_info "Checking OCM on $hub..."
    if ! kubectl --context "kind-${hub}" get crd \
         managedclusters.cluster.open-cluster-management.io >/dev/null 2>&1; then
      clusteradm init --context "kind-${hub}" --wait || true
    else
      log_info "OCM already initialized on $hub."
    fi
  done

  local auto_acceptor="${SANDBOX_REPO_DIR}/docs/multi-cluster/vm-level-ocm-multi-cluster/manifests/ocm-auto-acceptor-k8s.yaml"
  [ ! -f "$auto_acceptor" ] && auto_acceptor="${ROOT_DIR}/docs/multi-cluster/vm-level-ocm-multi-cluster/manifests/ocm-auto-acceptor-k8s.yaml"
  if [ -f "$auto_acceptor" ]; then
    log_info "Deploying ocm-auto-acceptor on primaryhub..."
    kubectl --context kind-primaryhub apply -f "$auto_acceptor" || true
  fi

  for hub in primaryhub secondaryhub; do
    log_info "Waiting for OCM registration webhook on $hub..."
    kubectl --context "kind-${hub}" -n open-cluster-management-hub wait \
      --for=condition=Available deployment/cluster-manager-registration-webhook \
      --timeout=60s 2>/dev/null || true
    _relax_webhook_failure_policy "kind-${hub}"
  done
}

phase_08_ocm_init() {
  run_phase "08" "Initializing OCM on PrimaryHub & SecondaryHub" _check_phase_08 _do_phase_08_ocm_init
}
