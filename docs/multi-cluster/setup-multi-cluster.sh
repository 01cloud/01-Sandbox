#!/usr/bin/env bash
set -euo pipefail

# ===========================================
# COLORS AND FORMATTING
# ===========================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# ===========================================
# GLOBAL CONFIGURATION
# ===========================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../" && pwd)"

# Version configuration
KIND_VERSION="v0.23.0"
HELM_VERSION="v3.14.0"
CILIUM_VERSION="0.18.6"
CILIUM_CNI_VERSION="1.18.6"

# Default values
DEFAULT_DOMAIN_SUFFIX="127-0-0-1.loki.com"

# Cluster tracking (Hubs & Spokes)
declare -a HUB_CLUSTER_NAMES=()
declare -a HUB_CLUSTER_IDS=()
declare -a HUB_CONTEXTS=()
declare -a HUB_POD_SUBNETS=()
declare -a HUB_SVC_SUBNETS=()
declare -a HUB_API_PORTS=()

declare -a SPOKE_CLUSTER_NAMES=()
declare -a SPOKE_CLUSTER_IDS=()
declare -a SPOKE_CONTEXTS=()
declare -a SPOKE_POD_SUBNETS=()
declare -a SPOKE_SVC_SUBNETS=()
declare -a SPOKE_API_PORTS=()

# ===========================================
# UTILITY FUNCTIONS
# ===========================================
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1" >&2
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

progress() {
    echo ""
    echo -e "${BLUE}==================================================${NC}"
    echo -e "${BLUE}[PROGRESS]${NC} $1"
    echo -e "${BLUE}==================================================${NC}"
}

print_header() {
    echo -e "${CYAN}"
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║           $1"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

cleanup_on_failure() {
    log_error "Setup failed. Cleaning up clusters..."
    for cluster in "${HUB_CLUSTER_NAMES[@]}"; do
        kind delete cluster --name "$cluster" 2>/dev/null || true
    done
    for cluster in "${SPOKE_CLUSTER_NAMES[@]}"; do
        kind delete cluster --name "$cluster" 2>/dev/null || true
    done
    exit 1
}

detect_existing_kind_clusters() {
    log_info "Detecting existing kind clusters from kubeconfig..."

    EXISTING_KIND_CONTEXTS=()

    while IFS= read -r ctx; do
        [[ -n "$ctx" ]] && EXISTING_KIND_CONTEXTS+=("$ctx")
    done < <(kubectl config get-contexts -o name 2>/dev/null | grep "^kind-" || true)

    if [ ${#EXISTING_KIND_CONTEXTS[@]} -eq 0 ]; then
        log_info "No existing kind clusters detected"
        return
    fi

    echo ""
    echo -e "${YELLOW}Existing kind clusters detected:${NC}"

    for ctx in "${EXISTING_KIND_CONTEXTS[@]}"; do
        cluster_name="${ctx#kind-}"
        api_port=$(kubectl config view --minify --context "$ctx" -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null | grep -oE '[0-9]+$')

        echo ""
        echo "  - Cluster: $ctx"
        echo "    API Server Port: ${api_port:-unknown}"
    done

    echo ""
}

trap cleanup_on_failure ERR

# ===========================================
# NETWORK UTILITIES
# ===========================================
get_ip_address() {
    local ip
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case $ID in
            ubuntu)
                ip=$(hostname -I | awk '{print $1}')
                ;;
            arch|manjaro)
                ip=$(ip -4 addr show scope global | awk '/inet/ {print $2}' | cut -d/ -f1 | head -1)
                ;;
            *)
                if command -v hostname >/dev/null 2>&1; then
                    ip=$(hostname -I | awk '{print $1}')
                elif command -v ip >/dev/null 2>&1; then
                    ip=$(ip -4 addr show scope global | awk '/inet/ {print $2}' | cut -d/ -f1 | head -1)
                fi
                ;;
        esac
    fi

    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        echo "$ip"
    else
        log_error "Unable to determine valid IP address. Got: $ip"
        exit 1
    fi
}

