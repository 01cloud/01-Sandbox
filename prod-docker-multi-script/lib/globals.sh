#!/usr/bin/env bash
# ==============================================================================
# lib/globals.sh – Configuration, logging, and phase runner
#
# Sections:
#   1. Colors and terminal tokens
#   2. Repository and state paths
#   3. Network configuration (transit + WireGuard overlay)
#   4. CRD metadata
#   5. Kata / Firecracker versions
#   6. Argument parsing
#   7. Logging functions
#   8. Phase runner (idempotent phase execution)
# ==============================================================================

# ── 1. Colors ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# ── 2. Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="${SCRIPT_DIR}"
SANDBOX_REPO_DIR="${ROOT_DIR}"
CODE_INSPECTOR_DIR="${ROOT_DIR}/codeInspector"
OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/opensandbox-server/docker-build"

# Detect repository location relative to the script or common paths
if [ -d "${SCRIPT_DIR}/../codeInspector" ]; then
  SANDBOX_REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
  CODE_INSPECTOR_DIR="${SANDBOX_REPO_DIR}/codeInspector"
  OPENSANDBOX_BUILD_DIR="${SANDBOX_REPO_DIR}/opensandbox-server/docker-build"
elif [ -d "${ROOT_DIR}/01-Sandbox/codeInspector" ]; then
  SANDBOX_REPO_DIR="${ROOT_DIR}/01-Sandbox"
  CODE_INSPECTOR_DIR="${ROOT_DIR}/01-Sandbox/codeInspector"
  OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/01-Sandbox/opensandbox-server/docker-build"
elif [ -d "${ROOT_DIR}/codeInspector" ]; then
  SANDBOX_REPO_DIR="${ROOT_DIR}"
  CODE_INSPECTOR_DIR="${ROOT_DIR}/codeInspector"
  OPENSANDBOX_BUILD_DIR="${ROOT_DIR}/opensandbox-server/docker-build"
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
GVISOR_CACHE_DIR="${STATE_DIR}/gvisor-assets"
mkdir -p "$PKI_DIR" "$WG_DIR" "$ENVOY_DIR" "$SEC_DIR" "$KATA_CACHE_DIR" "$GVISOR_CACHE_DIR"

# Pin KUBECONFIG to an isolated production state path
export KUBECONFIG="${STATE_DIR}/kubeconfig"
mkdir -p "$(dirname "$KUBECONFIG")"
touch "$KUBECONFIG" 2>/dev/null || {
  echo -e "${RED}${BOLD}[ERROR]${NC}   Cannot write to \$KUBECONFIG (${KUBECONFIG})." >&2
  exit 1
}

# ── 2b. Remote Node Configuration ─────────────────────────────────────────────
PROD_ENV_FILE="${ROOT_DIR}/prod.env"
if [ -f "$PROD_ENV_FILE" ]; then
  # Source environment variables while ignoring comments
  set -a
  source "$PROD_ENV_FILE"
  set +a
elif [ -f "${ROOT_DIR}/prod.env.example" ]; then
  log_warn "prod.env not found! Falling back to defaults. To configure remote EC2 instances, copy prod.env.example to prod.env."
fi

PRIMARYHUB_HOST="${PRIMARYHUB_HOST:-127.0.0.1}"
SECONDARYHUB_HOST="${SECONDARYHUB_HOST:-127.0.0.1}"
SPOKE1_HOST="${SPOKE1_HOST:-127.0.0.1}"
SPOKE2_HOST="${SPOKE2_HOST:-127.0.0.1}"

# Strip any user@ prefix from HOST variables if present, extracting default user
for _v in PRIMARYHUB SECONDARYHUB SPOKE1 SPOKE2; do
  _h_var="${_v}_HOST"
  _h_val="${!_h_var:-}"
  if [[ "$_h_val" == *"@"* ]]; then
    _u_val="${_h_val%%@*}"
    _h_val="${_h_val##*@}"
    eval "${_h_var}=\"$_h_val\""
    eval ": \"\${${_v}_SSH_USER:=$_u_val}\""
  fi
done

# SSH / Keypair Configuration
SSH_USER="${SSH_USER:-ubuntu}"
SSH_KEY_PATH="${SSH_KEY_PATH:-${HOME}/.ssh/id_rsa}"
SSH_PORT="${SSH_PORT:-22}"
WG_PORT="${WG_PORT:-51820}"

