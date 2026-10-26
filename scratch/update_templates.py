import glob
import sys

regions = ["us-east-1", "eu-west-1", "eu-central-1", "ap-south-1"]
base_dir = "/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox"

target_port_mapping = """  - containerPort: 30379
    hostPort: 30379
    protocol: TCP
  - containerPort: 30900
    hostPort: 30900
    protocol: TCP"""

old_port_mapping = """  - containerPort: 30379
    hostPort: 30379
    protocol: TCP"""

nat_rule_block = """iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || true

# Prometheus Remote Write Ingress NAT (Hubs): route WireGuard :30900 directly to KinD NodePort
if [ "$ROLE" = "hub" ]; then
  iptables -t nat -C PREROUTING ! -i br-+ -p tcp --dport 30900 -j DNAT --to-destination 172.18.0.2:30900 2>/dev/null || \\
  iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 30900 -j DNAT --to-destination 172.18.0.2:30900 2>/dev/null || true
  iptables -t nat -C OUTPUT -p tcp -o lo --dport 30900 -j DNAT --to-destination 172.18.0.2:30900 2>/dev/null || \\
  iptables -t nat -A OUTPUT -p tcp -o lo --dport 30900 -j DNAT --to-destination 172.18.0.2:30900 2>/dev/null || true
fi

# Spokes: Ensure no blanket DNAT rule on 30900 intercepts outbound Remote Write traffic
if [ "$ROLE" = "spoke" ]; then
  iptables -t nat -D PREROUTING -p tcp -m tcp --dport 30900 -j DNAT --to-destination 172.18.0.2:30900 2>/dev/null || true
  iptables -t nat -D OUTPUT -p tcp -m tcp --dport 30900 -j DNAT --to-destination 172.18.0.2:30900 2>/dev/null || true
fi"""

old_nat_line = "iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || true"

sync_nat_block = """iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || true

if [ "$${ROLE:-}" = "hub" ]; then
  iptables -t nat -C PREROUTING ! -i br-+ -p tcp --dport 30900 -j DNAT --to-destination 172.18.0.2:30900 2>/dev/null || \\
  iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 30900 -j DNAT --to-destination 172.18.0.2:30900 2>/dev/null || true
fi"""

old_sync_nat_line = "iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || true"

for reg in regions:
    path = f"{base_dir}/{reg}/compute/templates/user_data.sh.tftpl"
    with open(path, "r") as f:
        content = f.read()

    if "containerPort: 30900" in content:
        print(f"[{reg}] Already has 30900 extraPortMappings")
    else:
        if old_port_mapping not in content:
            print(f"[{reg}] ERROR: old_port_mapping not found")
            sys.exit(1)
        content = content.replace(old_port_mapping, target_port_mapping, 1)
        print(f"[{reg}] Added 30900 extraPortMappings")

    if "Prometheus Remote Write Ingress NAT" in content:
        print(f"[{reg}] Already has Prometheus Remote Write Ingress NAT")
    else:
        if old_nat_line not in content:
            print(f"[{reg}] ERROR: old_nat_line not found")
            sys.exit(1)
        content = content.replace(old_nat_line, nat_rule_block, 1)
        print(f"[{reg}] Added Prometheus Remote Write Ingress NAT rules")

    if 'if [ "$${ROLE:-}" = "hub" ]; then' in content:
        print(f"[{reg}] Already has sync_nat_block")
    else:
        if old_sync_nat_line in content:
            content = content.replace(old_sync_nat_line, sync_nat_block, 1)
            print(f"[{reg}] Added sync_nat_block to sync-wireguard-peers.sh")

    with open(path, "w") as f:
        f.write(content)

print("All regions updated successfully.")