is_port_in_use() {
    local port=$1
    if command -v ss >/dev/null 2>&1; then
        ss -tuln | awk '{print $5}' | grep -q -E ":${port}$"
    elif command -v netstat >/dev/null 2>&1; then
        netstat -tuln | awk '{print $4}' | grep -q -E ":${port}$"
    elif command -v lsof >/dev/null 2>&1; then
        lsof -i ":${port}" >/dev/null 2>&1
    else
        (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null && exec 3<&- && exec 3>&-
    fi
}

is_port_in_assigned_list() {
    local port=$1
    for p in "${HUB_API_PORTS[@]}" "${SPOKE_API_PORTS[@]}"; do
        if [ "$p" == "$port" ]; then
            return 0
        fi
    done
    return 1
}

get_next_available_port() {
    local start_port=$1
    local port=$start_port
    while is_port_in_use "$port" || is_port_in_assigned_list "$port"; do
        port=$((port + 1))
    done
    echo "$port"
}

# ===========================================
# WAITING FUNCTIONS
# ===========================================
wait_for_pods() {
    local context=$1
    local namespace=$2
    local selector=$3
    local timeout=${4:-300}

    log_info "Waiting for pods in $namespace (selector: $selector) on $context to be ready..."
    if kubectl --context "$context" wait --for=condition=Ready pods \
        --selector="$selector" \
        --namespace="$namespace" \
        --timeout="${timeout}s" 2>/dev/null; then
        log_info "Pods are ready in $namespace on $context"
    else
        log_warn "Timeout waiting for pods in $namespace on $context, continuing anyway..."
    fi
}

wait_for_namespace() {
    local context=$1
    local namespace=$2
    local timeout=${3:-60}

    log_info "Waiting for namespace $namespace on $context to be created..."
    local count=0
    while ! kubectl --context "$context" get namespace "$namespace" >/dev/null 2>&1; do
        if [ $count -ge $timeout ]; then
            log_error "Timeout waiting for namespace $namespace on $context"
            exit 1
        fi
        sleep 1
        count=$((count + 1))
    done
    log_info "Namespace $namespace on $context is ready"
}

# ===========================================
# USER INPUT COLLECTION
# ===========================================
collect_cluster_configuration() {
    print_header "Unified Kubernetes Multi-Cluster Setup Script"

    echo ""
    echo -e "${YELLOW}What would you like to set up?${NC}"
    echo "  1) Hub cluster(s) only"
    echo "  2) Hub cluster(s) + Spoke cluster(s)"
    echo "  3) Spoke cluster(s) only (requires existing hub)"
    echo ""
    read -p "Enter choice (1/2/3): " SETUP_MODE

    detect_existing_kind_clusters

    case $SETUP_MODE in
        1)
            SETUP_HUB=true
            SETUP_SPOKES=false
            ;;
        2)
            SETUP_HUB=true
            SETUP_SPOKES=true
            ;;
        3)
            SETUP_HUB=false
            SETUP_SPOKES=true
            echo ""
            log_info "Spoke-only mode selected. Checking existing environment..."
            ;;
        *)
            log_error "Invalid choice"
            exit 1
            ;;
    esac

    # Domain configuration
    echo ""
    read -p "Enter domain suffix (default: $DEFAULT_DOMAIN_SUFFIX): " DOMAIN_SUFFIX
    DOMAIN_SUFFIX=${DOMAIN_SUFFIX:-$DEFAULT_DOMAIN_SUFFIX}
    WILDCARD_DOMAIN="*.${DOMAIN_SUFFIX}"

    # Hub clusters configuration
    if [ "$SETUP_HUB" = true ]; then
        echo ""
        echo -e "${CYAN}=== Hub Clusters Configuration ===${NC}"

        read -p "How many hub clusters do you want to create? (default: 1): " NUM_HUBS
        NUM_HUBS=${NUM_HUBS:-1}

        if ! [[ "$NUM_HUBS" =~ ^[0-9]+$ ]] || [ "$NUM_HUBS" -lt 1 ]; then
            log_error "Invalid number of hub clusters"
            exit 1
        fi

        for ((i=1; i<=NUM_HUBS; i++)); do
            echo ""
            if [ "$NUM_HUBS" -eq 1 ]; then
                echo -e "${MAGENTA}--- Hub Cluster Configuration ---${NC}"
                default_hub_name="hub"
            else
                echo -e "${MAGENTA}--- Hub Cluster $i Configuration ---${NC}"
                default_hub_name="hub$i"
            fi

            read -p "Enter hub $i name (default: $default_hub_name): " hub_name
            hub_name=${hub_name:-$default_hub_name}
            HUB_CLUSTER_NAMES+=("$hub_name")
            HUB_CONTEXTS+=("kind-$hub_name")

            # Cluster ID (Hub IDs start from 1)
            HUB_CLUSTER_IDS+=("$i")

            candidate_port=$((6443 + (i - 1) * 1000))
            default_api_port=$(get_next_available_port "$candidate_port")
            read -p "Enter hub $i API port (default: $default_api_port): " api_port
            api_port=${api_port:-$default_api_port}
            if is_port_in_use "$api_port" || is_port_in_assigned_list "$api_port"; then
                free_port=$(get_next_available_port "$api_port")
                log_warn "Port $api_port is already in use on host! Automatically using available port $free_port."
                api_port=$free_port
            fi
            HUB_API_PORTS+=("$api_port")

            # Hub CIDR Subnets
            default_pod_subnet="10.$((10 + i * 2)).0.0/16"
            read -p "Enter hub $i pod subnet (default: $default_pod_subnet): " pod_subnet
            pod_subnet=${pod_subnet:-$default_pod_subnet}
            HUB_POD_SUBNETS+=("$pod_subnet")

            default_svc_subnet="10.$((11 + i * 2)).0.0/16"
            read -p "Enter hub $i service subnet (default: $default_svc_subnet): " svc_subnet
            svc_subnet=${svc_subnet:-$default_svc_subnet}
            HUB_SVC_SUBNETS+=("$svc_subnet")
        done
    fi

    # Spoke clusters configuration
    if [ "$SETUP_SPOKES" = true ]; then
        echo ""
        echo -e "${CYAN}=== Spoke Clusters Configuration ===${NC}"

        read -p "How many spoke clusters do you want to create? " NUM_SPOKES

        if ! [[ "$NUM_SPOKES" =~ ^[0-9]+$ ]] || [ "$NUM_SPOKES" -lt 1 ]; then
            log_error "Invalid number of spoke clusters"
            exit 1
        fi

        local hub_count=${#HUB_CLUSTER_NAMES[@]}
        [ $hub_count -eq 0 ] && hub_count=1

        for ((i=1; i<=NUM_SPOKES; i++)); do
            echo ""
            echo -e "${MAGENTA}--- Spoke Cluster $i Configuration ---${NC}"

            read -p "Enter spoke $i name (default: spoke$i): " spoke_name
            spoke_name=${spoke_name:-spoke$i}
            SPOKE_CLUSTER_NAMES+=("$spoke_name")
            SPOKE_CONTEXTS+=("kind-$spoke_name")

            # Cluster ID (Spoke IDs continue after Hub IDs)
            cluster_id=$((hub_count + i))
            SPOKE_CLUSTER_IDS+=("$cluster_id")

            candidate_port=$((6443 + (hub_count + i - 1) * 1000))
            default_spoke_api_port=$(get_next_available_port "$candidate_port")
            read -p "Enter spoke $i API port (default: $default_spoke_api_port): " api_port
            api_port=${api_port:-$default_spoke_api_port}
            if is_port_in_use "$api_port" || is_port_in_assigned_list "$api_port"; then
                free_port=$(get_next_available_port "$api_port")
                log_warn "Port $api_port is already in use on host! Automatically using available port $free_port."
                api_port=$free_port
            fi
            SPOKE_API_PORTS+=("$api_port")

            # Default subnets increment after hub count
            default_pod_subnet="10.$((10 + (hub_count + i) * 2)).0.0/16"
            read -p "Enter spoke $i pod subnet (default: $default_pod_subnet): " pod_subnet
            pod_subnet=${pod_subnet:-$default_pod_subnet}
            SPOKE_POD_SUBNETS+=("$pod_subnet")

            default_svc_subnet="10.$((11 + (hub_count + i) * 2)).0.0/16"
            read -p "Enter spoke $i service subnet (default: $default_svc_subnet): " svc_subnet
            svc_subnet=${svc_subnet:-$default_svc_subnet}
            SPOKE_SVC_SUBNETS+=("$svc_subnet")
        done
    fi

    # Display configuration summary
    display_configuration_summary
}

