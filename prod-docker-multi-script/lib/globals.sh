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

# ── 2b. Remote Node Configuration & Dynamic Fleet Registry ───────────────────
PROD_ENV_FILE="${ROOT_DIR}/prod.env"
if [ -f "$PROD_ENV_FILE" ]; then
  # Source environment variables while ignoring comments
  set -a
  source "$PROD_ENV_FILE"
  set +a
elif [ -f "${ROOT_DIR}/prod.env.example" ]; then
  log_warn "prod.env not found! Falling back to defaults. To configure remote EC2 instances, copy prod.env.example to prod.env."
fi

# Connection Mode (default: true)
USE_SSM="${USE_SSM:-true}"

# Global Fleet Registries (Dynamically populated from AWS SSM or prod.env)
declare -a ALL_NODES=()
declare -a HUB_LIST=()
declare -a SPOKE_LIST=()
declare -A NODE_ROLES=()
declare -A NODE_REGIONS=()
declare -A NODE_TARGETS=()
declare -A NODE_HOSTS=()
declare -A NODE_PRIVATE_IPS=()
declare -A WG_IPS=()
declare -A HUB_PRIORITIES=()
declare -A NODE_SSH_USERS=()
declare -A NODE_SSH_KEYS=()

_load_cluster_registry() {
  local ssm_path="/sandbox/clusters"
  local ssm_region="${AWS_DEFAULT_REGION:-us-east-1}"
  local ssm_loaded=false

  # 1. Attempt AWS Systems Manager Parameter Store fleet discovery
  if command -v aws >/dev/null 2>&1 && [ "${USE_SSM:-true}" != "false" ]; then
    local cluster_params
    cluster_params=$(aws ssm get-parameters-by-path       --path "$ssm_path"       --region "$ssm_region"       --query "Parameters[*].Value"       --output json 2>/dev/null || echo "[]")

    if [ -n "$cluster_params" ] && [ "$cluster_params" != "[]" ] && [ "$cluster_params" != "null" ]; then
      local raw_hubs=()
      while IFS= read -r entry; do
        [ -z "$entry" ] && continue
        local c_name c_role c_region c_wg_ip c_prio
        c_name=$(echo "$entry" | jq -r '.cluster_name // empty' 2>/dev/null || true)
        [ -z "$c_name" ] && continue

        c_role=$(echo "$entry" | jq -r '.role // "spoke"' 2>/dev/null || echo "spoke")
        c_region=$(echo "$entry" | jq -r '.region // "us-east-1"' 2>/dev/null || echo "us-east-1")
        c_wg_ip=$(echo "$entry" | jq -r '.wg_ip // empty' 2>/dev/null || true)
        c_prio=$(echo "$entry" | jq -r '.priority // "0"' 2>/dev/null || echo "0")

        ALL_NODES+=("$c_name")
        NODE_ROLES["$c_name"]="$c_role"
        NODE_REGIONS["$c_name"]="$c_region"
        NODE_TARGETS["$c_name"]="$c_name"
        NODE_HOSTS["$c_name"]="$c_name"
        [ -n "$c_wg_ip" ] && WG_IPS["$c_name"]="$c_wg_ip"

        if [ "$c_role" == "hub" ]; then
          HUB_PRIORITIES["$c_name"]="${c_prio:-0}"
          raw_hubs+=("${c_prio}:${c_name}")
        else
          SPOKE_LIST+=("$c_name")
        fi
      done < <(echo "$cluster_params" | jq -c '.[]' 2>/dev/null || true)

      if [ "${#ALL_NODES[@]}" -gt 0 ]; then
        ssm_loaded=true
        # Sort hubs by priority ascending (1, 2, 3...)
        if [ "${#raw_hubs[@]}" -gt 0 ]; then
          while IFS= read -r item; do
            [ -n "$item" ] && HUB_LIST+=("${item#*:}")
          done < <(printf '%s
' "${raw_hubs[@]}" | sort -n -t: -k1)
        fi
      fi
    fi
  fi

  # 2. Fallback to prod.env / environment / static defaults
  if [ "$ssm_loaded" = false ]; then
    ALL_NODES=("primaryhub" "secondaryhub" "spoke1" "spoke2")
    HUB_LIST=("primaryhub" "secondaryhub")
    SPOKE_LIST=("spoke1" "spoke2")

    NODE_ROLES["primaryhub"]="hub"; HUB_PRIORITIES["primaryhub"]="1"
    NODE_ROLES["secondaryhub"]="hub"; HUB_PRIORITIES["secondaryhub"]="2"
    NODE_ROLES["spoke1"]="spoke"
    NODE_ROLES["spoke2"]="spoke"

    NODE_REGIONS["primaryhub"]="${PRIMARYHUB_REGION:-us-east-1}"
    NODE_REGIONS["secondaryhub"]="${SECONDARYHUB_REGION:-eu-west-1}"
    NODE_REGIONS["spoke1"]="${SPOKE1_REGION:-eu-central-1}"
    NODE_REGIONS["spoke2"]="${SPOKE2_REGION:-ap-south-1}"

    NODE_TARGETS["primaryhub"]="${PRIMARYHUB_TARGET:-${PRIMARYHUB_HOST:-127.0.0.1}}"
    NODE_TARGETS["secondaryhub"]="${SECONDARYHUB_TARGET:-${SECONDARYHUB_HOST:-127.0.0.1}}"
    NODE_TARGETS["spoke1"]="${SPOKE1_TARGET:-${SPOKE1_HOST:-127.0.0.1}}"
    NODE_TARGETS["spoke2"]="${SPOKE2_TARGET:-${SPOKE2_HOST:-127.0.0.1}}"

    NODE_HOSTS["primaryhub"]="${PRIMARYHUB_HOST:-127.0.0.1}"
    NODE_HOSTS["secondaryhub"]="${SECONDARYHUB_HOST:-127.0.0.1}"
    NODE_HOSTS["spoke1"]="${SPOKE1_HOST:-127.0.0.1}"
    NODE_HOSTS["spoke2"]="${SPOKE2_HOST:-127.0.0.1}"

    WG_IPS["primaryhub"]="${WG_HUB1_IP:-10.99.0.1}"
    WG_IPS["secondaryhub"]="${WG_HUB2_IP:-10.99.0.2}"
    WG_IPS["spoke1"]="${WG_SPOKE1_IP:-10.99.0.3}"
    WG_IPS["spoke2"]="${WG_SPOKE2_IP:-10.99.0.4}"
  fi

  # 3. Synchronize legacy single-variable bindings for backward compatibility
  PRIMARYHUB_HOST="${NODE_HOSTS[${HUB_LIST[0]:-primaryhub}]:-${PRIMARYHUB_HOST:-127.0.0.1}}"
  PRIMARYHUB_REGION="${NODE_REGIONS[${HUB_LIST[0]:-primaryhub}]:-${PRIMARYHUB_REGION:-us-east-1}}"
  PRIMARYHUB_TARGET="${NODE_TARGETS[${HUB_LIST[0]:-primaryhub}]:-${PRIMARYHUB_TARGET:-$PRIMARYHUB_HOST}}"
  WG_HUB1_IP="${WG_IPS[${HUB_LIST[0]:-primaryhub}]:-10.99.0.1}"

  if [ "${#HUB_LIST[@]}" -gt 1 ]; then
    SECONDARYHUB_HOST="${NODE_HOSTS[${HUB_LIST[1]}]:-${SECONDARYHUB_HOST:-127.0.0.1}}"
    SECONDARYHUB_REGION="${NODE_REGIONS[${HUB_LIST[1]}]:-${SECONDARYHUB_REGION:-eu-west-1}}"
    SECONDARYHUB_TARGET="${NODE_TARGETS[${HUB_LIST[1]}]:-${SECONDARYHUB_TARGET:-$SECONDARYHUB_HOST}}"
    WG_HUB2_IP="${WG_IPS[${HUB_LIST[1]}]:-10.99.0.2}"
  else
    SECONDARYHUB_HOST="${SECONDARYHUB_HOST:-127.0.0.1}"
    SECONDARYHUB_REGION="${SECONDARYHUB_REGION:-eu-west-1}"
    SECONDARYHUB_TARGET="${SECONDARYHUB_TARGET:-$SECONDARYHUB_HOST}"
    WG_HUB2_IP="${WG_HUB2_IP:-10.99.0.2}"
  fi

  if [ "${#SPOKE_LIST[@]}" -gt 0 ]; then
    SPOKE1_HOST="${NODE_HOSTS[${SPOKE_LIST[0]}]:-${SPOKE1_HOST:-127.0.0.1}}"
    SPOKE1_REGION="${NODE_REGIONS[${SPOKE_LIST[0]}]:-${SPOKE1_REGION:-eu-central-1}}"
    SPOKE1_TARGET="${NODE_TARGETS[${SPOKE_LIST[0]}]:-${SPOKE1_TARGET:-$SPOKE1_HOST}}"
    WG_SPOKE1_IP="${WG_IPS[${SPOKE_LIST[0]}]:-10.99.0.3}"
  else
    SPOKE1_HOST="${SPOKE1_HOST:-127.0.0.1}"
    SPOKE1_REGION="${SPOKE1_REGION:-eu-central-1}"
    SPOKE1_TARGET="${SPOKE1_TARGET:-$SPOKE1_HOST}"
    WG_SPOKE1_IP="${WG_SPOKE1_IP:-10.99.0.3}"
  fi

  if [ "${#SPOKE_LIST[@]}" -gt 1 ]; then
    SPOKE2_HOST="${NODE_HOSTS[${SPOKE_LIST[1]}]:-${SPOKE2_HOST:-127.0.0.1}}"
    SPOKE2_REGION="${NODE_REGIONS[${SPOKE_LIST[1]}]:-${SPOKE2_REGION:-ap-south-1}}"
    SPOKE2_TARGET="${NODE_TARGETS[${SPOKE_LIST[1]}]:-${SPOKE2_TARGET:-$SPOKE2_HOST}}"
    WG_SPOKE2_IP="${WG_IPS[${SPOKE_LIST[1]}]:-10.99.0.4}"
  else
    SPOKE2_HOST="${SPOKE2_HOST:-127.0.0.1}"
    SPOKE2_REGION="${SPOKE2_REGION:-ap-south-1}"
    SPOKE2_TARGET="${SPOKE2_TARGET:-$SPOKE2_HOST}"
    WG_SPOKE2_IP="${WG_SPOKE2_IP:-10.99.0.4}"
  fi

  # Strip user@ prefixes if any
  for _node in "${ALL_NODES[@]}"; do
    local _h_val="${NODE_HOSTS[$_node]:-}"
    if [[ "$_h_val" == *"@"* ]]; then
      NODE_SSH_USERS["$_node"]="${_h_val%%@*}"
      NODE_HOSTS["$_node"]="${_h_val##*@}"
    fi
  done
}

# Initialize the cluster fleet registry
_load_cluster_registry

# Remote User & Fleet Key Configuration
REMOTE_USER="${REMOTE_USER:-ec2-user}"
SSH_USER="${SSH_USER:-${REMOTE_USER}}"
SSH_KEY_PATH="${SSH_KEY_PATH:-${HOME}/.ssh/bb-sandbox-fleet}"
SSH_PORT="${SSH_PORT:-22}"
WG_PORT="${WG_PORT:-51820}"

# ── SSM & Target Resolution Helpers ───────────────────────────────────────────
_get_target_for_node() {
  local host="$1"
  case "$host" in
    "$PRIMARYHUB_HOST"|"primaryhub")
      echo "${PRIMARYHUB_TARGET:-$PRIMARYHUB_HOST}" ;;
    "$SECONDARYHUB_HOST"|"secondaryhub")
      echo "${SECONDARYHUB_TARGET:-$SECONDARYHUB_HOST}" ;;
    "$SPOKE1_HOST"|"spoke1")
      echo "${SPOKE1_TARGET:-$SPOKE1_HOST}" ;;
    "$SPOKE2_HOST"|"spoke2")
      echo "${SPOKE2_TARGET:-$SPOKE2_HOST}" ;;
    *)
      echo "$host" ;;
  esac
}

