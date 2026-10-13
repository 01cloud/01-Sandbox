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
# Create a KinD cluster (idempotent, local or remote)
# Args: <name> <pod-subnet> <svc-subnet> <transit-ip> [use-shared-ca=false] [target-host=127.0.0.1] [wg-ip=""]
_sync_remote_kubeconfig() {
  local name="$1" target_host="$2" wg_ip="$3"
  log_info "Syncing remote kubeconfig for '$name' from $target_host..."
  remote_exec "$target_host" "kind export kubeconfig --name '$name' --kubeconfig /tmp/kubeconfig-${name}.export" 2>/dev/null || true
  local tmp_remote_kube="/tmp/kubeconfig-${name}"
  remote_copy_from "$target_host" "/tmp/kubeconfig-${name}.export" "$tmp_remote_kube"

  local local_port
  local_port=$(_get_tunnel_port_for_cluster "$name")
  ensure_cluster_ssh_tunnel "$name" "$target_host"

  # Rewrite server address to point through local SSH tunnel
  sed -i -E "s|server: https://[^:]+:[0-9]+|server: https://127.0.0.1:${local_port}|g" "$tmp_remote_kube"

  # Clean old stale context/cluster/user from KUBECONFIG first
  kubectl --kubeconfig "${KUBECONFIG}" config delete-context "kind-${name}" 2>/dev/null || true
  kubectl --kubeconfig "${KUBECONFIG}" config delete-cluster "kind-${name}" 2>/dev/null || true
  kubectl --kubeconfig "${KUBECONFIG}" config delete-user "kind-${name}" 2>/dev/null || true

  # Merge into local KUBECONFIG (fresh remote config first)
  KUBECONFIG="${tmp_remote_kube}:${KUBECONFIG}" kubectl config view --flatten > /tmp/kubeconfig-merged 2>/dev/null
  if [ -s /tmp/kubeconfig-merged ]; then
    mv /tmp/kubeconfig-merged "${KUBECONFIG}"
  fi
}

_ensure_systemwide_kubeconfig() {
  local name="$1" target_host="$2"
  log_info "Configuring system-wide kubeconfig on '$target_host' for '$name'..."
  remote_exec "$target_host" "sudo kind export kubeconfig --name '$name' --kubeconfig /etc/kubernetes/admin.conf 2>/dev/null || true; sudo chmod 644 /etc/kubernetes/admin.conf 2>/dev/null || true; echo 'export KUBECONFIG=/etc/kubernetes/admin.conf' | sudo tee /etc/profile.d/kubeconfig.sh >/dev/null; sudo chmod 644 /etc/profile.d/kubeconfig.sh; sudo mkdir -p /home/ssm-user/.kube /home/ec2-user/.kube /root/.kube; sudo cp /etc/kubernetes/admin.conf /home/ssm-user/.kube/config 2>/dev/null || true; sudo cp /etc/kubernetes/admin.conf /home/ec2-user/.kube/config 2>/dev/null || true; sudo cp /etc/kubernetes/admin.conf /root/.kube/config 2>/dev/null || true; sudo chown -R ssm-user:ssm-user /home/ssm-user/.kube 2>/dev/null || true; sudo chown -R ec2-user:ec2-user /home/ec2-user/.kube 2>/dev/null || true"
}

