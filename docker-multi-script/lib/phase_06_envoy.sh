#!/usr/bin/env bash
# ==============================================================================
# lib/phase_06_envoy.sh – Deploy Envoy Gateway container (VIP: WG_VIP)
#
# The envoy-gateway container runs WireGuard (wg0) and Envoy side-by-side.
# It sits on the transit network and presents a single VIP (10.99.0.100) that
# load-balances HTTP:80 and Kube-API:6443 across PrimaryHub and SecondaryHub
# over the encrypted WireGuard overlay.
# ==============================================================================

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
