#!/usr/bin/env bash
set -euo pipefail
CONF="/etc/wireguard/wg0.conf"
[ -f "$CONF" ] || exit 0
SELF_NAME="${cluster_name:-$(cat /etc/hostname 2>/dev/null || hostname)}"
SELF_REGION="${region:-us-east-1}"

iptables -C FORWARD -i wg0 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wg0 -j ACCEPT 2>/dev/null || true
iptables -C FORWARD -o wg0 -j ACCEPT 2>/dev/null || iptables -A FORWARD -o wg0 -j ACCEPT 2>/dev/null || true
iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || true

cluster_params=$(aws ssm get-parameters-by-path --path "/sandbox/clusters" --region "us-east-1" --query "Parameters[*].Value" --output json 2>/dev/null || echo "[]")
[ -z "$cluster_params" ] || [ "$cluster_params" == "[]" ] && exit 0

while IFS= read -r entry; do
  [ -z "$entry" ] && continue
  peer_cluster=$(echo "$entry" | jq -r ".cluster_name // empty" 2>/dev/null || true)
  peer_region=$(echo "$entry" | jq -r ".region // empty" 2>/dev/null || true)
  peer_wg_ip=$(echo "$entry" | jq -r ".wg_ip // empty" 2>/dev/null || true)
  peer_priv_ip=$(echo "$entry" | jq -r ".private_ip // empty" 2>/dev/null || true)
  [ -z "$peer_cluster" ] || [ "$peer_cluster" == "$SELF_NAME" ] && continue

  peer_pubkey=$(aws ssm get-parameter --name "/sandbox/wireguard/${peer_cluster}/public-key" --region "us-east-1" --query "Parameter.Value" --output text 2>/dev/null || true)
  [ -z "$peer_pubkey" ] || [ "$peer_pubkey" = "None" ] && continue

  configured_pubkey=$( (grep -B 2 -A 5 "$peer_wg_ip" "$CONF" 2>/dev/null || true) | awk '/PublicKey/ {print $3}' | head -n 1)

  if [ -n "$configured_pubkey" ] && [ "$configured_pubkey" != "$peer_pubkey" ]; then
    ip link show wg0 >/dev/null 2>&1 && wg set wg0 peer "$configured_pubkey" remove 2>/dev/null || true
    sed -i "/# ${peer_cluster}/,+6d" "$CONF" 2>/dev/null || true
    configured_pubkey=""
  fi

  if [[ "$peer_priv_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    current_ip="$peer_priv_ip"
  else
    current_ip=$(aws ec2 describe-instances --region "$peer_region" --filters "Name=tag:ClusterName,Values=${peer_cluster}" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].PrivateIpAddress" --output text 2>/dev/null || true)
  fi

  if [[ "$current_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    if [ -n "$configured_pubkey" ]; then
      configured_ip=$( (grep -A 5 "$configured_pubkey" "$CONF" 2>/dev/null || true) | awk -F"[: ]+" '/Endpoint/ {print $3}' | head -n 1)
      if [ "$configured_ip" != "$current_ip" ]; then
        sed -i -E "/${configured_pubkey}/,/PersistentKeepalive/ s|Endpoint = [0-9.]+:51820|Endpoint = ${current_ip}:51820|" "$CONF" 2>/dev/null || true
        ip link show wg0 >/dev/null 2>&1 && wg set wg0 peer "$configured_pubkey" endpoint "${current_ip}:51820" persistent-keepalive 25 2>/dev/null || true
      fi
    else
      cat >> "$CONF" <<EOF_PEER

[Peer]
# ${peer_cluster}
PublicKey = ${peer_pubkey}
AllowedIPs = ${peer_wg_ip}/32
Endpoint = ${current_ip}:51820
PersistentKeepalive = 25
EOF_PEER
      ip link show wg0 >/dev/null 2>&1 && wg set wg0 peer "$peer_pubkey" allowed-ips "${peer_wg_ip}/32" endpoint "${current_ip}:51820" persistent-keepalive 25 2>/dev/null || true
    fi
  fi
done < <(echo "$cluster_params" | jq -c ".[] | (if type==\"string\" then fromjson else . end)" 2>/dev/null || true)

ip link show wg0 >/dev/null 2>&1 || { systemctl enable --now wg-quick@wg0 2>/dev/null || wg-quick up wg0 2>/dev/null || true; }
