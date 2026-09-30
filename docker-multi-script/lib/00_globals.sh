#!/usr/bin/env bash
# ==============================================================================
# lib/00_globals.sh – Global variables, network config, CRD maps, arg parsing
# Sourced first by docker-multi-cluster.sh. All subsequent modules depend on
# the variables defined here.
# ==============================================================================

# ─── Color / logging tokens ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ─── Script / repository paths ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="${SCRIPT_DIR}"
SANDBOX_REPO_DIR="${ROOT_DIR}"
CODE_INSPECTOR_DIR="${ROOT_DIR}/codeInspector"
OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/opensandbox-server/docker-build"

# Detect repository location relative to the script or common paths
if [ -d "${ROOT_DIR}/01-Sandbox/codeInspector" ]; then
  SANDBOX_REPO_DIR="${ROOT_DIR}/01-Sandbox"
  CODE_INSPECTOR_DIR="${ROOT_DIR}/01-Sandbox/codeInspector"
  OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/01-Sandbox/opensandbox-server/docker-build"
elif [ -d "${ROOT_DIR}/codeInspector" ]; then
  SANDBOX_REPO_DIR="${ROOT_DIR}"
  CODE_INSPECTOR_DIR="${ROOT_DIR}/codeInspector"
  OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/opensandbox-server/docker-build"
elif [ -d "/home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector" ]; then
  SANDBOX_REPO_DIR="/home/berrybytes/Desktop/Kamal/01-Sandbox"
  CODE_INSPECTOR_DIR="/home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector"
  OPENSANDBOX_BUILD_DIR="/home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build"
elif [ -d "$(pwd)/01-Sandbox/codeInspector" ]; then
  SANDBOX_REPO_DIR="$(pwd)/01-Sandbox"
  CODE_INSPECTOR_DIR="$(pwd)/01-Sandbox/codeInspector"
  OPENSANDBOX_BUILD_DIR="$(pwd)/01-Sandbox/opensandbox-server/docker-build"
elif [ -d "$(pwd)/codeInspector" ]; then
  SANDBOX_REPO_DIR="$(pwd)"
  CODE_INSPECTOR_DIR="$(pwd)/codeInspector"
  OPENSANDBOX_BUILD_DIR="$(pwd)/opensandbox-server/docker-build"
fi

# ─── State directories ────────────────────────────────────────────────────────
STATE_DIR="${ROOT_DIR}/.sandbox-state"
PKI_DIR="${STATE_DIR}/pki"
WG_DIR="${STATE_DIR}/wg"
ENVOY_DIR="${STATE_DIR}/envoy"
SEC_DIR="${STATE_DIR}/sec"
KATA_CACHE_DIR="${STATE_DIR}/kata-assets"
mkdir -p "$PKI_DIR" "$WG_DIR" "$ENVOY_DIR" "$SEC_DIR" "$KATA_CACHE_DIR"

# ─── Kata Firecracker configuration ───────────────────────────────────────────
KATA_VERSION="${KATA_VERSION:-3.18.0}"
FIRECRACKER_VERSION="${FIRECRACKER_VERSION:-v1.11.1}"

# ─── Pin KUBECONFIG to a known-writable path ──────────────────────────────────
# Prevents failures on VMs where $KUBECONFIG already points at /etc/rancher/...
export KUBECONFIG="${HOME}/.kube/config"
mkdir -p "$(dirname "$KUBECONFIG")"
touch "$KUBECONFIG" 2>/dev/null || {
  echo -e "\033[0;31m\033[1m[ERROR]\033[0m   Cannot write to \$KUBECONFIG (${KUBECONFIG}). Check permissions on ${HOME}/.kube." >&2
  exit 1
}

# ─── Docker transit network ───────────────────────────────────────────────────
TRANSIT_NET_NAME="01sandbox-transit"
TRANSIT_SUBNET="172.30.0.0/24"

