#!/usr/bin/env bash
# ==============================================================================
# lib/phase_07_pki.sh – Verify shared Root CA and configure cluster-info VIP
# ==============================================================================

_check_phase_07() {
  local pca sca
  pca=$(docker exec primaryhub-control-plane   sha256sum /etc/kubernetes/pki/ca.crt 2>/dev/null | awk '{print $1}')
  sca=$(docker exec secondaryhub-control-plane sha256sum /etc/kubernetes/pki/ca.crt 2>/dev/null | awk '{print $1}')
  [ -n "$pca" ] && [ "$pca" == "$sca" ] || return 1

  local ctx
  for ctx in kind-primaryhub kind-secondaryhub; do
    kubectl --context "$ctx" get configmap cluster-info -n kube-public -o jsonpath='{.data.kubeconfig}' 2>/dev/null \
      | grep -q "$WG_VIP" || return 1
  done
  return 0
}

_do_phase_07_verify_root_ca_and_vip() {
  local primary_ca secondary_ca
  primary_ca=$(docker exec primaryhub-control-plane   sha256sum /etc/kubernetes/pki/ca.crt | awk '{print $1}')
  secondary_ca=$(docker exec secondaryhub-control-plane sha256sum /etc/kubernetes/pki/ca.crt | awk '{print $1}')

  if [ "$primary_ca" != "$secondary_ca" ]; then
    log_error "Root CA MISMATCH between primaryhub and secondaryhub!"
    exit 1
  fi
  log_success "Root CA synchronized (${primary_ca})."

  local primary_sa secondary_sa
  primary_sa=$(docker exec primaryhub-control-plane     sha256sum /etc/kubernetes/pki/sa.pub 2>/dev/null | awk '{print $1}' || echo "none")
  secondary_sa=$(docker exec secondaryhub-control-plane sha256sum /etc/kubernetes/pki/sa.pub 2>/dev/null | awk '{print $1}' || echo "none")

  if [ "$primary_sa" != "$secondary_sa" ]; then
    log_info "Syncing ServiceAccount keys from primaryhub to secondaryhub..."
    docker cp primaryhub-control-plane:/etc/kubernetes/pki/sa.key "${PKI_DIR}/sa.key"
    docker cp primaryhub-control-plane:/etc/kubernetes/pki/sa.pub "${PKI_DIR}/sa.pub"
    docker cp "${PKI_DIR}/sa.key" secondaryhub-control-plane:/etc/kubernetes/pki/sa.key
    docker cp "${PKI_DIR}/sa.pub" secondaryhub-control-plane:/etc/kubernetes/pki/sa.pub
  fi
  log_success "ServiceAccount keys synchronized."

  log_info "Updating cluster-info to advertise Gateway VIP..."
  for ctx in kind-primaryhub kind-secondaryhub; do
    kubectl --context "$ctx" get configmap cluster-info -n kube-public -o yaml 2>/dev/null | \
      sed "s|server:.*|server: https://${WG_VIP}:6443|g" | \
      kubectl --context "$ctx" apply -f - 2>/dev/null || true
  done
}

phase_07_verify_root_ca_and_vip() {
  run_phase "07" "Verifying Shared Root CA & VIP TLS SANs" _check_phase_07 _do_phase_07_verify_root_ca_and_vip
}
