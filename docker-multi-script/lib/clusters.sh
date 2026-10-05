#!/usr/bin/env bash
# ==============================================================================
# lib/clusters.sh – KinD cluster lifecycle, CRDs, OCM init, namespaces, images
#
# Functions (helpers):
#   _create_kind_cluster           – create a KinD cluster + transit net attach
#   _setup_wireguard               – configure wg0 inside a container
#   _wait_for_pod                  – poll until pod is 1/1 Running
#   _sanitize_agentgateway_crds    – strip CEL rules that exceed k8s 1.30 budget
#   _ensure_hub_crds               – idempotently apply all required CRDs
#   _install_crds                  – full CRD install: upstream URL + local fallback
#
# Orchestration steps:
#   create_hub_clusters            – primaryhub + secondaryhub KinD clusters
#   install_crds_on_hubs           – apply all CRDs on both hubs
#   ocm_init                       – clusteradm init on both hubs
#   create_namespaces              – opensandbox-system on all clusters
#   load_custom_image              – build & load opensandbox-server into hubs
#   create_spoke_clusters          – spoke1 + spoke2 KinD clusters
#   wireguard_on_all_clusters      – wg0 on spokes + hub re-apply with spoke peers
#   install_crds_on_spokes         – apply all CRDs on spoke clusters
# ==============================================================================
#
# Functions:
#   _create_kind_cluster   – create a KinD cluster and attach it to the transit net
#   _setup_wireguard       – configure wg0 inside a KinD control-plane container
#   _wait_for_pod          – poll until a pod matching a pattern is 1/1 Running

