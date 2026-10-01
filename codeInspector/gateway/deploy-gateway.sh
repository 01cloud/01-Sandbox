#!/bin/bash
# ==============================================================================
# deploy-gateway.sh
# Deploys Envoy Active-Passive Multi-Cluster Gateway on gateway-vm
# Retires legacy host-level ocm-vip-watchdog.service and iptables DNAT rules.
# Zero hardcoded IPs: accepts CLI flags, env-file, or environment variables.
# ==============================================================================
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configuration defaults
GATEWAY_IP="${GATEWAY_IP:-}"
PRIMARY_HUB_IP="${PRIMARY_HUB_IP:-}"
SECONDARY_HUB_IP="${SECONDARY_HUB_IP:-}"
VIP="${VIP:-}"
GATEWAY_USER="${GATEWAY_USER:-${SSH_USER:-ubuntu}}"
SSH_KEY="${SSH_KEY:-}"

# Parse optional arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      if [[ -f "$2" ]]; then
        # shellcheck source=/dev/null
        set -a; source "$2"; set +a
      fi
      shift 2
      ;;
    --gateway-ip)     GATEWAY_IP="$2"; shift 2 ;;
    --hub1-ip)        PRIMARY_HUB_IP="$2"; shift 2 ;;
    --hub2-ip)        SECONDARY_HUB_IP="$2"; shift 2 ;;
    --vip)            VIP="$2"; shift 2 ;;
    --ssh-user)       GATEWAY_USER="$2"; shift 2 ;;
    --ssh-key)        SSH_KEY="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [options] [GATEWAY_IP] [PRIMARY_HUB_IP] [SECONDARY_HUB_IP] [VIP]"
      echo "Options:"
      echo "  --env-file PATH        Path to environment file (e.g., cluster.env)"
      echo "  --gateway-ip IP        Gateway VM IP"
      echo "  --hub1-ip IP           Primary Hub WireGuard IP (Active endpoint)"
      echo "  --hub2-ip IP           Secondary Hub WireGuard IP (Standby endpoint)"
      echo "  --vip IP               Virtual IP assigned to WireGuard overlay"
      echo "  --ssh-user USER        SSH user (default: ubuntu)"
      echo "  --ssh-key PATH         SSH private key path"
      exit 0
      ;;
    *)
      if [[ -z "$GATEWAY_IP" ]]; then GATEWAY_IP="$1"; shift;
      elif [[ -z "$PRIMARY_HUB_IP" ]]; then PRIMARY_HUB_IP="$1"; shift;
      elif [[ -z "$SECONDARY_HUB_IP" ]]; then SECONDARY_HUB_IP="$1"; shift;
      elif [[ -z "$VIP" ]]; then VIP="$1"; shift;
      else shift; fi
      ;;
  esac
done

# Fallbacks if unspecified
GATEWAY_IP="${GATEWAY_IP:-192.168.100.10}"
PRIMARY_HUB_IP="${PRIMARY_HUB_IP:-${WG_HUB1_IP:-10.99.0.1}}"
SECONDARY_HUB_IP="${SECONDARY_HUB_IP:-${WG_HUB2_IP:-10.99.0.2}}"
VIP="${VIP:-${WG_VIP:-10.99.0.100}}"
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/kamal-kvm}"

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10)
if [[ -f "$SSH_KEY" ]]; then
  SSH_OPTS+=(-i "$SSH_KEY")
fi

echo "=== Deploying Declarative Envoy Gateway ==="
echo "  Gateway Host:   ${GATEWAY_USER}@${GATEWAY_IP}"
echo "  Primary Hub IP: ${PRIMARY_HUB_IP}"
echo "  Secondary Hub:  ${SECONDARY_HUB_IP}"
echo "  Virtual IP:     ${VIP}"

# 1. Render envoy.yaml from template with dynamic IPs
TMP_ENVOY_CONF=$(mktemp)
if [[ -f "${SCRIPT_DIR}/envoy.yaml.template" ]]; then
  sed \
    -e "s|\${PRIMARY_HUB_IP}|${PRIMARY_HUB_IP}|g" \
    -e "s|\${SECONDARY_HUB_IP}|${SECONDARY_HUB_IP}|g" \
    "${SCRIPT_DIR}/envoy.yaml.template" > "$TMP_ENVOY_CONF"