# Resolve SSH user for specific target host
_get_ssh_user_for_host() {
  local host="$1"
  if [ "$host" == "$PRIMARYHUB_HOST" ] && [ -n "${PRIMARYHUB_SSH_USER:-}" ]; then
    echo "$PRIMARYHUB_SSH_USER"
  elif [ "$host" == "$SECONDARYHUB_HOST" ] && [ -n "${SECONDARYHUB_SSH_USER:-}" ]; then
    echo "$SECONDARYHUB_SSH_USER"
  elif [ "$host" == "$SPOKE1_HOST" ] && [ -n "${SPOKE1_SSH_USER:-}" ]; then
    echo "$SPOKE1_SSH_USER"
  elif [ "$host" == "$SPOKE2_HOST" ] && [ -n "${SPOKE2_SSH_USER:-}" ]; then
    echo "$SPOKE2_SSH_USER"
  else
    echo "${SSH_USER:-ubuntu}"
  fi
}

# Resolve SSH private key for specific target host (supports global key or per-host key)
_get_ssh_key_for_host() {
  local host="$1"
  local key=""
  if [ "$host" == "$PRIMARYHUB_HOST" ] && [ -n "${PRIMARYHUB_SSH_KEY:-}" ]; then
    key="$PRIMARYHUB_SSH_KEY"
  elif [ "$host" == "$SECONDARYHUB_HOST" ] && [ -n "${SECONDARYHUB_SSH_KEY:-}" ]; then
    key="$SECONDARYHUB_SSH_KEY"
  elif [ "$host" == "$SPOKE1_HOST" ] && [ -n "${SPOKE1_SSH_KEY:-}" ]; then
    key="$SPOKE1_SSH_KEY"
  elif [ "$host" == "$SPOKE2_HOST" ] && [ -n "${SPOKE2_SSH_KEY:-}" ]; then
    key="$SPOKE2_SSH_KEY"
  else
    key="${SSH_KEY_PATH:-${HOME}/.ssh/id_rsa}"
  fi
  eval key="$key"
  echo "$key"
}

# Dynamic SSH and SCP command-line options per host
_get_ssh_opts_for_host() {
  local host="$1"
  local key
  key=$(_get_ssh_key_for_host "$host")
  echo "-p ${SSH_PORT} -i ${key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o BatchMode=yes -o LogLevel=ERROR"
}

_get_scp_opts_for_host() {
  local host="$1"
  local key
  key=$(_get_ssh_key_for_host "$host")
  echo "-P ${SSH_PORT} -i ${key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o BatchMode=yes -o LogLevel=ERROR"
}

remote_exec() {
  local host="$1"; shift
  if [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ]; then
    bash -c "$*"
  else
    local user key
    user=$(_get_ssh_user_for_host "$host")
    key=$(_get_ssh_key_for_host "$host")
    ssh -p "$SSH_PORT" -i "$key" \
      -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=10 -o BatchMode=yes -o LogLevel=ERROR \
      "${user}@${host}" "$@"
  fi
}

remote_copy_to() {
  local host="$1" local_path="$2" remote_path="$3"
  if [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ]; then
    cp -r "$local_path" "$remote_path"
  else
    local user key
    user=$(_get_ssh_user_for_host "$host")
    key=$(_get_ssh_key_for_host "$host")
    scp -P "$SSH_PORT" -i "$key" \
      -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=10 -o BatchMode=yes -o LogLevel=ERROR \
      -r "$local_path" "${user}@${host}:${remote_path}"
  fi
}

remote_copy_from() {
  local host="$1" remote_path="$2" local_path="$3"
  if [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ]; then
    cp -r "$remote_path" "$local_path"
  else
    local user key
    user=$(_get_ssh_user_for_host "$host")
    key=$(_get_ssh_key_for_host "$host")
    scp -P "$SSH_PORT" -i "$key" \
      -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=10 -o BatchMode=yes -o LogLevel=ERROR \
      -r "${user}@${host}:${remote_path}" "$local_path"
  fi
}

remote_test_ssh() {
  local host="$1"
  [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ] && return 0
  local user key
  user=$(_get_ssh_user_for_host "$host")
  key=$(_get_ssh_key_for_host "$host")
  ssh -p "$SSH_PORT" -i "$key" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=10 -o BatchMode=yes -o LogLevel=ERROR \
    "${user}@${host}" "echo ok" >/dev/null 2>&1
}