_create_kind_cluster() {
  local name="$1" pod_subnet="$2" svc_subnet="$3" transit_ip="$4"
  local use_shared_ca="${5:-false}"
  local target_host="${6:-127.0.0.1}"
  local wg_ip="${7:-}"

  [ -z "$target_host" ] && target_host="127.0.0.1"

  local is_remote=false
  if [ "$target_host" != "127.0.0.1" ] && [ "$target_host" != "localhost" ]; then
    is_remote=true
  fi

  if $is_remote; then
    if remote_exec "$target_host" "kind get clusters 2>/dev/null" | grep -q "^${name}$"; then
      if remote_exec "$target_host" "docker ps --filter name=${name}-control-plane --format '{{.Names}}' 2>/dev/null" | grep -q "^${name}-control-plane$"; then
        log_info "Remote KinD cluster '$name' on '$target_host' already exists and is running – syncing kubeconfig."
        _sync_remote_kubeconfig "$name" "$target_host" "$wg_ip"
        _ensure_systemwide_kubeconfig "$name" "$target_host"
        return 0
      else
        log_warn "Remote KinD cluster '$name' on '$target_host' exists in metadata but control-plane container is down/missing – recreating..."
        remote_exec "$target_host" "kind delete cluster --name '$name' 2>/dev/null || docker rm -f ${name}-control-plane 2>/dev/null || true"
      fi
    fi
    log_info "Creating remote KinD cluster '$name' on $target_host (Pod:$pod_subnet  Svc:$svc_subnet)..."
  else
    ensure_kernel_inotify_limits
    if kind get clusters 2>/dev/null | grep -q "^${name}$"; then
      log_info "KinD cluster '$name' already exists – preserving."
      kind export kubeconfig --name "$name" 2>/dev/null || true
      return 0
    fi
    log_info "Creating KinD cluster '$name' (Pod:$pod_subnet  Svc:$svc_subnet)..."
  fi

  local extra_mounts=""
  if [ "$use_shared_ca" == "true" ]; then
    local pki_remote_dir="${PKI_DIR}"
    if $is_remote; then
      pki_remote_dir="/tmp/pki"
      # Purge any stale root-owned directories from /tmp/pki created by previous Docker bind mounts
      remote_exec "$target_host" "sudo rm -rf /tmp/pki && sudo mkdir -p /tmp/pki && sudo chown -R \$(id -u):\$(id -g) /tmp/pki && chmod 700 /tmp/pki"
      for keyfile in ca.crt ca.key sa.key sa.pub; do
        if [ -s "${PKI_DIR}/$keyfile" ]; then
          remote_copy_to "$target_host" "${PKI_DIR}/$keyfile" "/tmp/pki/$keyfile"
        else
          remote_exec "$PRIMARYHUB_HOST" "docker exec primaryhub-control-plane cat /etc/kubernetes/pki/$keyfile 2>/dev/null" | \
            remote_exec "$target_host" "cat > /tmp/pki/$keyfile"
        fi
      done
      remote_exec "$target_host" "chmod 600 /tmp/pki/ca.key /tmp/pki/sa.key 2>/dev/null || true; chmod 644 /tmp/pki/ca.crt /tmp/pki/sa.pub 2>/dev/null || true"

      # Strictly verify on target host that all PKI files are non-empty regular files (NOT directories!)
      for keyfile in ca.crt ca.key sa.key sa.pub; do
        if ! remote_exec "$target_host" "test -f /tmp/pki/$keyfile -a -s /tmp/pki/$keyfile"; then
          log_error "Shared PKI file /tmp/pki/$keyfile is missing, empty, or a directory on $target_host!"
          return 1
        fi
      done
    fi
    extra_mounts="  extraMounts:
  - hostPath: ${pki_remote_dir}/ca.crt
    containerPath: /etc/kubernetes/pki/ca.crt
  - hostPath: ${pki_remote_dir}/ca.key
    containerPath: /etc/kubernetes/pki/ca.key
  - hostPath: ${pki_remote_dir}/sa.key
    containerPath: /etc/kubernetes/pki/sa.key
  - hostPath: ${pki_remote_dir}/sa.pub
    containerPath: /etc/kubernetes/pki/sa.pub"
  fi

  # Mount KVM and TUN for spoke clusters to support Kata Firecracker microVMs
  if [[ "$name" =~ ^spoke ]]; then
    if [ -z "$extra_mounts" ]; then
      extra_mounts="  extraMounts:"
    fi
    local has_kvm=false
    if $is_remote; then
      remote_exec "$target_host" "test -c /dev/kvm" 2>/dev/null && has_kvm=true || true
    else
      [ -c /dev/kvm ] && has_kvm=true || true
    fi
    if [ "$has_kvm" = "true" ]; then
      extra_mounts="${extra_mounts}
  - hostPath: /dev/kvm
    containerPath: /dev/kvm"
    fi
    extra_mounts="${extra_mounts}
  - hostPath: /dev/net/tun
    containerPath: /dev/net/tun"
  fi

  local target_private_ip=""
  if $is_remote; then
    target_private_ip=$(_resolve_node_private_ip "$target_host")
  fi

  local config_file="/tmp/kind-${name}.yaml"
  cat > "$config_file" <<EOF
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
    - "${target_host}"
    - "${target_private_ip}"
    - "${wg_ip}"
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
  - containerPort: 6443
    hostPort: 6443
    protocol: TCP
  - containerPort: 30432
    hostPort: 30432
    protocol: TCP
  - containerPort: 30379
    hostPort: 30379
    protocol: TCP
${extra_mounts}
EOF

  if $is_remote; then
    # Clean up lingering dead container or lock files
    remote_exec "$target_host" "docker rm -f ${name}-control-plane 2>/dev/null || true; sudo rm -f /etc/kubernetes/admin.conf.lock 2>/dev/null || true"
    remote_copy_to "$target_host" "$config_file" "/tmp/kind-${name}.yaml"
    if ! remote_exec "$target_host" "KUBECONFIG=/tmp/kubeconfig-${name}.export kind create cluster --name '$name' --config /tmp/kind-${name}.yaml --kubeconfig /tmp/kubeconfig-${name}.export"; then
      log_error "Failed to create remote KinD cluster '$name' on '$target_host'!"
      return 1
    fi
    if ! remote_exec "$target_host" "docker ps --filter name=${name}-control-plane --format '{{.Names}}' 2>/dev/null" | grep -q "^${name}-control-plane$"; then
      log_error "KinD control-plane container '${name}-control-plane' is not running on '$target_host' after creation!"
      return 1
    fi
    _ensure_systemwide_kubeconfig "$name" "$target_host"
    _sync_remote_kubeconfig "$name" "$target_host" "$wg_ip"
  else
    if ! kind create cluster --name "$name" --config "$config_file"; then
      log_error "Failed to create KinD cluster '$name'!"
      return 1
    fi
    kind export kubeconfig --name "$name" 2>/dev/null || true
    local cname="${name}-control-plane"
    docker update --restart=always "$cname" 2>/dev/null || true
    if docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$"; then
      docker network connect --ip "$transit_ip" "$TRANSIT_NET_NAME" "$cname" 2>/dev/null || true
    fi
  fi

  log_info "Ensuring control-plane and CoreDNS are ready on $name..."
  kubectl --context "kind-${name}" wait --for=condition=Ready node "${name}-control-plane" --timeout=60s 2>/dev/null || true
  kubectl --context "kind-${name}" -n kube-system wait --for=condition=Ready pods -l k8s-app=kube-dns --timeout=60s 2>/dev/null || true
}

