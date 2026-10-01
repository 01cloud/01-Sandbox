#!/usr/bin/env bash
# ==============================================================================
# lib/network.sh – Transit network, WireGuard overlay, PKI, and Envoy Gateway
#
# Functions:
#   setup_transit_network_and_wg_keys  – Docker transit net + WG keypair generation
#   setup_wireguard_on_hubs            – configure wg0 on hub containers
#   verify_root_ca_and_vip             – validate shared CA and VIP reachability
#   setup_envoy_gateway                – deploy Envoy as the WG overlay L4 proxy
# ==============================================================================

_check_phase_02() {
  docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$" || return 1

  local entity
  for entity in gateway primaryhub secondaryhub spoke1 spoke2; do
    [ -s "${WG_DIR}/${entity}.key" ] && [ -s "${WG_DIR}/${entity}.pub" ] || return 1
  done

  # Load keypairs into memory so later phases can use them even when skipped
  for entity in gateway primaryhub secondaryhub spoke1 spoke2; do
    WG_PRIV[$entity]=$(tr -d '\r\n' < "${WG_DIR}/${entity}.key")
    WG_PUB[$entity]=$(tr -d '\r\n'  < "${WG_DIR}/${entity}.pub")
  done
  return 0
}

_do_phase_02_transit_network_and_wg_keys() {
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

phase_02_transit_network_and_wg_keys() {
  run_phase "02" "Transit Network & WireGuard Key Generation" _check_phase_02 _do_phase_02_transit_network_and_wg_keys
}

_check_phase_05() {
  _check_wg_active primaryhub-control-plane   "$WG_HUB1_IP" || return 1
  _check_wg_active secondaryhub-control-plane "$WG_HUB2_IP" || return 1
  return 0
}

_do_phase_05_wireguard_on_hubs() {
  _setup_wireguard "primaryhub-control-plane"   "primaryhub"   "$WG_HUB1_IP"
  _setup_wireguard "secondaryhub-control-plane" "secondaryhub" "$WG_HUB2_IP"

  log_success "WireGuard overlay active on hub clusters."
}

phase_05_wireguard_on_hubs() {
  run_phase "05" "Bringing Up WireGuard Overlay on Hub Clusters" _check_phase_05 _do_phase_05_wireguard_on_hubs
}

_check_phase_07() {
  local pca sca
  pca=$(docker exec primaryhub-control-plane   sha256sum /etc/kubernetes/pki/ca.crt 2>/dev/null | awk '{print $1}')
  sca=$(docker exec secondaryhub-control-plane sha256sum /etc/kubernetes/pki/ca.crt 2>/dev/null | awk '{print $1}')
  [ -n "$pca" ] && [ "$pca" == "$sca" ] || return 1

  local ctx
  for ctx in kind-primaryhub kind-secondaryhub; do
    kubectl --context "$ctx" get configmap cluster-info -n kube-public -o jsonpath='{.data.kubeconfig}' 2>/dev/null \
      | grep -q "$WG_VIP" || return 1
  done
  return 0
}

_do_phase_07_verify_root_ca_and_vip() {
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

phase_07_verify_root_ca_and_vip() {
  run_phase "07" "Verifying Shared Root CA & VIP TLS SANs" _check_phase_07 _do_phase_07_verify_root_ca_and_vip
}
#
# The envoy-gateway container runs WireGuard (wg0) and Envoy side-by-side.
# It sits on the transit network and presents a single VIP (10.99.0.100) that
# load-balances HTTP:80 and Kube-API:6443 across PrimaryHub and SecondaryHub
# over the encrypted WireGuard overlay.

_check_phase_06() {
  [ "$(docker inspect -f '{{.State.Running}}' envoy-gateway 2>/dev/null)" = "true" ] || return 1
  docker exec primaryhub-control-plane ping -c 1 -W 1 "$WG_VIP" >/dev/null 2>&1 || return 1
  return 0
}

_do_phase_06_envoy_gateway() {
  # ── Static Envoy config (no variable substitution) ─────────────────────────
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
      - name: envoy.filters.network.tcp_proxy
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.tcp_proxy.v3.TcpProxy
          stat_prefix: ingress_http
          cluster: ingress_http_cluster
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

  # ── Dynamic cluster section (IP substitution) ──────────────────────────────
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

  # ── Gateway WireGuard config ───────────────────────────────────────────────
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

phase_06_envoy_gateway() {
  run_phase "06" "Deploying Envoy Gateway (VIP: ${WG_VIP})" _check_phase_06 _do_phase_06_envoy_gateway
}