_get_tunnel_port_for_cluster() {
  case "$1" in
    primaryhub)   echo "16443" ;;
    secondaryhub) echo "26443" ;;
    spoke1)       echo "36443" ;;
    spoke2)       echo "46443" ;;
    *)            echo "16443" ;;
  esac
}

ensure_cluster_ssh_tunnel() {
  local cluster="$1" target_host="$2"
  [ -z "$target_host" ] && return 0
  [ "$target_host" == "127.0.0.1" ] || [ "$target_host" == "localhost" ] && return 0

  local local_port
  local_port=$(_get_tunnel_port_for_cluster "$cluster")

  if curl -k -m 2 "https://127.0.0.1:${local_port}/version" >/dev/null 2>&1; then
    return 0
  fi

  local user key
  user=$(_get_ssh_user_for_host "$target_host")
  key=$(_get_ssh_key_for_host "$target_host")

  fuser -k "${local_port}/tcp" 2>/dev/null || true

  ssh -fN \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=10 \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -p "$SSH_PORT" -i "$key" \
    -L "${local_port}:127.0.0.1:6443" \
    "${user}@${target_host}" 2>/dev/null || true

  local _w=0
  while ! nc -z 127.0.0.1 "$local_port" >/dev/null 2>&1 && [ $_w -lt 10 ]; do
    sleep 0.5
    _w=$((_w + 1))
  done
}

ensure_all_cluster_tunnels() {
  local phase_num="${1:-}"
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && \
    ensure_cluster_ssh_tunnel "primaryhub" "$PRIMARYHUB_HOST"
  [ "$SECONDARYHUB_HOST" != "127.0.0.1" ] && [ "$SECONDARYHUB_HOST" != "localhost" ] && \
    ensure_cluster_ssh_tunnel "secondaryhub" "$SECONDARYHUB_HOST"

  # Only connect to spokes starting in phase 13 (when spoke clusters are created)
  local phase_int=0
  if [ -n "$phase_num" ]; then
    phase_int=$(echo "$phase_num" | sed 's/[^0-9]//g')
  fi
  if [ -z "$phase_num" ] || [ "${phase_int:-0}" -ge 13 ]; then
    [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && \
      ensure_cluster_ssh_tunnel "spoke1" "$SPOKE1_HOST"
    [ "$SPOKE2_HOST" != "127.0.0.1" ] && [ "$SPOKE2_HOST" != "localhost" ] && \
      ensure_cluster_ssh_tunnel "spoke2" "$SPOKE2_HOST"
  fi
}

# ── 3. Network ────────────────────────────────────────────────────────────────
TRANSIT_NET_NAME="01sandbox-transit"
TRANSIT_SUBNET="172.30.0.0/24"
GW_TRANSIT_IP="172.30.0.10"
HUB1_TRANSIT_IP="172.30.0.20"
HUB2_TRANSIT_IP="172.30.0.21"
SPOKE1_TRANSIT_IP="172.30.0.30"
SPOKE2_TRANSIT_IP="172.30.0.31"

WG_SUBNET_PREFIX="10.99.0"
WG_GATEWAY_IP="10.99.0.254"
WG_VIP="10.99.0.100"
WG_HUB1_IP="10.99.0.1"
WG_HUB2_IP="10.99.0.2"
WG_SPOKE1_IP="10.99.0.3"
WG_SPOKE2_IP="10.99.0.4"

# External endpoints for WireGuard across regions
GW_ENDPOINT="${PRIMARYHUB_HOST}:${WG_PORT}"
HUB1_ENDPOINT="${PRIMARYHUB_HOST}:${WG_PORT}"
HUB2_ENDPOINT="${SECONDARYHUB_HOST}:${WG_PORT}"
SPOKE1_ENDPOINT="${SPOKE1_HOST}:${WG_PORT}"
SPOKE2_ENDPOINT="${SPOKE2_HOST}:${WG_PORT}"

# WireGuard keypairs – populated by setup_transit_network_and_wg_keys
declare -A WG_PRIV=()
declare -A WG_PUB=()

# ── 4. CRD metadata ───────────────────────────────────────────────────────────
declare -A CRD_NAMES=(
  ["cloudnative-pg-crds.yaml"]="CloudNativePG (Postgres HA & Replication)"
  ["gateway-api-crds.yaml"]="Kubernetes Gateway API (Routing & Ingress)"
  ["metallb-crds.yaml"]="MetalLB (Bare-metal LoadBalancer)"
  ["sealed-secrets-crd.yaml"]="Bitnami SealedSecrets (GitOps Encryption)"
  ["agentgateway-crds.yaml"]="AgentGateway (AI Agent Orchestration)"
  ["opensandbox-crds.yaml"]="OpenSandbox (Workload Sandboxing & Pools)"
)
declare -A CRD_DETAILS=(
  ["cloudnative-pg-crds.yaml"]="clusters, backups, scheduledbackups, poolers"
  ["gateway-api-crds.yaml"]="gateways, gatewayclasses, httproutes, tcproutes"
  ["metallb-crds.yaml"]="ipaddresspools, l2advertisements, bgpadvertisements"
  ["sealed-secrets-crd.yaml"]="sealedsecrets.bitnami.com"
  ["agentgateway-crds.yaml"]="agentgatewaybackends, agentgatewayparameters"
  ["opensandbox-crds.yaml"]="batchsandboxes, pools"
)
declare -A CRD_URLS=(
  ["cloudnative-pg-crds.yaml"]="https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/main/releases/cnpg-latest.yaml"
  ["gateway-api-crds.yaml"]="https://github.com/kubernetes-sigs/gateway-api/releases/latest/download/experimental-install.yaml"
  ["metallb-crds.yaml"]="https://raw.githubusercontent.com/metallb/metallb/main/config/crd/bases/metallb.io_addresspools.yaml"
  ["sealed-secrets-crd.yaml"]="https://github.com/bitnami-labs/sealed-secrets/releases/latest/download/controller.yaml"
)

# ── 5. Kata / Firecracker versions ────────────────────────────────────────────
KATA_VERSION="${KATA_VERSION:-3.18.0}"
FIRECRACKER_VERSION="${FIRECRACKER_VERSION:-v1.11.1}"

# ── 6. Argument parsing ───────────────────────────────────────────────────────
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
    *) echo -e "${RED}${BOLD}[ERROR]${NC}   Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ── 7. Logging ────────────────────────────────────────────────────────────────