# Create a KinD cluster (idempotent) and attach it to the transit network.
# Args: <name> <pod-subnet> <svc-subnet> <transit-ip> [use-shared-ca=false]
_create_kind_cluster() {
  local name="$1" pod_subnet="$2" svc_subnet="$3" transit_ip="$4"
  local use_shared_ca="${5:-false}"

  ensure_kernel_inotify_limits

  if kind get clusters 2>/dev/null | grep -q "^${name}$"; then
    log_info "KinD cluster '$name' already exists – preserving."
    kind export kubeconfig --name "$name" 2>/dev/null || true
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
  if ! docker inspect "$cname" >/dev/null 2>&1; then
    log_warn "Cluster container '$cname' not found. Retrying creation..."
    kind delete cluster --name "$name" 2>/dev/null || true
    kind create cluster --name "$name" --config "/tmp/kind-${name}.yaml"
  fi

  kind export kubeconfig --name "$name" 2>/dev/null || true
  docker update --restart=always "$cname" 2>/dev/null || true
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

# Poll until a pod matching <grep-pattern> in <namespace> is 1/1 Running.
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
#
# Functions:
#   _sanitize_agentgateway_crds  – strip CEL rules that exceed k8s 1.30 budget
#   _ensure_hub_crds             – verify & idempotently apply all required CRDs
#   _install_crds                – full CRD install with upstream URL + local fallback

# Strip x-kubernetes-validations from agentgateway-crds.yaml to avoid CEL cost
# budget errors on Kubernetes 1.30+.
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

# Verify and idempotently apply all required CRDs on a cluster context.
# Args: <context>
_ensure_hub_crds() {
  local ctx="$1"
  ensure_sandbox_repo || true

  local crd_dir="${CODE_INSPECTOR_DIR}/crds"

  # Remove any blocking admission policy installed by upstream Gateway API
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

  # Wait for critical CRDs to reach Established condition
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

# Full CRD installation: try upstream URL first, fall back to local file.
# Prints a rich summary table of all established CRDs on the cluster.
# Args: <context>
_install_crds() {
  local ctx="$1"
  if [ "$FORCE_RECONFIGURE" != "true" ] && _check_hub_crds_established "$ctx"; then
    log_success "All required CRDs already established on [${ctx}]. Skipping re-install."
    return 0
  fi

  log_info "Installing Custom Resource Definitions (CRDs) on $ctx..."

  ensure_sandbox_repo || true

  local crd_dir="${CODE_INSPECTOR_DIR}/crds"
  local -a crd_manifests=()

  # Preferred install order
  local default_bundles=(
    "gateway-api-crds.yaml"
    "cloudnative-pg-crds.yaml"
    "metallb-crds.yaml"
    "sealed-secrets-crd.yaml"
    "agentgateway-crds.yaml"
    "opensandbox-crds.yaml"
  )

  # Collect available YAML files in preferred order, then append any extras
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

  # If no local files found, fall back to the known bundle list (URLs only)
  if [ ${#crd_manifests[@]} -eq 0 ]; then
    crd_manifests=("${default_bundles[@]}")
  fi

  # Apply each CRD bundle
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

    # 1. Try upstream URL
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

  # Remove safe-upgrades admission policy to prevent Helm being blocked
  kubectl --context "$ctx" delete validatingadmissionpolicy safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true
  kubectl --context "$ctx" delete validatingadmissionpolicybinding safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true

  # Idempotently ensure all required CRDs are established
  _ensure_hub_crds "$ctx"

  # Print summary table
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

_check_phase_04() {
  _check_hub_crds_established kind-primaryhub   || return 1
  _check_hub_crds_established kind-secondaryhub || return 1
  return 0
}

_do_phase_04_install_crds_on_hubs() {
  ensure_sandbox_repo || log_warn "Repository clone pending; local CRDs may be deferred."

  for hub in primaryhub secondaryhub; do
    _install_crds "kind-${hub}"
  done
  log_success "CRDs installed on all hub clusters."
}

phase_04_install_crds_on_hubs() {
  run_phase "04" "Installing CRDs on Hub Clusters" _check_phase_04 _do_phase_04_install_crds_on_hubs
}

_check_phase_08() {
  local ctx avail
  for ctx in kind-primaryhub kind-secondaryhub; do
    kubectl --context "$ctx" get crd managedclusters.cluster.open-cluster-management.io >/dev/null 2>&1 || return 1
    avail=$(kubectl --context "$ctx" -n open-cluster-management-hub get deployment \
      cluster-manager-registration-webhook -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0)
    [ "${avail:-0}" -ge 1 ] || return 1
  done
  return 0
}

_do_phase_08_ocm_init() {
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

phase_08_ocm_init() {
  run_phase "08" "Initializing OCM on PrimaryHub & SecondaryHub" _check_phase_08 _do_phase_08_ocm_init
}

_check_phase_09() {
  local hub ns
  for hub in primaryhub secondaryhub; do
    for ns in opensandbox-system metallb-system agentgateway-system; do
      kubectl --context "kind-${hub}" get namespace "$ns" >/dev/null 2>&1 || return 1
    done
  done
  return 0
}

_do_phase_09_create_namespaces() {
  for hub in primaryhub secondaryhub; do
    for ns in opensandbox-system metallb-system agentgateway-system; do
      kubectl --context "kind-${hub}" create namespace "$ns" \
        --dry-run=client -o yaml | kubectl --context "kind-${hub}" apply -f -
    done
  done

  log_success "Namespaces ready on both hubs."
}

phase_09_create_namespaces() {
  run_phase "09" "Creating Application Namespaces on Hub Clusters" _check_phase_09 _do_phase_09_create_namespaces
}

_check_phase_10() {
  local img="01community/01sandbox-opensandbox-server:v0.7.10-ocm"
  docker image inspect "$img" >/dev/null 2>&1 || return 1

  local hub
  for hub in primaryhub secondaryhub; do
    docker exec "${hub}-control-plane" crictl images 2>/dev/null | grep -q "01sandbox-opensandbox-server" || return 1
  done
  return 0
}

_do_phase_10_load_custom_image() {
  ensure_sandbox_repo

  local img="01community/01sandbox-opensandbox-server:v0.7.10-ocm"
  if docker image inspect "$img" >/dev/null 2>&1 && [ "$FORCE_RECONFIGURE" != "true" ]; then
    log_info "Image $img already built locally – skipping build."
  else
    if [ ! -d "${OPENSANDBOX_BUILD_DIR}" ] || [ ! -f "${OPENSANDBOX_BUILD_DIR}/Dockerfile" ]; then
      log_error "Dockerfile not found at detected build path: ${OPENSANDBOX_BUILD_DIR}"
      log_error "Failed to locate opensandbox-server/docker-build context."
      exit 1
    fi
    log_info "Building $img from: ${OPENSANDBOX_BUILD_DIR}..."
    docker build -t "$img" "${OPENSANDBOX_BUILD_DIR}"
    log_success "Built $img successfully with OCM workload provider support."
  fi

  for hub in primaryhub secondaryhub; do
    if docker exec "${hub}-control-plane" crictl images 2>/dev/null | grep -q "01sandbox-opensandbox-server" && [ "$FORCE_RECONFIGURE" != "true" ]; then
      log_info "$img already loaded in $hub – skipping kind load."
    else
      log_info "Loading $img into $hub..."
      kind load docker-image "$img" --name "$hub"
      kubectl --context "kind-${hub}" rollout restart deployment/opensandbox-server -n opensandbox-system 2>/dev/null || true
    fi
  done

  log_success "Custom image ready on both hubs."
}

phase_10_load_custom_image() {
  run_phase "10" "Building & Loading Custom opensandbox-server Image" _check_phase_10 _do_phase_10_load_custom_image
}

_check_phase_13() {
  kind get clusters 2>/dev/null | grep -q '^spoke1$' || return 1
  kind get clusters 2>/dev/null | grep -q '^spoke2$' || return 1
  kind export kubeconfig --name spoke1 2>/dev/null || true
  kind export kubeconfig --name spoke2 2>/dev/null || true
  kubectl --context kind-spoke1 get node spoke1-control-plane -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q "True" || return 1
  kubectl --context kind-spoke2 get node spoke2-control-plane -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q "True" || return 1
  # Ensure the control-plane NoSchedule taint is absent on both spokes
  kubectl --context kind-spoke1 get node spoke1-control-plane \
    -o jsonpath='{.spec.taints}' 2>/dev/null | grep -q 'NoSchedule' && return 1 || true
  kubectl --context kind-spoke2 get node spoke2-control-plane \
    -o jsonpath='{.spec.taints}' 2>/dev/null | grep -q 'NoSchedule' && return 1 || true
  return 0
}

_do_phase_13_create_spoke_clusters() {
  _create_kind_cluster "spoke1" "10.246.0.0/16" "10.98.0.0/16"  "$SPOKE1_TRANSIT_IP" "false"
  _create_kind_cluster "spoke2" "10.247.0.0/16" "10.100.0.0/16" "$SPOKE2_TRANSIT_IP" "false"

  # KinD single-node clusters taint the control-plane node with NoSchedule by default.
  # Since there are no worker nodes on spokes, klusterlet (and all user workloads)
  # would be permanently stuck in Pending / FailedScheduling.
  # Remove the taint so pods can schedule on the sole control-plane node.
  log_info "Removing control-plane NoSchedule taint from spoke nodes..."
  for spoke in spoke1 spoke2; do
    kubectl --context "kind-${spoke}" taint node "${spoke}-control-plane" \
      node-role.kubernetes.io/control-plane:NoSchedule- 2>/dev/null || true
    kubectl --context "kind-${spoke}" taint node "${spoke}-control-plane" \
      node-role.kubernetes.io/master:NoSchedule- 2>/dev/null || true
    log_info "  ${spoke}: control-plane taint removed."
  done

  # Verify both spoke containers exist before completing Phase 13
  for spoke in spoke1 spoke2; do
    if ! docker inspect "${spoke}-control-plane" >/dev/null 2>&1; then
      log_error "Critical: ${spoke}-control-plane container does not exist after Phase 13!"
      return 1
    fi
  done

  log_success "Spoke clusters created."
}

phase_13_create_spoke_clusters() {
  run_phase "13" "Creating Spoke Clusters" _check_phase_13 _do_phase_13_create_spoke_clusters
}
#
# After the spokes exist their public keys are known, so the hub wg0.conf
# files must be re-written to include the spoke [Peer] entries.

_check_phase_14() {
  _check_wg_active spoke1-control-plane       "$WG_SPOKE1_IP" || return 1
  _check_wg_active spoke2-control-plane       "$WG_SPOKE2_IP" || return 1
  _check_wg_active primaryhub-control-plane   "$WG_HUB1_IP"   || return 1
  _check_wg_active secondaryhub-control-plane "$WG_HUB2_IP"   || return 1

  # Check that hub configs already include the spoke peers
  docker exec primaryhub-control-plane grep -q "${WG_PUB[spoke1]}" /etc/wireguard/wg0.conf 2>/dev/null || return 1
  docker exec primaryhub-control-plane grep -q "${WG_PUB[spoke2]}" /etc/wireguard/wg0.conf 2>/dev/null || return 1

  return 0
}

_do_phase_14_wireguard_on_all_clusters() {
  _setup_wireguard "spoke1-control-plane" "spoke1" "$WG_SPOKE1_IP"
  _setup_wireguard "spoke2-control-plane" "spoke2" "$WG_SPOKE2_IP"

  # Re-apply on hubs so their wg0.conf now includes the spoke [Peer] entries
  log_info "Re-applying WireGuard on hubs (spoke peers now included)..."
  _setup_wireguard "primaryhub-control-plane"   "primaryhub"   "$WG_HUB1_IP"
  _setup_wireguard "secondaryhub-control-plane" "secondaryhub" "$WG_HUB2_IP"

  log_success "WireGuard overlay peer-complete on all 4 clusters."
}

phase_14_wireguard_on_all_clusters() {
  run_phase "14" "WireGuard on All Clusters (Spokes + Hub Re-apply)" _check_phase_14 _do_phase_14_wireguard_on_all_clusters
}

_check_phase_15() {
  _check_hub_crds_established kind-spoke1 || return 1
  _check_hub_crds_established kind-spoke2 || return 1
  return 0
}

_do_phase_15_install_crds_on_spokes() {
  ensure_sandbox_repo || log_warn "Repository clone pending; local CRDs may be deferred."

  for spoke in spoke1 spoke2; do
    _install_crds "kind-${spoke}"
  done
  log_success "CRDs installed on all spoke clusters."
}

phase_15_install_crds_on_spokes() {
  run_phase "15" "Installing CRDs on Spoke Clusters" _check_phase_15 _do_phase_15_install_crds_on_spokes
}
