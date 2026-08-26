# Multi-Cluster Architecture & High-Availability Implementation Guide (Post-CIDR Migration)

## Executive Overview

This document serves as the complete operational runbook for the high-availability multi-cluster setup following the network migration from a single L2 subnet (`192.168.122.0/24`) to **separate per-VM CIDR blocks** backed by a **WireGuard encrypted mesh overlay network**.

---

## 1. Network Topology & CIDR Breakdown

Each VM resides on a distinct libvirt bridge and `/24` subnet. Communication between VMs is encrypted end-to-end using WireGuard (`wg0`).

| Node | Libvirt Network | Bridge Interface | Gateway | Physical IP | WireGuard Overlay IP | Role |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **primaryhub** | `net-primaryhub` | `virbr-phub` | `10.1.0.1` | `10.1.0.10` | `10.100.0.1/24` | Primary Hub |
| **secondaryhub** | `net-secondaryhub` | `virbr-shub` | `10.2.0.1` | `10.2.0.10` | `10.100.0.2/24` | Standby Hub / Controller |
| **spoke1** | `net-spoke1` | `virbr-spoke1` | `10.3.0.1` | `10.3.0.10` | `10.100.0.3/24` | Managed Workload Cluster |

---

## 2. Host Level Prerequisites (Libvirt Bridge Routing)

Because each VM is on a separate bridge, raw cross-bridge traffic is isolated. To allow WireGuard UDP handshakes while keeping data encrypted, enable IP forwarding for **UDP Port 51820 only** on the **HOST machine**:

```bash
# On the HOST Machine:
sudo sysctl -w net.ipv4.ip_forward=1
echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.d/99-wireguard.conf

# Allow WireGuard UDP traffic (port 51820) between bridges:
sudo iptables -I FORWARD -i virbr-phub  -o virbr-shub   -p udp --dport 51820 -j ACCEPT
sudo iptables -I FORWARD -i virbr-shub  -o virbr-phub   -p udp --dport 51820 -j ACCEPT
sudo iptables -I FORWARD -i virbr-phub  -o virbr-spoke1 -p udp --dport 51820 -j ACCEPT
sudo iptables -I FORWARD -i virbr-spoke1 -o virbr-phub  -p udp --dport 51820 -j ACCEPT
sudo iptables -I FORWARD -i virbr-shub  -o virbr-spoke1 -p udp --dport 51820 -j ACCEPT
sudo iptables -I FORWARD -i virbr-spoke1 -o virbr-shub  -p udp --dport 51820 -j ACCEPT

# Save iptables rules permanently:
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

---

## 3. WireGuard Encrypted Mesh Setup

### Step 3.1: Generate Keypairs on Each VM

Run on each VM (`primaryhub`, `secondaryhub`, `spoke1`):

```bash
sudo apt update && sudo apt install -y wireguard wireguard-tools
wg genkey | sudo tee /etc/wireguard/privatekey | wg pubkey | sudo tee /etc/wireguard/publickey
```

### Step 3.2: Configure `primaryhub` (`10.100.0.1`)

File: `/etc/wireguard/wg0.conf` on **primaryhub**:

```ini
[Interface]
Address = 10.100.0.1/24
ListenPort = 51820
PrivateKey = PRIVKEY_PLACEHOLDER

# secondaryhub
[Peer]
PublicKey = <SECONDARYHUB_PUBLIC_KEY>
Endpoint = 10.2.0.10:51820
AllowedIPs = 10.100.0.2/32
PersistentKeepalive = 25

# spoke1
[Peer]
PublicKey = <SPOKE1_PUBLIC_KEY>
Endpoint = 10.3.0.10:51820
AllowedIPs = 10.100.0.3/32
PersistentKeepalive = 25
```

Enable and start:
```bash
sudo sed -i "s|PRIVKEY_PLACEHOLDER|$(sudo cat /etc/wireguard/privatekey)|" /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
```

### Step 3.3: Configure `secondaryhub` (`10.100.0.2`)

File: `/etc/wireguard/wg0.conf` on **secondaryhub**:

```ini
[Interface]
Address = 10.100.0.2/24
ListenPort = 51820
PrivateKey = PRIVKEY_PLACEHOLDER

# primaryhub
[Peer]
PublicKey = <PRIMARYHUB_PUBLIC_KEY>
Endpoint = 10.1.0.10:51820
AllowedIPs = 10.100.0.1/32
PersistentKeepalive = 25

