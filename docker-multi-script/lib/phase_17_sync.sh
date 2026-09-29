#!/usr/bin/env bash
# ==============================================================================
# lib/phase_17_sync.sh – Label spokes and sync registration to SecondaryHub
# ==============================================================================

_check_phase_17() {
  local spoke lbl
  for spoke in spoke1 spoke2; do
    kubectl --context kind-secondaryhub get managedcluster "$spoke" >/dev/null 2>&1 || return 1
    lbl=$(kubectl --context kind-primaryhub get managedcluster "$spoke" \
      -o jsonpath='{.metadata.labels.sandbox-workload-capable}' 2>/dev/null || echo "")
    [ "$lbl" == "true" ] || return 1
  done
  kubectl --context kind-secondaryhub get managedclusterset sandbox-spokes >/dev/null 2>&1 || return 1
  return 0
}

_do_phase_17_sync_spokes_to_secondaryhub() {
  for spoke in spoke1 spoke2; do
    local spoke_wg_ip
    [ "$spoke" == "spoke1" ] && spoke_wg_ip="$WG_SPOKE1_IP" || spoke_wg_ip="$WG_SPOKE2_IP"

    for ctx in kind-primaryhub kind-secondaryhub; do
      kubectl --context "$ctx" label managedcluster "$spoke" \
        wireguard-ip="${spoke_wg_ip}" \
        sandbox-workload-capable=true \
        runtime.gvisor=true runtime.kata=true \
        --overwrite 2>/dev/null || true
    done
  done

  log_info "Syncing spoke registration resources to secondaryhub..."
  for spoke in spoke1 spoke2; do
    kubectl --context kind-primaryhub get namespace "$spoke" -o yaml 2>/dev/null | \
      kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true
    kubectl --context kind-primaryhub get clusterrole \
      "open-cluster-management:managedcluster:${spoke}" -o yaml 2>/dev/null | \
      kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true
    kubectl --context kind-primaryhub get clusterrolebinding \
      "open-cluster-management:managedcluster:${spoke}" -o yaml 2>/dev/null | \
      kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true
    kubectl --context kind-primaryhub get rolebinding -n "$spoke" -o yaml 2>/dev/null | \
      kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true
    kubectl --context kind-primaryhub get managedcluster "$spoke" -o yaml 2>/dev/null | \
      kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true
  done

  kubectl --context kind-primaryhub get managedclusterset sandbox-spokes -o yaml 2>/dev/null | \
    kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true
  kubectl --context kind-primaryhub get managedclustersetbinding -A -o yaml 2>/dev/null | \
    kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true

  log_success "Spoke registration synced to both hubs."
}

phase_17_sync_spokes_to_secondaryhub() {
  run_phase "17" "Labeling Spokes & Syncing Registration to SecondaryHub" _check_phase_17 _do_phase_17_sync_spokes_to_secondaryhub
}