_get_region_for_host() {
  local host="$1"
  case "$host" in
    "$PRIMARYHUB_HOST"|"primaryhub"|"${PRIMARYHUB_TARGET:-}")
      echo "${PRIMARYHUB_REGION:-us-east-1}" ;;
    "$SECONDARYHUB_HOST"|"secondaryhub"|"${SECONDARYHUB_TARGET:-}")
      echo "${SECONDARYHUB_REGION:-eu-west-1}" ;;
    "$SPOKE1_HOST"|"spoke1"|"${SPOKE1_TARGET:-}")
      echo "${SPOKE1_REGION:-eu-central-1}" ;;
    "$SPOKE2_HOST"|"spoke2"|"${SPOKE2_TARGET:-}")
      echo "${SPOKE2_REGION:-ap-south-1}" ;;
    *)
      echo "${AWS_REGION:-us-east-1}" ;;
  esac
}

_resolve_node_instance_id() {
  local host="$1"
  local target
  target=$(_get_target_for_node "$host")
  local region
  region=$(_get_region_for_host "$host")

  local cluster_role=""
  case "$host" in
    "$PRIMARYHUB_HOST"|"primaryhub") cluster_role="primaryhub" ;;
    "$SECONDARYHUB_HOST"|"secondaryhub") cluster_role="secondaryhub" ;;
    "$SPOKE1_HOST"|"spoke1") cluster_role="spoke1" ;;
    "$SPOKE2_HOST"|"spoke2") cluster_role="spoke2" ;;
    *) cluster_role="" ;;
  esac

  if [[ "$target" =~ ^i-[0-9a-f]+$ ]]; then
    local state
    state=$(aws ec2 describe-instances --region "$region" --instance-ids "$target" \
      --query "Reservations[0].Instances[0].State.Name" --output text 2>/dev/null || true)
    if [ "$state" == "running" ]; then
      echo "$target"
      return 0
    fi
    # If the configured instance ID is terminated/stopped, fall through to tag resolution
  fi

  local cache_dir="/tmp/01sandbox-ssm-cache"
  mkdir -p "$cache_dir" 2>/dev/null || true
  local safe_target
  safe_target=$(echo "$target" | tr -c 'a-zA-Z0-9' '_')
  local cache_file="${cache_dir}/inst_${region}_${safe_target}"
  if [ -s "$cache_file" ]; then
    local cached_id
    cached_id=$(cat "$cache_file")
    local cstate
    cstate=$(aws ec2 describe-instances --region "$region" --instance-ids "$cached_id" \
      --query "Reservations[0].Instances[0].State.Name" --output text 2>/dev/null || true)
    if [ "$cstate" == "running" ]; then
      echo "$cached_id"
      return 0
    fi
    rm -f "$cache_file"
  fi

  # 1. Try finding running instance by Name tag (cluster role or target name)
  local lookup_names=()
  [ -n "$cluster_role" ] && lookup_names+=("$cluster_role")
  [[ ! "$target" =~ ^i-[0-9a-f]+$ ]] && lookup_names+=("$target")

  for name in "${lookup_names[@]}"; do
    local tag_inst
    tag_inst=$(aws ec2 describe-instances --region "$region" \
      --filters "Name=tag:Name,Values=$name" "Name=instance-state-name,Values=running" \
      --query "Reservations[0].Instances[0].InstanceId" --output text 2>/dev/null || true)
    if [ -n "$tag_inst" ] && [ "$tag_inst" != "None" ]; then
      echo "$tag_inst" > "$cache_file"
      echo "$tag_inst"
      return 0
    fi
  done

  # 2. Try finding running instance by Public or Private IP via AWS EC2 API
  if [[ "$target" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    local inst
    inst=$(aws ec2 describe-instances --region "$region" \
      --filters "Name=ip-address,Values=$target" "Name=instance-state-name,Values=running" \
      --query "Reservations[0].Instances[0].InstanceId" --output text 2>/dev/null || true)
    if [ -n "$inst" ] && [ "$inst" != "None" ]; then
      echo "$inst" > "$cache_file"
      echo "$inst"
      return 0
    fi
  fi

  # 3. Try finding running instance in Auto Scaling Group
  local asg_inst
  asg_inst=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$target" --region "$region" \
    --query "AutoScalingGroups[0].Instances[?LifecycleState=='InService'].InstanceId | [0]" --output text 2>/dev/null || true)
  if [ -n "$asg_inst" ] && [ "$asg_inst" != "None" ]; then
    echo "$asg_inst" > "$cache_file"
    echo "$asg_inst"
    return 0
  fi

  echo "$target"
}

_resolve_node_private_ip() {
  local host="$1"
  [ -z "$host" ] && echo "" && return 0
  [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ] && echo "127.0.0.1" && return 0

  # 1. Check if explicitly configured in environment / prod.env
  case "$host" in
    "$PRIMARYHUB_HOST"|"primaryhub"|"${PRIMARYHUB_TARGET:-}")
      if [[ "${PRIMARYHUB_PRIVATE_IP:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "$PRIMARYHUB_PRIVATE_IP"
        return 0
      fi
      ;;
    "$SECONDARYHUB_HOST"|"secondaryhub"|"${SECONDARYHUB_TARGET:-}")
      if [[ "${SECONDARYHUB_PRIVATE_IP:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "$SECONDARYHUB_PRIVATE_IP"
        return 0
      fi
      ;;
    "$SPOKE1_HOST"|"spoke1"|"${SPOKE1_TARGET:-}")
      if [[ "${SPOKE1_PRIVATE_IP:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "$SPOKE1_PRIVATE_IP"
        return 0
      fi
      ;;
    "$SPOKE2_HOST"|"spoke2"|"${SPOKE2_TARGET:-}")
      if [[ "${SPOKE2_PRIVATE_IP:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "$SPOKE2_PRIVATE_IP"
        return 0
      fi
      ;;
  esac

  # If $host itself is already an IPv4 address, return it
  if [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$host"
    return 0
  fi

  local region inst_id
  region=$(_get_region_for_host "$host")
  inst_id=$(_resolve_node_instance_id "$host")

  # 2. Check local disk cache
  local cache_dir="/tmp/01sandbox-ssm-cache"
  mkdir -p "$cache_dir" 2>/dev/null || true
  local cache_file="${cache_dir}/privip_${region}_${inst_id}"
  if [ -s "$cache_file" ]; then
    local cached_ip
    cached_ip=$(cat "$cache_file" | tr -d '[:space:]')
    if [[ "$cached_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "$cached_ip"
      return 0
    fi
    rm -f "$cache_file"
  fi

  # 3. Try AWS EC2 describe-instances via AWS CLI
  if [[ "$inst_id" =~ ^i-[0-9a-f]+$ ]]; then
    local aws_ip
    aws_ip=$(aws ec2 describe-instances --region "$region" --instance-ids "$inst_id" \
      --query "Reservations[0].Instances[0].PrivateIpAddress" --output text 2>/dev/null || true)
    if [[ "$aws_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "$aws_ip" > "$cache_file"
      echo "$aws_ip"
      return 0
    fi
  fi

  # 4. Fallback: Query directly from the EC2 instance via remote_exec (over SSM / SSH)
  local remote_ip
  remote_ip=$(remote_exec "$host" "hostname -I 2>/dev/null | awk '{print \$1}'" 2>/dev/null | tr -d '\r\n[:space:]' || true)
  if [[ "$remote_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$remote_ip" > "$cache_file"
    echo "$remote_ip"
    return 0
  fi

  # Fallback to host if unable to resolve
  echo "$host"
}

init_node_private_ips() {
  if [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ]; then
    log_info "Resolving EC2 private IPv4 addresses for WireGuard mesh over VPC Peering..."
    PRIMARYHUB_PRIVATE_IP=$(_resolve_node_private_ip "$PRIMARYHUB_HOST")
    SECONDARYHUB_PRIVATE_IP=$(_resolve_node_private_ip "$SECONDARYHUB_HOST")
    SPOKE1_PRIVATE_IP=$(_resolve_node_private_ip "$SPOKE1_HOST")
    SPOKE2_PRIVATE_IP=$(_resolve_node_private_ip "$SPOKE2_HOST")

    log_info "Private IPs: PrimaryHub=${PRIMARYHUB_PRIVATE_IP}, SecondaryHub=${SECONDARYHUB_PRIVATE_IP}, Spoke1=${SPOKE1_PRIVATE_IP}, Spoke2=${SPOKE2_PRIVATE_IP}"
    if [[ ! "$PRIMARYHUB_PRIVATE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
       [[ ! "$SECONDARYHUB_PRIVATE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
       [[ ! "$SPOKE1_PRIVATE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
       [[ ! "$SPOKE2_PRIVATE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      log_warn "One or more node private IPv4 addresses could not be resolved automatically. You can explicitly set PRIMARYHUB_PRIVATE_IP, etc. in prod.env."
    fi
  fi
}

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
    echo "${SSH_USER:-${REMOTE_USER:-ec2-user}}"
  fi
}

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
    key="${SSH_KEY_PATH:-${HOME}/.ssh/bb-sandbox-fleet}"
  fi
  eval key="$key"
  echo "$key"
}

ensure_node_ssm_access() {
  local host="$1"
  [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ] && return 0
  [ "${USE_SSM:-true}" != "true" ] && return 0

  local inst_id region user key
  inst_id=$(_resolve_node_instance_id "$host")
  region=$(_get_region_for_host "$host")
  user=$(_get_ssh_user_for_host "$host")
  key=$(_get_ssh_key_for_host "$host")

  [[ ! "$inst_id" =~ ^i-[0-9a-f]+$ ]] && return 0

  local key_dir
  key_dir=$(dirname "$key")
  mkdir -p "$key_dir" 2>/dev/null || true
  if [ ! -f "$key" ]; then
    log_info "Generating local fleet key for SSM access at ${key}..."
    ssh-keygen -t rsa -b 2048 -f "$key" -N "" -C "ssm-fleet-access" >/dev/null 2>&1 || true
    chmod 600 "$key" 2>/dev/null || true
  fi

  local pub_key=""
  if [ -f "${key}.pub" ]; then
    pub_key=$(cat "${key}.pub")
  elif [ -f "$key" ]; then
    pub_key=$(ssh-keygen -y -f "$key" 2>/dev/null || true)
  fi

  [ -z "$pub_key" ] && return 0

  # Check if SSM SSH already connects
  if ssh -i "$key" \
    -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region ${region}" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=5 \
    -o BatchMode=yes \
    -o LogLevel=ERROR \
    "${user}@${inst_id}" "true" >/dev/null 2>&1; then
    return 0
  fi

  log_info "Authorizing SSH key on ${inst_id} (${region}) via AWS SSM RunCommand..."
  local cmd="mkdir -p /home/${user}/.ssh && grep -qF '${pub_key}' /home/${user}/.ssh/authorized_keys 2>/dev/null || echo '${pub_key}' >> /home/${user}/.ssh/authorized_keys; chmod 700 /home/${user}/.ssh && chmod 600 /home/${user}/.ssh/authorized_keys && chown -R ${user}:${user} /home/${user}/.ssh"
  aws ssm send-command \
    --instance-ids "$inst_id" \
    --region "$region" \
    --document-name "AWS-RunShellScript" \
    --parameters "commands=[\"$cmd\"]" \
    --comment "Authorize fleet key for SSM access" \
    --output text --query "Command.CommandId" >/dev/null 2>&1 || true
  sleep 2
}

remote_exec() {
  local host="$1"; shift
  if [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ]; then
    bash -c "$*"
  else
    local user key region inst_id
    user=$(_get_ssh_user_for_host "$host")
    key=$(_get_ssh_key_for_host "$host")
    region=$(_get_region_for_host "$host")
    inst_id=$(_resolve_node_instance_id "$host")

    if [ "${USE_SSM:-true}" == "true" ] && [[ "$inst_id" =~ ^i-[0-9a-f]+$ ]]; then
      ssh -i "$key" \
        -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region ${region}" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=15 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        "${user}@${inst_id}" "$@"
    else
      ssh -p "${SSH_PORT:-22}" -i "$key" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        "${user}@${host}" "$@"
    fi
  fi
}

remote_pipe_to() {
  local host="$1"; shift
  if [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ]; then
    bash -c "$*"
  else
    local user key region inst_id
    user=$(_get_ssh_user_for_host "$host")
    key=$(_get_ssh_key_for_host "$host")
    region=$(_get_region_for_host "$host")
    inst_id=$(_resolve_node_instance_id "$host")

    if [ "${USE_SSM:-true}" == "true" ] && [[ "$inst_id" =~ ^i-[0-9a-f]+$ ]]; then
      ssh -i "$key" \
        -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region ${region}" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=15 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        "${user}@${inst_id}" "$@"
    else
      ssh -p "${SSH_PORT:-22}" -i "$key" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        "${user}@${host}" "$@"
    fi
  fi
}

remote_copy_to() {
  local host="$1" local_path="$2" remote_path="$3"
  if [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ]; then
    cp -r "$local_path" "$remote_path"
  else
    local user key region inst_id
    user=$(_get_ssh_user_for_host "$host")
    key=$(_get_ssh_key_for_host "$host")
    region=$(_get_region_for_host "$host")
    inst_id=$(_resolve_node_instance_id "$host")

    if [ "${USE_SSM:-true}" == "true" ] && [[ "$inst_id" =~ ^i-[0-9a-f]+$ ]]; then
      scp -i "$key" \
        -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region ${region}" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=15 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        -r "$local_path" "${user}@${inst_id}:${remote_path}"
    else
      scp -P "${SSH_PORT:-22}" -i "$key" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        -r "$local_path" "${user}@${host}:${remote_path}"
    fi
  fi
}

remote_copy_from() {
  local host="$1" remote_path="$2" local_path="$3"
  if [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ]; then
    cp -r "$remote_path" "$local_path"
  else
    local user key region inst_id
    user=$(_get_ssh_user_for_host "$host")
    key=$(_get_ssh_key_for_host "$host")
    region=$(_get_region_for_host "$host")
    inst_id=$(_resolve_node_instance_id "$host")

    if [ "${USE_SSM:-true}" == "true" ] && [[ "$inst_id" =~ ^i-[0-9a-f]+$ ]]; then
      scp -i "$key" \
        -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region ${region}" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=15 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        -r "${user}@${inst_id}:${remote_path}" "$local_path"
    else
      scp -P "${SSH_PORT:-22}" -i "$key" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        -r "${user}@${host}:${remote_path}" "$local_path"
    fi
  fi
}

remote_test_ssh() {
  local host="$1"
  [ "$host" == "127.0.0.1" ] || [ "$host" == "localhost" ] && return 0
  local user key region inst_id
  user=$(_get_ssh_user_for_host "$host")
  key=$(_get_ssh_key_for_host "$host")
  region=$(_get_region_for_host "$host")
  inst_id=$(_resolve_node_instance_id "$host")

  if [ "${USE_SSM:-true}" == "true" ] && [[ "$inst_id" =~ ^i-[0-9a-f]+$ ]]; then
    ssh -i "$key" \
      -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region ${region}" \
      -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=15 \
      -o BatchMode=yes \
      -o LogLevel=ERROR \
      "${user}@${inst_id}" "echo ok" >/dev/null 2>&1
  else
    ssh -p "${SSH_PORT:-22}" -i "$key" \
      -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=10 \
      -o BatchMode=yes \
      -o LogLevel=ERROR \
      "${user}@${host}" "echo ok" >/dev/null 2>&1
  fi
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

  local user key region inst_id
  user=$(_get_ssh_user_for_host "$target_host")
  key=$(_get_ssh_key_for_host "$target_host")
  region=$(_get_region_for_host "$target_host")
  inst_id=$(_resolve_node_instance_id "$target_host")

  fuser -k "${local_port}/tcp" 2>/dev/null || true

  if [ "${USE_SSM:-true}" == "true" ] && [[ "$inst_id" =~ ^i-[0-9a-f]+$ ]]; then
    ssh -fN \
      -i "$key" \
      -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region ${region}" \
      -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=15 \
      -o BatchMode=yes \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=3 \
      -L "${local_port}:127.0.0.1:6443" \
      "${user}@${inst_id}" 2>/dev/null || true
  else
    ssh -fN \
      -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=10 \
      -o BatchMode=yes \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=3 \
      -p "${SSH_PORT:-22}" -i "$key" \
      -L "${local_port}:127.0.0.1:6443" \
      "${user}@${target_host}" 2>/dev/null || true
  fi

  local _w=0
  while ! nc -z 127.0.0.1 "$local_port" >/dev/null 2>&1 && [ $_w -lt 10 ]; do
    sleep 0.5
    _w=$((_w + 1))
  done
}

ensure_all_cluster_tunnels() {
  local phase_num="${1:-}"
  local phase_int=0
  if [ -n "$phase_num" ]; then
    phase_int=$(echo "$phase_num" | sed 's/[^0-9]//g')
  fi

  for hub in "${HUB_LIST[@]}"; do
    local hub_host="${NODE_HOSTS[$hub]:-$hub}"
    [ "$hub_host" != "127.0.0.1" ] && [ "$hub_host" != "localhost" ] && \
      ensure_cluster_ssh_tunnel "$hub" "$hub_host"
  done

  # Only connect to spokes starting in phase 13 (when spoke clusters are created)
  if [ -z "$phase_num" ] || [ "${phase_int:-0}" -ge 13 ]; then
    for spoke in "${SPOKE_LIST[@]}"; do
      local spoke_host="${NODE_HOSTS[$spoke]:-$spoke}"
      [ "$spoke_host" != "127.0.0.1" ] && [ "$spoke_host" != "localhost" ] && \
        ensure_cluster_ssh_tunnel "$spoke" "$spoke_host"
    done
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
WG_HUB1_IP="${WG_IPS[${HUB_LIST[0]:-primaryhub}]:-10.99.0.1}"
WG_HUB2_IP="${WG_IPS[${HUB_LIST[1]:-secondaryhub}]:-10.99.0.2}"
WG_SPOKE1_IP="${WG_IPS[${SPOKE_LIST[0]:-spoke1}]:-10.99.0.3}"
WG_SPOKE2_IP="${WG_IPS[${SPOKE_LIST[1]:-spoke2}]:-10.99.0.4}"

# External endpoints for WireGuard across regions
GW_ENDPOINT="${PRIMARYHUB_HOST}:${WG_PORT}"
HUB1_ENDPOINT="${PRIMARYHUB_HOST}:${WG_PORT}"
HUB2_ENDPOINT="${SECONDARYHUB_HOST}:${WG_PORT}"
SPOKE1_ENDPOINT="${SPOKE1_HOST}:${WG_PORT}"
SPOKE2_ENDPOINT="${SPOKE2_HOST}:${WG_PORT}"

# WireGuard keypairs – populated by setup_transit_network_and_wg_keys
declare -A WG_PRIV=()
declare -A WG_PUB=()

_get_wg_ip_for_node() {
  local node="$1"
  if [ -n "${WG_IPS[$node]:-}" ]; then
    echo "${WG_IPS[$node]}"
    return 0
  fi
  case "$node" in
    primaryhub)   echo "${WG_HUB1_IP:-10.99.0.1}" ;;
    secondaryhub) echo "${WG_HUB2_IP:-10.99.0.2}" ;;
    spoke1)       echo "${WG_SPOKE1_IP:-10.99.0.3}" ;;
    spoke2)       echo "${WG_SPOKE2_IP:-10.99.0.4}" ;;
    *) echo "" ;;
  esac
}

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
log_fatal()   { echo -e "${RED}${BOLD}[FATAL]${NC}   $*" >&2; exit 1; }
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