display_configuration_summary() {
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║              Configuration Summary                         ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    echo "Domain Suffix:       $DOMAIN_SUFFIX"
    echo ""

    if [ "$SETUP_HUB" = true ]; then
        echo -e "${CYAN}Hub Clusters (${#HUB_CLUSTER_NAMES[@]}):${NC}"
        for ((i=0; i<${#HUB_CLUSTER_NAMES[@]}; i++)); do
            echo "  Hub $((i+1)):"
            echo "    Name:            ${HUB_CLUSTER_NAMES[$i]}"
            echo "    Context:         ${HUB_CONTEXTS[$i]}"
            echo "    Cluster ID:      ${HUB_CLUSTER_IDS[$i]}"
            echo "    API Port:        ${HUB_API_PORTS[$i]}"
            echo "    Pod Subnet:      ${HUB_POD_SUBNETS[$i]}"
            echo "    Service Subnet:  ${HUB_SVC_SUBNETS[$i]}"
            echo ""
        done
    fi

    if [ "$SETUP_SPOKES" = true ]; then
        echo -e "${CYAN}Spoke Clusters (${#SPOKE_CLUSTER_NAMES[@]}):${NC}"
        for ((i=0; i<${#SPOKE_CLUSTER_NAMES[@]}; i++)); do
            echo "  Spoke $((i+1)):"
            echo "    Name:            ${SPOKE_CLUSTER_NAMES[$i]}"
            echo "    Context:         ${SPOKE_CONTEXTS[$i]}"
            echo "    Cluster ID:      ${SPOKE_CLUSTER_IDS[$i]}"
            echo "    API Port:        ${SPOKE_API_PORTS[$i]}"
            echo "    Pod Subnet:      ${SPOKE_POD_SUBNETS[$i]}"
            echo "    Service Subnet:  ${SPOKE_SVC_SUBNETS[$i]}"
            echo ""
        done
    fi

    echo ""
    read -p "Proceed with this configuration? (y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_info "Setup cancelled by user"
        exit 0
    fi
}

# ===========================================
# CLUSTER CREATION
# ===========================================
create_cluster() {
    local name=$1
    local pod_subnet=$2
    local svc_subnet=$3
    local api_addr=$4
    local api_port=$5

    if kind get clusters 2>/dev/null | grep -q "^${name}$"; then
        log_info "Cluster $name already exists. Skipping..."
        return
    fi

    log_info "Creating cluster $name..."
    cat <<EOF | kind create cluster --name "$name" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  podSubnet: "$pod_subnet"
  serviceSubnet: "$svc_subnet"
  disableDefaultCNI: true
  apiServerAddress: "$api_addr"
  apiServerPort: $api_port
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: ClusterConfiguration
    apiServer:
      certSANs:
      - "$api_addr"
      - "127.0.0.1"
EOF
}

# ===========================================
# MCS API INSTALLATION
# ===========================================
install_mcs_crds() {
    local context=$1

    if [[ -z "$context" ]]; then
        log_error "No context provided to install_mcs_crds"
        return 1
    fi

    log_info "Installing MCS API CRDs on cluster context: $context"

    if kubectl --context "$context" get crd serviceexports.multicluster.x-k8s.io >/dev/null 2>&1; then
        log_info "MCS API CRDs already installed on $context. Skipping..."
    else
        log_info "Applying MCS API CRDs on $context..."
        kubectl --context "$context" apply -f https://raw.githubusercontent.com/kubernetes-sigs/mcs-api/refs/heads/master/config/crd/multicluster.x-k8s.io_serviceexports.yaml
        kubectl --context "$context" apply -f https://raw.githubusercontent.com/kubernetes-sigs/mcs-api/refs/heads/master/config/crd/multicluster.x-k8s.io_serviceimports.yaml
    fi
}

# ===========================================
# CILIUM INSTALLATION
# ===========================================
install_cilium_cli() {
    local CILIUM_CLI_VERSION="v${CILIUM_VERSION}"

    if command -v cilium &> /dev/null; then
        local INSTALLED_VERSION
        INSTALLED_VERSION=$(cilium version --client 2>/dev/null | awk '{print $1}' | sed 's/v//')

        if [ "$INSTALLED_VERSION" = "$CILIUM_VERSION" ]; then
            log_info "Cilium CLI already installed: v$INSTALLED_VERSION"
            return
        else
            log_info "Different Cilium CLI version detected (v$INSTALLED_VERSION). Reinstalling..."
        fi
    fi

    local CLI_ARCH="amd64"
    if [ "$(uname -m)" = "aarch64" ]; then
        CLI_ARCH="arm64"
    fi

    log_info "Installing Cilium CLI ${CILIUM_CLI_VERSION} (${CLI_ARCH})..."

    local TMP_DIR
    TMP_DIR=$(mktemp -d)
    cd "$TMP_DIR" || exit 1

    curl -L --fail --remote-name-all \
        https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${CLI_ARCH}.tar.gz{,.sha256sum}

    sha256sum --check cilium-linux-${CLI_ARCH}.tar.gz.sha256sum

    local INSTALL_DIR="/usr/local/bin"
    if [ ! -w "$INSTALL_DIR" ]; then
        INSTALL_DIR="$HOME/.local/bin"
        mkdir -p "$INSTALL_DIR"
        export PATH="$INSTALL_DIR:$PATH"
        if ! grep -q "$INSTALL_DIR" ~/.bashrc 2>/dev/null; then
            echo "export PATH=$INSTALL_DIR:\$PATH" >> ~/.bashrc
        fi
    fi

    tar xzvfC cilium-linux-${CLI_ARCH}.tar.gz "$INSTALL_DIR"

    cd - >/dev/null || exit 1
    rm -rf "$TMP_DIR"

    log_info "Cilium CLI installed successfully"
}

install_cilium() {
    local context=$1
    local cluster_name=$2
    local cluster_id=$3

    log_info "Installing Cilium on $cluster_name (ID: $cluster_id)..."
    kubectl config use-context "$context"

    local VALUES_FILE="/tmp/cilium-values-${cluster_name}.yaml"
    cat <<EOF > "$VALUES_FILE"
cluster:
  name: $cluster_name
  id: $cluster_id

clustermesh:
  useAPIServer: true
  maxConnectedClusters: 255
  enableEndpointSliceSynchronization: false
  enableMCSAPISupport: true
  annotations: {}
  config:
    enabled: false
    domain: mesh.cilium.io
    clusters: []

hubble:
  enabled: false
  relay:
    enabled: false
  ui:
    enabled: false

operator:
  replicas: 1

enableIPv4Masquerade: true
enableIPv6Masquerade: false
EOF

    helm upgrade --install cilium cilium/cilium \
        --namespace kube-system \
        --values "$VALUES_FILE" \
        --wait --timeout 10m --version ${CILIUM_CNI_VERSION}

    cilium status --context "$context" --wait

    rm -f "$VALUES_FILE"

    log_info "Cilium installation completed on $cluster_name"
}

# ===========================================
# METALLB INSTALLATION
# ===========================================
install_metallb() {
    local context=$1
    local cluster_name=$2

    log_info "Installing MetalLB on $cluster_name ($context)"

    kubectl --context "$context" apply -f https://raw.githubusercontent.com/metallb/metallb/v0.13.5/config/manifests/metallb-native.yaml
    sleep 5

    log_info "Waiting for MetalLB controller pod to be ready..."
    while true; do
        ready=$(kubectl --context "$context" get pod -n metallb-system -l component=controller 2>/dev/null | grep controller | awk '{print $2}' || echo "")
        if [ "$ready" == "1/1" ]; then
            break
        fi
        echo "MetalLB status :: $ready, sleeping for 10 seconds..."
        sleep 10
    done

    kubectl --context "$context" wait --for=condition=Available --timeout=60s deployment/controller -n metallb-system || {
        log_warn "Webhook deployment not marked available, sleeping 10s just in case..."
        sleep 10
    }

    log_info "Sleeping 10s to ensure webhook is reachable..."
    sleep 10

    # Detect Docker network
    network=$(docker network inspect kind \
      | grep -oE '"Subnet": *"([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]+"' \
      | head -n1 \
      | cut -d '"' -f4 \
      | cut -d '.' -f1,2)

    if [ -z "$network" ]; then
        log_warn "Could not detect IPv4 subnet from Docker, using default 172.18"
        network="172.18"
    else
        log_info "Detected Docker IPv4 network prefix: $network"
    fi

    # Assign unique IP pool per hub/spoke cluster
    local cluster_idx=0
    local is_hub=false
    for ((i=0; i<${#HUB_CLUSTER_NAMES[@]}; i++)); do
        if [ "${HUB_CLUSTER_NAMES[$i]}" == "$cluster_name" ]; then
            cluster_idx=$i
            is_hub=true
            break
        fi
    done

    if [ "$is_hub" = true ]; then
        local start_ip=$((200 + cluster_idx * 10))
        local end_ip=$((start_ip + 9))
        ip_range="$network.254.$start_ip-$network.254.$end_ip"
    else
        local spoke_idx=0
        for ((i=0; i<${#SPOKE_CLUSTER_NAMES[@]}; i++)); do
            if [ "${SPOKE_CLUSTER_NAMES[$i]}" == "$cluster_name" ]; then
                spoke_idx=$i
                break
            fi
        done
        local hub_count=${#HUB_CLUSTER_NAMES[@]}
        [ $hub_count -eq 0 ] && hub_count=1
        local start_ip=$((200 + hub_count * 10 + spoke_idx * 10 + 1))
        local end_ip=$((start_ip + 9))
        ip_range="$network.254.$start_ip-$network.254.$end_ip"
    fi

    log_info "Configuring IPAddressPool for $cluster_name with range: $ip_range"

    kubectl --context "$context" delete ipaddresspool metallb-pool -n metallb-system --ignore-not-found

    cat <<EOF | kubectl --context "$context" apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: metallb-pool
  namespace: metallb-system
spec:
  addresses:
  - $ip_range
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: metallb-l2
  namespace: metallb-system
EOF

    log_info "MetalLB installed successfully on $cluster_name"
}

# ===========================================
# CERT-MANAGER & MKCERT SETUP
# ===========================================
setup_cert_manager() {
    progress "Setting up trusted local HTTPS (mkcert + cert-manager)"

    if ! command -v mkcert &> /dev/null; then
        log_info "Installing mkcert..."
        if [[ "$OSTYPE" == "darwin"* ]]; then
            if ! command -v brew &> /dev/null; then
                log_error "Homebrew is required for macOS installation"
                exit 1
            fi
            brew install mkcert
        elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
            sudo apt update
            sudo apt install -y mkcert
        else
            log_error "Unsupported operating system"
            exit 1
        fi
    fi

    mkcert -install
    log_info "mkcert root CA installed into system trust stores"
}

install_cert_manager() {
    local ctx="$1"
    local version="v1.19.2"

    log_info "Installing/Upgrading cert-manager → ${version} on context: $ctx"

    helm upgrade --install cert-manager \
        oci://quay.io/jetstack/charts/cert-manager \
        --kube-context "$ctx" \
        --namespace cert-manager \
        --create-namespace \
        --version "${version}" \
        --set crds.enabled=true \
        --wait \
        --timeout 5m

    if [ $? -ne 0 ]; then
        log_error "cert-manager installation/upgrade failed on $ctx"
        exit 1
    fi

    log_info "cert-manager ${version} successfully installed/upgraded on $ctx"
}

create_mkcert_issuer() {
    local ctx="$1"
    log_info "Creating mkcert trusted CA Issuer on $ctx"

    kubectl --context "$ctx" create secret tls mkcert-ca-secret \
        --cert="$(mkcert -CAROOT)/rootCA.pem" \
        --key="$(mkcert -CAROOT)/rootCA-key.pem" \
        --namespace cert-manager \
        --dry-run=client -o yaml | kubectl --context "$ctx" apply -f -

    cat <<EOF | kubectl --context "$ctx" apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: mkcert-ca
spec:
  ca:
    secretName: mkcert-ca-secret
EOF
}

# ===========================================
# INGRESS-NGINX INSTALLATION
# ===========================================
install_ingress_nginx_trusted() {
    local ctx="$1"
    local ns="ingress-nginx"

    log_info "Installing/Upgrading ingress-nginx with trusted wildcard cert on $ctx"

    helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx --force-update
    helm repo update ingress-nginx

    cat <<EOF | kubectl --context "$ctx" apply -f -
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: wildcard-local-tls
  namespace: default
spec:
  secretName: wildcard-local-tls
  dnsNames:
  - "${WILDCARD_DOMAIN}"
  - "${DOMAIN_SUFFIX}"
  issuerRef:
    name: mkcert-ca
    kind: ClusterIssuer
EOF

    log_info "Waiting for wildcard-local-tls secret to be issued on $ctx..."
    local count=0
    while ! kubectl --context "$ctx" get secret wildcard-local-tls -n default >/dev/null 2>&1; do
        if [ $count -ge 60 ]; then
            log_warn "Timeout waiting for wildcard-local-tls secret on $ctx, proceeding anyway..."
            break
        fi
        sleep 2
        count=$((count + 2))
    done

    helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
        --kube-context "$ctx" \
        --namespace "$ns" \
        --create-namespace \
        --wait --timeout 12m \
        --set controller.service.type=LoadBalancer \
        --set controller.metrics.enabled=true \
        --set defaultBackend.enabled=true \
        --set "controller.extraArgs.default-ssl-certificate=default/wildcard-local-tls"
}

# ===========================================
# OCM HUB INITIALIZATION
# ===========================================
initialize_ocm_hub() {
    local hub_context=$1

    progress "Initializing OCM hub on $hub_context"
    kubectl config use-context "$hub_context"

    if ! kubectl get ns open-cluster-management >/dev/null 2>&1; then
        log_info "Initializing OCM hub on $hub_context..."
        clusteradm init --wait
    else
        log_info "OCM hub already initialized on $hub_context"
    fi

    wait_for_namespace "$hub_context" "open-cluster-management"
}

# ===========================================
# SPOKE CLUSTER JOINING
# ===========================================
join_spoke_clusters() {
    local hub_context=$1

    progress "Joining spoke clusters to hub ($hub_context)"

    kubectl config use-context "$hub_context"

    local HUB_API_SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
    log_info "Hub API server ($hub_context): $HUB_API_SERVER"

    local TOKEN=$(clusteradm get token | grep -oP 'token=\K[^ ]+' | head -1)
    if [ -z "$TOKEN" ]; then
        log_error "Failed to get OCM join token for hub $hub_context"
        exit 1
    fi
    log_info "Retrieved OCM join token for hub $hub_context"

    for ((i=0; i<${#SPOKE_CONTEXTS[@]}; i++)); do
        local CTX=${SPOKE_CONTEXTS[$i]}
        local CLUSTER_NAME=${SPOKE_CLUSTER_NAMES[$i]}

        kubectl config use-context "$CTX"
        if ! kubectl get ns open-cluster-management-agent >/dev/null 2>&1; then
            log_info "Joining $CLUSTER_NAME ($CTX) to hub ($hub_context)..."
            clusteradm join \
                --hub-token "$TOKEN" \
                --hub-apiserver "$HUB_API_SERVER" \
                --wait \
                --cluster-name "$CLUSTER_NAME" \
                --force-internal-endpoint-lookup \
                --context "$CTX"
        else
            log_info "$CLUSTER_NAME ($CTX) is already joined to hub ($hub_context). Skipping..."
        fi
    done
}

# ===========================================
# ACCEPT MANAGED CLUSTERS
# ===========================================
accept_managed_clusters() {
    local hub_context=$1

    progress "Accepting managed clusters on hub ($hub_context)"
    kubectl config use-context "$hub_context"

    local clusters_csv=$(IFS=,; echo "${SPOKE_CLUSTER_NAMES[*]}")
    log_info "Accepting managed clusters on $hub_context: $clusters_csv"
    clusteradm accept --clusters "$clusters_csv" --wait

    # Verify managed clusters
    for cluster in "${SPOKE_CLUSTER_NAMES[@]}"; do
        local ready=false
        for i in {1..15}; do
            local status
            status=$(kubectl --context "$hub_context" get managedcluster "$cluster" -o jsonpath='{.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status}' 2>/dev/null || echo "")
            if [ "$status" = "True" ]; then
                log_info "Cluster $cluster is ready and available on hub $hub_context"
                ready=true
                break
            fi
            log_info "Waiting for cluster $cluster to be ready on hub $hub_context... ($i/15)"
            sleep 2
        done

        if [ "$ready" = false ]; then
            log_error "Cluster $cluster failed to join hub $hub_context properly"
            kubectl --context "$hub_context" get managedcluster "$cluster" -o yaml
            exit 1
        fi
    done
}

# ===========================================
# LABEL MANAGED CLUSTERS
# ===========================================
label_managed_clusters() {
    local hub_context=$1

    log_info "Labeling ManagedClusters on hub $hub_context with clusterset: location-es"
    for cluster in "${SPOKE_CLUSTER_NAMES[@]}"; do
        log_info "Adding label to ManagedCluster: $cluster"
        kubectl --context "$hub_context" label managedcluster "$cluster" \
            cluster.open-cluster-management.io/clusterset=location-es --overwrite
    done
    log_info "Labels applied successfully on $hub_context"
    kubectl --context "$hub_context" get managedclusters --show-labels
}

# ===========================================
# ENABLE MANIFESTWORK FEATURE
# ===========================================
enable_manifestwork_feature() {
    local hub_context=$1
    log_info "Enabling ManifestWork feature on hub context: $hub_context..."
    kubectl --context "$hub_context" label clusterrolebinding -l component=klusterlet-work-agent --overwrite feature=manifestwork 2>/dev/null || true
}

# ===========================================
# CILIUM CLUSTERMESH
# ===========================================
enable_cilium_clustermesh() {
    local all_contexts=("$@")

    progress "Enabling Cilium Clustermesh connectivity"

    # Enable clustermesh on all clusters
    for ctx in "${all_contexts[@]}"; do
        log_info "Enabling clustermesh on $ctx..."
        cilium clustermesh enable --context "$ctx" --service-type=NodePort
    done

    # Wait for clustermesh to be ready
    for ctx in "${all_contexts[@]}"; do
        log_info "Waiting for clustermesh to be ready on $ctx..."
        cilium clustermesh status --context "$ctx" --wait || log_warn "Clustermesh status check failed for $ctx"
    done

    # Connect each cluster with every other cluster
    for ((i=0; i<${#all_contexts[@]}; i++)); do
        for ((j=i+1; j<${#all_contexts[@]}; j++)); do
            c1="${all_contexts[$i]}"
            c2="${all_contexts[$j]}"
            log_info "Connecting $c1 <--> $c2"
            cilium clustermesh connect --context "$c1" --destination-context "$c2" || log_warn "Failed to connect"
        done
    done
}

# ===========================================
# MAIN EXECUTION
# ===========================================
main() {
    print_header "Unified Multi-Cluster Setup Script"

    # Collect user input
    collect_cluster_configuration

    # Get host IP
    HOST_IP=$(get_ip_address)
    log_info "Using host IP: $HOST_IP"

    # Build list of all contexts for later use
    ALL_CONTEXTS=()
    if [ "$SETUP_HUB" = true ]; then
        ALL_CONTEXTS+=("${HUB_CONTEXTS[@]}")
    fi
    if [ "$SETUP_SPOKES" = true ]; then
        ALL_CONTEXTS+=("${SPOKE_CONTEXTS[@]}")
    fi

    # ===========================================
    # CLUSTER CREATION
    # ===========================================
    if [ "$SETUP_HUB" = true ]; then
        progress "Creating hub cluster(s)"
        for ((i=0; i<${#HUB_CLUSTER_NAMES[@]}; i++)); do
            create_cluster "${HUB_CLUSTER_NAMES[$i]}" "${HUB_POD_SUBNETS[$i]}" "${HUB_SVC_SUBNETS[$i]}" "$HOST_IP" "${HUB_API_PORTS[$i]}"
        done
    fi

    if [ "$SETUP_SPOKES" = true ]; then
        progress "Creating spoke cluster(s)"
        for ((i=0; i<${#SPOKE_CLUSTER_NAMES[@]}; i++)); do
            create_cluster "${SPOKE_CLUSTER_NAMES[$i]}" "${SPOKE_POD_SUBNETS[$i]}" "${SPOKE_SVC_SUBNETS[$i]}" "$HOST_IP" "${SPOKE_API_PORTS[$i]}"
        done
    fi

    # ===========================================
    # MCS API INSTALLATION
    # ===========================================
    progress "Installing MCS API CRDs"
    for ctx in "${ALL_CONTEXTS[@]}"; do
        install_mcs_crds "$ctx"
    done

    # ===========================================
    # CILIUM INSTALLATION
    # ===========================================
    progress "Installing Cilium CLI"
    install_cilium_cli

    if ! helm repo list | grep -q "cilium"; then
        log_info "Adding Cilium Helm repository..."
        helm repo add cilium https://helm.cilium.io/
    fi
    helm repo update cilium

    progress "Installing Cilium CNI"
    if [ "$SETUP_HUB" = true ]; then
        for ((i=0; i<${#HUB_CLUSTER_NAMES[@]}; i++)); do
            install_cilium "${HUB_CONTEXTS[$i]}" "${HUB_CLUSTER_NAMES[$i]}" "${HUB_CLUSTER_IDS[$i]}"
        done
    fi

    if [ "$SETUP_SPOKES" = true ]; then
        for ((i=0; i<${#SPOKE_CLUSTER_NAMES[@]}; i++)); do
            install_cilium "${SPOKE_CONTEXTS[$i]}" "${SPOKE_CLUSTER_NAMES[$i]}" "${SPOKE_CLUSTER_IDS[$i]}"
        done
    fi

    # ===========================================
    # METALLB INSTALLATION
    # ===========================================
    progress "Installing MetalLB on all clusters"
    if [ "$SETUP_HUB" = true ]; then
        for ((i=0; i<${#HUB_CLUSTER_NAMES[@]}; i++)); do
            install_metallb "${HUB_CONTEXTS[$i]}" "${HUB_CLUSTER_NAMES[$i]}"
        done
    fi
    if [ "$SETUP_SPOKES" = true ]; then
        for ((i=0; i<${#SPOKE_CLUSTER_NAMES[@]}; i++)); do
            install_metallb "${SPOKE_CONTEXTS[$i]}" "${SPOKE_CLUSTER_NAMES[$i]}"
        done
    fi

    # ===========================================
    # CERT-MANAGER & MKCERT
    # ===========================================
    setup_cert_manager

    progress "Installing cert-manager on all clusters"
    for ctx in "${ALL_CONTEXTS[@]}"; do
        install_cert_manager "$ctx"
    done

    # Wait for cert-manager
    if [ "$SETUP_HUB" = true ]; then
        for ctx in "${HUB_CONTEXTS[@]}"; do
            wait_for_pods "$ctx" "cert-manager" "app.kubernetes.io/instance=cert-manager" 300
        done
    fi
    if [ "$SETUP_SPOKES" = true ]; then
        for ctx in "${SPOKE_CONTEXTS[@]}"; do
            wait_for_pods "$ctx" "cert-manager" "app.kubernetes.io/instance=cert-manager" 180 || true
        done
    fi

    progress "Creating mkcert CA Issuer on all clusters"
    for ctx in "${ALL_CONTEXTS[@]}"; do
        create_mkcert_issuer "$ctx"
    done

    # ===========================================
    # INGRESS-NGINX INSTALLATION
    # ===========================================
    progress "Installing ingress-nginx with trusted certificates"
    for ctx in "${ALL_CONTEXTS[@]}"; do
        install_ingress_nginx_trusted "$ctx"
    done

    if [ "$SETUP_HUB" = true ]; then
        for ctx in "${HUB_CONTEXTS[@]}"; do
            wait_for_pods "$ctx" "ingress-nginx" "app.kubernetes.io/name=ingress-nginx" 300
        done
    fi

    # ===========================================
    # OCM HUB INITIALIZATION
    # ===========================================
    if [ "$SETUP_HUB" = true ]; then
        for ctx in "${HUB_CONTEXTS[@]}"; do
            initialize_ocm_hub "$ctx"
        done
    fi

    # ===========================================
    # SPOKE CLUSTER JOINING
    # ===========================================
    if [ "$SETUP_SPOKES" = true ] && [ "$SETUP_HUB" = true ]; then
        PRIMARY_HUB_CONTEXT="${HUB_CONTEXTS[0]}"
        log_info "Registering spoke clusters with Primary OCM Hub ($PRIMARY_HUB_CONTEXT)..."
        join_spoke_clusters "$PRIMARY_HUB_CONTEXT"
        accept_managed_clusters "$PRIMARY_HUB_CONTEXT"
        label_managed_clusters "$PRIMARY_HUB_CONTEXT"
        enable_manifestwork_feature "$PRIMARY_HUB_CONTEXT"
    fi

    # ===========================================
    # CILIUM CLUSTERMESH
    # ===========================================
    progress "Enabling Cilium Clustermesh"
    enable_cilium_clustermesh "${ALL_CONTEXTS[@]}"

    # ===========================================
    # SETUP COMPLETE
    # ===========================================
    progress "Setup Complete!"

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           Setup Completed Successfully!                   ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Run main function
main "$@"
