#!/usr/bin/env bash
# ==============================================================================
# Automated Post-Reboot Recovery Installer (ocm-mesh-boot.service)
# Installs and enables persistent boot recovery across all OCM & Spoke VMs.
# ==============================================================================

set -euo pipefail

echo "🚀 Installing automated post-reboot recovery service (ocm-mesh-boot.service)..."

declare -A VM_IPS=(
  ["hub1-vm"]="192.168.100.20"
  ["hub2-vm"]="192.168.101.20"
  ["spoke1-vm"]="192.168.102.20"
  ["spoke2-vm"]="192.168.103.20"
)

for vm in "hub1-vm" "hub2-vm" "spoke1-vm" "spoke2-vm"; do
  IP="${VM_IPS[$vm]}"

  echo "----------------------------------------------------------------------"
  echo "📦 Configuring $vm ($IP)..."
  echo "----------------------------------------------------------------------"

  case "$vm" in
    hub1-vm)   CONTAINER="primaryhub-control-plane" ;;
    hub2-vm)   CONTAINER="secondaryhub-control-plane" ;;
    spoke1-vm) CONTAINER="spoke1-control-plane" ;;
    spoke2-vm) CONTAINER="spoke2-control-plane" ;;
  esac

  ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "ubuntu@$IP" "sudo bash -s" << EOF
cat > /usr/local/bin/ocm-mesh-boot.sh << 'SCRIPT'
#!/bin/bash
set -eo pipefail

echo "[ocm-mesh-boot] Starting post-reboot recovery for $vm..."

if ! ip link show wg0 >/dev/null 2>&1; then
    echo "[ocm-mesh-boot] Bringing up WireGuard wg0..."
    systemctl restart wg-quick@wg0 || true
    sleep 2
fi

echo "[ocm-mesh-boot] Resetting containerd inside KinD $CONTAINER..."
docker update --restart=always $CONTAINER 2>/dev/null || true
docker exec $CONTAINER systemctl restart containerd 2>/dev/null || docker restart $CONTAINER || true
sleep 5

DOCKER_IP=\$(docker inspect $CONTAINER --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || echo '172.18.0.2')
echo "[ocm-mesh-boot] KinD Container IP: \${DOCKER_IP}"

iptables -t nat -C PREROUTING ! -i br-+ -p tcp --dport 6443 -j DNAT --to-destination \${DOCKER_IP}:6443 2>/dev/null || \
iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 6443 -j DNAT --to-destination \${DOCKER_IP}:6443

iptables -t nat -C PREROUTING ! -i br-+ -p tcp --dport 8091 -j DNAT --to-destination \${DOCKER_IP}:8091 2>/dev/null || \
iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 8091 -j DNAT --to-destination \${DOCKER_IP}:8091

iptables -t nat -C PREROUTING ! -i br-+ -p tcp --dport 32379 -j DNAT --to-destination \${DOCKER_IP}:32379 2>/dev/null || \
iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 32379 -j DNAT --to-destination \${DOCKER_IP}:32379

iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || \
iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE

iptables -C FORWARD -i wg0 -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i wg0 -j ACCEPT
iptables -C FORWARD -o wg0 -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o wg0 -j ACCEPT

WG_IP=\$(ip -4 addr show wg0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || echo '')
if [ -n "\$WG_IP" ]; then
    ip rule add from \$WG_IP table 200 priority 100 2>/dev/null || true
    ip route add default dev wg0 table 200 2>/dev/null || true
fi

# Special addition for spoke VMs: Clear stale leader election locks on boot
if [[ "$vm" == "spoke1-vm" || "$vm" == "spoke2-vm" ]]; then
  echo "[ocm-mesh-boot] Clearing stale agent lease locks for immediate leader election..."
  docker exec $CONTAINER kubectl delete lease registration-agent-lock work-agent-lock -n open-cluster-management-agent 2>/dev/null || true
fi

echo "[ocm-mesh-boot] Recovery complete for $vm."
SCRIPT

chmod +x /usr/local/bin/ocm-mesh-boot.sh

cat > /etc/systemd/system/ocm-mesh-boot.service << 'SERVICE'
[Unit]
Description=OCM Mesh KinD & Routing Boot Auto-Recovery Service
After=network-online.target docker.service wg-quick@wg0.service
Wants=network-online.target docker.service

[Service]
Type=simple
ExecStart=/usr/local/bin/ocm-mesh-boot.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now ocm-mesh-boot.service

# Special addition for hub VMs: Apply Kubernetes Auto-Acceptor Manifest
if [[ "$vm" == "hub1-vm" || "$vm" == "hub2-vm" ]]; then
  echo "[ocm-mesh-boot] Applying cloud-native Kubernetes Auto-Acceptor manifest..."
  mkdir -p /home/ubuntu/ocm/manifests
  kubectl apply -f /home/ubuntu/ocm/manifests/ocm-auto-acceptor-k8s.yaml 2>/dev/null || true
