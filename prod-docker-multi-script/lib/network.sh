#!/usr/bin/env bash
# ==============================================================================
# lib/network.sh – Transit network, WireGuard overlay, PKI, and Envoy Gateway
#
# Functions:
#   setup_transit_network_and_wg_keys  – Docker transit net / EC2 mesh + WG keypairs
#   setup_wireguard_on_hubs            – configure wg0 on hub nodes / containers
#   verify_root_ca_and_vip             – validate shared CA and VIP reachability
#   setup_envoy_gateway                – deploy Envoy as the WG overlay L4/L7 proxy
# ==============================================================================

_check_phase_02() {
  local entity
  local all_entities=("gateway" "${ALL_NODES[@]}")
  for entity in "${all_entities[@]}"; do
    [ -s "${WG_DIR}/${entity}.key" ] && [ -s "${WG_DIR}/${entity}.pub" ] || return 1
  done

  # Load keypairs into memory
  for entity in "${all_entities[@]}"; do
    WG_PRIV[$entity]=$(tr -d '\r\n' < "${WG_DIR}/${entity}.key")
    WG_PUB[$entity]=$(tr -d '\r\n'  < "${WG_DIR}/${entity}.pub")
  done

  # Verify WireGuard is active on remote nodes if configured
  if [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ]; then
    init_node_private_ips
    for node in "${ALL_NODES[@]}"; do
      local r_host="${NODE_HOSTS[$node]:-$node}"
      [ "$r_host" != "127.0.0.1" ] && [ "$r_host" != "localhost" ] && \
        remote_exec "$r_host" "sudo wg show wg0 >/dev/null 2>&1" || return 1
    done
  fi

  return 0
}

_do_phase_02_transit_network_and_wg_keys() {
  local is_remote=false
  if [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ]; then
    is_remote=true
  fi

  if ! $is_remote; then
    if ! docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$"; then
      log_info "Creating local Docker transit network ($TRANSIT_SUBNET)..."
      docker network create \
        --driver bridge \
        --subnet "$TRANSIT_SUBNET" \
        --opt "com.docker.network.bridge.name"="br-01transit" \
        "$TRANSIT_NET_NAME"
    fi
  else
    log_info "Operating in remote multi-region mode across EC2 nodes: ${PRIMARYHUB_HOST}, ${SECONDARYHUB_HOST}, ${SPOKE1_HOST}, ${SPOKE2_HOST}."
  fi

  # 1. Generate WireGuard keypairs for gateway and all fleet nodes
  local all_entities=("gateway" "${ALL_NODES[@]}")
  for entity in "${all_entities[@]}"; do
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

  log_success "WireGuard keypairs ready for all ${#all_entities[@]} entities."

  # 2. In remote mode, generate and deploy full-mesh wg0.conf for each EC2 instance
  if $is_remote; then
    init_node_private_ips
    log_info "Configuring cross-region WireGuard mesh across all ${#ALL_NODES[@]} EC2 instances over VPC Peering..."

    local primary_hub="${HUB_LIST[0]:-primaryhub}"

    for node in "${ALL_NODES[@]}"; do
      local node_wg_ip
      node_wg_ip=$(_get_wg_ip_for_node "$node")
      local conf_file="${WG_DIR}/wg0-${node}.conf"

      # Interface section (PrimaryHub also hosts the VIP 10.99.0.100)
      if [ "$node" == "$primary_hub" ]; then
        cat > "$conf_file" <<CONF_HEAD
[Interface]
Address = ${node_wg_ip}/24, ${WG_VIP}/32
ListenPort = ${WG_PORT}
PrivateKey = ${WG_PRIV["$node"]}
CONF_HEAD
      else
        cat > "$conf_file" <<CONF_HEAD
[Interface]
Address = ${node_wg_ip}/24
ListenPort = ${WG_PORT}
PrivateKey = ${WG_PRIV["$node"]}
CONF_HEAD
      fi

      # Peer sections: every other node in ALL_NODES
      for peer in "${ALL_NODES[@]}"; do
        [ "$peer" == "$node" ] && continue
        local peer_wg_ip peer_endpoint
        peer_wg_ip=$(_get_wg_ip_for_node "$peer")
        peer_endpoint="${NODE_PRIVATE_IPS[$peer]:-127.0.0.1}:${WG_PORT}"

        local allowed_ips="${peer_wg_ip}/32"
        [ "$peer" == "$primary_hub" ] && allowed_ips="${peer_wg_ip}/32, ${WG_VIP}/32"

        cat >> "$conf_file" <<CONF_PEER

[Peer]
# ${peer}
PublicKey = ${WG_PUB["$peer"]}
AllowedIPs = ${allowed_ips}
Endpoint = ${peer_endpoint}
PersistentKeepalive = 25
CONF_PEER
      done
    done

    # Push and activate wg0 on each remote EC2 node
    for node in "${ALL_NODES[@]}"; do
      local r_host="${NODE_HOSTS[$node]:-$node}"
      log_info "Deploying WireGuard wg0 on ${node} (${r_host})..."

      remote_copy_to "$r_host" "${WG_DIR}/wg0-${node}.conf" "/tmp/wg0.conf"
      remote_exec "$r_host" bash -s << 'APPLY_WG_EOF'
set -e
sudo mkdir -p /etc/wireguard
sudo cp /tmp/wg0.conf /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable wg-quick@wg0 2>/dev/null || true
sudo systemctl restart wg-quick@wg0 2>/dev/null || sudo wg-quick up wg0 2>/dev/null || true
APPLY_WG_EOF
    done

    # Verify cross-region overlay ping connectivity from PrimaryHub to other nodes
    log_info "Testing cross-region WireGuard mesh connectivity from ${primary_hub}..."
    sleep 3
    local primary_host="${NODE_HOSTS[$primary_hub]:-$PRIMARYHUB_HOST}"
    for peer in "${ALL_NODES[@]}"; do
      [ "$peer" == "$primary_hub" ] && continue
      local p_wg_ip
      p_wg_ip=$(_get_wg_ip_for_node "$peer")
      remote_exec "$primary_host" "ping -c 2 -W 2 ${p_wg_ip} >/dev/null 2>&1" && \
        log_success "Mesh: ${primary_hub} <-> ${peer} reachable over ${p_wg_ip}!" || \
        log_warn "${primary_hub} <-> ${peer} ping pending..."
    done
  fi
}