# Configure WireGuard wg0 inside a KinD control-plane container.
# Args: <container> <entity-name> <wg-ip> [extra-ips]
_setup_wireguard() {
  local container="$1" entity="$2" wg_ip="$3" extra_ips="${4:-}"

  # If WireGuard wg0 is already configured with all peers and active, skip reconfiguration
  if [ "$FORCE_RECONFIGURE" != "true" ] && _check_wg_active "$container" "$wg_ip" && \
     docker exec "$container" grep -q "${WG_PUB["gateway"]}" /etc/wireguard/wg0.conf 2>/dev/null && \
     docker exec "$container" grep -q "${WG_PUB["primaryhub"]}" /etc/wireguard/wg0.conf 2>/dev/null && \
     docker exec "$container" grep -q "${WG_PUB["secondaryhub"]}" /etc/wireguard/wg0.conf 2>/dev/null && \
     docker exec "$container" grep -q "${WG_PUB["spoke1"]}" /etc/wireguard/wg0.conf 2>/dev/null && \
     docker exec "$container" grep -q "${WG_PUB["spoke2"]}" /etc/wireguard/wg0.conf 2>/dev/null; then
    log_info "WireGuard wg0 already configured and active in $container ($wg_ip) – skipping."
    return 0
  fi

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
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    [ -s "${PKI_DIR}/ca.crt" ] && [ -s "${PKI_DIR}/ca.key" ] && \
    [ -s "${PKI_DIR}/sa.key" ] && [ -s "${PKI_DIR}/sa.pub" ] || return 1

    remote_exec "$PRIMARYHUB_HOST" "kind get clusters 2>/dev/null" | grep -q '^primaryhub$' || return 1
    remote_exec "$SECONDARYHUB_HOST" "kind get clusters 2>/dev/null" | grep -q '^secondaryhub$' || return 1
    ensure_cluster_ssh_tunnel "primaryhub" "$PRIMARYHUB_HOST"
    ensure_cluster_ssh_tunnel "secondaryhub" "$SECONDARYHUB_HOST"
    kubectl --context kind-primaryhub   get node primaryhub-control-plane   >/dev/null 2>&1 || return 1
    kubectl --context kind-secondaryhub get node secondaryhub-control-plane >/dev/null 2>&1 || return 1
    return 0
  fi

  kind get clusters 2>/dev/null | grep -q '^primaryhub$'   || return 1
  kind get clusters 2>/dev/null | grep -q '^secondaryhub$' || return 1

  [ -s "${PKI_DIR}/ca.crt" ] && [ -s "${PKI_DIR}/ca.key" ] && \
  [ -s "${PKI_DIR}/sa.key" ] && [ -s "${PKI_DIR}/sa.pub" ] || return 1

  kubectl --context kind-primaryhub   get node primaryhub-control-plane   >/dev/null 2>&1 || return 1
  kubectl --context kind-secondaryhub get node secondaryhub-control-plane >/dev/null 2>&1 || return 1

  # Ensure Root CA matches between primaryhub and secondaryhub
  local pca sca
  pca=$(docker exec primaryhub-control-plane   sha256sum /etc/kubernetes/pki/ca.crt 2>/dev/null | awk '{print $1}')
  sca=$(docker exec secondaryhub-control-plane sha256sum /etc/kubernetes/pki/ca.crt 2>/dev/null | awk '{print $1}')
  [ -n "$pca" ] && [ "$pca" == "$sca" ] || return 1

  docker inspect primaryhub-control-plane   --format '{{json .NetworkSettings.Networks}}' 2>/dev/null | grep -q "$TRANSIT_NET_NAME" || return 1
  docker inspect secondaryhub-control-plane --format '{{json .NetworkSettings.Networks}}' 2>/dev/null | grep -q "$TRANSIT_NET_NAME" || return 1

  return 0
}

