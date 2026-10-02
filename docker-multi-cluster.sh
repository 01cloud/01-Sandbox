#!/usr/bin/env bash
# ==============================================================================
# docker-multi-cluster.sh
#
# One-Shot Automated Multi-Cluster Platform (KinD + Docker)
#
# Every provisioning step is a named function.
# The main() function at the bottom calls them in the correct order.
# You can comment out, reorder, or call any phase individually.
#
# Usage:
#   ./docker-multi-cluster.sh           # Full one-shot setup
#   ./docker-multi-cluster.sh --clean   # Tear down all clusters & networks
#   ./docker-multi-cluster.sh --verify  # End-to-end health verification
# ==============================================================================

set -eo pipefail

# ─── Color helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}${BOLD}[INFO]${NC}    $1"; }
log_success() { echo -e "${GREEN}${BOLD}[SUCCESS]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}${BOLD}[WARN]${NC}    $1"; }
log_error()   { echo -e "${RED}${BOLD}[ERROR]${NC}   $1"; }
log_step() {
  echo -e "\n${CYAN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "${CYAN}${BOLD}▶  $1${NC}"
  echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
}

# ─── Global paths & network config ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}"
SANDBOX_REPO_DIR="${ROOT_DIR}"
CODE_INSPECTOR_DIR="${ROOT_DIR}/codeInspector"
OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/opensandbox-server/docker-build"

# Robust initial detection for repository directories if running inside cloned repo or next to it
if [ -d "${ROOT_DIR}/01-Sandbox/codeInspector" ]; then
  SANDBOX_REPO_DIR="${ROOT_DIR}/01-Sandbox"
  CODE_INSPECTOR_DIR="${ROOT_DIR}/01-Sandbox/codeInspector"
  OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/01-Sandbox/opensandbox-server/docker-build"
elif [ -d "${ROOT_DIR}/codeInspector" ]; then
  SANDBOX_REPO_DIR="${ROOT_DIR}"
  CODE_INSPECTOR_DIR="${ROOT_DIR}/codeInspector"
  OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/opensandbox-server/docker-build"
elif [ -d "$(pwd)/01-Sandbox/codeInspector" ]; then
  SANDBOX_REPO_DIR="$(pwd)/01-Sandbox"
  CODE_INSPECTOR_DIR="$(pwd)/01-Sandbox/codeInspector"
  OPENSANDBOX_BUILD_DIR="$(pwd)/01-Sandbox/opensandbox-server/docker-build"
elif [ -d "$(pwd)/codeInspector" ]; then
  SANDBOX_REPO_DIR="$(pwd)"
  CODE_INSPECTOR_DIR="$(pwd)/codeInspector"
  OPENSANDBOX_BUILD_DIR="$(pwd)/opensandbox-server/docker-build"
fi

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

TRANSIT_NET_NAME="01sandbox-transit"
TRANSIT_SUBNET="172.30.0.0/24"

# Underlay (WireGuard UDP 51820 only)
GW_TRANSIT_IP="172.30.0.10"
HUB1_TRANSIT_IP="172.30.0.20"
HUB2_TRANSIT_IP="172.30.0.21"
SPOKE1_TRANSIT_IP="172.30.0.30"
SPOKE2_TRANSIT_IP="172.30.0.31"
HUB1_METALLB_IP="172.30.0.200"
HUB2_METALLB_IP="172.30.0.201"

# WireGuard overlay (all application / K8s traffic)
WG_SUBNET_PREFIX="10.99.0"
WG_GATEWAY_IP="10.99.0.254"
WG_VIP="10.99.0.100"
WG_HUB1_IP="10.99.0.1"
WG_HUB2_IP="10.99.0.2"
WG_SPOKE1_IP="10.99.0.3"
WG_SPOKE2_IP="10.99.0.4"

# WireGuard keypairs – populated by phase_02_transit_network_and_wg_keys()
declare -A WG_PRIV=()
declare -A WG_PUB=()

# CRD Package metadata & descriptions
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

# Upstream URLs for CRDs available on the internet.
# agentgateway-crds.yaml and opensandbox-crds.yaml have no public upstream → always local.
declare -A CRD_URLS=(
  ["cloudnative-pg-crds.yaml"]="https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/main/releases/cnpg-latest.yaml"
  ["gateway-api-crds.yaml"]="https://github.com/kubernetes-sigs/gateway-api/releases/latest/download/experimental-install.yaml"
  ["metallb-crds.yaml"]="https://raw.githubusercontent.com/metallb/metallb/main/config/crd/bases/metallb.io_addresspools.yaml"
  ["sealed-secrets-crd.yaml"]="https://github.com/bitnami-labs/sealed-secrets/releases/latest/download/controller.yaml"
)

# ─── Argument parsing ──────────────────────────────────────────────────────────
ACTION="deploy"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean|clean|--destroy|destroy) ACTION="clean";  shift ;;
    --verify)                         ACTION="verify"; shift ;;
    -h|--help)
      echo "Usage: $0 [--clean | --verify]"
      echo "  (no flags)  Full one-shot automated setup"
      echo "  --clean     Tear down all clusters, containers, and networks"
      echo "  --verify    Run end-to-end health checks on an existing deployment"
      exit 0 ;;
    *) log_error "Unknown argument: $1"; exit 1 ;;
  esac
done

# ==============================================================================
# ── UTILITY HELPERS ────────────────────────────────────────────────────────────
# ==============================================================================

# Ensure host inotify limits are sufficient for running multiple KinD clusters.
# KinD control-plane nodes run systemd, containerd, and multiple daemonsets.
# Default limits (watches: 8192/65536, instances: 128) lead to EMFILE /
# "could not find a log line that matches Reached target Multi-User System" when
# launching the 3rd or 4th cluster.
ensure_kernel_inotify_limits() {
  local cur_watches cur_instances
  cur_watches=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)
  cur_instances=$(cat /proc/sys/fs/inotify/max_user_instances 2>/dev/null || echo 0)

  local need_watches=524288
  local need_instances=8192

  if [ "${cur_watches:-0}" -lt "$need_watches" ] || [ "${cur_instances:-0}" -lt "$need_instances" ]; then
    log_info "Tuning host inotify limits for multi-cluster KinD (watches: $cur_watches -> $need_watches, instances: $cur_instances -> $need_instances)..."
    local SUDO=""
    if [ "$EUID" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
      SUDO="sudo"
    fi
    $SUDO sysctl -w fs.inotify.max_user_watches=$need_watches >/dev/null 2>&1 || sysctl -w fs.inotify.max_user_watches=$need_watches >/dev/null 2>&1 || true
    $SUDO sysctl -w fs.inotify.max_user_instances=$need_instances >/dev/null 2>&1 || sysctl -w fs.inotify.max_user_instances=$need_instances >/dev/null 2>&1 || true

    if [ -d /etc/sysctl.d ]; then
      printf "fs.inotify.max_user_watches = %d\nfs.inotify.max_user_instances = %d\n" "$need_watches" "$need_instances" | \
        $SUDO tee /etc/sysctl.d/99-kind-inotify.conf >/dev/null 2>&1 || true
    fi
  fi
}

# Ensure admission webhooks do not cause circular deadlocks or timeout errors
# during bootstrap or node reboots when webhook pods are spinning up.
_relax_webhook_failure_policy() {
  local ctx="$1"
  log_info "Ensuring admission webhooks on $ctx do not block deployment..."

  for vwh in managedclustersetbindingvalidators.admission.cluster.open-cluster-management.io \
             managedclustervalidators.admission.cluster.open-cluster-management.io \
             manifestworkvalidators.admission.work.open-cluster-management.io; do
    if kubectl --context "$ctx" get validatingwebhookconfiguration "$vwh" >/dev/null 2>&1; then
      kubectl --context "$ctx" get validatingwebhookconfiguration "$vwh" -o json 2>/dev/null | \
        jq '(.webhooks[].failurePolicy) = "Ignore"' 2>/dev/null | \
        kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
    fi
  done

  if kubectl --context "$ctx" get mutatingwebhookconfiguration cnpg-mutating-webhook-configuration >/dev/null 2>&1; then
    kubectl --context "$ctx" get mutatingwebhookconfiguration cnpg-mutating-webhook-configuration -o json 2>/dev/null | \
      jq '(.webhooks[].failurePolicy) = "Ignore"' 2>/dev/null | \
      kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
  fi

  if kubectl --context "$ctx" get validatingwebhookconfiguration cnpg-validating-webhook-configuration >/dev/null 2>&1; then
    kubectl --context "$ctx" get validatingwebhookconfiguration cnpg-validating-webhook-configuration -o json 2>/dev/null | \
      jq '(.webhooks[].failurePolicy) = "Ignore"' 2>/dev/null | \
      kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
  fi
}

# Create a KinD cluster and attach it to the transit network.
# Args: <name> <pod-subnet> <svc-subnet> <transit-ip> [use-shared-ca=false]
_create_kind_cluster() {
  local name="$1" pod_subnet="$2" svc_subnet="$3" transit_ip="$4"
  local use_shared_ca="${5:-false}"

  ensure_kernel_inotify_limits

  if kind get clusters 2>/dev/null | grep -q "^${name}$"; then
    log_info "KinD cluster '$name' already exists – preserving."
  else
    log_info "Creating KinD cluster '$name' (Pod:$pod_subnet  Svc:$svc_subnet)..."

    local extra_mounts=""
    if [ "$use_shared_ca" == "true" ]; then
      extra_mounts="  extraMounts:
  - hostPath: ${PKI_DIR}/ca.crt
    containerPath: /etc/kubernetes/pki/ca.crt
  - hostPath: ${PKI_DIR}/ca.key
    containerPath: /etc/kubernetes/pki/ca.key
  - hostPath: ${PKI_DIR}/sa.key
    containerPath: /etc/kubernetes/pki/sa.key
  - hostPath: ${PKI_DIR}/sa.pub
    containerPath: /etc/kubernetes/pki/sa.pub"
    fi

    # Mount KVM and TUN for spoke clusters to support Kata Firecracker microVMs
    if [[ "$name" =~ ^spoke ]] && [ -e "/dev/kvm" ]; then
      if [ -z "$extra_mounts" ]; then
        extra_mounts="  extraMounts:"
      fi
      extra_mounts="${extra_mounts}
  - hostPath: /dev/kvm
    containerPath: /dev/kvm
  - hostPath: /dev/net/tun
    containerPath: /dev/net/tun"
    fi

    cat > "/tmp/kind-${name}.yaml" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  podSubnet: "${pod_subnet}"
  serviceSubnet: "${svc_subnet}"
kubeadmConfigPatches:
- |
  apiVersion: kubeadm.k8s.io/v1beta3
  kind: ClusterConfiguration
  apiServer:
    certSANs:
    - "127.0.0.1"
    - "${WG_HUB1_IP}"
    - "${WG_HUB2_IP}"
    - "${WG_VIP}"
    - "kubernetes"
    - "kubernetes.default"
    - "kubernetes.default.svc"
    - "kubernetes.default.svc.cluster.local"
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 30432
    hostPort: 0
    protocol: TCP
  - containerPort: 30379
    hostPort: 0
    protocol: TCP
${extra_mounts}
EOF
    kind create cluster --name "$name" --config "/tmp/kind-${name}.yaml"
  fi

  local cname="${name}-control-plane"
  if ! docker inspect "$cname" --format '{{json .NetworkSettings.Networks}}' \
       2>/dev/null | grep -q "$TRANSIT_NET_NAME"; then
    log_info "Connecting $cname to transit net @ $transit_ip..."
    docker network connect --ip "$transit_ip" "$TRANSIT_NET_NAME" "$cname"
  fi

  log_info "Ensuring control-plane and CoreDNS are ready on $name..."
  kubectl --context "kind-${name}" wait --for=condition=Ready node "${name}-control-plane" --timeout=60s 2>/dev/null || true
  kubectl --context "kind-${name}" -n kube-system wait --for=condition=Ready pods -l k8s-app=kube-dns --timeout=60s 2>/dev/null || true
}