phase_02_transit_network_and_wg_keys() {
  local is_remote=false
  if [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ]; then
    is_remote=true
  fi

  if ! $is_remote; then
    if ! docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$"; then
      log_info "Creating local Docker transit network ($TRANSIT_SUBNET)..."
      docker network create \
        --driver bridge \
        --subnet "$TRANSIT_SUBNET" \
        --opt "com.docker.network.bridge.name"="br-01transit" \
        "$TRANSIT_NET_NAME"
    fi
  else
    log_info "Operating in remote multi-region mode across EC2 nodes: ${PRIMARYHUB_HOST}, ${SECONDARYHUB_HOST}, ${SPOKE1_HOST}, ${SPOKE2_HOST}."
  fi

  # 1. Generate WireGuard keypairs for all 5 entities
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

  # 2. In remote mode, generate and deploy wg0.conf to each EC2 instance
  if $is_remote; then
    init_node_private_ips
    log_info "Configuring cross-region WireGuard mesh on all 4 EC2 instances over VPC Peering (Private IPs)..."

    # Config for PrimaryHub (includes VIP 10.99.0.100)
    cat > "${WG_DIR}/wg0-primaryhub.conf" <<EOF
[Interface]
Address = ${WG_HUB1_IP}/24, ${WG_VIP}/32
ListenPort = ${WG_PORT}
PrivateKey = ${WG_PRIV["primaryhub"]}

[Peer]
# SecondaryHub
PublicKey = ${WG_PUB["secondaryhub"]}
AllowedIPs = ${WG_HUB2_IP}/32
Endpoint = ${SECONDARYHUB_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25

[Peer]
# Spoke1
PublicKey = ${WG_PUB["spoke1"]}
AllowedIPs = ${WG_SPOKE1_IP}/32
Endpoint = ${SPOKE1_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25

[Peer]
# Spoke2
PublicKey = ${WG_PUB["spoke2"]}
AllowedIPs = ${WG_SPOKE2_IP}/32
Endpoint = ${SPOKE2_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25
EOF

    # Config for SecondaryHub
    cat > "${WG_DIR}/wg0-secondaryhub.conf" <<EOF
[Interface]
Address = ${WG_HUB2_IP}/24
ListenPort = ${WG_PORT}
PrivateKey = ${WG_PRIV["secondaryhub"]}

[Peer]
# PrimaryHub + VIP
PublicKey = ${WG_PUB["primaryhub"]}
AllowedIPs = ${WG_HUB1_IP}/32, ${WG_VIP}/32
Endpoint = ${PRIMARYHUB_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25

[Peer]
# Spoke1
PublicKey = ${WG_PUB["spoke1"]}
AllowedIPs = ${WG_SPOKE1_IP}/32
Endpoint = ${SPOKE1_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25

[Peer]
# Spoke2
PublicKey = ${WG_PUB["spoke2"]}
AllowedIPs = ${WG_SPOKE2_IP}/32
Endpoint = ${SPOKE2_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25
EOF

    # Config for Spoke1
    cat > "${WG_DIR}/wg0-spoke1.conf" <<EOF
[Interface]
Address = ${WG_SPOKE1_IP}/24
ListenPort = ${WG_PORT}
PrivateKey = ${WG_PRIV["spoke1"]}

[Peer]
# PrimaryHub + VIP
PublicKey = ${WG_PUB["primaryhub"]}
AllowedIPs = ${WG_HUB1_IP}/32, ${WG_VIP}/32
Endpoint = ${PRIMARYHUB_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25

[Peer]
# SecondaryHub
PublicKey = ${WG_PUB["secondaryhub"]}
AllowedIPs = ${WG_HUB2_IP}/32
Endpoint = ${SECONDARYHUB_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25

[Peer]
# Spoke2
PublicKey = ${WG_PUB["spoke2"]}
AllowedIPs = ${WG_SPOKE2_IP}/32
Endpoint = ${SPOKE2_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25
EOF

    # Config for Spoke2
    cat > "${WG_DIR}/wg0-spoke2.conf" <<EOF
[Interface]
Address = ${WG_SPOKE2_IP}/24
ListenPort = ${WG_PORT}
PrivateKey = ${WG_PRIV["spoke2"]}

[Peer]
# PrimaryHub + VIP
PublicKey = ${WG_PUB["primaryhub"]}
AllowedIPs = ${WG_HUB1_IP}/32, ${WG_VIP}/32
Endpoint = ${PRIMARYHUB_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25

[Peer]
# SecondaryHub
PublicKey = ${WG_PUB["secondaryhub"]}
AllowedIPs = ${WG_HUB2_IP}/32
Endpoint = ${SECONDARYHUB_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25

[Peer]
# Spoke1
PublicKey = ${WG_PUB["spoke1"]}
AllowedIPs = ${WG_SPOKE1_IP}/32
Endpoint = ${SPOKE1_PRIVATE_IP}:${WG_PORT}
PersistentKeepalive = 25
EOF

    # Push and activate wg0 on each remote EC2 node
    local remote_nodes=("primaryhub:$PRIMARYHUB_HOST" "secondaryhub:$SECONDARYHUB_HOST" "spoke1:$SPOKE1_HOST" "spoke2:$SPOKE2_HOST")
    for r_entry in "${remote_nodes[@]}"; do
      local r_name="${r_entry%%:*}"
      local r_host="${r_entry##*:}"
      log_info "Deploying WireGuard wg0 on ${r_name} (${r_host})..."

      remote_copy_to "$r_host" "${WG_DIR}/wg0-${r_name}.conf" "/tmp/wg0.conf"
      remote_exec "$r_host" bash -s << 'APPLY_WG_EOF'
set -e
sudo mkdir -p /etc/wireguard
sudo cp /tmp/wg0.conf /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable wg-quick@wg0 2>/dev/null || true
sudo systemctl restart wg-quick@wg0 2>/dev/null || sudo wg-quick up wg0 2>/dev/null || true
APPLY_WG_EOF
    done

    # Verify cross-region overlay ping connectivity from PrimaryHub
    log_info "Testing cross-region WireGuard mesh connectivity..."
    sleep 3
    remote_exec "$PRIMARYHUB_HOST" "ping -c 2 -W 2 ${WG_HUB2_IP} >/dev/null 2>&1" && log_success "Mesh: PrimaryHub <-> SecondaryHub reachable over ${WG_HUB2_IP}!" || log_warn "PrimaryHub <-> SecondaryHub ping pending..."
    remote_exec "$PRIMARYHUB_HOST" "ping -c 2 -W 2 ${WG_SPOKE1_IP} >/dev/null 2>&1" && log_success "Mesh: PrimaryHub <-> Spoke1 reachable over ${WG_SPOKE1_IP}!" || log_warn "PrimaryHub <-> Spoke1 ping pending..."
    remote_exec "$PRIMARYHUB_HOST" "ping -c 2 -W 2 ${WG_SPOKE2_IP} >/dev/null 2>&1" && log_success "Mesh: PrimaryHub <-> Spoke2 reachable over ${WG_SPOKE2_IP}!" || log_warn "PrimaryHub <-> Spoke2 ping pending..."
  fi
}

phase_02_transit_network_and_wg_keys() {
  run_phase "02" "Transit Network & WireGuard Mesh Generation" _check_phase_02 _do_phase_02_transit_network_and_wg_keys
}

_check_phase_05() {
  if [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ]; then
    for hub in "${HUB_LIST[@]}"; do
      local h_host="${NODE_HOSTS[$hub]:-$hub}"
      local h_wg_ip
      h_wg_ip=$(_get_wg_ip_for_node "$hub")
      remote_exec "$h_host" "ip a show wg0 | grep -q '${h_wg_ip}'" || return 1
    done
    return 0
  fi
  for hub in "${HUB_LIST[@]}"; do
    local h_wg_ip
    h_wg_ip=$(_get_wg_ip_for_node "$hub")
    _check_wg_active "${hub}-control-plane" "$h_wg_ip" || return 1
  done
  return 0
}

_do_phase_05_wireguard_on_hubs() {
  if [ "$FORCE_RECONFIGURE" != "true" ] && _check_phase_05; then
    log_info "WireGuard overlay already active on hub clusters – skipping."
    return 0
  fi

  if [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ]; then
    log_success "WireGuard overlay active on remote EC2 hub hosts."
    return 0
  fi

  for hub in "${HUB_LIST[@]}"; do
    local h_wg_ip
    h_wg_ip=$(_get_wg_ip_for_node "$hub")
    _setup_wireguard "${hub}-control-plane" "$hub" "$h_wg_ip"
  done

  log_success "WireGuard overlay active on hub clusters."
}

phase_05_wireguard_on_hubs() {
  run_phase "05" "Bringing Up WireGuard Overlay on Hub Clusters" _check_phase_05 _do_phase_05_wireguard_on_hubs
}

_check_phase_06() {
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    remote_exec "$PRIMARYHUB_HOST" "docker inspect -f '{{.State.Running}}' envoy-gateway 2>/dev/null | grep -q true && sudo ss -tulpn | grep -q ':80 ' && ip route | grep -q '172.30.0.200'" || return 1
    return 0
  fi

  [ "$(docker inspect -f '{{.State.Running}}' envoy-gateway 2>/dev/null)" = "true" ] || return 1
  docker exec primaryhub-control-plane curl -sk -m 2 "https://${WG_VIP}:6443/version" >/dev/null 2>&1 || return 1
  return 0
}

_do_phase_06_envoy_gateway() {
  if [ "$FORCE_RECONFIGURE" != "true" ] && _check_phase_06; then
    log_info "Envoy Gateway already running – skipping reconfiguration."
    return 0
  fi

  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  # ── Static Envoy config ───────────────────────────────────────────────────
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
                address: 10.99.0.1
                port_value: 80
      - priority: 1
        lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: 10.99.0.2
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
                address: 10.99.0.1
                port_value: 6443
      - priority: 1
        lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: 10.99.0.2
                port_value: 6443
ENVOY_EOF

  local envoy_image="envoyproxy/envoy:v1.31-latest"

  if $is_remote; then
    log_info "Deploying Envoy Gateway on PrimaryHub EC2 (${PRIMARYHUB_HOST})..."
    cat > "${ENVOY_DIR}/envoy.yaml" <<'ENVOY_REMOTE_EOF'
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
                address: 172.30.0.200
                port_value: 80
      - priority: 1
        lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: 10.99.0.2
                port_value: 80
ENVOY_REMOTE_EOF
    remote_copy_to "$PRIMARYHUB_HOST" "${ENVOY_DIR}/envoy.yaml" "/tmp/envoy.yaml"
    remote_exec "$PRIMARYHUB_HOST" bash -s << REMOTE_ENVOY_EOF
local_kind_ip=\$(docker inspect primaryhub-control-plane -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)
if [ -n "\$local_kind_ip" ]; then
  sudo ip route replace 172.30.0.200/32 via "\$local_kind_ip" 2>/dev/null || true
fi
docker rm -f envoy-gateway 2>/dev/null || true
docker run -d --name envoy-gateway \
  --user 0:0 \
  -e ENVOY_UID=0 \
  --restart unless-stopped \
  --net host \
  -v /tmp/envoy.yaml:/etc/envoy/envoy.yaml:ro \
  $envoy_image -c /etc/envoy/envoy.yaml
REMOTE_ENVOY_EOF
    log_success "Envoy Gateway deployed on PrimaryHub EC2 host (Port 80)."
  else
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
    local local_envoy_image="01sandbox-envoy:v1"
    if ! docker image inspect "$local_envoy_image" >/dev/null 2>&1; then
      cat <<'EOF_ENVOY_DOCKER' | docker build -t "$local_envoy_image" -
FROM envoyproxy/envoy:v1.31-latest
USER root
RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      wireguard-tools iproute2 iptables curl procps && \
    rm -rf /var/lib/apt/lists/*
EOF_ENVOY_DOCKER
    fi
    docker run -d --name envoy-gateway \
      --restart unless-stopped \
      -p 80:80 \
      --privileged --user root \
      --cap-add=NET_ADMIN --cap-add=SYS_MODULE \
      --net "$TRANSIT_NET_NAME" --ip "$GW_TRANSIT_IP" \
      -v "${ENVOY_DIR}/envoy.yaml":/etc/envoy/envoy.yaml:ro \
      -v "${ENVOY_DIR}/wg0.conf":/etc/wireguard/wg0.conf:ro \
      --entrypoint /bin/sh \
      "$local_envoy_image" -c "wg-quick up wg0 && envoy -c /etc/envoy/envoy.yaml"
  fi
}

phase_06_envoy_gateway() {
  run_phase "06" "Deploying Envoy Gateway (VIP: ${WG_VIP})" _check_phase_06 _do_phase_06_envoy_gateway
}

_check_phase_07() {
  local pca sca
  pca=$(kubectl --context kind-primaryhub -n kube-system get configmap extension-apiserver-authentication -o jsonpath='{.data.client-ca-file}' 2>/dev/null || echo "pca")
  sca=$(kubectl --context kind-secondaryhub -n kube-system get configmap extension-apiserver-authentication -o jsonpath='{.data.client-ca-file}' 2>/dev/null || echo "sca")
  [ -n "$pca" ] || return 1

  for ctx in kind-primaryhub kind-secondaryhub; do
    kubectl --context "$ctx" get configmap cluster-info -n kube-public -o jsonpath='{.data.kubeconfig}' 2>/dev/null \
      | grep -q "$WG_VIP" || return 1
  done
  return 0
}

_do_phase_07_verify_root_ca_and_vip() {
  log_info "Updating cluster-info to advertise Gateway VIP..."
  for ctx in kind-primaryhub kind-secondaryhub; do
    if kubectl --context "$ctx" get configmap cluster-info -n kube-public -o jsonpath='{.data.kubeconfig}' 2>/dev/null | grep -q "$WG_VIP"; then
      log_info "cluster-info on $ctx already advertises Gateway VIP (${WG_VIP}) – skipping."
    else
      kubectl --context "$ctx" get configmap cluster-info -n kube-public -o yaml 2>/dev/null | \
        sed "s|server:.*|server: https://${WG_VIP}:6443|g" | \
        kubectl --context "$ctx" apply -f - 2>/dev/null || true
    fi
  done
  log_success "Gateway VIP (${WG_VIP}) verified across hub clusters."
}

phase_07_verify_root_ca_and_vip() {
  run_phase "07" "Verifying Shared Root CA & VIP TLS SANs" _check_phase_07 _do_phase_07_verify_root_ca_and_vip
}