_do_phase_03_create_hub_clusters() {
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  # PrimaryHub – this IS the Root CA source
  _create_kind_cluster "primaryhub" "10.244.0.0/16" "10.96.0.0/16" "$HUB1_TRANSIT_IP" "false" "$PRIMARYHUB_HOST" "$WG_HUB1_IP" || {
    log_error "Failed to create or verify primaryhub cluster."
    return 1
  }

  # Always extract shared Root CA & ServiceAccount keys from primaryhub to ensure PKI_DIR is authoritative
  log_info "Extracting shared Root CA & ServiceAccount keys from primaryhub..."
  if $is_remote; then
    remote_exec "$PRIMARYHUB_HOST" "docker cp primaryhub-control-plane:/etc/kubernetes/pki/ca.crt /tmp/ca.crt && \
                                   docker cp primaryhub-control-plane:/etc/kubernetes/pki/ca.key /tmp/ca.key && \
                                   docker cp primaryhub-control-plane:/etc/kubernetes/pki/sa.key /tmp/sa.key && \
                                   docker cp primaryhub-control-plane:/etc/kubernetes/pki/sa.pub /tmp/sa.pub"
    remote_copy_from "$PRIMARYHUB_HOST" "/tmp/ca.crt" "${PKI_DIR}/ca.crt"
    remote_copy_from "$PRIMARYHUB_HOST" "/tmp/ca.key" "${PKI_DIR}/ca.key"
    remote_copy_from "$PRIMARYHUB_HOST" "/tmp/sa.key" "${PKI_DIR}/sa.key"
    remote_copy_from "$PRIMARYHUB_HOST" "/tmp/sa.pub" "${PKI_DIR}/sa.pub"
  else
    docker cp primaryhub-control-plane:/etc/kubernetes/pki/ca.crt "${PKI_DIR}/ca.crt"
    docker cp primaryhub-control-plane:/etc/kubernetes/pki/ca.key "${PKI_DIR}/ca.key"
    docker cp primaryhub-control-plane:/etc/kubernetes/pki/sa.key "${PKI_DIR}/sa.key"
    docker cp primaryhub-control-plane:/etc/kubernetes/pki/sa.pub "${PKI_DIR}/sa.pub"
  fi

  for keyfile in ca.crt ca.key sa.key sa.pub; do
    if [ ! -s "${PKI_DIR}/$keyfile" ]; then
      log_error "Authoritative PKI file ${PKI_DIR}/$keyfile is missing or empty!"
      return 1
    fi
  done

  # If secondaryhub already exists, verify its Root CA matches primaryhub. If not, recreate it.
  if $is_remote; then
    if remote_exec "$SECONDARYHUB_HOST" "kind get clusters 2>/dev/null" | grep -q '^secondaryhub$'; then
      local pca sca
      pca=$(remote_exec "$PRIMARYHUB_HOST" "docker exec primaryhub-control-plane sha256sum /etc/kubernetes/pki/ca.crt" 2>/dev/null | awk '{print $1}')
      sca=$(remote_exec "$SECONDARYHUB_HOST" "docker exec secondaryhub-control-plane sha256sum /etc/kubernetes/pki/ca.crt" 2>/dev/null | awk '{print $1}')
      if [ -n "$pca" ] && [ "$pca" != "$sca" ]; then
        log_warn "secondaryhub has mismatched Root CA ($sca != $pca) – recreating secondaryhub to sync Root CA..."
        remote_exec "$SECONDARYHUB_HOST" "kind delete cluster --name secondaryhub 2>/dev/null || docker rm -f secondaryhub-control-plane 2>/dev/null || true"
      fi
    fi
  else
    if kind get clusters 2>/dev/null | grep -q '^secondaryhub$'; then
      local pca sca
      pca=$(docker exec primaryhub-control-plane   sha256sum /etc/kubernetes/pki/ca.crt 2>/dev/null | awk '{print $1}')
      sca=$(docker exec secondaryhub-control-plane sha256sum /etc/kubernetes/pki/ca.crt 2>/dev/null | awk '{print $1}')
      if [ -n "$pca" ] && [ "$pca" != "$sca" ]; then
        log_warn "secondaryhub has mismatched Root CA ($sca != $pca) – recreating secondaryhub to sync Root CA..."
        kind delete cluster --name secondaryhub 2>/dev/null || docker rm -f secondaryhub-control-plane 2>/dev/null || true
      fi
    fi
  fi

  # SecondaryHub – mounted with PrimaryHub's shared Root CA
  _create_kind_cluster "secondaryhub" "10.245.0.0/16" "10.97.0.0/16" "$HUB2_TRANSIT_IP" "true" "$SECONDARYHUB_HOST" "$WG_HUB2_IP" || {
    log_error "Failed to create or verify secondaryhub cluster."
    return 1
  }

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
    if ! _check_hub_crds_established "kind-${hub}"; then
      log_error "CRDs failed to establish on kind-${hub}!"
      return 1
    fi
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
    if kubectl --context kind-primaryhub get deployment ocm-auto-acceptor -n default >/dev/null 2>&1 || \
       kubectl --context kind-primaryhub get deployment ocm-auto-acceptor -n open-cluster-management >/dev/null 2>&1 || \
       kubectl --context kind-primaryhub get deployment -A 2>/dev/null | grep -q "ocm-auto-acceptor"; then
      log_info "ocm-auto-acceptor already deployed on primaryhub – skipping."
    else
      log_info "Deploying ocm-auto-acceptor on primaryhub..."
      kubectl --context kind-primaryhub apply -f "$auto_acceptor" || true
    fi
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
      if kubectl --context "kind-${hub}" get namespace "$ns" >/dev/null 2>&1; then
        log_info "Namespace '$ns' already exists on $hub – skipping."
      else
        kubectl --context "kind-${hub}" create namespace "$ns" \
          --dry-run=client -o yaml | kubectl --context "kind-${hub}" apply -f -
      fi
    done
  done

  log_success "Namespaces ready on both hubs."
}