log_info()    { echo -e "${BLUE}${BOLD}[INFO]${NC}    $*"; }
log_success() { echo -e "${GREEN}${BOLD}[OK]${NC}      $*"; }
log_warn()    { echo -e "${YELLOW}${BOLD}[WARN]${NC}    $*" >&2; }
log_error()   { echo -e "${RED}${BOLD}[ERROR]${NC}   $*" >&2; }
log_step() {
  echo -e "\n${CYAN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "${CYAN}${BOLD}  $*${NC}"
  echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}\n"
}

# ── 8. Phase runner ───────────────────────────────────────────────────────────
# run_phase <num> <title> <check_fn> <do_fn>
#   Calls <check_fn>; if 0 AND --force not set → skip.
#   Otherwise runs <do_fn>.
run_phase() {
  local num="$1" title="$2" check_fn="$3" do_fn="$4"
  if [ "$num" != "01" ] && [ "$num" != "02" ]; then
    ensure_all_cluster_tunnels "$num"
  fi
  echo -e "\n${CYAN}${BOLD}[Phase ${num}]${NC} ${title}"
  echo -e "${CYAN}$(printf '─%.0s' {1..70})${NC}"
  if [ "$FORCE_RECONFIGURE" != "true" ] && $check_fn 2>/dev/null; then
    log_success "Phase ${num} – already configured. Skipping."
    return 0
  fi
  log_info "Running phase ${num}: ${title}..."
  if $do_fn; then
    log_success "Phase ${num} complete."
  else
    log_error "Phase ${num} (${title}) encountered errors."
    return 1
  fi
}

# Shared predicate: wg0 UP inside <container> with <expected-ip>
_check_wg_active() {
  docker exec "$1" ip addr show wg0 2>/dev/null | grep -q "$2"
}

# Shared predicate: all required CRDs Established on <context>
_check_hub_crds_established() {
  local ctx="$1"
  local crds=(
    agentgatewaypolicies.agentgateway.dev
    agentgatewaybackends.agentgateway.dev
    tcproutes.gateway.networking.k8s.io
    httproutes.gateway.networking.k8s.io
    gateways.gateway.networking.k8s.io
    clusters.postgresql.cnpg.io
    batchsandboxes.sandbox.opensandbox.io
    pools.sandbox.opensandbox.io
    ipaddresspools.metallb.io
  )
  for c in "${crds[@]}"; do
    kubectl --context "$ctx" get crd "$c" >/dev/null 2>&1 || return 1
  done
  return 0
}
