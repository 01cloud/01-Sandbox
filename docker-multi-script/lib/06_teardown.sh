#!/usr/bin/env bash
# ==============================================================================
# lib/06_teardown.sh – Full environment teardown
# ==============================================================================

cleanup_environment() {
  log_step "Tearing down Multi-Cluster Platform..."

  ensure_docker_access
  docker rm -f envoy-gateway 2>/dev/null || true

  for cluster in primaryhub secondaryhub spoke1 spoke2; do
    if kind get clusters 2>/dev/null | grep -q "^${cluster}$"; then
      log_info "Deleting KinD cluster '$cluster'..."
      kind delete cluster --name "$cluster" 2>/dev/null || true
    fi
    docker rm -f "${cluster}-control-plane" 2>/dev/null || true
    kubectl config delete-context "kind-${cluster}"  2>/dev/null || true
    kubectl config delete-cluster "kind-${cluster}"  2>/dev/null || true
    kubectl config unset "users.kind-${cluster}"     2>/dev/null || true
  done

  if docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$"; then
    for cid in $(docker network inspect "$TRANSIT_NET_NAME" \
                   -f '{{range $k, $v := .Containers}}{{$k}} {{end}}' 2>/dev/null || true); do
      docker network disconnect -f "$TRANSIT_NET_NAME" "$cid" 2>/dev/null || true
    done
    docker network rm "$TRANSIT_NET_NAME" 2>/dev/null || true
  fi

  [ -z "$(kind get clusters 2>/dev/null || true)" ] && \
    docker network rm kind 2>/dev/null || true

  rm -rf "$STATE_DIR" /tmp/01sandbox-* /tmp/kind-*.yaml /tmp/spoke*-* 2>/dev/null || true
  docker volume prune -f 2>/dev/null || true

  log_success "Cleanup complete."
}