# Configure WireGuard wg0 inside a KinD control-plane container.
# Args: <container> <entity-name> <wg-ip> [extra-ips]
_setup_wireguard() {
  local container="$1" entity="$2" wg_ip="$3" extra_ips="${4:-}"

  log_info "Configuring WireGuard wg0 inside $container ($wg_ip)..."

  docker exec "$container" bash -c "
    if ! command -v wg >/dev/null 2>&1; then
      apt-get update -qq && apt-get install -y -qq wireguard-tools iptables >/dev/null 2>&1 || true
    fi
    mkdir -p /etc/wireguard
  "

  local addr_str="${wg_ip}/24"
  [ -n "$extra_ips" ] && addr_str="${addr_str}, ${extra_ips}"

  docker exec -i "$container" bash -c "cat > /etc/wireguard/wg0.conf" <<EOF
[Interface]
Address = ${addr_str}
ListenPort = 51820
PrivateKey = ${WG_PRIV[$entity]}

[Peer]
# Gateway / VIP
PublicKey = ${WG_PUB["gateway"]}
AllowedIPs = ${WG_GATEWAY_IP}/32, ${WG_VIP}/32
Endpoint = ${GW_TRANSIT_IP}:51820
PersistentKeepalive = 25

[Peer]
# PrimaryHub
PublicKey = ${WG_PUB["primaryhub"]}
AllowedIPs = ${WG_HUB1_IP}/32
Endpoint = ${HUB1_TRANSIT_IP}:51820
PersistentKeepalive = 25

[Peer]
# SecondaryHub
PublicKey = ${WG_PUB["secondaryhub"]}
AllowedIPs = ${WG_HUB2_IP}/32
Endpoint = ${HUB2_TRANSIT_IP}:51820
PersistentKeepalive = 25

[Peer]
# Spoke1
PublicKey = ${WG_PUB["spoke1"]}
AllowedIPs = ${WG_SPOKE1_IP}/32
Endpoint = ${SPOKE1_TRANSIT_IP}:51820
PersistentKeepalive = 25

[Peer]
# Spoke2
PublicKey = ${WG_PUB["spoke2"]}
AllowedIPs = ${WG_SPOKE2_IP}/32
Endpoint = ${SPOKE2_TRANSIT_IP}:51820
PersistentKeepalive = 25
EOF

  docker exec "$container" bash -c "
    wg-quick down wg0 2>/dev/null || true
    ip link del dev wg0 2>/dev/null || true
    systemctl enable wg-quick@wg0 2>/dev/null || true
    systemctl restart wg-quick@wg0 2>/dev/null || wg-quick up wg0
    sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
  "
}

# Poll until a pod matching <grep-pattern> is 1/1 Running.
# Args: <context> <namespace> <pod-name-pattern> [max-iterations=60] [display-label]
_wait_for_pod() {
  local ctx="$1" ns="$2" pod_grep="$3" max="${4:-60}" label="${5:-$3}"
  for i in $(seq 1 "$max"); do
    if kubectl --context "$ctx" get pod -n "$ns" 2>/dev/null \
         | grep -qE "${pod_grep}.*1/1.*Running"; then
      log_success "$label is 1/1 Running!"
      return 0
    fi
    log_info "Waiting for $label to become Ready ($i/$max)..."
    sleep 5
  done
  log_warn "$label did not become Ready within timeout – continuing anyway."
}

# Strip x-kubernetes-validations from agentgateway-crds.yaml if present to avoid CEL cost budget limits in Kubernetes 1.30+
_sanitize_agentgateway_crds() {
  local f="${CODE_INSPECTOR_DIR}/crds/agentgateway-crds.yaml"
  if [ -f "$f" ] && grep -q "x-kubernetes-validations:" "$f" 2>/dev/null; then
    log_info "Optimizing agentgateway-crds.yaml (stripping CEL rules exceeding API server cost budget)..."
    python3 -c "
import yaml
with open('$f') as fp:
    docs = list(yaml.safe_load_all(fp))
def rm_cel(obj):
    if isinstance(obj, dict):
        obj.pop('x-kubernetes-validations', None)
        for v in obj.values(): rm_cel(v)
    elif isinstance(obj, list):
        for item in obj: rm_cel(item)
for d in docs: rm_cel(d)
with open('$f', 'w') as fp:
    yaml.dump_all(docs, fp, default_flow_style=False, sort_keys=False)
" 2>/dev/null || true
  fi
}

