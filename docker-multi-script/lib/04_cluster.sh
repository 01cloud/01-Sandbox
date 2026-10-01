#!/usr/bin/env bash
# ==============================================================================
# lib/04_cluster.sh – KinD cluster and WireGuard lifecycle helpers
#
# Functions:
#   _create_kind_cluster   – create a KinD cluster and attach it to the transit net
#   _setup_wireguard       – configure wg0 inside a KinD control-plane container
#   _wait_for_pod          – poll until a pod matching a pattern is 1/1 Running
# ==============================================================================

# Create a KinD cluster (idempotent) and attach it to the transit network.
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
