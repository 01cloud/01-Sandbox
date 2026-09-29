#!/usr/bin/env bash
# ==============================================================================
# lib/phase_03_hubs.sh – Create PrimaryHub and SecondaryHub KinD clusters
# ==============================================================================

_check_phase_03() {
  kind get clusters 2>/dev/null | grep -q '^primaryhub$'   || return 1
  kind get clusters 2>/dev/null | grep -q '^secondaryhub$' || return 1

  [ -s "${PKI_DIR}/ca.crt" ] && [ -s "${PKI_DIR}/ca.key" ] && \
  [ -s "${PKI_DIR}/sa.key" ] && [ -s "${PKI_DIR}/sa.pub" ] || return 1

  kubectl --context kind-primaryhub   get node primaryhub-control-plane   >/dev/null 2>&1 || return 1
  kubectl --context kind-secondaryhub get node secondaryhub-control-plane >/dev/null 2>&1 || return 1

  docker inspect primaryhub-control-plane   --format '{{json .NetworkSettings.Networks}}' 2>/dev/null | grep -q "$TRANSIT_NET_NAME" || return 1
  docker inspect secondaryhub-control-plane --format '{{json .NetworkSettings.Networks}}' 2>/dev/null | grep -q "$TRANSIT_NET_NAME" || return 1

  return 0
}

_do_phase_03_create_hub_clusters() {
  # PrimaryHub – this IS the Root CA source
  _create_kind_cluster "primaryhub" "10.244.0.0/16" "10.96.0.0/16" "$HUB1_TRANSIT_IP" "false"

  # Extract shared Root CA + ServiceAccount keys from PrimaryHub
  log_info "Extracting shared Root CA & ServiceAccount keys from primaryhub..."
  docker cp primaryhub-control-plane:/etc/kubernetes/pki/ca.crt "${PKI_DIR}/ca.crt"
  docker cp primaryhub-control-plane:/etc/kubernetes/pki/ca.key "${PKI_DIR}/ca.key"
  docker cp primaryhub-control-plane:/etc/kubernetes/pki/sa.key "${PKI_DIR}/sa.key"
  docker cp primaryhub-control-plane:/etc/kubernetes/pki/sa.pub "${PKI_DIR}/sa.pub"

  # SecondaryHub – mounted with PrimaryHub's shared Root CA
  _create_kind_cluster "secondaryhub" "10.245.0.0/16" "10.97.0.0/16" "$HUB2_TRANSIT_IP" "true"

  log_success "Hub clusters created."
}

phase_03_create_hub_clusters() {
  run_phase "03" "Creating Hub Clusters (PrimaryHub + SecondaryHub)" _check_phase_03 _do_phase_03_create_hub_clusters
}
