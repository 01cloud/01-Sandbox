#!/bin/bash
# ==============================================================================
# Pure Docker Multi-Cluster Teardown & Destruction Script
# ==============================================================================
# Usage: ./docker-multi-cluster-destroy.sh
# Purpose: Completely destroys all KinD clusters, Envoy gateway, transit network,
#          WireGuard artifacts, and temporary PKI created by docker-multi-cluster.sh.
# ==============================================================================

set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_step()    { echo -e "\n${BOLD}======================================================================${NC}\n${BOLD}▶ $1${NC}\n${BOLD}======================================================================${NC}"; }

TRANSIT_NET_NAME="01sandbox-transit"
CLUSTERS=("primaryhub" "secondaryhub" "spoke1" "spoke2")

log_step "Tearing down Pure Docker Multi-Cluster Platform..."

# 1. Stop and remove Envoy Gateway container
if docker ps -a --format '{{.Names}}' | grep -q '^envoy-gateway$'; then
  log_info "Removing envoy-gateway container..."
  docker rm -f envoy-gateway 2>/dev/null || true
  log_success "Envoy Gateway container removed."
else
  log_info "No envoy-gateway container found."
fi

# 2. Delete KinD clusters
for cluster in "${CLUSTERS[@]}"; do
  if kind get clusters 2>/dev/null | grep -q "^${cluster}$"; then
    log_info "Deleting KinD cluster '$cluster'..."
    kind delete cluster --name "$cluster" 2>/dev/null || true
    log_success "KinD cluster '$cluster' deleted."
  else
    log_info "KinD cluster '$cluster' does not exist."
  fi
  # Safety-net: Force remove node container if KinD left any orphan
  docker rm -f "${cluster}-control-plane" 2>/dev/null || true
done

# 3. Clean up residual kubeconfig contexts & clusters if any
for cluster in "${CLUSTERS[@]}"; do
  kubectl config delete-context "kind-${cluster}" 2>/dev/null || true
  kubectl config delete-cluster "kind-${cluster}" 2>/dev/null || true
  kubectl config unset "users.kind-${cluster}" 2>/dev/null || true
done

# 4. Remove isolated transit Docker network
if docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$"; then
  log_info "Removing transit Docker network '$TRANSIT_NET_NAME'..."
  # Disconnect any remaining dangling containers if any
  for container in $(docker network inspect "$TRANSIT_NET_NAME" -f '{{range $k, $v := .Containers}}{{$k}} {{end}}' 2>/dev/null || true); do
    docker network disconnect -f "$TRANSIT_NET_NAME" "$container" 2>/dev/null || true
  done
  docker network rm "$TRANSIT_NET_NAME" 2>/dev/null || true
  log_success "Transit Docker network '$TRANSIT_NET_NAME' removed."
else
  log_info "Transit Docker network '$TRANSIT_NET_NAME' does not exist."
fi

# 5. Remove any dangling KinD Docker network if empty
if [ -z "$(kind get clusters 2>/dev/null || true)" ]; then
  docker network rm kind 2>/dev/null || true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${SCRIPT_DIR}/.sandbox-state"

# 6. Remove persistent state, temporary PKI, WireGuard configs, and cluster YAMLs
log_info "Cleaning up state and temporary files (${STATE_DIR}, /tmp/01sandbox-*)..."
rm -rf "$STATE_DIR" /tmp/01sandbox-* 2>/dev/null || true
rm -f /tmp/kind-*.yaml 2>/dev/null || true
rm -f /tmp/spoke*-* 2>/dev/null || true

# 7. Prune dangling volumes left by deleted KinD containers
docker volume prune -f 2>/dev/null || true

log_step "Teardown Complete!"
echo -e "${GREEN}${BOLD}✔ All clusters, gateway containers, transit networks, and configs destroyed.${NC}"
echo -e "${BLUE}You can now provision everything fresh with: ./docker-multi-cluster.sh${NC}\n"