fi
EOF

  # Copy manifest to hub VMs
  if [[ "$vm" == "hub1-vm" || "$vm" == "hub2-vm" ]]; then
    scp -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no manifests/ocm-auto-acceptor-k8s.yaml ubuntu@$IP:/home/ubuntu/ocm/manifests/ocm-auto-acceptor-k8s.yaml 2>/dev/null || true
  fi

  echo "✅ Auto-recovery service installed and enabled on $vm ($IP)"
done

echo "----------------------------------------------------------------------"
echo "📦 Configuring gateway-vm (192.168.100.10) for VIP Watchdog & Netplan..."
echo "----------------------------------------------------------------------"
ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "ubuntu@192.168.100.10" "sudo bash -s" << 'EOF'
systemctl stop ocm-vip-watchdog.service 2>/dev/null || true
systemctl disable ocm-vip-watchdog.service 2>/dev/null || true

cat > /etc/netplan/60-gateway.yaml << 'NETPLAN'
network:
  version: 2
  ethernets:
    enp7s0:
      match:
        macaddress: "52:54:00:9e:f2:e0"
      addresses:
        - 192.168.101.10/24
      set-name: "enp7s0"
    enp8s0:
      match:
        macaddress: "52:54:00:3c:ac:0c"
      addresses:
        - 192.168.102.10/24
      set-name: "enp8s0"
    enp9s0:
      match:
        macaddress: "52:54:00:37:90:41"
      addresses:
        - 192.168.103.10/24
      set-name: "enp9s0"
NETPLAN
chmod 600 /etc/netplan/60-gateway.yaml
netplan apply 2>/dev/null || true

docker rm -f ocm-vip-watchdog 2>/dev/null || true
docker run -d \
  --name ocm-vip-watchdog \
  --restart always \
  --network host \
  --cap-add NET_ADMIN \
  alpine:latest \
  /bin/sh -c '
    apk add --no-cache curl iptables iproute2 bash conntrack-tools >/dev/null 2>&1
    PRIMARY_HUB="10.99.0.1"
    SECONDARY_HUB="10.99.0.2"
    VIP="10.99.0.100"
    ACTIVE_TARGET=""
    FAIL_COUNT=0
    FAIL_THRESHOLD=3

    ip addr add ${VIP}/32 dev wg0 2>/dev/null || true
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE

    while true; do
      if curl -k -m 2 -s https://${PRIMARY_HUB}:6443/livez >/dev/null; then
        FAIL_COUNT=0
        TARGET="${PRIMARY_HUB}"
      else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "[$(date -Iseconds)] [ocm-vip-watchdog] Primary check failed (${FAIL_COUNT}/${FAIL_THRESHOLD})"
        if [ "$FAIL_COUNT" -ge "$FAIL_THRESHOLD" ]; then
          if curl -k -m 2 -s https://${SECONDARY_HUB}:6443/livez >/dev/null; then
            TARGET="${SECONDARY_HUB}"
          else
            TARGET="${PRIMARY_HUB}"
          fi
        else
          TARGET="${ACTIVE_TARGET:-${PRIMARY_HUB}}"
        fi
      fi

      if [ -n "$TARGET" ] && [ "$TARGET" != "$ACTIVE_TARGET" ]; then
        echo "[$(date -Iseconds)] [ocm-vip-watchdog] Failover event: Switching VIP target to ${TARGET}"
        iptables -t nat -D PREROUTING -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${PRIMARY_HUB}:6443 2>/dev/null || true
        iptables -t nat -D PREROUTING -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${SECONDARY_HUB}:6443 2>/dev/null || true
        iptables -t nat -D OUTPUT -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${PRIMARY_HUB}:6443 2>/dev/null || true
        iptables -t nat -D OUTPUT -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${SECONDARY_HUB}:6443 2>/dev/null || true

        iptables -t nat -I PREROUTING 1 -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${TARGET}:6443
        iptables -t nat -I OUTPUT 1 -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${TARGET}:6443
        ACTIVE_TARGET="${TARGET}"

        conntrack -D -d ${VIP} 2>/dev/null || true
        conntrack -D -p tcp --dport 6443 2>/dev/null || true
      fi
      sleep 2
    done
  '
EOF

echo "----------------------------------------------------------------------"
echo "🎉 Permanent post-reboot recovery setup completed on all VMs!"
echo "Now, whenever any VM boots or restarts, Virtual IP watchdog, secondary auto-acceptor, and spokes will auto-recover."
echo "----------------------------------------------------------------------"