# Transit IPs (WireGuard UDP only – nodes connect over this underlay)
GW_TRANSIT_IP="172.30.0.10"
HUB1_TRANSIT_IP="172.30.0.20"
HUB2_TRANSIT_IP="172.30.0.21"
SPOKE1_TRANSIT_IP="172.30.0.30"
SPOKE2_TRANSIT_IP="172.30.0.31"
HUB1_METALLB_IP="172.30.0.200"
HUB2_METALLB_IP="172.30.0.201"

# ─── WireGuard overlay (all application / K8s traffic) ───────────────────────
WG_SUBNET_PREFIX="10.99.0"
WG_GATEWAY_IP="10.99.0.254"
WG_VIP="10.99.0.100"
WG_HUB1_IP="10.99.0.1"
WG_HUB2_IP="10.99.0.2"
WG_SPOKE1_IP="10.99.0.3"
WG_SPOKE2_IP="10.99.0.4"

# WireGuard keypairs – populated by phase 02 (or its idempotency check)
declare -A WG_PRIV=()
declare -A WG_PUB=()

# ─── CRD metadata (used by _install_crds for rich output) ────────────────────
declare -A CRD_NAMES=(
  ["cloudnative-pg-crds.yaml"]="CloudNativePG (Postgres HA & Replication)"
  ["gateway-api-crds.yaml"]="Kubernetes Gateway API (Routing & Ingress)"
  ["metallb-crds.yaml"]="MetalLB (Bare-metal LoadBalancer)"
  ["sealed-secrets-crd.yaml"]="Bitnami SealedSecrets (GitOps Encryption)"
  ["agentgateway-crds.yaml"]="AgentGateway (AI Agent Orchestration)"
  ["opensandbox-crds.yaml"]="OpenSandbox (Workload Sandboxing & Pools)"
)

declare -A CRD_DETAILS=(
  ["cloudnative-pg-crds.yaml"]="clusters, backups, scheduledbackups, poolers, publications, subscriptions, clusterimagecatalogs, databaseroles, databases, failoverquorums, imagecatalogs"
  ["gateway-api-crds.yaml"]="gateways, gatewayclasses, httproutes, grpcroutes, tcproutes, tlsroutes, udproutes, referencegrants, backendtlspolicies"
  ["metallb-crds.yaml"]="addresspools, ipaddresspools, l2advertisements, bgpadvertisements, bgppeers, bfdprofiles, communities"
  ["sealed-secrets-crd.yaml"]="sealedsecrets.bitnami.com"
  ["agentgateway-crds.yaml"]="agentgatewaybackends, agentgatewayparameters, agentgatewaypolicies"
  ["opensandbox-crds.yaml"]="batchsandboxes, pools"
)

declare -A CRD_URLS=(
  ["cloudnative-pg-crds.yaml"]="https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/main/releases/cnpg-latest.yaml"
  ["gateway-api-crds.yaml"]="https://github.com/kubernetes-sigs/gateway-api/releases/latest/download/experimental-install.yaml"
  ["metallb-crds.yaml"]="https://raw.githubusercontent.com/metallb/metallb/main/config/crd/bases/metallb.io_addresspools.yaml"
  ["sealed-secrets-crd.yaml"]="https://github.com/bitnami-labs/sealed-secrets/releases/latest/download/controller.yaml"
)

# ─── Argument parsing ─────────────────────────────────────────────────────────
ACTION="deploy"
FORCE_RECONFIGURE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean|clean|--destroy|destroy) ACTION="clean";         shift ;;
    --verify)                         ACTION="verify";        shift ;;
    --force)                          FORCE_RECONFIGURE=true; shift ;;
    -h|--help)
      echo "Usage: $0 [--clean | --verify] [--force]"
      echo "  (no flags)  Full one-shot automated setup (skips phases already configured)"
      echo "  --force     Re-run every phase's full configuration, ignoring existing state"
      echo "  --clean     Tear down all clusters, containers, and networks"
      echo "  --verify    Run end-to-end health checks on an existing deployment"
      exit 0 ;;
    *) echo -e "\033[0;31m\033[1m[ERROR]\033[0m   Unknown argument: $1" >&2; exit 1 ;;
  esac
done