# Verify and enforce that all required CRDs are applied and established on a cluster context.
_ensure_hub_crds() {
  local ctx="$1"
  ensure_sandbox_repo || true

  local crd_dir="${CODE_INSPECTOR_DIR}/crds"

  # Delete any blocking admission policy installed by Gateway API
  kubectl --context "$ctx" delete validatingadmissionpolicy safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true
  kubectl --context "$ctx" delete validatingadmissionpolicybinding safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true

  # 1. Gateway API CRDs (TCPRoute, HTTPRoute, Gateway, ReferenceGrant)
  if ! kubectl --context "$ctx" get crd tcproutes.gateway.networking.k8s.io >/dev/null 2>&1 || \
     ! kubectl --context "$ctx" get crd httproutes.gateway.networking.k8s.io >/dev/null 2>&1 || \
     ! kubectl --context "$ctx" get crd gateways.gateway.networking.k8s.io >/dev/null 2>&1; then
    log_info "Ensuring Gateway API CRDs (including TCPRoute) are applied on $ctx..."
    if [ -f "${crd_dir}/gateway-api-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/gateway-api-crds.yaml" >/dev/null 2>&1 || \
      kubectl --context "$ctx" apply -f "${crd_dir}/gateway-api-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # 2. AgentGateway CRDs (AgentgatewayPolicy, AgentgatewayBackend, AgentgatewayParameters)
  if ! kubectl --context "$ctx" get crd agentgatewaypolicies.agentgateway.dev >/dev/null 2>&1 || \
     ! kubectl --context "$ctx" get crd agentgatewaybackends.agentgateway.dev >/dev/null 2>&1; then
    log_info "Ensuring AgentGateway CRDs (including AgentgatewayPolicy) are applied on $ctx..."
    _sanitize_agentgateway_crds
    if [ -f "${crd_dir}/agentgateway-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts --field-manager=crd-installer -f "${crd_dir}/agentgateway-crds.yaml" >/dev/null 2>&1 || \
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/agentgateway-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # 3. OpenSandbox CRDs (BatchSandbox, Pool)
  if ! kubectl --context "$ctx" get crd batchsandboxes.sandbox.opensandbox.io >/dev/null 2>&1 || \
     ! kubectl --context "$ctx" get crd pools.sandbox.opensandbox.io >/dev/null 2>&1; then
    log_info "Ensuring OpenSandbox CRDs are applied on $ctx..."
    if [ -f "${crd_dir}/opensandbox-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/opensandbox-crds.yaml" >/dev/null 2>&1 || \
      kubectl --context "$ctx" apply -f "${crd_dir}/opensandbox-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # 4. MetalLB CRDs (IPAddressPool, L2Advertisement)
  if ! kubectl --context "$ctx" get crd ipaddresspools.metallb.io >/dev/null 2>&1; then
    log_info "Ensuring MetalLB CRDs are applied on $ctx..."
    if [ -f "${crd_dir}/metallb-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/metallb-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # 5. CloudNativePG CRDs
  if ! kubectl --context "$ctx" get crd clusters.postgresql.cnpg.io >/dev/null 2>&1; then
    log_info "Ensuring CloudNativePG CRDs are applied on $ctx..."
    if [ -f "${crd_dir}/cloudnative-pg-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/cloudnative-pg-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # Wait for critical CRDs to become Established
  local wait_crds=(
    "agentgatewaypolicies.agentgateway.dev"
    "agentgatewaybackends.agentgateway.dev"
    "tcproutes.gateway.networking.k8s.io"
    "httproutes.gateway.networking.k8s.io"
    "gateways.gateway.networking.k8s.io"
    "clusters.postgresql.cnpg.io"
    "batchsandboxes.sandbox.opensandbox.io"
    "pools.sandbox.opensandbox.io"
  )
  for c in "${wait_crds[@]}"; do
    if kubectl --context "$ctx" get crd "$c" >/dev/null 2>&1; then
      kubectl --context "$ctx" wait --for condition=established --timeout=30s "crd/${c}" >/dev/null 2>&1 || true
    fi
  done
}

# Install CRDs on a cluster context: internet URL first, local file as fallback.
# Displays detailed information about each CRD package and verified CRDs.
# Args: <context>
_install_crds() {
  local ctx="$1"
  log_info "Installing Custom Resource Definitions (CRDs) on $ctx..."

  ensure_sandbox_repo || true

  local crd_dir="${CODE_INSPECTOR_DIR}/crds"
  local -a crd_manifests=()

  # Preferred order for CRD bundles
  local default_bundles=(
    "gateway-api-crds.yaml"
    "cloudnative-pg-crds.yaml"
    "metallb-crds.yaml"
    "sealed-secrets-crd.yaml"
    "agentgateway-crds.yaml"
    "opensandbox-crds.yaml"
  )

  # Check if directory exists and collect available YAML files in ordered sequence
  if [ -d "$crd_dir" ]; then
    for f in "${default_bundles[@]}"; do
      [ -f "${crd_dir}/$f" ] && crd_manifests+=("$f")
    done
    for f in "$crd_dir"/*.yaml; do
      if [ -f "$f" ]; then
        local bname
        bname=$(basename "$f")
        if [[ ! " ${crd_manifests[*]} " =~ " ${bname} " ]]; then
          crd_manifests+=("$bname")
        fi
      fi
    done
  fi

  # If directory has no YAML files, fallback to default known bundles
  if [ ${#crd_manifests[@]} -eq 0 ]; then
    crd_manifests=("${default_bundles[@]}")
  fi

  # Apply each CRD bundle with full information
  for fname in "${crd_manifests[@]}"; do
    local title="${CRD_NAMES[$fname]:-$fname}"
    local details="${CRD_DETAILS[$fname]:-}"
    local url="${CRD_URLS[$fname]:-}"
    local local_file="${crd_dir}/${fname}"
    local applied=false

    echo -e "\n  ${CYAN}▸ [CRD Package] ${BOLD}${title}${NC} (${fname})"
    if [ -n "$details" ]; then
      echo -e "    ${BOLD}CRDs included:${NC} ${details}"
    fi

    # 1. Try internet upstream URL if configured
    if [ -n "$url" ]; then
      log_info "    Fetching from upstream URL..."
      if curl -fsSL --connect-timeout 5 --max-time 15 "$url" -o /tmp/_crd_dl.yaml 2>/dev/null; then
        if kubectl --context "$ctx" apply --server-side --force-conflicts -f /tmp/_crd_dl.yaml >/dev/null 2>&1; then
          log_success "    Applied ${title} from upstream repository."
          applied=true
        fi
      fi
      if [ "$applied" = false ]; then
        log_warn "    Upstream download unavailable – falling back to local copy..."
      fi
    fi

    # 2. Local copy fallback
    if [ "$applied" = false ]; then
      if [ -f "$local_file" ]; then
        log_info "    Applying local manifest: ${local_file}"
        if kubectl --context "$ctx" apply --server-side --force-conflicts --field-manager=crd-installer -f "$local_file" >/dev/null 2>&1 || \
           kubectl --context "$ctx" apply --server-side --force-conflicts -f "$local_file" >/dev/null 2>&1; then
          log_success "    Applied ${title} from local manifest."
          applied=true
        else
          log_warn "    Server-side apply warning; retrying standard apply..."
          kubectl --context "$ctx" apply -f "$local_file" >/dev/null 2>&1 || true
          applied=true
        fi
      else
        log_error "    Manifest not found at ${local_file} and no upstream available!"
      fi
    fi
  done

  # Remove safe-upgrades validating admission policy if installed by upstream gateway-api.
  # This prevents Helm or local tools from being blocked by version admission checks.
  kubectl --context "$ctx" delete validatingadmissionpolicy safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true
  kubectl --context "$ctx" delete validatingadmissionpolicybinding safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true

  # Ensure critical CRDs are established
  _ensure_hub_crds "$ctx"

  # ── Summary of all CRDs actually installed and established on the cluster ──
  echo -e "\n  ${GREEN}${BOLD}Established CRDs on cluster [${ctx}]:${NC}"
  local crd_table
  crd_table=$(kubectl --context "$ctx" get crds --no-headers -o custom-columns='NAME:.metadata.name,GROUP:.spec.group' 2>/dev/null | sort || true)
  if [ -n "$crd_table" ]; then
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      local cname cgroup
      cname=$(echo "$line" | awk '{print $1}')
      cgroup=$(echo "$line" | awk '{print $2}')
      printf "    %-52s %s\n" "${cname}" "(${cgroup})"
    done <<< "$crd_table"
    local total_count
    total_count=$(echo "$crd_table" | wc -l)
    log_success "Total ${total_count} CRDs successfully established on ${ctx}.\n"
  else
    log_warn "No CRDs detected yet on ${ctx}."
  fi
}

# ==============================================================================
# ── TEARDOWN ───────────────────────────────────────────────────────────────────
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

# ==============================================================================
# ── VERIFICATION ───────────────────────────────────────────────────────────────
# ==============================================================================
run_verification() {
  log_step "End-to-End Health Verification"

  echo -e "\n${BOLD}[1a] OCM Managed Clusters – PrimaryHub:${NC}"
  kubectl --context kind-primaryhub get managedclusters 2>/dev/null || true

  echo -e "\n${BOLD}[1b] OCM Managed Clusters – SecondaryHub (Standby):${NC}"
  kubectl --context kind-secondaryhub get managedclusters 2>/dev/null || true

  echo -e "\n${BOLD}[2] PostgreSQL Replication Sender (PrimaryHub):${NC}"
  kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
    -c postgres -- psql -U postgres -d apikeys \
    -c "SELECT client_addr,application_name,state,sync_state FROM pg_stat_replication;" \
    2>/dev/null || true

  echo -e "\n${BOLD}[3] PostgreSQL WAL Receiver (SecondaryHub):${NC}"
  kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
    -c postgres -- psql -U postgres -d apikeys \
    -c "SELECT status,sender_host,sender_port,latest_end_lsn FROM pg_stat_wal_receiver;" \
    2>/dev/null || true

  echo -e "\n${BOLD}[4] Valkey Replication (SecondaryHub):${NC}"
  kubectl --context kind-secondaryhub exec -n opensandbox-system deploy/valkey -- \
    valkey-cli info replication | grep -E "role|master_host|master_port|master_link_status" \
    2>/dev/null || true

  echo -e "\n${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "${GREEN}${BOLD}  MULTI-CLUSTER HEALTH VERIFICATION COMPLETE${NC}"
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}\n"
}

# ==============================================================================
# ══ PHASE FUNCTIONS ════════════════════════════════════════════════════════════
# ==============================================================================

# ── Helper: Ensure Docker daemon is active and socket is accessible ────────────
ensure_docker_access() {
  command -v docker >/dev/null 2>&1 || return 0

  if ! docker info >/dev/null 2>&1; then
    local SUDO=""
    if [ "$EUID" -ne 0 ]; then
      command -v sudo >/dev/null 2>&1 && SUDO="sudo"
    fi

    if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
      $SUDO systemctl enable --now docker 2>/dev/null || true
      $SUDO systemctl start docker 2>/dev/null || true
      $SUDO service docker start 2>/dev/null || true

      # Wait up to 10s for /var/run/docker.sock to appear
      local _w=0
      while [ ! -S /var/run/docker.sock ] && [ $_w -lt 20 ]; do
        sleep 0.5
        _w=$((_w + 1))
      done

      # Add user to docker group permanently
      $SUDO usermod -aG docker "$USER" 2>/dev/null || true

      # Grant immediate read/write access to docker.sock for the current running session
      if [ -S /var/run/docker.sock ]; then
        $SUDO chmod 666 /var/run/docker.sock 2>/dev/null || true
        command -v setfacl >/dev/null 2>&1 && $SUDO setfacl -m u:"$USER":rw /var/run/docker.sock 2>/dev/null || true
      fi
    fi
  fi
}

# ── Helper: Apply detected repository path to dependent directories ───────────
_apply_repo_paths() {
  local base="$1"
  SANDBOX_REPO_DIR="$base"
  OPENSANDBOX_BUILD_DIR="${base}/opensandbox-server/docker-build"
  CODE_INSPECTOR_DIR="${base}/codeInspector"

  # Ensure the repository is checked out to branch feat/production
  if [ -d "${base}/.git" ]; then
    local current_branch
    current_branch=$(git -C "$base" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ "$current_branch" != "feat/production" ]; then
      log_info "Switching 01-Sandbox repository from '$current_branch' to branch 'feat/production'..."
      git -C "$base" checkout feat/production 2>/dev/null || \
      git -C "$base" checkout -b feat/production origin/feat/production 2>/dev/null || \
      log_warn "Could not switch to feat/production; remaining on $current_branch."
    fi
  fi

  # Ensure ConfigMap template handles boolean values as strings gracefully
  local cmap_tmpl="${CODE_INSPECTOR_DIR}/charts/apiServer/templates/configmap.yaml"
  if [ -f "$cmap_tmpl" ]; then
    sed -i 's/tpl \$value \$/tpl (\$value | toString) \$/g' "$cmap_tmpl" 2>/dev/null || true
  fi
}

# ── Helper: Ensure 01-Sandbox repository is cloned or located ──────────────────
ensure_sandbox_repo() {
  # 1. If already valid, return
  if [ -d "${OPENSANDBOX_BUILD_DIR}" ] && [ -f "${OPENSANDBOX_BUILD_DIR}/Dockerfile" ]; then
    _apply_repo_paths "$SANDBOX_REPO_DIR"
    return 0
  fi

  # 2. Search common candidate locations
  local candidates=(
    "${ROOT_DIR}/01-Sandbox"
    "${ROOT_DIR}"
    "$(pwd)/01-Sandbox"
    "$(pwd)"
  )

  for cand in "${candidates[@]}"; do
    if [ -d "${cand}/opensandbox-server/docker-build" ] && [ -f "${cand}/opensandbox-server/docker-build/Dockerfile" ]; then
      log_info "Detected 01-Sandbox repository at: ${cand}"
      _apply_repo_paths "$cand"
      return 0
    fi
  done

  # 3. Search under parent and user home
  local found
  found=$(find "${ROOT_DIR}" "${HOME}" -maxdepth 4 -type d -path "*/opensandbox-server/docker-build" 2>/dev/null | head -1 || true)
  if [ -n "$found" ] && [ -f "${found}/Dockerfile" ]; then
    local repo_base
    repo_base="$(dirname "$(dirname "$found")")"
    log_info "Discovered 01-Sandbox repository at: ${repo_base}"
    _apply_repo_paths "$repo_base"
    return 0
  fi

  # 4. Clone repository if not found locally
  log_step "Cloning 01-Sandbox Repository"
  local clone_target="${ROOT_DIR}/01-Sandbox"
  [ -d "$clone_target" ] && clone_target="${STATE_DIR}/01-Sandbox"

  local repo_url="${REPO_URL:-git@github.com:01cloud/01-Sandbox.git}"
  local repo_branch="${REPO_BRANCH:-feat/production}"

  log_info "Cloning 01-Sandbox via SSH (${repo_url}, branch: ${repo_branch}) into: ${clone_target}..."
  mkdir -p "$(dirname "$clone_target")"
  # Remove incomplete target directory if it exists without the project content
  [ -d "$clone_target" ] && [ ! -d "${clone_target}/opensandbox-server" ] && rm -rf "$clone_target"

  local clone_ok=false
  # Clone via SSH (try feat/production branch first, fallback to default branch)
  if GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --depth 1 -b "$repo_branch" "$repo_url" "$clone_target" 2>/dev/null || \
     GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --depth 1 "$repo_url" "$clone_target"; then
    clone_ok=true
  fi

  if [ "$clone_ok" = true ] && [ -d "${clone_target}/opensandbox-server/docker-build" ]; then
    log_success "01-Sandbox repository cloned successfully to: ${clone_target}"
    _apply_repo_paths "$clone_target"
    return 0
  else
    log_error "Unable to locate or clone 01-Sandbox repository via SSH into ${clone_target}."
    log_error "Please ensure your SSH key is added to GitHub (ssh -T git@github.com) or clone manually:"
    log_error "  git clone git@github.com:01cloud/01-Sandbox.git ${clone_target}"
    return 1
  fi
}

# ── Phase 1: Pre-flight toolchain check + auto-install ────────────────────────
phase_01_preflight() {
  log_step "PHASE 1: Checking Host Toolchain (Auto-Install if Missing)"

  # ── Detect package manager ──────────────────────────────────────────────────
  local PKG_MGR=""
  if command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
  elif command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
  elif command -v yum >/dev/null 2>&1; then
    PKG_MGR="yum"
  else
    log_warn "No supported package manager found (apt/dnf/yum). Will attempt binary installs only."
  fi

  # ── Ensure sudo is usable ───────────────────────────────────────────────────
  local SUDO=""
  if [ "$EUID" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
      SUDO="sudo"
    else
      log_warn "Not running as root and 'sudo' not found – installations may fail."
    fi
  fi

  # Helper: refresh apt cache once if needed
  local _apt_updated=false
  _apt_update_once() {
    if [ "$_apt_updated" = false ] && [ "$PKG_MGR" = "apt" ]; then
      log_info "Updating apt package index..."
      $SUDO apt-get update -qq
      _apt_updated=true
    fi
  }

  # ── install_docker ──────────────────────────────────────────────────────────
  _install_docker() {
    log_info "Installing Docker Engine..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq ca-certificates gnupg lsb-release curl >/dev/null 2>&1
      $SUDO install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
      $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
        $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
      $SUDO apt-get update -qq
      $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q yum-utils >/dev/null
      $SUDO yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo >/dev/null
      $SUDO "$PKG_MGR" install -y -q docker-ce docker-ce-cli containerd.io >/dev/null
    else
      log_warn "Cannot install Docker automatically. Please install Docker manually: https://docs.docker.com/engine/install/"
      return 1
    fi
    $SUDO systemctl enable --now docker 2>/dev/null || true
    $SUDO systemctl start docker 2>/dev/null || true
    $SUDO service docker start 2>/dev/null || true

    # Wait for docker socket to appear
    local _w=0
    while [ ! -S /var/run/docker.sock ] && [ $_w -lt 20 ]; do
      sleep 0.5
      _w=$((_w + 1))
    done

    # Allow current user to use docker permanently
    $SUDO usermod -aG docker "$USER" 2>/dev/null || true

    # Make socket immediately accessible for the current running process
    if [ -S /var/run/docker.sock ]; then
      $SUDO chmod 666 /var/run/docker.sock 2>/dev/null || true
      command -v setfacl >/dev/null 2>&1 && $SUDO setfacl -m u:"$USER":rw /var/run/docker.sock 2>/dev/null || true
    fi
    log_success "Docker installed."
  }

  # ── install_kind ────────────────────────────────────────────────────────────
  _install_kind() {
    log_info "Installing KinD (Kubernetes in Docker)..."
    local arch
    arch=$(uname -m)
    case "$arch" in
      x86_64)  arch="amd64" ;;
      aarch64) arch="arm64" ;;
      *)        log_warn "Unsupported arch $arch for KinD"; return 1 ;;
    esac
    local kind_version
    kind_version=$(curl -fsSL https://api.github.com/repos/kubernetes-sigs/kind/releases/latest \
      | grep '"tag_name"' | cut -d'"' -f4)
    kind_version="${kind_version:-v0.23.0}"
    curl -fsSL "https://kind.sigs.k8s.io/dl/${kind_version}/kind-linux-${arch}" \
      -o /tmp/kind-bin
    chmod +x /tmp/kind-bin
    $SUDO mv /tmp/kind-bin /usr/local/bin/kind
    log_success "KinD ${kind_version} installed."
  }

  # ── install_kubectl ─────────────────────────────────────────────────────────
  _install_kubectl() {
    log_info "Installing kubectl..."
    local arch
    arch=$(uname -m); [ "$arch" = "x86_64" ] && arch="amd64" || arch="arm64"
    local k8s_version
    k8s_version=$(curl -fsSL https://dl.k8s.io/release/stable.txt)
    k8s_version="${k8s_version:-v1.30.0}"
    curl -fsSL "https://dl.k8s.io/release/${k8s_version}/bin/linux/${arch}/kubectl" \
      -o /tmp/kubectl-bin
    chmod +x /tmp/kubectl-bin
    $SUDO mv /tmp/kubectl-bin /usr/local/bin/kubectl
    log_success "kubectl ${k8s_version} installed."
  }

  # ── install_helm ────────────────────────────────────────────────────────────
  _install_helm() {
    log_info "Installing Helm v3..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | \
      $SUDO bash >/dev/null 2>&1
    log_success "Helm installed ($(helm version --short 2>/dev/null || echo 'ok'))."
  }

  # ── install_clusteradm ──────────────────────────────────────────────────────
  _install_clusteradm() {
    log_info "Installing clusteradm (OCM CLI)..."
    curl -fsSL https://raw.githubusercontent.com/open-cluster-management-io/clusteradm/main/install.sh | \
      $SUDO bash >/dev/null 2>&1
    # Fallback: manual binary download if the installer script fails
    if ! command -v clusteradm >/dev/null 2>&1; then
      local arch
      arch=$(uname -m); [ "$arch" = "x86_64" ] && arch="amd64" || arch="arm64"
      local ver
      ver=$(curl -fsSL https://api.github.com/repos/open-cluster-management-io/clusteradm/releases/latest \
        | grep '"tag_name"' | cut -d'"' -f4)
      ver="${ver:-v0.7.0}"
      curl -fsSL "https://github.com/open-cluster-management-io/clusteradm/releases/download/${ver}/clusteradm_linux_${arch}.tar.gz" \
        | $SUDO tar -xz -C /usr/local/bin clusteradm
    fi
    log_success "clusteradm installed."
  }

  # ── install_jq ──────────────────────────────────────────────────────────────
  _install_jq() {
    log_info "Installing jq..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq jq >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q jq >/dev/null
    else
      local arch
      arch=$(uname -m); [ "$arch" = "x86_64" ] && arch="amd64" || arch="arm64"
      local ver
      ver=$(curl -fsSL https://api.github.com/repos/jqlang/jq/releases/latest \
        | grep '"tag_name"' | cut -d'"' -f4)
      ver="${ver:-jq-1.7.1}"
      curl -fsSL "https://github.com/jqlang/jq/releases/download/${ver}/jq-linux-${arch}" \
        -o /tmp/jq-bin
      chmod +x /tmp/jq-bin
      $SUDO mv /tmp/jq-bin /usr/local/bin/jq
    fi
    log_success "jq installed."
  }

  # ── install_curl ────────────────────────────────────────────────────────────
  _install_curl() {
    log_info "Installing curl..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq curl >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q curl >/dev/null
    else
      log_warn "Please install curl manually."
      return 1
    fi
    log_success "curl installed."
  }

  # ── install_wireguard ───────────────────────────────────────────────────────
  _install_wireguard() {
    log_info "Installing WireGuard tools..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq wireguard wireguard-tools >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q wireguard-tools >/dev/null
    else
      log_warn "Please install wireguard-tools manually."
      return 1
    fi
    log_success "WireGuard tools installed."
  }

  # ── install_git ─────────────────────────────────────────────────────────────
  _install_git() {
    log_info "Installing git..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq git >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q git >/dev/null
    else
      log_warn "Please install git manually."
      return 1
    fi
    log_success "git installed."
  }

  # ── Dispatch: check + install each tool ────────────────────────────────────
  local failed=()

  _check_and_install() {
    local tool="$1"
    local installer="$2"
    if ! command -v "$tool" >/dev/null 2>&1; then
      log_warn "'$tool' not found – attempting automatic installation..."
      if $installer; then
        if command -v "$tool" >/dev/null 2>&1; then
          log_success "'$tool' is now available."
        else
          log_error "Installation of '$tool' completed but binary not found in PATH."
          failed+=("$tool")
        fi
      else
        log_error "Failed to install '$tool' automatically."
        failed+=("$tool")
      fi
    else
      log_info "'$tool' already installed: $(command -v "$tool")"
    fi
  }

  _check_and_install git         _install_git
  _check_and_install docker      _install_docker
  _check_and_install kind        _install_kind
  _check_and_install kubectl     _install_kubectl
  _check_and_install helm        _install_helm
  _check_and_install clusteradm  _install_clusteradm
  _check_and_install jq          _install_jq
  _check_and_install curl        _install_curl
  _check_and_install wg          _install_wireguard

  if [ ${#failed[@]} -gt 0 ]; then
    log_error "The following tools could not be installed automatically: ${failed[*]}"
    log_error "Please install them manually and re-run the script."
    exit 1
  fi

  log_success "All required tools are available."

  # ── Ensure Docker daemon is running & socket accessible to current user ────
  ensure_docker_access
  if ! docker info >/dev/null 2>&1; then
    log_error "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."
    log_error "Permission denied or Docker daemon is not active."
    log_error "Please run: sudo chmod 666 /var/run/docker.sock && sudo systemctl start docker"
    exit 1
  fi
  log_success "Docker daemon is running and accessible."

  # ── Ensure 01-Sandbox repository is available locally ──────────────────────
  ensure_sandbox_repo || log_warn "Repository clone pending; will retry in Phase 10."

  # Load WireGuard kernel module (non-fatal; may be built-in)
  $SUDO modprobe wireguard 2>/dev/null || modprobe wireguard 2>/dev/null || true

  # Ensure kernel inotify limits for multi-cluster KinD
  ensure_kernel_inotify_limits
}

# ── Phase 2: Transit network + WireGuard key generation ────────────────────────
phase_02_transit_network_and_wg_keys() {
  log_step "PHASE 2: Transit Network & WireGuard Key Generation"

  if ! docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$"; then
    log_info "Creating Docker transit network ($TRANSIT_SUBNET)..."
    docker network create \
      --driver bridge \
      --subnet "$TRANSIT_SUBNET" \
      --opt "com.docker.network.bridge.name"="br-01transit" \
      "$TRANSIT_NET_NAME"
  else
    log_info "Transit network '$TRANSIT_NET_NAME' already exists."
  fi

  for entity in gateway primaryhub secondaryhub spoke1 spoke2; do
    if [ ! -s "${WG_DIR}/${entity}.key" ] || [ ! -s "${WG_DIR}/${entity}.pub" ]; then
      log_info "Generating WireGuard keypair for $entity..."
      read -r priv pub < <(python3 -c "
from cryptography.hazmat.primitives.asymmetric import x25519
import base64
k = x25519.X25519PrivateKey.generate()
print(f'{base64.b64encode(k.private_bytes_raw()).decode()} {base64.b64encode(k.public_key().public_bytes_raw()).decode()}')
")
      echo "$priv" > "${WG_DIR}/${entity}.key"
      echo "$pub"  > "${WG_DIR}/${entity}.pub"
    fi
    WG_PRIV[$entity]=$(tr -d '\r\n' < "${WG_DIR}/${entity}.key")
    WG_PUB[$entity]=$(tr -d '\r\n'  < "${WG_DIR}/${entity}.pub")
  done

  log_success "WireGuard keypairs ready for all 5 entities."
}

# ── Phase 3: Create hub clusters (PrimaryHub + SecondaryHub) ───────────────────
phase_03_create_hub_clusters() {
  log_step "PHASE 3: Creating Hub Clusters (PrimaryHub + SecondaryHub)"

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

# ── Phase 4: Install CRDs on hub clusters ─────────────────────────────────────
phase_04_install_crds_on_hubs() {
  log_step "PHASE 4: Installing CRDs on Hub Clusters"

  ensure_sandbox_repo || log_warn "Repository clone pending; local CRDs may be deferred."

  for hub in primaryhub secondaryhub; do
    _install_crds "kind-${hub}"
  done
  log_success "CRDs installed on all hub clusters."
}

# ── Phase 5: WireGuard overlay on hub clusters ────────────────────────────────
phase_05_wireguard_on_hubs() {
  log_step "PHASE 5: Bringing Up WireGuard Overlay on Hub Clusters"

  _setup_wireguard "primaryhub-control-plane"   "primaryhub"   "$WG_HUB1_IP"
  _setup_wireguard "secondaryhub-control-plane" "secondaryhub" "$WG_HUB2_IP"

  log_success "WireGuard overlay active on hub clusters."
}

# ── Phase 6: Envoy Gateway VIP container ──────────────────────────────────────
phase_06_envoy_gateway() {
  log_step "PHASE 6: Deploying Envoy Gateway (VIP: ${WG_VIP})"

  # Static part (no variable substitution needed)
  cat > "${ENVOY_DIR}/envoy.yaml" <<'ENVOY_EOF'
admin:
  address:
    socket_address:
      protocol: TCP
      address: 127.0.0.1
      port_value: 9901
static_resources:
  listeners:
  - name: ingress_http_listener
    address:
      socket_address:
        protocol: TCP
        address: 0.0.0.0
        port_value: 80
    filter_chains:
    - filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: ingress_http
          codec_type: AUTO
          stream_idle_timeout: 15s
          route_config:
            name: ingress_http_route
            virtual_hosts:
            - name: backend
              domains: ["*"]
              routes:
              - match:
                  prefix: "/"
                route:
                  cluster: ingress_http_cluster
                  timeout: 120s
                  upgrade_configs:
                  - upgrade_type: websocket
          http_filters:
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
  - name: ingress_kube_api_listener
    address:
      socket_address:
        protocol: TCP
        address: 0.0.0.0
        port_value: 6443
    filter_chains:
    - filters:
      - name: envoy.filters.network.tcp_proxy
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.tcp_proxy.v3.TcpProxy
          stat_prefix: ingress_kube_api
          cluster: ingress_kube_api_cluster
          idle_timeout: 5s
          max_downstream_connection_duration: 60s
ENVOY_EOF

  # Dynamic cluster section with IP substitution
  cat >> "${ENVOY_DIR}/envoy.yaml" <<EOF
  clusters:
  - name: ingress_http_cluster
    connect_timeout: 0.5s
    type: STATIC
    lb_policy: ROUND_ROBIN
    close_connections_on_host_health_failure: true
    health_checks:
    - timeout: 1s
      interval: 1s
      unhealthy_threshold: 2
      healthy_threshold: 2
      tcp_health_check: {}
    load_assignment:
      cluster_name: ingress_http_cluster
      endpoints:
      - priority: 0
        lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: ${HUB1_METALLB_IP}
                port_value: 80
      - priority: 1
        lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: ${HUB2_METALLB_IP}
                port_value: 80
  - name: ingress_kube_api_cluster
    connect_timeout: 0.5s
    type: STATIC
    lb_policy: ROUND_ROBIN
    close_connections_on_host_health_failure: true
    health_checks:
    - timeout: 1s
      interval: 1s
      unhealthy_threshold: 2
      healthy_threshold: 2
      tcp_health_check: {}
    load_assignment:
      cluster_name: ingress_kube_api_cluster
      endpoints:
      - priority: 0
        lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: ${WG_HUB1_IP}
                port_value: 6443
      - priority: 1
        lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: ${WG_HUB2_IP}
                port_value: 6443
EOF

  cat > "${ENVOY_DIR}/wg0.conf" <<EOF
[Interface]
Address = ${WG_GATEWAY_IP}/24, ${WG_VIP}/32
ListenPort = 51820
PrivateKey = ${WG_PRIV["gateway"]}

[Peer]
PublicKey = ${WG_PUB["primaryhub"]}
AllowedIPs = ${WG_HUB1_IP}/32
Endpoint = ${HUB1_TRANSIT_IP}:51820
PersistentKeepalive = 25

[Peer]
PublicKey = ${WG_PUB["secondaryhub"]}
AllowedIPs = ${WG_HUB2_IP}/32
Endpoint = ${HUB2_TRANSIT_IP}:51820
PersistentKeepalive = 25

[Peer]
PublicKey = ${WG_PUB["spoke1"]}
AllowedIPs = ${WG_SPOKE1_IP}/32
Endpoint = ${SPOKE1_TRANSIT_IP}:51820
PersistentKeepalive = 25

[Peer]
PublicKey = ${WG_PUB["spoke2"]}
AllowedIPs = ${WG_SPOKE2_IP}/32
Endpoint = ${SPOKE2_TRANSIT_IP}:51820
PersistentKeepalive = 25
EOF

  docker rm -f envoy-gateway 2>/dev/null || true

  local envoy_image="01sandbox-envoy:v1"
  if ! docker image inspect "$envoy_image" >/dev/null 2>&1 || \
     ! docker run --rm --entrypoint which "$envoy_image" wg-quick >/dev/null 2>&1; then
    log_info "Building robust envoy-gateway image ($envoy_image) with wireguard-tools..."
    cat <<'EOF_ENVOY_DOCKER' | docker build -t "$envoy_image" -
FROM envoyproxy/envoy:v1.31-latest
USER root
RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      wireguard-tools iproute2 iptables curl procps && \
    rm -rf /var/lib/apt/lists/*
EOF_ENVOY_DOCKER
  fi

  local envoy_cmd="wg-quick up wg0 && envoy -c /etc/envoy/envoy.yaml"

  log_info "Starting envoy-gateway using $envoy_image..."
  docker run -d --name envoy-gateway \
    --restart unless-stopped \
    -p 80:80 \
    --privileged --user root \
    --cap-add=NET_ADMIN --cap-add=SYS_MODULE \
    --net "$TRANSIT_NET_NAME" --ip "$GW_TRANSIT_IP" \
    -v "${ENVOY_DIR}/envoy.yaml":/etc/envoy/envoy.yaml:ro \
    -v "${ENVOY_DIR}/wg0.conf":/etc/wireguard/wg0.conf:ro \
    --entrypoint /bin/sh \
    "$envoy_image" -c "$envoy_cmd"

  log_info "Verifying VIP ${WG_VIP} reachability from primaryhub..."
  local vip_ok=false
  for i in {1..20}; do
    if docker exec primaryhub-control-plane ping -c 1 -W 1 "$WG_VIP" >/dev/null 2>&1; then
      log_success "VIP ${WG_VIP} reachable over WireGuard overlay!"
      vip_ok=true
      break
    fi
    sleep 1
  done
  if [ "$vip_ok" = false ]; then
    log_warn "VIP ${WG_VIP} not immediately pingable from primaryhub – checking container logs..."
    docker logs --tail 20 envoy-gateway || true
  fi
}

# ── Phase 7: Verify shared Root CA + configure cluster-info VIP ───────────────
phase_07_verify_root_ca_and_vip() {
  log_step "PHASE 7: Verifying Shared Root CA & VIP TLS SANs"

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

# ── Phase 8: Initialize OCM on both hubs ──────────────────────────────────────
phase_08_ocm_init() {
  log_step "PHASE 8: Initializing OCM on PrimaryHub & SecondaryHub"

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

# ── Phase 9: Create application namespaces on both hubs ───────────────────────
phase_09_create_namespaces() {
  log_step "PHASE 9: Creating Application Namespaces on Hub Clusters"

  for hub in primaryhub secondaryhub; do
    for ns in opensandbox-system metallb-system agentgateway-system; do
      kubectl --context "kind-${hub}" create namespace "$ns" \
        --dry-run=client -o yaml | kubectl --context "kind-${hub}" apply -f -
    done
  done

  log_success "Namespaces ready on both hubs."
}

# ── Phase 10: Build & load custom opensandbox-server image ────────────────────
phase_10_load_custom_image() {
  log_step "PHASE 10: Building & Loading Custom opensandbox-server Image"

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

# ── Phase 11: PrimaryHub – PostgreSQL first, then full stack ──────────────────
phase_11_primaryhub_deploy() {
  log_step "PHASE 11a: PrimaryHub – PostgreSQL (CNPG Primary) First"

  # Ensure codeInspector repository and templates are ready
  ensure_sandbox_repo
  local cmap_tmpl="${CODE_INSPECTOR_DIR}/charts/apiServer/templates/configmap.yaml"
  if [ -f "$cmap_tmpl" ]; then
    sed -i 's/tpl \$value \$/tpl (\$value | toString) \$/g' "$cmap_tmpl" 2>/dev/null || true
  fi

  # Ensure all necessary CRDs (Gateway API, AgentGateway, CNPG, etc.) are established
  _ensure_hub_crds "kind-primaryhub"
  _relax_webhook_failure_policy "kind-primaryhub"

  log_info "Using codeInspector directory: ${CODE_INSPECTOR_DIR}"
  log_info "Deploying CNPG operator + PostgreSQL primary on primaryhub..."
  helm upgrade --install codeinspector "${CODE_INSPECTOR_DIR}" \
    --skip-crds \
    --kube-context kind-primaryhub \
    --namespace opensandbox-system \
    --create-namespace \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --set global.ocm.enabled=false \
    --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
    --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
    --set agentgateway.enabled=false \
    --set "agentgateway-controller.enabled=false" \
    --set apiServer.enabled=true \
    --set apiServer.deployment.replicaCount=0 \
    --set apiServer.rabbitmq.enabled=false \
    --set apiServer.failoverController.enabled=false \
    --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
    --set opensandbox.enabled=false \
    --set metallb.enabled=false \
    --set "sealed-secrets.enabled=false" \
    --set prometheus.enabled=false \
    --set grafana.enabled=false \
    --set opensandboxResourcePool.enabled=false \
    --timeout 5m

  log_info "Waiting for postgresql-primary-1 to be Ready before deploying full stack..."
  _wait_for_pod "kind-primaryhub" "opensandbox-system" "postgresql-primary-1" 60 "postgresql-primary-1"

  log_step "PHASE 11b: PrimaryHub – Full Stack (agentgateway, apiServer, opensandbox, metallb…)"

  # Ensure AgentgatewayPolicy and all other required CRDs are installed and established
  _ensure_hub_crds "kind-primaryhub"
  _relax_webhook_failure_policy "kind-primaryhub"
  if ! kubectl --context kind-primaryhub get crd agentgatewaypolicies.agentgateway.dev >/dev/null 2>&1; then
    log_warn "Explicitly applying agentgateway-crds.yaml on kind-primaryhub..."
    _sanitize_agentgateway_crds
    kubectl --context kind-primaryhub apply --server-side --force-conflicts --field-manager=crd-installer -f "${CODE_INSPECTOR_DIR}/crds/agentgateway-crds.yaml"
    kubectl --context kind-primaryhub wait --for condition=established --timeout=60s crd/agentgatewaypolicies.agentgateway.dev
  fi

  helm upgrade --install codeinspector "${CODE_INSPECTOR_DIR}" \
    --skip-crds \
    --kube-context kind-primaryhub \
    --namespace opensandbox-system \
    --create-namespace \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
    --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
    --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
    --timeout 10m

  kubectl --context kind-primaryhub rollout status deployment/valkey \
    -n opensandbox-system --timeout=120s || true
  kubectl --context kind-primaryhub rollout status deployment/sandbox-api \
    -n opensandbox-system --timeout=120s || true
  kubectl --context kind-primaryhub rollout status deployment/opensandbox-server \
    -n opensandbox-system --timeout=120s || true

  log_success "PrimaryHub full stack deployed."
}

# ── Phase 12: SecondaryHub – PostgreSQL first, then full stack ────────────────
phase_12_secondaryhub_deploy() {
  log_step "PHASE 12a: SecondaryHub – PostgreSQL (CNPG Standby) First"

  # Ensure codeInspector repository and templates are ready
  ensure_sandbox_repo
  local cmap_tmpl="${CODE_INSPECTOR_DIR}/charts/apiServer/templates/configmap.yaml"
  if [ -f "$cmap_tmpl" ]; then
    sed -i 's/tpl \$value \$/tpl (\$value | toString) \$/g' "$cmap_tmpl" 2>/dev/null || true
  fi

  # Ensure all necessary CRDs are established
  _ensure_hub_crds "kind-secondaryhub"
  _relax_webhook_failure_policy "kind-secondaryhub"

  log_info "Using codeInspector directory: ${CODE_INSPECTOR_DIR}"
  log_info "Deploying CNPG standby on secondaryhub (WAL from ${WG_HUB1_IP}:30432)..."
  helm upgrade --install codeinspector "${CODE_INSPECTOR_DIR}" \
    --skip-crds \
    --kube-context kind-secondaryhub \
    --namespace opensandbox-system \
    --create-namespace \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --values "${CODE_INSPECTOR_DIR}/values-secondary.yaml" \
    --set global.ocm.enabled=false \
    --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
    --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
    --set agentgateway.enabled=false \
    --set "agentgateway-controller.enabled=false" \
    --set apiServer.enabled=true \
    --set apiServer.deployment.replicaCount=0 \
    --set apiServer.rabbitmq.enabled=false \
    --set apiServer.failoverController.enabled=false \
    --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
    --set apiServer.valkey.replication.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.valkey.replication.primaryPort=30379 \
    --set apiServer.cnpg.replication.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.cnpg.replication.primaryPort=30432 \
    --set apiServer.failoverController.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.failoverController.primaryPort=30432 \
    --set opensandbox.enabled=false \
    --set metallb.enabled=false \
    --set "sealed-secrets.enabled=false" \
    --set prometheus.enabled=false \
    --set grafana.enabled=false \
    --set opensandboxResourcePool.enabled=false \
    --timeout 5m

  log_info "Waiting for postgresql-secondary-1 to be Ready before deploying full stack..."
  _wait_for_pod "kind-secondaryhub" "opensandbox-system" "postgresql-secondary-1" 60 "postgresql-secondary-1"

  log_step "PHASE 12b: SecondaryHub – Full Stack + Failover Controller"

  # Ensure AgentgatewayPolicy and all other required CRDs are installed and established
  _ensure_hub_crds "kind-secondaryhub"
  _relax_webhook_failure_policy "kind-secondaryhub"
  if ! kubectl --context kind-secondaryhub get crd agentgatewaypolicies.agentgateway.dev >/dev/null 2>&1; then
    log_warn "Explicitly applying agentgateway-crds.yaml on kind-secondaryhub..."
    _sanitize_agentgateway_crds
    kubectl --context kind-secondaryhub apply --server-side --force-conflicts --field-manager=crd-installer -f "${CODE_INSPECTOR_DIR}/crds/agentgateway-crds.yaml"
    kubectl --context kind-secondaryhub wait --for condition=established --timeout=60s crd/agentgatewaypolicies.agentgateway.dev
  fi

  helm upgrade --install codeinspector "${CODE_INSPECTOR_DIR}" \
    --skip-crds \
    --kube-context kind-secondaryhub \
    --namespace opensandbox-system \
    --create-namespace \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --values "${CODE_INSPECTOR_DIR}/values-secondary.yaml" \
    --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
    --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
    --set apiServer.valkey.replication.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.valkey.replication.primaryPort=30379 \
    --set apiServer.cnpg.replication.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.cnpg.replication.primaryPort=30432 \
    --set apiServer.failoverController.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.failoverController.primaryPort=30432 \
    --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
    --timeout 10m

  kubectl --context kind-secondaryhub rollout status deployment/valkey \
    -n opensandbox-system --timeout=120s || true
  kubectl --context kind-secondaryhub rollout status deployment/sandbox-api \
    -n opensandbox-system --timeout=120s || true
  kubectl --context kind-secondaryhub rollout status deployment/opensandbox-server \
    -n opensandbox-system --timeout=120s || true
  kubectl --context kind-secondaryhub rollout status deployment/ocm-failover-controller \
    -n opensandbox-system --timeout=120s || true

  # Pre-render secondary CNPG re-clone manifest for in-controller failback
  mkdir -p "$SEC_DIR"
  helm template codeinspector "${CODE_INSPECTOR_DIR}" \
    -s charts/apiServer/templates/cnpg-cluster.yaml \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --values "${CODE_INSPECTOR_DIR}/values-secondary.yaml" \
    --set apiServer.cnpg.replication.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.cnpg.replication.primaryPort=30432 \
    --set apiServer.valkey.replication.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.valkey.replication.primaryPort=30379 \
    --set apiServer.failoverController.primaryHost="${WG_HUB1_IP}" \
    --set apiServer.failoverController.primaryPort=30432 \
    > "${SEC_DIR}/postgresql-secondary-cluster.yaml"
  docker cp "${SEC_DIR}/postgresql-secondary-cluster.yaml" \
    secondaryhub-control-plane:/root/postgresql-secondary-cluster.yaml 2>/dev/null || true

  log_success "SecondaryHub full stack deployed."
}

# ── Phase 13: Create spoke clusters ───────────────────────────────────────────
phase_13_create_spoke_clusters() {
  log_step "PHASE 13: Creating Spoke Clusters"

  _create_kind_cluster "spoke1" "10.246.0.0/16" "10.98.0.0/16"  "$SPOKE1_TRANSIT_IP" "false"
  _create_kind_cluster "spoke2" "10.247.0.0/16" "10.100.0.0/16" "$SPOKE2_TRANSIT_IP" "false"

  log_success "Spoke clusters created."
}

# ── Phase 14: WireGuard on spoke clusters (+ hub re-apply with spoke peers) ───
phase_14_wireguard_on_all_clusters() {
  log_step "PHASE 14: WireGuard on All Clusters (Spokes + Hub Re-apply)"

  _setup_wireguard "spoke1-control-plane" "spoke1" "$WG_SPOKE1_IP"
  _setup_wireguard "spoke2-control-plane" "spoke2" "$WG_SPOKE2_IP"

  # Re-apply on hubs so their wg0.conf now includes the spoke [Peer] entries
  log_info "Re-applying WireGuard on hubs (spoke peers now included)..."
  _setup_wireguard "primaryhub-control-plane"   "primaryhub"   "$WG_HUB1_IP"
  _setup_wireguard "secondaryhub-control-plane" "secondaryhub" "$WG_HUB2_IP"

  log_success "WireGuard overlay peer-complete on all 4 clusters."
}

# ── Phase 15: Install CRDs on spoke clusters ──────────────────────────────────
phase_15_install_crds_on_spokes() {
  log_step "PHASE 15: Installing CRDs on Spoke Clusters"

  ensure_sandbox_repo || log_warn "Repository clone pending; local CRDs may be deferred."

  for spoke in spoke1 spoke2; do
    _install_crds "kind-${spoke}"
  done
  log_success "CRDs installed on all spoke clusters."
}

# ── Phase 15-B: Setup Kata Firecracker (kata-fc) Runtime on Spokes ────────────
_ensure_kata_host_assets() {
  mkdir -p "${KATA_CACHE_DIR}"

  # 1. Prefer existing host /opt/kata if complete
  if [ -f "/opt/kata/bin/containerd-shim-kata-v2" ] && \
     [ -f "/opt/kata/bin/firecracker" ] && \
     [ -f "/opt/kata/share/kata-containers/vmlinux.container" ]; then
    log_info "Reusing existing host Kata assets from /opt/kata."
    KATA_SOURCE_DIR="/opt/kata"
    return 0
  fi

  # 2. Otherwise download static release tarball into cache
  KATA_SOURCE_DIR="${KATA_CACHE_DIR}/opt/kata"
  local kata_tar="${KATA_CACHE_DIR}/kata-static-${KATA_VERSION}-amd64.tar.xz"
  if [ ! -f "$kata_tar" ]; then
    log_info "Downloading Kata Containers static release v${KATA_VERSION}..."
    curl -fSL "https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/kata-static-${KATA_VERSION}-amd64.tar.xz" \
      -o "$kata_tar"
  fi

  if [ ! -f "${KATA_SOURCE_DIR}/bin/containerd-shim-kata-v2" ]; then
    log_info "Extracting Kata static binaries into cache..."
    mkdir -p "${KATA_CACHE_DIR}/extract"
    tar -xJf "$kata_tar" -C "${KATA_CACHE_DIR}/extract"
    mkdir -p "${KATA_SOURCE_DIR}"
    cp -r "${KATA_CACHE_DIR}/extract/opt/kata/"* "${KATA_SOURCE_DIR}/"
    rm -rf "${KATA_CACHE_DIR}/extract"
  fi

  # 3. Ensure firecracker is present
  if [ ! -f "${KATA_SOURCE_DIR}/bin/firecracker" ]; then
    log_info "Downloading Firecracker binary ${FIRECRACKER_VERSION}..."
    local fc_tar="${KATA_CACHE_DIR}/firecracker-${FIRECRACKER_VERSION}-x86_64.tgz"
    if [ ! -f "$fc_tar" ]; then
      curl -fSL "https://github.com/firecracker-microvm/firecracker/releases/download/${FIRECRACKER_VERSION}/firecracker-${FIRECRACKER_VERSION}-x86_64.tgz" \
        -o "$fc_tar"
    fi
    tar -xzf "$fc_tar" -C "${KATA_CACHE_DIR}"
    cp "${KATA_CACHE_DIR}/release-${FIRECRACKER_VERSION}-x86_64/firecracker-${FIRECRACKER_VERSION}-x86_64" "${KATA_SOURCE_DIR}/bin/firecracker"
    cp "${KATA_CACHE_DIR}/release-${FIRECRACKER_VERSION}-x86_64/jailer-${FIRECRACKER_VERSION}-x86_64" "${KATA_SOURCE_DIR}/bin/jailer"
    chmod +x "${KATA_SOURCE_DIR}/bin/firecracker" "${KATA_SOURCE_DIR}/bin/jailer"
  fi
}

_configure_spoke_kata_fc() {
  local spoke="$1"
  local cname="${spoke}-control-plane"
  local ctx="kind-${spoke}"
  local vg_name="containerd-vg-${spoke}"
  local pool_name="containerd--vg--${spoke}-containerd--pool"

  log_info "── Configuring Kata Firecracker on ${spoke} (${cname}) ──"

  # 1. Purge any conflict kernel image packages & install lvm2 + thin-provisioning-tools
  docker exec "$cname" bash -c "
    dpkg --purge linux-image-rt-amd64 2>/dev/null || true
    apt-get update -qq && apt-get install -y -qq -f >/dev/null 2>&1 || true
    apt-get install -y -qq lvm2 thin-provisioning-tools >/dev/null 2>&1 || true
    mkdir -p /opt/kata/bin /opt/kata/share/kata-containers /etc/kata-containers /var/lib/containerd/io.containerd.snapshotter.v1.devmapper
  "

  # 2. Inject containerd binary with devmapper enabled (host binary has devmapper built-in)
  if [ -f "/usr/bin/containerd" ]; then
    docker cp /usr/bin/containerd "${cname}:/usr/local/bin/containerd"
  fi

  # 3. Copy Kata binaries and assets
  docker cp "${KATA_SOURCE_DIR}/bin/containerd-shim-kata-v2" "${cname}:/opt/kata/bin/"
  docker cp "${KATA_SOURCE_DIR}/bin/firecracker" "${cname}:/opt/kata/bin/"
  docker cp "${KATA_SOURCE_DIR}/bin/jailer" "${cname}:/opt/kata/bin/" 2>/dev/null || true
  docker cp "${KATA_SOURCE_DIR}/bin/kata-runtime" "${cname}:/opt/kata/bin/" 2>/dev/null || true
  docker cp "${KATA_SOURCE_DIR}/bin/kata-ctl" "${cname}:/opt/kata/bin/" 2>/dev/null || true
  docker cp "${KATA_SOURCE_DIR}/share/kata-containers/." "${cname}:/opt/kata/share/kata-containers/"

  # 4. Copy or generate /etc/kata-containers/configuration.toml
  if [ -f "/etc/kata-containers/configuration.toml" ]; then
    docker cp /etc/kata-containers/configuration.toml "${cname}:/etc/kata-containers/configuration.toml"
  else
    docker exec "$cname" bash -c "
      cp /opt/kata/share/defaults/kata-containers/configuration-fc.toml /etc/kata-containers/configuration.toml 2>/dev/null || true
      sed -i 's|^path = .*|path = \"/usr/local/bin/firecracker\"|g' /etc/kata-containers/configuration.toml 2>/dev/null || true
    "
  fi

  # 5. Set symlinks inside the container
  docker exec "$cname" bash -c "
    ln -sf /opt/kata/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
    ln -sf /opt/kata/bin/firecracker /usr/local/bin/firecracker
    ln -sf /opt/kata/bin/jailer /usr/local/bin/jailer 2>/dev/null || true
    ln -sf /opt/kata/bin/kata-runtime /usr/local/bin/kata-runtime 2>/dev/null || true
    ln -sf /opt/kata/bin/kata-ctl /usr/local/bin/kata-ctl 2>/dev/null || true
  "

  # 6. Install robust dmsetup wrapper to auto-create /dev/dm-* and /dev/mapper/* device nodes in KinD
  docker exec "$cname" bash -c '
    if [ ! -f /usr/sbin/dmsetup.orig ]; then
      mv /usr/sbin/dmsetup /usr/sbin/dmsetup.orig
    fi
    cat << "EOF" > /usr/sbin/dmsetup
#!/bin/sh
/usr/sbin/dmsetup.orig "$@"
ret=$?
if [ $ret -eq 0 ]; then
  /usr/sbin/dmsetup.orig mknodes >/dev/null 2>&1 || true
  /usr/sbin/dmsetup.orig ls 2>/dev/null | while read -r name majmin; do
    maj=$(echo "$majmin" | tr -d "()" | cut -d: -f1)
    min=$(echo "$majmin" | tr -d "()" | cut -d: -f2)
    if [ -n "$maj" ] && [ -n "$min" ]; then
      [ ! -e "/dev/dm-${min}" ] && mknod "/dev/dm-${min}" b "$maj" "$min" 2>/dev/null || true
      [ ! -e "/dev/mapper/${name}" ] && ln -sf "/dev/dm-${min}" "/dev/mapper/${name}" 2>/dev/null || true
    fi
  done
fi
exit $ret
EOF
    chmod +x /usr/sbin/dmsetup
  '

  # 7. Provision dedicated loopback disk & LVM thin-pool for this spoke
  docker exec "$cname" bash -c "
    IMG=\"/var/lib/containerd-pool-disk-${spoke}.img\"
    VG=\"${vg_name}\"
    POOL=\"containerd-pool\"

    if ! vgs \"\$VG\" >/dev/null 2>&1; then
      truncate -s 15G \"\$IMG\"
      LOOP_DEV=\$(losetup -fP --show \"\$IMG\")
      pvcreate -y \"\$LOOP_DEV\" >/dev/null 2>&1
      vgcreate \"\$VG\" \"\$LOOP_DEV\" >/dev/null 2>&1
      lvcreate -y --config 'activation { udev_sync = 0 udev_rules = 0 }' -W n -Z n --size 12G --thinpool \"\$POOL\" \"\$VG\" >/dev/null 2>&1
    fi
    vgchange -ay --monitor y \"\$VG\" >/dev/null 2>&1 || true
    /usr/sbin/dmsetup mknodes >/dev/null 2>&1 || true
  "

  # 8. Configure containerd with devmapper snapshotter and kata-fc runtime handler
  docker exec "$cname" bash -c "
    sed -i 's/discard_unpacked_layers = true/discard_unpacked_layers = false/' /etc/containerd/config.toml 2>/dev/null || true

    if ! grep -q 'plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.kata-fc' /etc/containerd/config.toml; then
      cat << 'EOF' >> /etc/containerd/config.toml

# Kata Firecracker Runtime (kata-fc) using devmapper snapshotter
[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.kata-fc]
  runtime_type = \"io.containerd.kata.v2\"
  snapshotter = \"devmapper\"

[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.kata-fc.options]
  ConfigPath = \"/etc/kata-containers/configuration.toml\"

# Devmapper Snapshotter Plugin for Kata
[plugins.\"io.containerd.snapshotter.v1.devmapper\"]
  root_path = \"/var/lib/containerd/io.containerd.snapshotter.v1.devmapper\"
  pool_name = \"${pool_name}\"
  base_image_size = \"4GB\"
  discard_blocks = false
  fs_type = \"ext4\"
EOF
    else
      sed -i 's/pool_name = .*/pool_name = \"${pool_name}\"/' /etc/containerd/config.toml 2>/dev/null || true
      sed -i 's/discard_blocks = true/discard_blocks = false/' /etc/containerd/config.toml 2>/dev/null || true
    fi

    # Restart containerd cleanly
    systemctl restart containerd
  "

  # Wait for containerd to become active
  local ready=false
  for i in $(seq 1 30); do
    if docker exec "$cname" ctr plugins ls 2>/dev/null | grep -E "devmapper\s+linux/amd64\s+ok" >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 1
  done

  if [ "$ready" = true ]; then
    log_success "Containerd devmapper plugin successfully initialized on ${spoke}."
  else
    log_warn "Containerd devmapper plugin check did not report ok yet on ${spoke}."
  fi

  # 9. Apply Kubernetes RuntimeClass kata-fc
  cat << EOF | kubectl --context "$ctx" apply -f - >/dev/null
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: kata-fc
handler: kata-fc
EOF
  log_success "RuntimeClass kata-fc created on ${spoke}."

  # 10. Ensure opensandbox-workloads namespace & klusterlet execution permissions exist
  kubectl --context "$ctx" create namespace opensandbox-workloads --dry-run=client -o yaml | kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
  kubectl --context "$ctx" create clusterrolebinding klusterlet-work-cluster-admin \
    --clusterrole=cluster-admin \
    --serviceaccount=open-cluster-management-agent:klusterlet-work-sa \
    --dry-run=client -o yaml | kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
}

_smoke_test_kata_fc() {
  local spoke="$1"
  local ctx="kind-${spoke}"
  local pod_name="kata-fc-smoke-${spoke}"

  log_info "Running Kata Firecracker smoke test on ${spoke}..."

  cat << EOF | kubectl --context "$ctx" apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: default
spec:
  runtimeClassName: kata-fc
  restartPolicy: Never
  containers:
  - name: test
    image: busybox:musl
    command: ["sh", "-c", "echo 'KATA_SUCCESS' && uname -a && sleep 60"]
EOF

  local pod_ok=false
  for i in $(seq 1 45); do
    local phase
    phase=$(kubectl --context "$ctx" get pod "${pod_name}" -n default -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    if [ "$phase" == "Running" ] || [ "$phase" == "Succeeded" ]; then
      pod_ok=true
      break
    fi
    sleep 2
  done

  if [ "$pod_ok" = true ]; then
    local k_ver
    k_ver=$(kubectl --context "$ctx" logs "${pod_name}" -n default 2>/dev/null | grep -i "Linux" | head -n 1 || echo "")
    log_success "Smoke test pod on ${spoke} is Running under microVM guest kernel: ${k_ver}"
  else
    log_warn "Smoke test pod on ${spoke} did not reach Running state within timeout (check logs)."
  fi

  # Cleanup test pod
  kubectl --context "$ctx" delete pod "${pod_name}" -n default --grace-period=0 --force >/dev/null 2>&1 || true
}

phase_15b_setup_kata_firecracker() {
  log_step "PHASE 15-B: Setting up Kata Firecracker (kata-fc) Runtime on Spokes"

  # Preflight hardware virtualization check
  if [ ! -e "/dev/kvm" ]; then
    log_warn "Host /dev/kvm was not detected! Firecracker requires hardware virtualization."
    log_warn "If running inside a VM, ensure nested virtualization is enabled."
  else
    log_success "Host /dev/kvm verified."
  fi

  _ensure_kata_host_assets

  for spoke in spoke1 spoke2; do
    _configure_spoke_kata_fc "$spoke"
  done

  # Run quick verification smoke test
  for spoke in spoke1 spoke2; do
    _smoke_test_kata_fc "$spoke"
  done

  log_success "Phase 15-B: Kata Containers + Firecracker (kata-fc) runtime successfully configured on spoke clusters."
}

# ── Phase 16: Join spokes to OCM via Gateway VIP ─────────────────────────────
# ==============================================================================
# Replacement for phase_16_join_spokes_to_ocm() in docker-multi-cluster.sh
# (delete the old function + paste this block in its place; main() is unchanged)
#
# Flow per spoke:
#   1. clusteradm join against PrimaryHub's WG IP (installs klusterlet operator + CR)
#   2. Create one bootstrap-kubeconfig secret per hub in open-cluster-management-agent
#        primaryhub-kubeconfig   -> https://10.99.0.1:6443  (index 0 = priority 1)
#        secondaryhub-kubeconfig -> https://10.99.0.2:6443  (index 1 = priority 2)
#   3. Patch klusterlet: MultipleHubs feature gate + LocalSecrets bootstrap
#   4. Accept the spoke's CSR on the hub(s) and wait for Available
# ==============================================================================

# Build a bootstrap kubeconfig that talks DIRECTLY to one hub over the WG overlay.
# Both hubs share the same Root CA (phase 3/7), so one CA bundle works for both.
# Args: <out-file> <server-url> <token>
_write_hub_bootstrap_kubeconfig() {
  local out="$1" server="$2" token="$3" ca_b64
  ca_b64=$(base64 -w0 < "${PKI_DIR}/ca.crt")
  cat > "$out" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: hub
  cluster:
    server: ${server}
    certificate-authority-data: ${ca_b64}
contexts:
- name: bootstrap
  context:
    cluster: hub
    user: bootstrap
current-context: bootstrap
users:
- name: bootstrap
  user:
    token: ${token}
EOF
  chmod 600 "$out"
}

# Extract the join token from `clusteradm get token`
# Args: <hub-context>
_get_hub_token() {
  local ctx="$1" out tok err_file
  err_file=$(mktemp)

  # After an apiserver restart the OCM hub controllers need a moment to recover
  kubectl --context "$ctx" -n open-cluster-management-hub wait \
    --for=condition=Available deployment --all --timeout=120s >&2 2>/dev/null || true

  for i in $(seq 1 20); do
    # Generate long-lived (10-year) token for agent-registration-bootstrap so bootstrap secrets never expire
    tok=$(kubectl --context "$ctx" -n open-cluster-management create token agent-registration-bootstrap --duration=87600h 2>/dev/null || true)
    if [ -n "$tok" ]; then
      rm -f "$err_file"
      echo "$tok"
      return 0
    fi

    out=$(clusteradm get token --context "$ctx" 2>"$err_file" || true)
    tok=$(echo "$out" | grep '^token=' | head -1 | cut -d'=' -f2- || true)
    if [ -z "$tok" ]; then
      tok=$(echo "$out" | grep -oP '(?<=--hub-token )\S+' | head -1 || true)
    fi
    if [ -n "$tok" ]; then
      rm -f "$err_file"
      echo "$tok"
      return 0
    fi
    sleep 5
  done

  log_warn "clusteradm get token failed for ${ctx}. Last error:" >&2
  sed 's/^/    /' "$err_file" >&2 || true
  kubectl --context "$ctx" get clustermanager 2>&1 | sed 's/^/    /' >&2 || true
  kubectl --context "$ctx" -n open-cluster-management-hub get pods 2>&1 | sed 's/^/    /' >&2 || true
  rm -f "$err_file"
  echo ""
}


_dump_klusterlet_debug() {
  local ctx="$1"
  log_warn "── Klusterlet debug for ${ctx} ──"
  kubectl --context "$ctx" -n open-cluster-management-agent get pods 2>&1 || true
  kubectl --context "$ctx" -n open-cluster-management-agent get secrets 2>&1 || true
  kubectl --context "$ctx" -n open-cluster-management-agent logs deploy/klusterlet-registration-agent --tail=30 2>&1 || true
  kubectl --context "$ctx" get klusterlet klusterlet \
    -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}' 2>&1 || true
}

# Make sure a hub's kube-apiserver serving cert is valid for the WG overlay IPs.
# Existing KinD clusters keep their old cert, so the certSANs in the kind config
# are not applied retroactively. This regenerates apiserver.crt (same CA) with the
# extra SANs and restarts the apiserver static pod. Idempotent.
# Args: <cluster-name> <service-cidr>
_ensure_hub_apiserver_sans() {
  local cluster="$1" svc_cidr="$2" node="${1}-control-plane"
  local crt_text
  crt_text=$(docker exec "$node" cat /etc/kubernetes/pki/apiserver.crt 2>/dev/null \
             | openssl x509 -noout -text 2>/dev/null || true)

  if echo "$crt_text" | grep -q "IP Address:${WG_HUB1_IP}\b" && \
     echo "$crt_text" | grep -q "IP Address:${WG_HUB2_IP}\b" && \
     echo "$crt_text" | grep -q "IP Address:${WG_VIP}\b"; then
    log_info "${cluster}: apiserver cert already contains WG SANs."
    return 0
  fi

  log_warn "${cluster}: apiserver cert is missing WG SANs – regenerating (CA unchanged)..."
  local node_ip transit_ip sans
  node_ip=$(docker inspect -f '{{ .NetworkSettings.Networks.kind.IPAddress }}' "$node")
  transit_ip=$(docker inspect -f "{{ (index .NetworkSettings.Networks \"${TRANSIT_NET_NAME}\").IPAddress }}" "$node" 2>/dev/null || true)
  sans="localhost,127.0.0.1,0.0.0.0,${WG_HUB1_IP},${WG_HUB2_IP},${WG_VIP},${node_ip}"
  [ -n "$transit_ip" ] && sans="${sans},${transit_ip}"

  docker exec "$node" bash -c "
    set -e
    # Ensure /kind/kubeadm.conf preserves SANs across container restarts/reboots
    if [ -f /kind/kubeadm.conf ]; then
      for ip in ${WG_HUB1_IP} ${WG_HUB2_IP} ${WG_VIP} 0.0.0.0; do
        if ! grep -q \"\- \${ip}\" /kind/kubeadm.conf; then
          sed -i \"/certSANs:/a \ \ - \${ip}\" /kind/kubeadm.conf
        fi
      done
    fi
    cd /etc/kubernetes/pki
    mkdir -p /root/pki-backup && cp -f apiserver.crt apiserver.key /root/pki-backup/
    rm -f apiserver.crt apiserver.key
    kubeadm init phase certs apiserver \
      --cert-dir /etc/kubernetes/pki \
      --kubernetes-version \$(kubeadm version -o short) \
      --service-cidr '${svc_cidr}' \
      --apiserver-advertise-address '${node_ip}' \
      --apiserver-cert-extra-sans '${sans}'
    # kubelet recreates the static pod, which loads the new cert
    crictl ps --name kube-apiserver -q | xargs -r crictl stop >/dev/null
  "

  log_info "${cluster}: waiting for kube-apiserver to come back..."
  local ok=false
  for i in $(seq 1 60); do
    if docker exec "$node" curl -sk -m 3 https://127.0.0.1:6443/readyz >/dev/null 2>&1; then
      ok=true; break
    fi
    sleep 2
  done
  [ "$ok" = true ] || { log_error "${cluster}: apiserver did not become ready after cert rotation."; exit 1; }

  if docker exec "$node" cat /etc/kubernetes/pki/apiserver.crt | openssl x509 -noout -text \
       | grep -q "IP Address:${WG_HUB1_IP}\b"; then
    log_success "${cluster}: apiserver cert now valid for ${WG_HUB1_IP}, ${WG_HUB2_IP}, ${WG_VIP}."
  else
    log_error "${cluster}: cert regeneration did not add the WG SANs."
    exit 1
  fi
}

phase_16_join_spokes_to_ocm() {
  log_step "PHASE 16: Joining Spokes to OCM (MultipleHubs: primaryhub → secondaryhub)"

  # Hubs must present a cert valid for their WG IPs, or spoke TLS will fail
  _ensure_hub_apiserver_sans "primaryhub"   "10.96.0.0/16"
  _ensure_hub_apiserver_sans "secondaryhub" "10.97.0.0/16"

  local agent_ns="open-cluster-management-agent"
  local hub1_url="https://${WG_HUB1_IP}:6443"
  local hub2_url="https://${WG_HUB2_IP}:6443"

  # ── Tokens (one per hub – each hub has its own bootstrap identity) ──────────
  local hub1_token hub2_token
  hub1_token=$(_get_hub_token kind-primaryhub)
  hub2_token=$(_get_hub_token kind-secondaryhub)
  if [ -z "$hub1_token" ] || [ -z "$hub2_token" ]; then
    log_error "Could not obtain join token(s): primaryhub='${hub1_token:+ok}' secondaryhub='${hub2_token:+ok}'"
    log_error "Is OCM initialised on both hubs? (phase 8)"
    exit 1
  fi

  # Write bootstrap kubeconfigs (order matters: index 0 = primary)
  _write_hub_bootstrap_kubeconfig "${STATE_DIR}/primaryhub-bootstrap.kubeconfig"   "$hub1_url" "$hub1_token"
  _write_hub_bootstrap_kubeconfig "${STATE_DIR}/secondaryhub-bootstrap.kubeconfig" "$hub2_url" "$hub2_token"

  for spoke in spoke1 spoke2; do
    local ctx="kind-${spoke}"
    log_info "── ${spoke} ──"

    # Underlay/overlay reachability sanity check (direct to each hub API server)
    for target in "$hub1_url" "$hub2_url"; do
      if docker exec "${spoke}-control-plane" curl -sk -m 5 "${target}/version" >/dev/null 2>&1; then
        log_success "${spoke} can reach ${target}"
      else
        log_warn "${spoke} cannot reach ${target} over WireGuard – check phase 14."
      fi
    done

    # 1. Install klusterlet (operator + CR) if it is not there yet ──────────────
    if ! kubectl --context "$ctx" get klusterlet klusterlet >/dev/null 2>&1; then
      log_info "Running clusteradm join for ${spoke} (initial bootstrap via primaryhub)..."
      # NOTE: must run INSIDE the spoke node – clusteradm contacts the hub API
      # (cluster-info) and 10.99.0.x is only routable from within the WG overlay.
      if ! docker exec "${spoke}-control-plane" test -x /usr/local/bin/clusteradm; then
        docker cp "$(command -v clusteradm)" "${spoke}-control-plane:/usr/local/bin/clusteradm"
      fi
      docker exec "${spoke}-control-plane" bash -c "
        export KUBECONFIG=/etc/kubernetes/admin.conf
        clusteradm join \
          --hub-token '${hub1_token}' \
          --hub-apiserver '${hub1_url}' \
          --cluster-name '${spoke}'
      " || log_warn "clusteradm join returned non-zero; continuing to verify."

      kubectl --context "$ctx" wait --for=condition=established \
        crd/klusterlets.operator.open-cluster-management.io --timeout=120s || true

      local k_ok=false
      for i in $(seq 1 30); do
        if kubectl --context "$ctx" get klusterlet klusterlet >/dev/null 2>&1; then
          k_ok=true; break
        fi
        sleep 2
      done
      if [ "$k_ok" = false ]; then
        log_error "Klusterlet CR was not created on ${spoke}."
        _dump_klusterlet_debug "$ctx"
        exit 1
      fi
    else
      log_info "Klusterlet already present on ${spoke}."
    fi

    # 2. Per-hub bootstrap kubeconfig secrets (key name MUST be 'kubeconfig') ───
    kubectl --context "$ctx" create namespace "$agent_ns" --dry-run=client -o yaml \
      | kubectl --context "$ctx" apply -f - >/dev/null

    kubectl --context "$ctx" -n "$agent_ns" create secret generic primaryhub-kubeconfig \
      --from-file=kubeconfig="${STATE_DIR}/primaryhub-bootstrap.kubeconfig" \
      --dry-run=client -o yaml | kubectl --context "$ctx" apply -f -
    kubectl --context "$ctx" -n "$agent_ns" create secret generic secondaryhub-kubeconfig \
      --from-file=kubeconfig="${STATE_DIR}/secondaryhub-bootstrap.kubeconfig" \
      --dry-run=client -o yaml | kubectl --context "$ctx" apply -f -

    # 3. Enable MultipleHubs on the klusterlet ─────────────────────────────────
    #    Priority = array order: [0] primaryhub, [1] secondaryhub
    #    Patch CRD schema to allow sub-minute hubConnectionTimeoutSeconds (60s failover)
    log_info "Patching CRD schema and klusterlet on ${spoke}: MultipleHubs + LocalSecrets (60s failover)..."
    kubectl --context "$ctx" patch crd klusterlets.operator.open-cluster-management.io --type json -p '[{"op":"replace","path":"/spec/versions/0/schema/openAPIV3Schema/properties/spec/properties/registrationConfiguration/properties/bootstrapKubeConfigs/properties/localSecretsConfig/properties/hubConnectionTimeoutSeconds/minimum","value":10}]' 2>/dev/null || true

    kubectl --context "$ctx" patch klusterlet klusterlet --type=merge -p '{
      "spec": {
        "registrationConfiguration": {
          "featureGates": [
            { "feature": "MultipleHubs", "mode": "Enable" }
          ],
          "bootstrapKubeConfigs": {
            "type": "LocalSecrets",
            "localSecretsConfig": {
              "hubConnectionTimeoutSeconds": 60,
              "kubeConfigSecrets": [
                { "name": "primaryhub-kubeconfig" },
                { "name": "secondaryhub-kubeconfig" }
              ]
            }
          }
        }
      }
    }'

    # 4. Approve registration on the active (primary) hub ──────────────────────
    log_info "Accepting ${spoke} on primaryhub..."
    local accepted=false
    for i in $(seq 1 45); do
      if clusteradm accept --context kind-primaryhub --clusters "$spoke" >/dev/null 2>&1; then
        accepted=true; break
      fi
      sleep 2
    done
    [ "$accepted" = false ] && log_warn "clusteradm accept did not succeed for ${spoke} yet (CSR may still be pending)."
    kubectl --context kind-primaryhub patch managedcluster "$spoke" --type merge -p '{"spec":{"leaseDurationSeconds":5}}' 2>/dev/null || true

    # Wait for ManagedClusterConditionAvailable on primaryhub
    local avail="False"
    for i in $(seq 1 45); do
      # Re-run accept: the work-agent registration produces a second CSR
      clusteradm accept --context kind-primaryhub --clusters "$spoke" >/dev/null 2>&1 || true
      avail=$(kubectl --context kind-primaryhub get managedcluster "$spoke" \
        -o jsonpath='{.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status}' \
        2>/dev/null || echo "False")
      [ "$avail" == "True" ] && break
      sleep 4
    done

    if [ "$avail" == "True" ]; then
      log_success "Spoke '${spoke}' joined primaryhub and is Available (secondaryhub kept as standby)."
    else
      log_error "Spoke '${spoke}' did not become Available on primaryhub."
      _dump_klusterlet_debug "$ctx"
    fi

    # Best-effort: pre-approve on the standby hub (no-op until the agent fails over / phase 17 sync)
    clusteradm accept --context kind-secondaryhub --clusters "$spoke" >/dev/null 2>&1 || true
  done
}

# ── Phase 17: Label spokes & sync registration to SecondaryHub ────────────────
phase_17_sync_spokes_to_secondaryhub() {
  log_step "PHASE 17: Labeling Spokes & Syncing Registration to SecondaryHub"

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
    kubectl --context kind-primaryhub get managedcluster "$spoke" -o json 2>/dev/null | \
      jq 'del(.metadata.uid, .metadata.resourceVersion, .metadata.creationTimestamp, .metadata.ownerReferences, .status) | .spec.hubAcceptsClient = false' | \
      kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true
  done

  kubectl --context kind-primaryhub get managedclusterset sandbox-spokes -o yaml 2>/dev/null | \
    kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true
  kubectl --context kind-primaryhub get managedclustersetbinding -A -o yaml 2>/dev/null | \
    kubectl --context kind-secondaryhub apply -f - 2>/dev/null || true

  for spoke in spoke1 spoke2; do
    local spoke_wg_ip
    [ "$spoke" == "spoke1" ] && spoke_wg_ip="$WG_SPOKE1_IP" || spoke_wg_ip="$WG_SPOKE2_IP"

    for ctx in kind-primaryhub kind-secondaryhub; do
      kubectl --context "$ctx" label managedcluster "$spoke" \
        wireguard-ip="${spoke_wg_ip}" \
        sandbox-workload-capable=true \
        runtime.gvisor=true runtime.kata=true runtime.kata-fc=true \
        --overwrite 2>/dev/null || true
    done
  done

  log_success "Spoke registration synced to both hubs."
}

# ── Phase 18: End-to-end verification + summary ───────────────────────────────
phase_18_verify_and_summary() {
  run_verification

  local host_ip
  host_ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')
  [ -z "$host_ip" ] && host_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  [ -z "$host_ip" ] && host_ip="127.0.0.1"

  echo -e "\n${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "${GREEN}${BOLD}  MULTI-CLUSTER SETUP COMPLETE${NC}"
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "  Transit Network : ${TRANSIT_NET_NAME} (${TRANSIT_SUBNET})"
  echo -e "  WireGuard Mesh  : ${WG_SUBNET_PREFIX}.0/24 (encrypted overlay)"
  echo -e "  Gateway VIP     : https://${WG_VIP}:6443 & :80 (Envoy)"
  echo -e "  PrimaryHub      : ${WG_HUB1_IP} (Master Read-Write)"
  echo -e "  SecondaryHub    : ${WG_HUB2_IP} (Warm Standby)"
  echo -e "  Spoke1          : ${WG_SPOKE1_IP} (workload)"
  echo -e "  Spoke2          : ${WG_SPOKE2_IP} (workload)"
  echo -e "  DB Replication  : PostgreSQL physical WAL streaming (primary -> standby)"
  echo -e "  Failover Ctrl   : ocm-failover-controller active on SecondaryHub"
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"

  echo -e "\n${CYAN}${BOLD}  ┌──────────────────────────────────────────────────────────────────┐${NC}"
  echo -e "${CYAN}${BOLD}  │              🚀 FRONTEND & API INTEGRATION DETAILS               │${NC}"
  echo -e "${CYAN}${BOLD}  ├──────────────────────────────────────────────────────────────────┤${NC}"
  echo -e "${CYAN}${BOLD}  │${NC} Detected Machine IP : ${YELLOW}${BOLD}${host_ip}${NC}"
  echo -e "${CYAN}${BOLD}  │${NC} Envoy Gateway Port  : ${YELLOW}80${NC} (mapped from 172.30.0.10:80)"
  echo -e "${CYAN}${BOLD}  │${NC}"
  echo -e "${CYAN}${BOLD}  │${NC} Set this in your frontend ${BOLD}z1sandbox-website/.env${NC}:"
  echo -e "${CYAN}${BOLD}  │${NC}   ${GREEN}${BOLD}VITE_API_BASE_URL=http://${host_ip}${NC}"
  echo -e "${CYAN}${BOLD}  │${NC}"
  echo -e "${CYAN}${BOLD}  │${NC} Health Check URL    : ${BLUE}http://${host_ip}/health${NC}"
  echo -e "${CYAN}${BOLD}  │${NC} Swagger API Docs    : ${BLUE}http://${host_ip}/docs${NC}"
  echo -e "${CYAN}${BOLD}  └──────────────────────────────────────────────────────────────────┘${NC}\n"

  # Auto-update z1sandbox-website/.env if present
  local env_file="${ROOT_DIR}/z1sandbox-website/.env"
  if [ -f "$env_file" ]; then
    sed -i -E "s|^VITE_API_BASE_URL=.*|VITE_API_BASE_URL=http://${host_ip}|" "$env_file" 2>/dev/null || true
    echo -e "  ${GREEN}✔ Automatically updated ${env_file} with VITE_API_BASE_URL=http://${host_ip}${NC}\n"
  fi
}

# ==============================================================================
# ══ MAIN – calls every phase in order ════════════════════════════════════════
# To skip or reorder a phase, comment it out or move it below.
# ==============================================================================
main() {
  phase_01_preflight
  phase_02_transit_network_and_wg_keys
  phase_03_create_hub_clusters
  phase_04_install_crds_on_hubs
  phase_05_wireguard_on_hubs
  phase_06_envoy_gateway
  phase_07_verify_root_ca_and_vip
  phase_08_ocm_init
  phase_09_create_namespaces
  phase_10_load_custom_image
  phase_11_primaryhub_deploy
  phase_12_secondaryhub_deploy
  phase_13_create_spoke_clusters
  phase_14_wireguard_on_all_clusters
  phase_15_install_crds_on_spokes
  phase_15b_setup_kata_firecracker
  phase_16_join_spokes_to_ocm
  phase_17_sync_spokes_to_secondaryhub
  phase_18_verify_and_summary
}

# ==============================================================================
# Entry point
# ==============================================================================
case "$ACTION" in
  clean)  cleanup_environment ;;
  verify) run_verification    ;;
  *)      main                ;;
esac