phase_09_create_namespaces() {
  run_phase "09" "Creating Application Namespaces on Hub Clusters" _check_phase_09 _do_phase_09_create_namespaces
}

_check_phase_10() {
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    remote_exec "$PRIMARYHUB_HOST" "docker exec primaryhub-control-plane crictl images 2>/dev/null" | grep -q "01sandbox-opensandbox-server" || return 1
    remote_exec "$SECONDARYHUB_HOST" "docker exec secondaryhub-control-plane crictl images 2>/dev/null" | grep -q "01sandbox-opensandbox-server" || return 1
    return 0
  fi

  local img="01community/01sandbox-opensandbox-server:v0.7.10-ocm"
  docker image inspect "$img" >/dev/null 2>&1 || return 1

  local hub
  for hub in primaryhub secondaryhub; do
    docker exec "${hub}-control-plane" crictl images 2>/dev/null | grep -q "01sandbox-opensandbox-server" || return 1
  done
  return 0
}

_do_phase_10_load_custom_image() {
  local img="01community/01sandbox-opensandbox-server:v0.7.10-ocm"
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  local hubs=("primaryhub" "secondaryhub")
  for hub in "${hubs[@]}"; do
    local target_host="127.0.0.1"
    [ "$hub" == "primaryhub" ] && target_host="$PRIMARYHUB_HOST"
    [ "$hub" == "secondaryhub" ] && target_host="$SECONDARYHUB_HOST"

    if $is_remote; then
      if remote_exec "$target_host" "docker exec ${hub}-control-plane crictl images 2>/dev/null" | grep -q "01sandbox-opensandbox-server" && [ "$FORCE_RECONFIGURE" != "true" ]; then
        log_info "$img already loaded in $hub on $target_host – skipping."
      else
        ensure_remote_sandbox_repo "$target_host"
        local user
        user=$(_get_ssh_user_for_host "$target_host")
        local remote_build_dir="/home/${user}/01-Sandbox/opensandbox-server/docker-build"

        if ! remote_exec "$target_host" "test -f '${remote_build_dir}/Dockerfile'"; then
          log_error "Dockerfile not found on $target_host at ${remote_build_dir}"
          return 1
        fi

        log_info "Building $img inside remote EC2 instance $target_host ($hub)..."
        remote_exec "$target_host" "docker build -t '$img' '$remote_build_dir'"
        log_success "Built $img successfully on $target_host."

        log_info "Loading $img into KinD cluster $hub on $target_host..."
        if ! remote_exec "$target_host" "kind load docker-image '$img' --name '$hub' 2>/dev/null"; then
          remote_exec "$target_host" "docker save '$img' | docker exec -i ${hub}-control-plane ctr -n k8s.io images import --local -"
        fi
        log_success "Loaded $img into $hub."

        kubectl --context "kind-${hub}" rollout restart deployment/opensandbox-server -n opensandbox-system 2>/dev/null || true
      fi
    else
      if docker exec "${hub}-control-plane" crictl images 2>/dev/null | grep -q "01sandbox-opensandbox-server" && [ "$FORCE_RECONFIGURE" != "true" ]; then
        log_info "$img already loaded in $hub – skipping kind load."
      else
        ensure_sandbox_repo
        if ! docker image inspect "$img" >/dev/null 2>&1 || [ "$FORCE_RECONFIGURE" == "true" ]; then
          if [ ! -d "${OPENSANDBOX_BUILD_DIR}" ] || [ ! -f "${OPENSANDBOX_BUILD_DIR}/Dockerfile" ]; then
            log_error "Dockerfile not found at detected build path: ${OPENSANDBOX_BUILD_DIR}"
            return 1
          fi
          log_info "Building $img from: ${OPENSANDBOX_BUILD_DIR}..."
          docker build -t "$img" "${OPENSANDBOX_BUILD_DIR}"
          log_success "Built $img successfully."
        fi
        log_info "Loading $img into $hub..."
        if ! kind load docker-image "$img" --name "$hub" 2>/dev/null; then
          docker save "$img" | docker exec -i "${hub}-control-plane" ctr -n k8s.io images import --local -
        fi
        kubectl --context "kind-${hub}" rollout restart deployment/opensandbox-server -n opensandbox-system 2>/dev/null || true
      fi
    fi
  done

  log_success "Custom image ready on both hubs."
}