# spoke1
[Peer]
PublicKey = <SPOKE1_PUBLIC_KEY>
Endpoint = 10.3.0.10:51820
AllowedIPs = 10.100.0.3/32
PersistentKeepalive = 25
```

Enable and start:
```bash
sudo sed -i "s|PRIVKEY_PLACEHOLDER|$(sudo cat /etc/wireguard/privatekey)|" /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
```

### Step 3.4: Configure `spoke1` (`10.100.0.3`)

File: `/etc/wireguard/wg0.conf` on **spoke1**:

```ini
[Interface]
Address = 10.100.0.3/24
ListenPort = 51820
PrivateKey = PRIVKEY_PLACEHOLDER

# primaryhub
[Peer]
PublicKey = <PRIMARYHUB_PUBLIC_KEY>
Endpoint = 10.1.0.10:51820
AllowedIPs = 10.100.0.1/32
PersistentKeepalive = 25

# secondaryhub
[Peer]
PublicKey = <SECONDARYHUB_PUBLIC_KEY>
Endpoint = 10.2.0.10:51820
AllowedIPs = 10.100.0.2/32
PersistentKeepalive = 25
```

Enable and start:
```bash
sudo sed -i "s|PRIVKEY_PLACEHOLDER|$(sudo cat /etc/wireguard/privatekey)|" /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
```

### Step 3.5: Architectural Note — Host-Level WireGuard vs. Cilium CNI WireGuard

> **Decision:** **Do NOT enable Cilium CNI WireGuard (`cilium config set enable-wireguard true` or `encrypt-node: "true"`).**

#### Reason:
- **Host-Level WireGuard (`wg0`)** is already running at the Linux kernel OS layer. It encrypts **100% of network packets** leaving each VM (including API Server, SSH, Kubelet, Pod-to-Pod overlay, OCM, and Failover Controller traffic) over `10.100.0.x`.
- Enabling Cilium WireGuard in addition to Host WireGuard creates **double-encryption** (encrypting data twice), introducing unnecessary CPU overhead and redundant `cilium_wg0` virtual interfaces without adding security.

| Scope | Host WireGuard (`wg0`) — **APPLIED** | Cilium CNI WireGuard (`enable-wireguard`) — **NOT NEEDED** |
| :--- | :--- | :--- |
| **Encrypted Coverage** | All Host + Pod + API + Control plane traffic | Pod-to-Pod overlay traffic only |
| **IP Range** | WireGuard Mesh `10.100.0.0/24` | Pod Subnet `10.42.0.0/16` |
| **Status** | Systemd Service (`wg-quick@wg0`) | Omitted (prevents double encryption) |

---

## 4. RKE2 Configuration Updates & Troubleshooting

### 4.1 Consolidating `tls-san` Entries in `config.yaml`

A critical issue occurs when multiple `tls-san:` keys are appended to `/etc/rancher/rke2/config.yaml`. YAML parsers overwrite earlier keys, leading to server boot failure.

**Correct `/etc/rancher/rke2/config.yaml` on `primaryhub`:**

```yaml
cluster-init: true
write-kubeconfig-mode: "0644"

node-ip: "10.1.0.10"
node-external-ip: "10.1.0.10"
advertise-address: "10.1.0.10"

cni: cilium
disable-kube-proxy: true

cluster-cidr: "10.42.0.0/16"
service-cidr: "10.43.0.0/16"

tls-san:
  - "10.1.0.10"
  - "10.100.0.1"
  - "127.0.0.1"
  - "localhost"
  - "primaryhub"

disable:
  - rke2-canal
  - rke2-ingress-nginx

kubelet-arg:
  - "max-pods=250"
  - "serialize-image-pulls=false"
```

**Correct `/etc/rancher/rke2/config.yaml` on `secondaryhub`:**

```yaml
write-kubeconfig-mode: "0644"

node-ip: "10.2.0.10"
node-external-ip: "10.2.0.10"
advertise-address: "10.2.0.10"

cni: cilium
disable-kube-proxy: true

cluster-cidr: "10.42.0.0/16"
service-cidr: "10.43.0.0/16"

tls-san:
  - "10.2.0.10"
  - "10.100.0.2"
  - "127.0.0.1"
  - "localhost"
  - "secondaryhub"

disable:
  - rke2-canal
  - rke2-ingress-nginx

kubelet-arg:
  - "max-pods=250"
  - "serialize-image-pulls=false"
