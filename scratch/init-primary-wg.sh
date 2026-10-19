#!/bin/bash
set -euo pipefail
mkdir -p /etc/wireguard
if [ ! -s /etc/wireguard/private.key ]; then
  wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
  chmod 600 /etc/wireguard/private.key /etc/wireguard/public.key
fi
PRIVKEY=$(cat /etc/wireguard/private.key)
PUBKEY=$(cat /etc/wireguard/public.key)

cat > /etc/wireguard/wg0.conf <<EOF_WG
[Interface]
Address = 10.99.0.1/24
ListenPort = 51820
PrivateKey = $PRIVKEY
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -A FORWARD -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o wg0 -j MASQUERADE
EOF_WG
chmod 600 /etc/wireguard/wg0.conf

aws ssm put-parameter --name "/sandbox/wireguard/primaryhub/public-key" --value "$PUBKEY" --type "String" --overwrite --region "us-east-1" 2>/dev/null || true

ip link show wg0 >/dev/null 2>&1 || systemctl restart wg-quick@wg0 || wg-quick up wg0 || true
/usr/local/bin/sync-wireguard-peers.sh || true
wg show wg0