phase_10_load_custom_image() {
  run_phase "10" "Building & Loading Custom opensandbox-server Image" _check_phase_10 _do_phase_10_load_custom_image
}

_check_phase_13() {
  local is_remote=false
  [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    remote_exec "$SPOKE1_HOST" "kind get clusters 2>/dev/null" | grep -q '^spoke1$' || return 1
    remote_exec "$SPOKE2_HOST" "kind get clusters 2>/dev/null" | grep -q '^spoke2$' || return 1
    ensure_cluster_ssh_tunnel "spoke1" "$SPOKE1_HOST"
    ensure_cluster_ssh_tunnel "spoke2" "$SPOKE2_HOST"
  else
    kind get clusters 2>/dev/null | grep -q '^spoke1$' || return 1
    kind get clusters 2>/dev/null | grep -q '^spoke2$' || return 1
    kind export kubeconfig --name spoke1 2>/dev/null || true
    kind export kubeconfig --name spoke2 2>/dev/null || true
  fi

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
  local is_remote=false
  [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && is_remote=true

  _create_kind_cluster "spoke1" "10.246.0.0/16" "10.98.0.0/16"  "$SPOKE1_TRANSIT_IP" "false" "$SPOKE1_HOST" "$WG_SPOKE1_IP" || return 1
  _create_kind_cluster "spoke2" "10.247.0.0/16" "10.100.0.0/16" "$SPOKE2_TRANSIT_IP" "false" "$SPOKE2_HOST" "$WG_SPOKE2_IP" || return 1

  # KinD single-node clusters taint the control-plane node with NoSchedule by default.
  # Since there are no worker nodes on spokes, klusterlet (and all user workloads)
  # would be permanently stuck in Pending / FailedScheduling.
  # Remove the taint so pods can schedule on the sole control-plane node.
  log_info "Ensuring control-plane NoSchedule taint is absent from spoke nodes..."
  for spoke in spoke1 spoke2; do
    if ! kubectl --context "kind-${spoke}" get node "${spoke}-control-plane" -o jsonpath='{.spec.taints}' 2>/dev/null | grep -q 'NoSchedule'; then
      log_info "  ${spoke}: control-plane NoSchedule taint already absent – skipping."
    else
      kubectl --context "kind-${spoke}" taint node "${spoke}-control-plane" \
        node-role.kubernetes.io/control-plane:NoSchedule- 2>/dev/null || true
      kubectl --context "kind-${spoke}" taint node "${spoke}-control-plane" \
        node-role.kubernetes.io/master:NoSchedule- 2>/dev/null || true
      log_info "  ${spoke}: control-plane taint removed."
    fi
  done

  # Verify both spoke containers exist before completing Phase 13
  for spoke in spoke1 spoke2; do
    local target_host="$SPOKE1_HOST"
    [ "$spoke" == "spoke2" ] && target_host="$SPOKE2_HOST"
    if $is_remote; then
      if ! remote_exec "$target_host" "docker inspect ${spoke}-control-plane" >/dev/null 2>&1; then
        log_error "Critical: ${spoke}-control-plane container does not exist on ${target_host} after Phase 13!"
        return 1
      fi
    else
      if ! docker inspect "${spoke}-control-plane" >/dev/null 2>&1; then
        log_error "Critical: ${spoke}-control-plane container does not exist after Phase 13!"
        return 1
      fi
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
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    init_node_private_ips
    remote_exec "$PRIMARYHUB_HOST" "sudo wg show wg0 >/dev/null 2>&1" || return 1
    remote_exec "$SECONDARYHUB_HOST" "sudo wg show wg0 >/dev/null 2>&1" || return 1
    remote_exec "$SPOKE1_HOST" "sudo wg show wg0 >/dev/null 2>&1" || return 1
    remote_exec "$SPOKE2_HOST" "sudo wg show wg0 >/dev/null 2>&1" || return 1
    remote_exec "$PRIMARYHUB_HOST" "sudo grep -q '${SPOKE1_PRIVATE_IP}' /etc/wireguard/wg0.conf 2>/dev/null" || return 1
    return 0
  fi

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
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    log_info "Ensuring WireGuard mesh configuration is up to date on all 4 EC2 instances..."
    _do_phase_02_transit_network_and_wg_keys
    log_success "WireGuard mesh active and synchronized across all 4 EC2 instances."
    return 0
  fi

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

_check_phase_15d() {
  local is_remote=false
  [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && is_remote=true

  for c in spoke1 spoke2; do
    local target_host="$SPOKE1_HOST"
    [ "$c" == "spoke2" ] && target_host="$SPOKE2_HOST"

    for lang in python go java node k8s rust shell cpp ruby terraform; do
      if $is_remote; then
        remote_exec "$target_host" "docker exec ${c}-control-plane crictl images 2>/dev/null" | grep -q "01sandbox-scanner-${lang}" || return 1
      else
        docker exec "${c}-control-plane" crictl images 2>/dev/null | grep -q "01sandbox-scanner-${lang}" || return 1
      fi
    done
  done
  return 0
}

_do_phase_15d_load_scanner_images() {
  if [ "$FORCE_RECONFIGURE" != "true" ] && _check_phase_15d; then
    log_info "All language scanner images already loaded on spokes – skipping."
    return 0
  fi
  log_info "Ensuring all language-specific scanner images are built and loaded on spokes..."

  local is_remote=false
  [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    for spoke in spoke1 spoke2; do
      local target_host="$SPOKE1_HOST"
      [ "$spoke" == "spoke2" ] && target_host="$SPOKE2_HOST"

      ensure_remote_sandbox_repo "$target_host"
      local user
      user=$(_get_ssh_user_for_host "$target_host")
      local remote_build_script="/home/${user}/01-Sandbox/code-interpreter/scripts/build-and-load-scanners.sh"

      if remote_exec "$target_host" "test -f '${remote_build_script}'"; then
        log_info "Building and loading language scanner images directly on remote EC2 instance $target_host (${spoke})..."
        remote_exec "$target_host" "bash '${remote_build_script}' --clusters '${spoke}'"
        log_success "Scanner images built and loaded on ${spoke} ($target_host)."
      else
        log_error "build-and-load-scanners.sh not found on $target_host at ${remote_build_script}!"
        return 1
      fi
    done
  else
    local build_script="${SANDBOX_REPO_DIR}/code-interpreter/scripts/build-and-load-scanners.sh"
    [ ! -f "$build_script" ] && build_script="${ROOT_DIR}/code-interpreter/scripts/build-and-load-scanners.sh"
    if [ -f "$build_script" ]; then
      bash "$build_script" --clusters "spoke1,spoke2"
    else
      log_warn "build-and-load-scanners.sh not found at ${build_script}"
    fi
  fi
}

phase_15d_load_scanner_images() {
  run_phase "15d" "Building & Loading Language Scanner Images on Spokes" _check_phase_15d _do_phase_15d_load_scanner_images
}