```

**Certificate Rotation Command (run after editing `config.yaml` on each hub):**

```bash
sudo systemctl stop rke2-server
sudo rke2 certificate rotate
sudo systemctl start rke2-server
```

### 4.2 Resolving `etcd` Peer URL Mismatch

**Symptom:**
```text
Failed to test data store connection: this server is not a member of the etcd cluster.
Found [primaryhub-1784d016=https://192.168.122.225:2380], expect: primaryhub-1784d016=https://10.1.0.10:2380
```

**Resolution:**
Update `etcd` peer URL in the live data store without resetting etcd or losing state:

```bash
# Locate etcdctl snapshot binary:
ETCDCTL=$(sudo find /var/lib/rancher/rke2 -name "etcdctl" | head -n 1)

# List current member details:
sudo $ETCDCTL \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/client.key \
  member list

# Update peer URL for member ID (e.g., a32461b004ab6884):
sudo $ETCDCTL \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/client.key \
  member update a32461b004ab6884 --peer-urls=https://10.1.0.10:2380
```

### 4.3 Cilium eBPF Table Refresh Post IP Migration

**Symptom:**
```text
Sending HTTP 502 response: dial tcp 10.42.0.x:10250: connect: network is unreachable
```

**Resolution:**
Restart the Cilium DaemonSet to flush stale eBPF routing tables:

```bash
kubectl rollout restart ds/cilium -n kube-system
```

---

## 4.4 Inter-Hub Kubeconfig & SCP Communication

Because physical IPs (`10.x.0.10`) are isolated on separate libvirt bridges, all `scp`, `ssh`, and `kubectl` cross-VM operations **must target WireGuard Overlay IPs (`10.100.0.x`)**.

### Copying `rke2.yaml` from `secondaryhub` to `primaryhub`

Since `/etc/rancher/rke2/rke2.yaml` is owned by `root:root` with `0600` permissions on `secondaryhub`:

**Option A (Direct SSH command with sudo):**
```bash
# Run on primaryhub:
ssh secondaryhub@10.100.0.2 "sudo cat /etc/rancher/rke2/rke2.yaml" > ~/.kube/config-secondaryhub
```

**Option B (Temp file copy):**
```bash
# Step 1: On secondaryhub
sudo cp /etc/rancher/rke2/rke2.yaml /tmp/rke2.yaml
sudo chmod 644 /tmp/rke2.yaml

# Step 2: On primaryhub over WireGuard
scp secondaryhub@10.100.0.2:/tmp/rke2.yaml ~/.kube/config-secondaryhub
```

---

## 5. Dual-Hub OCM Registration Over WireGuard

With separate CIDRs, ARP-based kube-vip is removed. Redundancy is handled natively via **OCM Dual Klusterlets**.

```
spoke1
├── open-cluster-management-agent/             → primaryhub   (10.100.0.1:6443)
└── open-cluster-management-agent-secondaryhub/ → secondaryhub (10.100.0.2:6443)
```

### Step 5.1: Join `primaryhub` (from `spoke1`)

```bash
# Get token on primaryhub:
KUBECONFIG=~/.kube/config-hubs clusteradm get token --context primaryhub

# Join from spoke1 over WireGuard:
clusteradm join \
  --hub-token <PRIMARY_HUB_TOKEN> \
  --hub-apiserver https://10.100.0.1:6443 \
  --cluster-name spoke1 \
  --wait

# Approve & Accept on primaryhub:
KUBECONFIG=~/.kube/config-hubs kubectl --context primaryhub certificate approve <CSR_NAME>
KUBECONFIG=~/.kube/config-hubs clusteradm accept --clusters spoke1 --context primaryhub --skip-approve-check
```

### Step 5.2: Join `secondaryhub` (from `spoke1`)

```bash
# Pull CA from secondaryhub over WireGuard:
openssl s_client -connect 10.100.0.2:6443 -showcerts </dev/null 2>/dev/null | \
  sed -ne '/-BEGIN CERTIFICATE-/,/-END CERTIFICATE-/p' > /tmp/secondary_ca.crt
SECONDARY_CA=$(base64 -w0 /tmp/secondary_ca.crt)

kubectl create namespace open-cluster-management-agent-secondaryhub --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic bootstrap-hub-kubeconfig \
  -n open-cluster-management-agent-secondaryhub \
  --from-literal=kubeconfig="apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: $SECONDARY_CA
    server: https://10.100.0.2:6443
  name: secondaryhub
contexts:
- context:
    cluster: secondaryhub
    user: bootstrap
  name: bootstrap
current-context: bootstrap
kind: Config
users:
- name: bootstrap
  user:
    token: <SECONDARY_HUB_TOKEN>" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f - <<'EOF'
apiVersion: operator.open-cluster-management.io/v1
kind: Klusterlet
metadata:
  name: klusterlet-secondaryhub
spec:
  namespace: open-cluster-management-agent-secondaryhub
  clusterName: spoke1
  registrationImagePullSpec: quay.io/open-cluster-management/registration:v1.3.1
  workImagePullSpec: quay.io/open-cluster-management/work:v1.3.1
  deployOption:
    mode: Default
EOF

# Approve & Accept on secondaryhub:
KUBECONFIG=~/.kube/config-hubs kubectl --context secondaryhub certificate approve <CSR_NAME>
KUBECONFIG=~/.kube/config-hubs clusteradm accept --clusters spoke1 --context secondaryhub --skip-approve-check
```

---

## 6. Failover Controller Watchdog Update

The Failover Controller running on `secondaryhub` monitors `primaryhub` and the `spoke1` witness server over WireGuard overlay IPs.

Update ConfigMap `/scripts/run.sh` to target WireGuard IPs:

```bash
WITNESS_URL="http://10.100.0.3:9999"
PRIMARY_HUB_IP="10.100.0.1"
```

---

## 7. Verification Matrix

| Verification Step | Command | Expected Result |
| :--- | :--- | :--- |
| **WireGuard Ping** | `ping -c 2 10.100.0.2` (from primaryhub) | `0% packet loss` |
| **RKE2 API Server** | `kubectl get nodes` | `primaryhub Ready` |
| **OCM Primary Hub** | `kubectl --context primaryhub get managedcluster spoke1` | `AVAILABLE: True` |
| **OCM Secondary Hub** | `kubectl --context secondaryhub get managedcluster spoke1` | `AVAILABLE: True` |
| **Quorum Witness** | `curl http://10.100.0.3:9999` | `reachable` |

---

## 8. Troubleshooting & Operational Field Lessons Learned

### 8.1 TLS SAN Verification Failure on WireGuard Overlay IPs (`10.100.0.x`)

- **Symptom:**
  ```text
  Unable to connect to the server: tls: failed to verify certificate:
  x509: certificate is valid for 10.1.0.10, 127.0.0.1... not 10.100.0.1
  ```
- **Root Cause:**
  When connecting to RKE2 API servers over WireGuard Overlay IPs (`10.100.0.1` / `10.100.0.2`), Go TLS client rejects the connection if those IPs are not explicitly listed under `tls-san` in `/etc/rancher/rke2/config.yaml`.
- **Resolution:**
  1. Update `/etc/rancher/rke2/config.yaml` on each hub to include its WireGuard overlay IP:
     - `primaryhub`: add `"10.100.0.1"`
     - `secondaryhub`: add `"10.100.0.2"`
  2. Rotate RKE2 certificates and restart:
     ```bash
     sudo systemctl stop rke2-server
     sudo rke2 certificate rotate
     sudo systemctl start rke2-server
     ```

---

### 8.2 Duplicate `tls-san:` Keys in `config.yaml`

- **Symptom:**
  RKE2 server fails to start with `level=fatal msg="Error: preparing..."`.
- **Root Cause:**
  YAML parsers take only the **last** occurrence of duplicate keys. If `tls-san:` is appended multiple times, RKE2 ignores earlier SAN entries.
- **Resolution:**
  Consolidate all SANs into a single YAML list:
  ```yaml
  tls-san:
    - "10.1.0.10"
    - "10.100.0.1"
    - "127.0.0.1"
    - "localhost"
    - "primaryhub"
  ```

---

### 8.3 Inter-VM SSH/SCP `Connection Refused` Across Subnets

- **Symptom:**
  ```text
  primaryhub@primaryhub:~$ scp secondaryhub@10.2.0.10:/etc/rancher/rke2/rke2.yaml ~/.kube/config-secondaryhub
  ssh: connect to host 10.2.0.10 port 22: Connection refused
  ```
- **Root Cause:**
  Physical IPs (`10.x.0.10`) reside on separate libvirt bridges. Direct TCP traffic between physical IPs is blocked.
- **Resolution:**
  Target the WireGuard Overlay IP (`10.100.0.2`):
  ```bash
  ssh secondaryhub@10.100.0.2 "sudo cat /etc/rancher/rke2/rke2.yaml" > ~/.kube/config-secondaryhub
  ```

---

### 8.4 `etcd` Peer URL Mismatch Post IP Migration

- **Symptom:**
  RKE2 startup loop logging `this server is not a member of the etcd cluster. Found [...=https://192.168.122.225:2380], expect: [...=https://10.1.0.10:2380]`.
- **Root Cause:**
  `etcd`'s on-disk raft metadata preserves old peer URLs across server restarts.
- **Resolution:**
  Update peer URL using `etcdctl member update`:
  ```bash
  ETCDCTL=$(sudo find /var/lib/rancher/rke2 -name "etcdctl" | head -n 1)
  sudo $ETCDCTL --endpoints=https://127.0.0.1:2379 \
    --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
    --cert=/var/lib/rancher/rke2/server/tls/etcd/client.crt \
    --key=/var/lib/rancher/rke2/server/tls/etcd/client.key \
    member list

  sudo $ETCDCTL --endpoints=https://127.0.0.1:2379 \
    --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
    --cert=/var/lib/rancher/rke2/server/tls/etcd/client.crt \
    --key=/var/lib/rancher/rke2/server/tls/etcd/client.key \
    member update <MEMBER_ID> --peer-urls=https://10.1.0.10:2380
  ```