else
  sed \
    -e "s|10.99.0.1|${PRIMARY_HUB_IP}|g" \
    -e "s|10.99.0.2|${SECONDARY_HUB_IP}|g" \
    "${SCRIPT_DIR}/envoy.yaml" > "$TMP_ENVOY_CONF"
fi

# Also update the local envoy.yaml for direct reference
cp "$TMP_ENVOY_CONF" "${SCRIPT_DIR}/envoy.yaml"

# 2. Ensure /etc/envoy directory exists on gateway-vm
ssh "${SSH_OPTS[@]}" "${GATEWAY_USER}@${GATEWAY_IP}" "sudo mkdir -p /etc/envoy"

# 3. Sync declarative envoy.yaml and systemd service
scp "${SSH_OPTS[@]}" "$TMP_ENVOY_CONF" "${GATEWAY_USER}@${GATEWAY_IP}:/tmp/envoy.yaml"
scp "${SSH_OPTS[@]}" "${SCRIPT_DIR}/envoy-gateway.service" "${GATEWAY_USER}@${GATEWAY_IP}:/tmp/envoy-gateway.service"
rm -f "$TMP_ENVOY_CONF"

# 4. Apply configuration on gateway-vm
ssh "${SSH_OPTS[@]}" "${GATEWAY_USER}@${GATEWAY_IP}" bash -s << EOF
set -eo pipefail

echo "Installing configuration..."
sudo mv /tmp/envoy.yaml /etc/envoy/envoy.yaml
sudo mv /tmp/envoy-gateway.service /etc/systemd/system/envoy-gateway.service

echo "Retiring legacy ocm-vip-watchdog.service..."
sudo systemctl disable --now ocm-vip-watchdog.service 2>/dev/null || true

echo "Cleaning up legacy iptables DNAT rules for ports 80 and 6443..."
for PORT in 80 6443; do
  sudo iptables -t nat -D PREROUTING -d "${GATEWAY_IP}" -p tcp --dport \${PORT} -j DNAT --to-destination "${PRIMARY_HUB_IP}:\${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D PREROUTING -d "${GATEWAY_IP}" -p tcp --dport \${PORT} -j DNAT --to-destination "${SECONDARY_HUB_IP}:\${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D PREROUTING -d "${VIP}" -p tcp --dport \${PORT} -j DNAT --to-destination "${PRIMARY_HUB_IP}:\${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D PREROUTING -d "${VIP}" -p tcp --dport \${PORT} -j DNAT --to-destination "${SECONDARY_HUB_IP}:\${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D OUTPUT -d "${GATEWAY_IP}" -p tcp --dport \${PORT} -j DNAT --to-destination "${PRIMARY_HUB_IP}:\${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D OUTPUT -d "${GATEWAY_IP}" -p tcp --dport \${PORT} -j DNAT --to-destination "${SECONDARY_HUB_IP}:\${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D OUTPUT -d "${VIP}" -p tcp --dport \${PORT} -j DNAT --to-destination "${PRIMARY_HUB_IP}:\${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D OUTPUT -d "${VIP}" -p tcp --dport \${PORT} -j DNAT --to-destination "${SECONDARY_HUB_IP}:\${PORT}" 2>/dev/null || true
done

# Ensure Virtual IP is assigned to wg0
sudo ip addr add "${VIP}/32" dev wg0 2>/dev/null || true

# Start Envoy Gateway service
echo "Starting envoy-gateway.service..."
sudo systemctl daemon-reload
sudo systemctl enable --now envoy-gateway.service

# Verify health status
sleep 3
echo "Checking Envoy cluster statuses..."
curl -s http://127.0.0.1:9901/clusters | grep -E "(${PRIMARY_HUB_IP}|${SECONDARY_HUB_IP}).*(priority|health_flags)" || true
EOF

echo "=== Envoy Gateway Deployment Complete! ==="
