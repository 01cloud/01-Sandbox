### Create clusters

```bash
kind create cluster --name primaryhub --config primaryhub-cluster.yaml
kind create cluster --name secondaryhub --config secondaryhub-cluster.yaml
kind create cluster --name spoke1 --config spoke1-cluster.yaml
```


### Verify
```bash
kind get clusters
```


### Check each cluster's CIDRs
```bash
kubectl --context kind-primaryhub cluster-info dump | grep -m1 -E "cluster-cidr|service-cluster-ip-range"
kubectl --context kind-secondaryhub cluster-info dump | grep -m1 -E "cluster-cidr|service-cluster-ip-range"
kubectl --context kind-spoke1 cluster-info dump | grep -m1 -E "cluster-cidr|service-cluster-ip-range"
```

### Find the internal IP of the primaryhub control plane, secondaryhub control plane and spokes control plane container
```bash
kubectl --context kind-primaryhub get nodes -o wide
kubectl --context kind-secondaryhub get nodes -o wide
kubectl --context kind-spoke1 get nodes -o wide
```

> 💡 **Understanding Node IPs (`172.19.0.x`) vs. Cluster CIDRs (`10.x.x.x`)**:
> - **Docker Host / Node IPs (`172.19.0.x`)**: Assigned by Docker on your host machine to each KinD container on the shared `kind` Docker bridge network (`172.19.0.0/16`). This allows nodes and API servers to communicate with each other over container IPs.
> - **Kubernetes Pod & Service CIDRs**: Configured in cluster YAMLs (`podSubnet` and `serviceSubnet`). These are **100% unique per cluster** to prevent IP overlap during Cilium ClusterMesh routing:
>
> | Cluster | Config File | Pod CIDR (`podSubnet`) | Service CIDR (`serviceSubnet`) | Docker Node IP |
> | :--- | :--- | :--- | :--- | :--- |
> | **`primaryhub`** | `primaryhub-cluster.yaml` | **`10.244.0.0/16`** | **`10.96.0.0/16`** | `172.19.0.2` |
> | **`secondaryhub`** | `secondaryhub-cluster.yaml` | **`10.245.0.0/16`** | **`10.97.0.0/16`** | `172.19.0.3` |
> | **`spoke1`** | `spoke1-cluster.yaml` | **`10.246.0.0/16`** | **`10.98.0.0/16`** | `172.19.0.4` |
> | **`spoke2`** | `spoke2-cluster.yaml` | **`10.247.0.0/16`** | **`10.99.0.0/16`** | `172.19.0.5` |


### Switch between clusters via kubeconfig contexts

```bash
kubectl config get-contexts                 # list all cluster contexts
kubectl config use-context <context-name>   # switch
kubectl get nodes                           # now shows that cluster's nodes
```

### Or without switching context, target a specific one inline:

```bash
kubectl --context=kind-primaryhub get nodes
kubectl --context=kind-secondaryhub get nodes
kubectl --context=kind-spoke1 get nodes
---

## CRD Lifecycle & Automated Installation Matrix

> 💡 **Key Takeaway**: You do **NOT** need to manually download or install any Custom Resource Definitions (CRDs). Every required CRD is automatically registered and managed by the CLI installation tools (`clusteradm init`, `clusteradm join`, and `cilium install`).

| Component / Phase | Registered CRDs | Installation Trigger | Manual Action Required? |
| :--- | :--- | :--- | :---: |
| **OCM Hub Control Plane** | `managedclusters`, `placements`, `placementdecisions`, `addonplacementscores`, `manifestworks`, `managedclustersets` | `clusteradm init` | **NO** |
| **OCM Agent (Klusterlet)** | `klusterlets`, `appliedmanifestworks`, `clusterclaims` | `clusteradm join` / Klusterlet Operator | **NO** |
| **Cilium CNI & Mesh** | `ciliumnodes`, `ciliumendpoints`, `ciliumnetworkpolicies`, `ciliumclusterwidenetworkpolicies` | `cilium install` | **NO** |
| **Quorum Witness** | None (Uses core `ConfigMap` & `DaemonSet`) | `kubectl apply` | **NO** |
| **Score-Based Placement** | `placements`, `addonplacementscores` | Installed by `clusteradm init` | **NO** |

---

### Install OCM in both the Hub Clusters
curl -L https://raw.githubusercontent.com/open-cluster-management-io/clusteradm/main/install.sh | bash
clusteradm version

### Switch and Initialize OCM on Primary Hub (primaryhub)

```bash
kubectl config use-context kind-primaryhub
```


```bash
clusteradm init \
  --wait \
  --output-join-command-file /tmp/join-primary.txt

cat /tmp/join-primary.txt
```

###  Switch and Initialize OCM on Secondary Hub (secondaryhub)
```bash
kubectl config use-context kind-secondaryhub
```

```bash
clusteradm init \
  --wait \
  --output-join-command-file /tmp/join-secondary.txt

cat /tmp/join-secondary.txt
```

### Prepare Spoke Clusters & Install OCM Klusterlet Agent

💡 OCM on Spokes ≠ OCM Hub. You do not install the full OCM Hub on Spokes. Instead, clusteradm join installs a lightweight agent called the Klusterlet on the Spoke. The Klusterlet's only job is to:

- Register itself with the Hub and send heartbeats (CPU/RAM/status)
- Watch for ManifestWork objects dispatched from the Hub
- Apply those manifests locally (e.g., boot a Kata microVM worker pod)

> 💡 **Dual-Hub Architecture Rule (Multi-Klusterlet):**
> Standard `clusteradm join` overwrites `hub-kubeconfig-secret` when joining a second hub. To keep **both hubs `AVAILABLE: True` permanently**, deploy a **second independent Klusterlet instance** in namespace `open-cluster-management-agent-secondary` for `secondaryhub`.

### Register spoke1 with Primary Hub (`172.19.0.2`)

```bash
clusteradm join \
  --context kind-spoke1 \
  --hub-token <TOKEN_FROM_PRIMARY> \
  --hub-apiserver https://172.19.0.2:6443 \
  --cluster-name spoke1 \
  --wait
```

#### Accept spoke1 CSR on Primary Hub:
```bash
clusteradm --context kind-primaryhub accept --clusters spoke1
```

---

### Register spoke1 with Secondary Hub (`172.19.0.3`) via Second Klusterlet Instance

#### 1. Create agent namespace for Secondary Hub on `spoke1`:
```bash
kubectl --context kind-spoke1 create ns open-cluster-management-agent-secondary
```

#### 2. Create `klusterlet-secondary` Custom Resource on `spoke1`:
> 💡 **Why create `klusterlet-secondary` Custom Resource?**
> - **Dual-Hub Active-Active Topology**: Enables `spoke1` to be concurrently registered and managed by both `primaryhub` and `secondaryhub`.
> - **Prevents Overwriting Primary Klusterlet**: Standard `clusteradm join` overwrites the single default `klusterlet` CR and its secret in `open-cluster-management-agent`, which would disconnect `spoke1` from `primaryhub`.
> - **Agent Isolation**: Instantiates a dedicated set of OCM agents (registration and work agents) in `open-cluster-management-agent-secondary`, completely isolated from the primary hub's agent.

```bash
cat <<EOF | kubectl --context kind-spoke1 apply -f -
apiVersion: operator.open-cluster-management.io/v1
kind: Klusterlet
metadata:
  name: klusterlet-secondary
spec:
  clusterName: spoke1
  deployOption:
    mode: Default
  externalServerURLs:
  - url: https://spoke1-control-plane:6443
  imagePullSpec: quay.io/open-cluster-management/registration-operator:v1.3.1
  namespace: open-cluster-management-agent-secondary
  registrationImagePullSpec: quay.io/open-cluster-management/registration:v1.3.1
  workImagePullSpec: quay.io/open-cluster-management/work:v1.3.1
EOF
```

#### 3. Create bootstrap secret for Secondary Hub:
> 💡 **Why create `bootstrap-hub-kubeconfig` secret?**
> - **Initial Bootstrap Authentication**: Contains the secondary hub's API server endpoint and bootstrap token (`SEC_TOKEN`) generated from `secondaryhub`, allowing `klusterlet-secondary` to authenticate during initial registration.
> - **Triggers CSR Generation**: `klusterlet-secondary` uses these bootstrap credentials to generate a private key and submit a Certificate Signing Request (CSR) to `secondaryhub`.
> - **Namespace-Scoped Lookup**: `klusterlet-secondary` specifically looks for the `bootstrap-hub-kubeconfig` secret within its designated namespace (`open-cluster-management-agent-secondary`). Once `secondaryhub` accepts the CSR, signed TLS client certificates are saved into the permanent `hub-kubeconfig-secret`.

```bash
# Obtain secondary token
clusteradm --context kind-secondaryhub get token

# Create bootstrap secret in secondary agent namespace
kubectl --context kind-spoke1 create secret generic bootstrap-hub-kubeconfig \
  -n open-cluster-management-agent-secondary \
  --from-literal=kubeconfig='<KUBECONFIG_WITH_SECONDARY_TOKEN>'
```

#### 4. Accept spoke1 CSR on Secondary Hub:
```bash
clusteradm --context kind-secondaryhub accept --clusters spoke1 --skip-approve-check
```

### Verify spoke1 is registered with both hubs

```bash
kubectl --context kind-primaryhub get managedclusters
kubectl --context kind-secondaryhub get managedclusters
```

---

### How to Check and Verify Klusterlet Agent Health on Spoke Clusters

When a spoke cluster (`spoke1`, `spoke2`, `spoke3`) joins a Hub cluster via `clusteradm join`, OCM deploys the Klusterlet operator and agent pods onto the spoke cluster.

#### 1. Check Klusterlet Custom Resources & Operator
```bash
# Check Klusterlet Custom Resource on spoke cluster:
kubectl --context kind-spoke1 get klusterlet

# Check Klusterlet Operator Pod:
kubectl --context kind-spoke1 get pods -n open-cluster-management
```

#### 2. Check Klusterlet Registration & Work Agent Pods
```bash
# Check Primary Hub Agent Pods (Registration & Work Agent):
kubectl --context kind-spoke1 get pods -n open-cluster-management-agent

# Check Secondary Hub Agent Pods (Dual-Hub Setup):
kubectl --context kind-spoke1 get pods -n open-cluster-management-agent-secondary
```
*Key Agent Roles*:
- **`klusterlet-registration-agent`**: Submits/renews TLS client certificates via CSRs, maintains heartbeats with Hub (`AVAILABLE: True`), and reports node capacity.
- **`klusterlet-work-agent`**: Watches `ManifestWork` objects on the Hub and applies workloads (Deployments, Services) locally on the spoke.

#### 3. Inspect Agent Logs & Health
```bash
# View Primary Registration Agent logs:
kubectl --context kind-spoke1 -n open-cluster-management-agent logs deployment/klusterlet-registration-agent --tail=30

# View Primary Work Agent logs:
kubectl --context kind-spoke1 -n open-cluster-management-agent logs deployment/klusterlet-work-agent --tail=30
```

#### 4. Architecture: How Klusterlet Enables Communication
* **Pull-Based (Outbound Only)**: The Hub cluster (`primaryhub`/`secondaryhub`) **never** connects directly into the spoke cluster.
* **Outbound HTTPS (`https://<HUB-IP>:6443`)**: The Klusterlet agents on the spoke initiate outbound HTTPS TLS connections to the Hub API server to send heartbeats and pull `ManifestWork` items.
* **Firewall Friendly**: Spoke clusters can sit behind NAT or private firewalls without exposing any inbound ports.

---


## Token & Secret Storage Architecture

### 1. On Hub Clusters (`primaryhub` & `secondaryhub`)
* **Namespace**: `open-cluster-management`
* **Service Account**: `agent-registration-bootstrap`
* **Secret**: `agent-registration-bootstrap`
* **Purpose**: Holds the bootstrap token generated by `clusteradm get token` used by spokes for initial CSR requests.

---

### 2. On Spoke Clusters (`spoke1` & `spoke2`)
On spoke clusters, hub credentials are segregated into separate namespaces for each registered Hub:

#### A. Primary Hub Agent (`open-cluster-management-agent`)
* **Temporary Stage (Initial Join)**:
  * **Secret Name**: `bootstrap-hub-kubeconfig`
  * **Purpose**: Stores the initial `--hub-token` JWT passed during `clusteradm join`. Used to authenticate initial CSR request to `primaryhub`.
* **Permanent Stage (After Approval)**:
  * **Secret Name**: `hub-kubeconfig-secret`
  * **Purpose**: Stores signed TLS client certificates (`tls.crt` / `tls.key`) and API server endpoint for `primaryhub`.

#### B. Secondary Hub Agent (`open-cluster-management-agent-secondary`)
* **Temporary Stage (Initial Join)**:
  * **Secret Name**: `bootstrap-hub-kubeconfig`
  * **Purpose**: Stores the secondary hub's token and API endpoint. `klusterlet-secondary` uses this secret to authenticate and submit its CSR to `secondaryhub`.
* **Permanent Stage (After Approval)**:
  * **Secret Name**: `hub-kubeconfig-secret`
  * **Purpose**: Stores signed TLS client certificates (`tls.crt` / `tls.key`) and API server endpoint for `secondaryhub`.

```bash
# Inspect permanent hub certificates & configs on spoke1:
kubectl --context kind-spoke1 get secret -n open-cluster-management-agent hub-kubeconfig-secret -o yaml
kubectl --context kind-spoke1 get secret -n open-cluster-management-agent-secondary hub-kubeconfig-secret -o yaml
```

---

## Cilium ClusterMesh + WireGuard Tunnel Setup

```text
 PRIMARY HUB (primaryhub)                      SECONDARY HUB (secondaryhub)
  ─────────────────────                      ───────────────────────
  Cilium CNI (Cluster ID: 1)                 Cilium CNI (Cluster ID: 2)
          │                                           │
  cilium clustermesh enable                  cilium clustermesh enable
  → Deploys clustermesh-apiserver            → Deploys clustermesh-apiserver
          │                                           │
          └────────── cilium clustermesh connect ───────┘
                    → Exchanging TLS certs & ETCD credentials
                    → Establishes bidirectional peer mesh
          │                                           │
  WireGuard (cilium_wg0)                      WireGuard (cilium_wg0)
  → Encrypts all cross-cluster traffic       → Encrypts all cross-cluster traffic
```

> 💡 **ClusterMesh Scope & Spoke Cluster Architecture**:
> - **Hub Clusters Only**: Cilium ClusterMesh and WireGuard encryption are established **exclusively between Hub clusters** (`primaryhub` and `secondaryhub`).
> - **Spoke Clusters (`spoke1`, `spoke2`)**: Spoke clusters do **NOT** run Cilium or ClusterMesh. They use standard Kubernetes CNI (`kindnet`).
> - **Hub-to-Spoke Communication**: Spokes communicate with Hubs using Open Cluster Management (OCM) pull-based Klusterlet agents over outbound HTTPS (`:6443`) TLS tunnels.


### Phase 1: Install Cilium CNI on Both Hub Clusters

```bash
# Install Cilium on Primary Hub (Cluster ID: 1)
cilium install \
  --context kind-primaryhub \
  --set cluster.name=primaryhub \
  --set cluster.id=1

# Install Cilium on Secondary Hub (Cluster ID: 2)
cilium install \
  --context kind-secondaryhub \
  --set cluster.name=secondaryhub \
  --set cluster.id=2
```

---

### Phase 2: Enable WireGuard Encryption

Enable kernel WireGuard transparent encryption (`cilium_wg0`) for cross-cluster traffic:

```bash
# Enable WireGuard on Primary Hub
cilium config set encryption-type wireguard --context kind-primaryhub
cilium config set enable-wireguard true --context kind-primaryhub

# Enable WireGuard on Secondary Hub
cilium config set encryption-type wireguard --context kind-secondaryhub
cilium config set enable-wireguard true --context kind-secondaryhub
```

---

### Phase 3: Enable ClusterMesh on Both Hubs

```bash
# Enable ClusterMesh on Primary Hub
cilium clustermesh enable \
  --context kind-primaryhub \
  --service-type NodePort

# Enable ClusterMesh on Secondary Hub
cilium clustermesh enable \
  --context kind-secondaryhub \
  --service-type NodePort
```

#### Verify ClusterMesh Status:
```bash
cilium clustermesh status --context kind-primaryhub --wait
cilium clustermesh status --context kind-secondaryhub --wait
```

---

### Phase 4: Connect Clusters (Bidirectional Peer Mesh)

Exchanges TLS certificates and establishes cross-cluster peering:

```bash
cilium clustermesh connect \
  --context kind-primaryhub \
  --destination-context kind-secondaryhub
```

> 💡 **Under the Hood: How TLS Certs & ETCD Credentials Are Exchanged**
> - **Isolated `clustermesh-apiserver`**: Cilium does **not** touch or expose the primary Kubernetes control plane etcd. It deploys an isolated internal `clustermesh-apiserver` etcd instance in `kube-system` on each hub.
> - **Automated Certificate Extraction**: The CLI connects to both cluster API servers, extracts `secondaryhub`'s external etcd endpoint and mTLS client certificates (`ca.crt`, `tls.crt`, `tls.key`), and injects them into `primaryhub` as a Kubernetes Secret named `cilium-clustermesh` (under key `secondaryhub`) and `clustermesh-apiserver-remote-cert`.
> - **Bi-directional ETCD Peering**: The process repeats in reverse for `secondaryhub`. Cilium agents on each hub read these secrets and establish direct mTLS peer connections to synchronize cross-cluster pod IP and node identity state.

---

### Phase 5: Verification & Executive Proof Suite

#### 1. Check Mesh Status
```bash
cilium clustermesh status --context kind-primaryhub
cilium clustermesh status --context kind-secondaryhub
```

#### 2. Prove TLS Cert & ETCD Credential Exchange

##### Proof A: Inspect Exchanged ETCD Config Secret (`cilium-clustermesh`)
```bash
# Decode the secondaryhub etcd connection config stored on primaryhub:
kubectl --context kind-primaryhub -n kube-system get secret cilium-clustermesh -o jsonpath='{.data.secondaryhub}' | base64 -d
```
*Expected Output*:
```yaml
endpoints:
- https://clustermesh-apiserver.kube-system.svc:2379
trusted-ca-file: /var/lib/cilium/clustermesh/local-etcd-client-ca.crt
key-file: /var/lib/cilium/clustermesh/local-etcd-client.key
cert-file: /var/lib/cilium/clustermesh/local-etcd-client.crt
```
*What this proves*: `primaryhub` has received and stored `secondaryhub`'s etcd endpoint configuration and TLS certificate paths.

##### Proof B: Inspect Exchanged mTLS X.509 Certificates (`clustermesh-apiserver-remote-cert`)
```bash
# Decode and view the remote client TLS certificate details:
kubectl --context kind-primaryhub -n kube-system get secret clustermesh-apiserver-remote-cert -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -text -noout | grep -E "(Issuer|Subject|Not After)"
```
*Expected Output*:
```text
Issuer: CN=Clustermesh-CA
Subject: CN=remote
Not After : Aug 27 10:06:07 2029 GMT
```
*What this proves*: Valid mutual TLS (mTLS) X.509 certificates generated by the ClusterMesh CA are stored and active for cross-cluster authentication.

#### 3. Verify WireGuard Encryption (`cilium_wg0`)
```bash
# Check WireGuard status directly from the Cilium agent pod:
kubectl --context kind-primaryhub -n kube-system exec ds/cilium -c cilium-agent -- cilium status --verbose | grep -i wireguard

# Or check the cilium_wg0 interface on the container node:
docker exec primaryhub-control-plane ip link show cilium_wg0
```

#### 4. Run Multi-Cluster Connectivity Test
```bash
cilium connectivity test \
  --context kind-primaryhub \
  --multi-cluster kind-secondaryhub
```

#### 5. Monitor & Prove WireGuard Encryption (3-Step Verification Suite)

##### Proof 1: Check Cilium Active WireGuard Status & Peer Keys
```bash
kubectl --context kind-primaryhub -n kube-system exec ds/cilium -c cilium-agent -- cilium status --verbose | grep -i wireguard
kubectl --context kind-secondaryhub -n kube-system exec ds/cilium -c cilium-agent -- cilium status --verbose | grep -i wireguard
```
*Expected Output*:
```text
# primaryhub:
Encryption:       Wireguard       [NodeEncryption: Disabled, cilium_wg0 (Pubkey: S6+vV89m90lKMs25W6EBzwH/HVZBCllzZII9dgLOTwc=, Port: 51871, Peers: 1)]

# secondaryhub:
Encryption:       Wireguard       [NodeEncryption: Disabled, cilium_wg0 (Pubkey: nFgiXQgWU2vWePdaneFtu9fMeJrjci32bvuJq+zfGBk=, Port: 51871, Peers: 1)]
```

> 💡 **Technical Breakdown: Cross-Cluster WireGuard Validation & Key Exchange**
> - **Why `Peers: 1` Proves Cross-Cluster Peering**: Since each hub is a single-node KinD cluster (`primaryhub-control-plane` and `secondaryhub-control-plane`), there are no intra-cluster peer nodes. The **1 Peer** listed under `primaryhub`'s `cilium_wg0` interface is specifically `secondaryhub-control-plane` (`172.19.0.3:51871`), and vice-versa.
> - **Automated Key Exchange via ClusterMesh**: During `cilium clustermesh connect`, ClusterMesh etcd sync automatically exchanges the WireGuard public keys, Node IPs, and UDP ports (`51871`) between `primaryhub` and `secondaryhub`.
> - **eBPF Routing (`encryptkey=255`)**: Cilium configures eBPF `ipcache` entries (`cilium bpf ipcache list`) for remote Pod CIDRs (e.g. `10.245.0.0/24`) with `encryptkey=255`. Outbound pod-to-pod traffic targeting the remote cluster is intercepted by BPF and automatically routed into the `cilium_wg0` tunnel.
> - **Encrypted Scope**:
>   - **Cross-Cluster Pod-to-Pod & Pod-to-Node Traffic**: Fully encrypted via WireGuard (`cilium_wg0`).
>   - **ClusterMesh Control Plane Sync**: Encrypted via mTLS (etcd sync).
>   - **Raw Host Traffic**: Unencrypted (as indicated by `NodeEncryption: Disabled`).
>
> 💡 **Why `NodeEncryption: Disabled` is Normal & Expected:**
> - **Default Cilium WireGuard Mode**: When `encryption-type=wireguard` is enabled, Cilium enables transparent **Pod-to-Pod & Cross-Cluster encryption** by default (`cilium_wg0`, `Peers: 1`).
> - **Reason for `NodeEncryption: Disabled`**: Node Encryption specifically targets raw host-network traffic originating from the node itself (e.g., host-to-host `ssh`, node daemons). It is kept disabled by default to avoid CPU overhead on host management traffic (which is already secured over HTTPS/TLS).
> - **Optional Toggle**: If raw host-to-host traffic encryption is required, enable it via `cilium config set enable-wireguard-encrypted-node-traffic true`.

##### Proof 2: Compare Live Transmitted (TX) and Received (RX) Encrypted Bytes
```bash
# Check Primary Hub WireGuard statistics:
docker exec primaryhub-control-plane ip -s link show cilium_wg0

# Check Secondary Hub WireGuard statistics:
docker exec secondaryhub-control-plane ip -s link show cilium_wg0
```
*Expected Output*: Matching TX bytes on `primaryhub` = RX bytes on `secondaryhub`.

##### Proof 3: Live Packet Capture (`tcpdump` - Zero Plaintext Proof)
```bash
# Install tcpdump inside Kind container node (if not installed):
docker exec primaryhub-control-plane apt-get update -qq && docker exec primaryhub-control-plane apt-get install -y -qq tcpdump

# Run live packet capture on WireGuard port 51871:
docker exec primaryhub-control-plane tcpdump -i eth0 udp port 51871 -n -c 5
```
*Expected Output*: 100% encrypted WireGuard UDP datagrams on port 51871:
```text
11:57:11.101731 IP 172.19.0.2.51871 > 172.19.0.3.51871: UDP, length 144
11:57:11.102319 IP 172.19.0.3.51871 > 172.19.0.2.51871: UDP, length 144
```

---

## Full Active-Passive Hub Failover Setup (Kind Multi-Cluster)

```text
 ┌──────────────────────┐                     ┌────────────────────────┐
 │   PRIMARY HUB        │                     │   SECONDARY HUB        │
 │ (172.19.0.2:6443)    │                     │ (172.19.0.3:6443)      │
 └──────────▲───────────┘                     └───────────▲────────────┘
            │                                             │
      TCP   │                                       Polls │ TCP 6443
     Check  │                                       every │ & Quorum Witness
            │                                       5s    │
 ┌──────────┴───────────┐                                 │
 │     SPOOKE1          ├─────────────────────────────────┘
 │ Quorum Witness       │  http://172.19.0.4:9999
 │ (172.19.0.4:9999)    │  (Confirms 2-of-2 Quorum before failover)
 └──────────────────────┘
```

### Network & WireGuard Encryption Path Breakdown

| Component | Target Destination | WireGuard Encrypted? | Routing Details |
| :--- | :--- | :---: | :--- |
| **Failover Controller** | `primaryhub` (`172.19.0.2:6443`) | **YES** ✅ | Pod on `secondaryhub` calling `primaryhub` is routed through `cilium_wg0` tunnel. |
| **Failover Controller** | `spoke1` Witness (`172.19.0.4:9999`) | **NO** | Standard Docker network (`172.19.0.0/16`) to `spoke1` host IP. |
| **Quorum Witness** | `primaryhub` (`172.19.0.2:6443`) | **NO** | Bypasses Cilium pod network (`hostNetwork: true`) to query primary hub TCP 6443. |

#### Detailed Routing & Encryption Architecture:

1. **Why `Failover Controller ➔ primaryhub (172.19.0.2:6443)` IS WireGuard Encrypted (YES ✅):**
   - **Mesh Registration**: `primaryhub` (ID: 1) and `secondaryhub` (ID: 2) are connected via Cilium ClusterMesh with `enable-wireguard=true`.
   - **eBPF Datapath Interception**: When `failover-controller` on `secondaryhub` runs `kubectl --context kind-primaryhub get nodes`, Cilium's eBPF datapath intercepts all outbound traffic to `172.19.0.2:6443`.
   - **Kernel Encapsulation**: Cilium automatically encapsulates and encrypts API calls inside the kernel WireGuard interface (`cilium_wg0`) over UDP port 51871 between hubs.

2. **Why `Failover Controller ➔ spoke1 Witness (172.19.0.4:9999)` is NOT WireGuard Encrypted (NO):**
   - **Outside Mesh Scope**: `spoke1` is not part of Cilium ClusterMesh. Cilium on `secondaryhub` treats `spoke1` (`172.19.0.4`) as a standard external network endpoint.
   - **Direct Container Bridge Routing**: When `failover-controller` runs `curl http://172.19.0.4:9999`, the traffic bypasses `cilium_wg0` and travels un-encapsulated over the Docker bridge network (`172.19.0.0/16`).
   - **Host Network Listener**: The witness daemon on `spoke1` runs with `hostNetwork: true`, listening directly on port `9999` of `spoke1`'s host IP (`172.19.0.4`).

3. **Why `Quorum Witness ➔ primaryhub (172.19.0.2:6443)` is NOT WireGuard Encrypted (NO):**
   - **Bypasses Overlay**: The witness daemonset on `spoke1` uses `hostNetwork: true` to run TCP reachability checks (`nc -z -w 3 172.19.0.2 6443`).
   - **No WireGuard on Spoke**: Since `spoke1` does not run Cilium WireGuard, TCP connections from `spoke1` to `primaryhub:6443` travel over standard host IP network interfaces.

4. **Quorum Failover Logic**:
   - `primaryhub` is **Priority #1 (Active Hub)**.
   - `secondaryhub` is **Priority #2 (Passive / Watchdog Hub)**.
   - If `secondaryhub` loses contact with `primaryhub`, it queries `spoke1` (`http://172.19.0.4:9999`).
   - If `spoke1` reports **`reachable`**, `secondaryhub` aborts failover (preventing split-brain).
   - If `spoke1` reports **`unreachable`** (2-of-2 quorum confirmed), `secondaryhub` immediately takes over as Active Hub.

---

### Phase A — Deploy Quorum Witness on `spoke1`

> 💡 **Why Deploy Quorum Witness on `spoke1`?**
>
> 1. **Prevents Split-Brain & False-Positive Failovers**:
>    - If network connectivity between `primaryhub` (`172.19.0.2`) and `secondaryhub` (`172.19.0.3`) drops due to an inter-hub network partition or WireGuard tunnel failure while `primaryhub` is actually healthy, `secondaryhub` might falsely assume `primaryhub` is dead.
>    - Without a witness, `secondaryhub` would unilaterally promote itself to Active Hub while `primaryhub` is still healthy. This creates a **Split-Brain** condition where two active hubs concurrently manage workloads, dispatches conflicting manifests, and causes data corruption.
>
> 2. **Enforces 2-of-2 Quorum Consensus**:
>    - The Quorum Witness acts as an independent external tie-breaker running on `spoke1` (`172.19.0.4:9999`).
>    - Before `secondaryhub` initiates a failover sequence, it MUST verify a **2-of-2 consensus**:
>      - **Check 1**: `secondaryhub` itself cannot reach `primaryhub:6443`.
>      - **Check 2**: `secondaryhub` queries the `spoke1` witness (`http://172.19.0.4:9999`), and `spoke1` also reports `unreachable`.
>    - Only when BOTH nodes independently confirm `primaryhub` is unreachable does `secondaryhub` proceed with failover.
>
> 3. **Failure Scenario Matrix**:
>    - **Primary Hub Crash**: `secondaryhub` loses connection AND `spoke1` loses connection $\rightarrow$ **2/2 Quorum Confirmed** $\rightarrow$ Failover triggered.
>    - **Inter-Hub Network Partition**: `secondaryhub` loses connection to `primaryhub`, BUT `spoke1` can still reach `primaryhub` (returns `reachable`) $\rightarrow$ **Failover Aborted** (Split-brain prevented).
>    - **Spoke Network Partition**: `spoke1` loses connection, but `secondaryhub` can still reach `primaryhub` $\rightarrow$ No failover needed; `primaryhub` remains active.
>
> 4. **Technical Implementation Breakdown**:
>    - **ConfigMap (`witness-script`)**: Executes an infinite shell loop polling `https://172.19.0.2:6443/livez` (Primary Hub API server health check endpoint) using `curl -sk --max-time 3`. It returns `reachable` or `unreachable` on port `9999` using a lightweight netcat (`nc -l -p 9999`) HTTP response.
>    - **DaemonSet (`hub-witness`)**: Runs an `alpine:3.19` container on `spoke1` in `kube-system` using `hostNetwork: true`. This exposes port `9999` directly on `spoke1`'s host IP (`172.19.0.4`), allowing `secondaryhub` to query the HTTP endpoint directly over the Docker container bridge network (`172.19.0.0/16`).

- **Primary Hub Endpoint**: `https://172.19.0.2:6443/livez`
- **Primary Hub IP**: `172.19.0.2`
- **Primary Hub Port**: `6443`
- **Spoke1 Witness IP / Port**: `172.19.0.4:9999`

```bash
kubectl --context kind-spoke1 apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: witness-script
  namespace: kube-system
data:
  witness.sh: |
    #!/bin/sh
    PRIMARY_URL="https://172.19.0.2:6443/livez"
    while true; do
      if curl -sk --max-time 3 "$PRIMARY_URL" >/dev/null 2>&1; then
        STATUS="reachable"
      else
        STATUS="unreachable"
      fi
      printf "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n%s" "$STATUS" | \
        nc -l -p 9999 -q 1 2>/dev/null
      sleep 1
    done
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: hub-witness
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: hub-witness
  template:
    metadata:
      labels:
        app: hub-witness
    spec:
      hostNetwork: true
      tolerations:
      - effect: NoSchedule
        operator: Exists
      containers:
      - name: witness
        image: alpine:3.19
        command: ["/bin/sh", "-c", "apk add -q curl netcat-openbsd && /bin/sh /scripts/witness.sh"]
        ports:
        - containerPort: 9999
          hostPort: 9999
        volumeMounts:
        - name: script
          mountPath: /scripts
      volumes:
      - name: script
        configMap:
          name: witness-script
          defaultMode: 0755
EOF
```

#### Verify Witness on `spoke1`:
```bash
# Check Witness Pod
kubectl --context kind-spoke1 get pods -n kube-system -l app=hub-witness

# Verify status from spoke1 directly:
curl http://172.19.0.4:9999
# Expected: reachable
```

---

### Phase B — Deploy Failover Controller on `secondaryhub`

Runs on `secondaryhub`. Polls `primaryhub` TCP port `6443` every 5 seconds. After 3 consecutive misses + `spoke1` quorum confirmation → fences `primaryhub`, promotes database, and marks `spoke1` active on `secondaryhub`.

#### Step 1: Create Hub Kubeconfig Secret on `secondaryhub`

> 💡 **Why this Secret is Required:**
> 1. **Cross-Cluster Authentication**: `failover-controller` is a pod running on `secondaryhub`. By default, it only has a local ServiceAccount token which cannot authenticate to `primaryhub`.
> 2. **Multi-Cluster Access**: `hub-kubeconfig` bundles API endpoints (`https://172.19.0.2:6443` & `https://172.19.0.3:6443`) and admin TLS certificates for both hubs into `/root/.kube/config` mounted inside the container.
> 3. **Kind Internal Routing**: In Kind, loopback IPs (`127.0.0.1:<PORT>`) from your host machine fail inside containers. The secret uses internal container IPs (`172.19.0.2` & `172.19.0.3`) and `insecure-skip-tls-verify: true` to bypass IP hostname mismatch while preserving mTLS security.

Run this automated 1-click command to extract your local cluster certificates, configure the internal container endpoints (`https://172.19.0.2:6443` & `https://172.19.0.3:6443`), and create the `hub-kubeconfig` Secret on `secondaryhub`:

```bash
# 1. Automatically extract certs into variables
PRIM_CERT=$(kubectl config view --raw -o jsonpath='{.users[?(@.name=="kind-primaryhub")].user.client-certificate-data}')
PRIM_KEY=$(kubectl config view --raw -o jsonpath='{.users[?(@.name=="kind-primaryhub")].user.client-key-data}')
SEC_CERT=$(kubectl config view --raw -o jsonpath='{.users[?(@.name=="kind-secondaryhub")].user.client-certificate-data}')
SEC_KEY=$(kubectl config view --raw -o jsonpath='{.users[?(@.name=="kind-secondaryhub")].user.client-key-data}')

# 2. Apply secret manifest with injected certs
kubectl --context kind-secondaryhub apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: hub-kubeconfig
  namespace: kube-system
type: Opaque
stringData:
  config: |
    apiVersion: v1
    kind: Config
    clusters:
    - cluster:
        insecure-skip-tls-verify: true
        server: https://172.19.0.2:6443
      name: kind-primaryhub
    - cluster:
        insecure-skip-tls-verify: true
        server: https://172.19.0.3:6443
      name: kind-secondaryhub
    contexts:
    - context:
        cluster: kind-primaryhub
        user: kind-primaryhub
      name: kind-primaryhub
    - context:
        cluster: kind-secondaryhub
        user: kind-secondaryhub
      name: kind-secondaryhub
    current-context: kind-secondaryhub
    users:
    - name: kind-primaryhub
      user:
        client-certificate-data: ${PRIM_CERT}
        client-key-data: ${PRIM_KEY}
    - name: kind-secondaryhub
      user:
        client-certificate-data: ${SEC_CERT}
        client-key-data: ${SEC_KEY}
EOF
```

#### Step 2: Deploy Failover Controller on `secondaryhub`
```bash
kubectl --context kind-secondaryhub apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: failover-script
  namespace: kube-system
data:
  run.sh: |
    #!/bin/sh
    WITNESS_URL="http://172.19.0.4:9999"
    FAIL_COUNT=0
    THRESHOLD=3
    PROMOTED=false
    KUBECONF="/root/.kube/config"

    echo "[failover] Starting watchdog. Witness: $WITNESS_URL"

    while true; do
      sleep 5

      # Check if primaryhub API server is reachable via kubectl
      if kubectl --kubeconfig=$KUBECONF --context kind-primaryhub \
           get nodes --request-timeout=3s >/dev/null 2>&1; then
        if [ "$PROMOTED" = "true" ]; then
          echo "[failover] primaryhub recovered — executing automatic failback reset"
          kubectl --kubeconfig=$KUBECONF --context kind-secondaryhub \
            annotate managedcluster spoke1 failover.hub/active- --overwrite >/dev/null 2>&1 || true
        elif [ "$FAIL_COUNT" -gt 0 ]; then
          echo "[failover] primaryhub recovered — resetting counter"
        fi
        FAIL_COUNT=0
        PROMOTED=false
        continue
      fi

      FAIL_COUNT=$((FAIL_COUNT + 1))
      echo "[failover] primaryhub UNREACHABLE ($FAIL_COUNT/$THRESHOLD)"
      [ "$FAIL_COUNT" -lt "$THRESHOLD" ] && continue
      [ "$PROMOTED" = "true" ] && continue

      # Quorum check — ask spoke1 witness
      WITNESS=$(curl -sk --max-time 5 "$WITNESS_URL" 2>/dev/null || echo "unreachable")
      echo "[failover] spoke1 witness says: $WITNESS"

      if ! echo "$WITNESS" | grep -q "unreachable"; then
        echo "[failover] SPLIT-BRAIN SUSPECTED — spoke1 can reach primaryhub. Aborting."
        FAIL_COUNT=0
        continue
      fi

      echo "[failover] QUORUM CONFIRMED (2/2) — starting failover sequence"

      # Step 1: Fence — delete control-plane pods on primaryhub (best-effort)
      kubectl --kubeconfig=/root/.kube/config --context kind-primaryhub \
        delete pod -n kube-system -l tier=control-plane 2>/dev/null && \
        echo "[failover] primaryhub fenced" || \
        echo "[failover] fence via API failed (node down) — expected"

      # Step 2: Promote PostgreSQL (skipped gracefully if not deployed)
      kubectl --kubeconfig=/root/.kube/config --context kind-secondaryhub \
        cnpg promote postgresql-secondary -n opensandbox-system 2>/dev/null && \
        echo "[failover] PostgreSQL promoted to Primary" || \
        echo "[failover] PostgreSQL skipped (not deployed yet)"

      # Step 3: Annotate OCM managedcluster on secondaryhub
      kubectl --kubeconfig=/root/.kube/config --context kind-secondaryhub \
        annotate managedcluster spoke1 failover.hub/active="true" --overwrite 2>/dev/null && \
        echo "[failover] OCM spoke1 marked as active on secondaryhub" || true

      echo "[failover] === FAILOVER COMPLETE at $(date -u) ==="
      PROMOTED=true
    done
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: failover-controller
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: failover-controller-role
rules:
- apiGroups: ["apps"]
  resources: ["daemonsets"]
  verbs: ["get", "patch", "update"]
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["list", "delete"]
- apiGroups: ["cluster.open-cluster-management.io"]
  resources: ["managedclusters"]
  verbs: ["get", "patch", "update", "annotate"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: failover-controller-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: failover-controller-role
subjects:
- kind: ServiceAccount
  name: failover-controller
  namespace: kube-system
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: failover-controller
  namespace: kube-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: failover-controller
  template:
    metadata:
      labels:
        app: failover-controller
    spec:
      serviceAccountName: failover-controller
      containers:
      - name: controller
        image: bitnami/kubectl:latest
        command: ["/bin/sh", "/scripts/run.sh"]
        volumeMounts:
        - name: script
          mountPath: /scripts
        - name: kubeconfig
          mountPath: /root/.kube
      volumes:
      - name: script
        configMap:
          name: failover-script
          defaultMode: 0755
      - name: kubeconfig
        secret:
          secretName: hub-kubeconfig
EOF
```

#### Step 3: Verify Failover Controller on `secondaryhub`
```bash
# Check pod status on secondaryhub
kubectl --context kind-secondaryhub get pods -n kube-system -l app=failover-controller

# Stream watchdog logs
kubectl --context kind-secondaryhub logs -n kube-system deploy/failover-controller -f
```

---

### Step 4: Testing Failover & Automatic Recovery

#### 1. Simulate Primary Hub Failure (Pause container):
```bash
docker pause primaryhub-control-plane
```
Watch `secondaryhub` failover controller logs:
`kubectl --context kind-secondaryhub logs -n kube-system deploy/failover-controller -f`
*Expected log output*:
```text
[failover] primaryhub UNREACHABLE (1/3)
[failover] primaryhub UNREACHABLE (2/3)
[failover] primaryhub UNREACHABLE (3/3)
[failover] spoke1 witness says: unreachable
[failover] QUORUM CONFIRMED (2/2) — starting failover sequence
[failover] OCM spoke1 marked as active on secondaryhub
[failover] === FAILOVER COMPLETE ===
```

#### 2. Simulate Primary Hub Recovery (Unpause container):
```bash
docker unpause primaryhub-control-plane
```
Watch `secondaryhub` failover controller logs:
*Expected log output*:
```text
[failover] primaryhub recovered — executing automatic failback reset
```

---

### Step 5: Uninstall / Disable Failover Setup

Run these commands to remove the Failover Controller and Quorum Witness from your clusters:

```bash
# 1. Remove Failover Controller & RBAC from secondaryhub
kubectl --context kind-secondaryhub delete deployment failover-controller -n kube-system --ignore-not-found
kubectl --context kind-secondaryhub delete configmap failover-script -n kube-system --ignore-not-found
kubectl --context kind-secondaryhub delete secret hub-kubeconfig -n kube-system --ignore-not-found
kubectl --context kind-secondaryhub delete clusterrolebinding failover-controller-binding --ignore-not-found
kubectl --context kind-secondaryhub delete clusterrole failover-controller-role --ignore-not-found
kubectl --context kind-secondaryhub delete serviceaccount failover-controller -n kube-system --ignore-not-found

# 2. Remove failover annotations from managedcluster on secondaryhub
kubectl --context kind-secondaryhub annotate managedcluster spoke1 failover.hub/active- --overwrite 2>/dev/null || true

# 3. Remove Quorum Witness DaemonSet & ConfigMap from spoke1
kubectl --context kind-spoke1 delete daemonset hub-witness -n kube-system --ignore-not-found
kubectl --context kind-spoke1 delete configmap witness-script -n kube-system --ignore-not-found
```

---

## Workload Deployment & Cross-Cluster Reachability

### Method 1: Deploy NGINX to `spoke1` via OCM `ManifestWork`

In Open Cluster Management (OCM), workloads are dispatched from Hubs to Spokes using `ManifestWork` CRDs.

#### 1. Dispatch NGINX Deployment from `primaryhub` to `spoke1`:
```bash
kubectl --context kind-primaryhub apply -f - <<'EOF'
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: nginx-primaryhub-workload
  namespace: spoke1
spec:
  workload:
    manifests:
    - apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: nginx-from-primaryhub
        namespace: default
      spec:
        replicas: 2
        selector:
          matchLabels:
            app: nginx-from-primaryhub
        template:
          metadata:
            labels:
              app: nginx-from-primaryhub
          spec:
            containers:
            - name: nginx
              image: nginx:alpine
              ports:
              - containerPort: 80
EOF
```

#### 2. Dispatch NGINX Deployment from `secondaryhub` to `spoke1`:
```bash
kubectl --context kind-secondaryhub apply -f - <<'EOF'
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: nginx-secondaryhub-workload
  namespace: spoke1
spec:
  workload:
    manifests:
    - apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: nginx-from-secondaryhub
        namespace: default
      spec:
        replicas: 2
        selector:
          matchLabels:
            app: nginx-from-secondaryhub
        template:
          metadata:
            labels:
              app: nginx-from-secondaryhub
          spec:
            containers:
            - name: nginx
              image: nginx:alpine
              ports:
              - containerPort: 80
EOF
```

#### 3. Verify NGINX Pods on `spoke1`:
```bash
kubectl --context kind-spoke1 get pods
```

#### 4. List & Inspect `ManifestWork` on `primaryhub` & `secondaryhub`:
```bash
# List ManifestWork on primaryhub:
kubectl --context kind-primaryhub get manifestwork -n spoke1

# List ManifestWork on secondaryhub:
kubectl --context kind-secondaryhub get manifestwork -n spoke1

# View detailed status and applied resource conditions:
kubectl --context kind-primaryhub describe manifestwork nginx-primaryhub-workload -n spoke1
kubectl --context kind-secondaryhub describe manifestwork nginx-secondaryhub-workload -n spoke1
```

#### 5. Describe Pods & Stream Logs on `spoke1`:
```bash
# Describe NGINX pod events on spoke1:
kubectl --context kind-spoke1 describe pod -l app=nginx-from-primaryhub

# Stream live logs for NGINX deployed by primaryhub:
kubectl --context kind-spoke1 logs -l app=nginx-from-primaryhub -f

# Stream live logs for NGINX deployed by secondaryhub:
kubectl --context kind-spoke1 logs -l app=nginx-from-secondaryhub -f

# View live logs by specific pod name:
kubectl --context kind-spoke1 logs <POD_NAME>
```
---

### Outage Operations: Managing & Viewing `spoke1` Workloads when `primaryhub` is DOWN

> 💡 **Log Inspection during Outage:** `kubectl --context kind-spoke1 logs` connects directly to `spoke1`'s API server (`172.19.0.4:6443`) and does NOT pass through `primaryhub`. You can stream `spoke1` logs directly even if `primaryhub` is completely offline!

#### 1. Viewing `spoke1` Pod Status via `secondaryhub` during Outage
When `primaryhub` is offline, `secondaryhub` reads the live status of `spoke1` workloads reported by its own active agent (`klusterlet-secondary`):

```bash
# View ManifestWork resource status feedback on secondaryhub:
kubectl --context kind-secondaryhub get manifestwork -n spoke1 -o yaml
```

#### 2. Stopping/Deleting Workloads Dispatched by `primaryhub` from `secondaryhub`
If `primaryhub` is down and you need to stop or delete a deployment on `spoke1` originally created by `primaryhub`, apply a ManifestWork from `secondaryhub` with `spec.replicas: 0`:

```bash
kubectl --context kind-secondaryhub apply -f - <<'EOF'
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: stop-primary-nginx
  namespace: spoke1
spec:
  workload:
    manifests:
    - apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: nginx-from-primaryhub
        namespace: default
      spec:
        replicas: 0
        selector:
          matchLabels:
            app: nginx-from-primaryhub
        template:
          metadata:
            labels:
              app: nginx-from-primaryhub
          spec:
            containers:
            - name: nginx
              image: nginx:alpine
EOF
```
*Result*: `secondaryhub`'s active agent on `spoke1` receives the ManifestWork and scales `replicas: 0`, terminating all NGINX pods on `spoke1` immediately! Then clean up the temporary ManifestWork:

```bash
kubectl --context kind-secondaryhub delete manifestwork stop-primary-nginx -n spoke1
```

---

## Kubernetes-Native Virtual IP (kube-vip) Active-Passive Setup

`kube-vip` runs as a pure **Kubernetes DaemonSet** in the `kube-system` namespace on both `primaryhub` and `secondaryhub`. It manages a single floating Virtual IP (`172.19.0.250`) via Layer-2 Gratuitous ARP (GARP). Users and spokes connect to `172.19.0.250:6443` (or a domain mapped to it) without needing individual hub IPs.

### 1. Deploy `kube-vip` DaemonSet on `primaryhub` & `secondaryhub`

```bash
# Apply kube-vip manifest on primaryhub:
cat <<'EOF' | kubectl --context kind-primaryhub apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kube-vip
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kube-vip-role
rules:
- apiGroups: [""]
  resources: ["services", "services/status", "nodes", "endpoints"]
  verbs: ["list", "get", "watch", "update", "patch"]
- apiGroups: ["coordination.k8s.io"]
  resources: ["leases"]
  verbs: ["list", "get", "watch", "update", "create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kube-vip-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kube-vip-role
subjects:
- kind: ServiceAccount
  name: kube-vip
  namespace: kube-system
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kube-vip-ds
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: kube-vip-ds
  template:
    metadata:
      labels:
        app.kubernetes.io/name: kube-vip-ds
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: node-role.kubernetes.io/control-plane
                operator: Exists
      containers:
      - image: ghcr.io/kube-vip/kube-vip:v0.8.9
        name: kube-vip
        command: ["/kube-vip", "manager"]
        securityContext:
          capabilities:
            add:
            - NET_ADMIN
            - NET_RAW
        env:
        - name: vip_arp
          value: "true"
        - name: port
          value: "6443"
        - name: vip_interface
          value: "eth0"
        - name: vip_cidr
          value: "32"
        - name: cp_enable
          value: "true"
        - name: cp_namespace
          value: "kube-system"
        - name: vip_leaderelection
          value: "true"
        - name: vip_leasename
          value: "plnk-vip-lease"
        - name: address
          value: "172.19.0.250"
      hostNetwork: true
      serviceAccountName: kube-vip
EOF

# Apply kube-vip manifest on secondaryhub (Active-Passive Standby Mode):
cat <<'EOF' | kubectl --context kind-secondaryhub apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kube-vip
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kube-vip-role
rules:
- apiGroups: [""]
  resources: ["services", "services/status", "nodes", "endpoints"]
  verbs: ["list", "get", "watch", "update", "patch"]
- apiGroups: ["coordination.k8s.io"]
  resources: ["leases"]
  verbs: ["list", "get", "watch", "update", "create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kube-vip-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kube-vip-role
subjects:
- kind: ServiceAccount
  name: kube-vip
  namespace: kube-system
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kube-vip-ds
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: kube-vip-ds
  template:
    metadata:
      labels:
        app.kubernetes.io/name: kube-vip-ds
    spec:
      nodeSelector:
        failover.open-cluster-management.io/vip: active
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: node-role.kubernetes.io/control-plane
                operator: Exists
      containers:
      - image: ghcr.io/kube-vip/kube-vip:v0.8.9
        name: kube-vip
        command: ["/kube-vip", "manager"]
        securityContext:
          capabilities:
            add:
            - NET_ADMIN
            - NET_RAW
        env:
        - name: vip_arp
          value: "true"
        - name: port
          value: "6443"
        - name: vip_interface
          value: "eth0"
        - name: vip_cidr
          value: "32"
        - name: cp_enable
          value: "true"
        - name: cp_namespace
          value: "kube-system"
        - name: vip_leaderelection
          value: "true"
        - name: vip_leasename
          value: "plnk-vip-lease"
        - name: address
          value: "172.19.0.250"
      hostNetwork: true
      serviceAccountName: kube-vip
EOF
```

### 2. Verify `kube-vip` Status & Virtual IP Binding

```bash
# Verify DaemonSet pods on primaryhub & secondaryhub:
kubectl --context kind-primaryhub get pods -n kube-system -l app.kubernetes.io/name=kube-vip-ds
kubectl --context kind-secondaryhub get pods -n kube-system -l app.kubernetes.io/name=kube-vip-ds

# Verify Virtual IP (172.19.0.250) is bound on active hub:
docker exec primaryhub-control-plane ip addr show eth0 | grep 172.19.0.250
```

---

### 3. Live 3-Step Proof of Automatic Virtual IP Failover

Run this single test script to empirically prove Virtual IP failover:

```bash
# Step 1: Query API via Virtual IP (primaryhub active)
curl -sk https://172.19.0.250:6443/version

# Step 2: Pause primaryhub (simulate outage)
docker pause primaryhub-control-plane
sleep 2

# Step 3: Query SAME Virtual IP again (secondaryhub claimed VIP in 1s!)
curl -sk https://172.19.0.250:6443/version

# Step 4: Unpause primaryhub
docker unpause primaryhub-control-plane
```

*Expected Output*: Both Step 1 and Step 3 return `gitVersion: v1.35.1` with **zero connection errors**, proving the VIP failed over to `secondaryhub` automatically!

### 3. Expose Services via Virtual IP (`EXTERNAL-IP` Display)

When you expose a Deployment as a `type: LoadBalancer` Service and annotate it with the Virtual IP, Kubernetes displays `172.19.0.250` under **`EXTERNAL-IP`**:

```bash
# Expose Service with Virtual IP on primaryhub:
kubectl --context kind-primaryhub create deployment nginx-lb --image=nginx:alpine
kubectl --context kind-primaryhub expose deployment nginx-lb --type=LoadBalancer --name=nginx-lb-svc --port=80
kubectl --context kind-primaryhub annotate service nginx-lb-svc kube-vip.io/loadbalancerIPs="172.19.0.250" --overwrite

# Verify EXTERNAL-IP displays 172.19.0.250:
kubectl --context kind-primaryhub get svc nginx-lb-svc
```

*Expected Output*:
```text
NAME           TYPE           CLUSTER-IP      EXTERNAL-IP    PORT(S)        AGE
nginx-lb-svc   LoadBalancer   10.96.181.223   172.19.0.250   80:30355/TCP   3s
```

---

### 4. Active-Passive Unified LoadBalancer Service Manifest

#### Service Manifest (`my-app-svc.yaml`):
```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-app-svc
  annotations:
    kube-vip.io/loadbalancerIPs: "172.19.0.250"
spec:
  type: LoadBalancer
  ports:
  - port: 80
    targetPort: 80
  selector:
    app: my-app
```

#### Apply Manifest on Both Hubs:
```bash
# Apply on Primary Hub:
kubectl --context kind-primaryhub apply -f my-app-svc.yaml

# Apply on Secondary Hub:
kubectl --context kind-secondaryhub apply -f my-app-svc.yaml
```

#### Check Service Status on Both Hubs:
```bash
# View Service on Primary Hub (Active: EXTERNAL-IP = 172.19.0.250):
kubectl --context kind-primaryhub get svc my-app-svc

# View Service on Secondary Hub (Standby: EXTERNAL-IP = <pending>):
kubectl --context kind-secondaryhub get svc my-app-svc
```

*Expected Outputs during Normal State*:

- **`primaryhub` Output (Active)**:
  ```text
  NAME         TYPE           CLUSTER-IP     EXTERNAL-IP    PORT(S)        AGE
  my-app-svc   LoadBalancer   10.96.54.199   172.19.0.250   80:30525/TCP   5m
  ```

- **`secondaryhub` Output (Standby)**:
  ```text
  NAME         TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)        AGE
  my-app-svc   LoadBalancer   10.97.51.88   <pending>     80:32235/TCP   5m
  ```

> 💡 **Why `secondaryhub` shows `<pending>` during Normal State:**
> Only one cluster can active-bind Virtual IP `172.19.0.250` at a time. While `primaryhub` is healthy, `secondaryhub` stays in Standby mode (`EXTERNAL-IP: <pending>`). When `primaryhub` drops during a failover event, `secondaryhub` promotes itself and automatically binds `EXTERNAL-IP: 172.19.0.250`!

---

## Adding Additional Spoke Clusters (e.g. `spoke2`)

To scale out and register an additional spoke cluster (`spoke2`) connected concurrently to both `primaryhub` and `secondaryhub`:

### Step 1: Create `spoke2` Kind Cluster
```bash
kind create cluster --name spoke2 --config - <<EOF
apiVersion: kind.x-k8s.io/v1alpha4
kind: Cluster
networking:
  podSubnet: "10.246.0.0/16"
  serviceSubnet: "10.102.0.0/16"
nodes:
- role: control-plane
EOF
```

### Step 2: Register `spoke2` with `primaryhub` (Agent 1)

```bash
# 1. Print the token from primaryhub
clusteradm --context kind-primaryhub get token

# 2. Save the token into variable TOKEN
TOKEN=$(clusteradm --context kind-primaryhub get token | head -n1 | cut -d= -f2)

# 3. Join spoke2 to primaryhub
clusteradm --context kind-spoke2 join \
  --hub-apiserver https://172.19.0.2:6443 \
  --hub-token "$TOKEN" \
  --cluster-name spoke2 \
  --wait

# 4. Accept spoke2 request on primaryhub
clusteradm --context kind-primaryhub accept --clusters spoke2 --skip-approve-check
```

### Step 3: Register `spoke2` with `secondaryhub` (Agent 2 - Secondary Klusterlet)

```bash
# 1. Create secondary agent namespace on spoke2
kubectl --context kind-spoke2 create ns open-cluster-management-agent-secondary

# 2. Get the token and CA data from secondaryhub
SEC_TOKEN=$(clusteradm --context kind-secondaryhub get token | head -n1 | cut -d= -f2)
SEC_CA=$(kubectl --context kind-secondaryhub config view --flatten --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')

# 3. Create bootstrap secret directly on spoke2 for secondaryhub
kubectl --context kind-spoke2 create secret generic bootstrap-hub-kubeconfig \
  -n open-cluster-management-agent-secondary \
  --from-literal=kubeconfig="apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: ${SEC_CA}
    server: https://172.19.0.3:6443
  name: hub
contexts:
- context:
    cluster: hub
    user: bootstrap
  name: bootstrap
current-context: bootstrap
users:
- name: bootstrap
  user:
    token: ${SEC_TOKEN}"

# 4. Deploy Klusterlet Secondary CR on spoke2:
cat <<'EOF' | kubectl --context kind-spoke2 apply -f -
apiVersion: operator.open-cluster-management.io/v1
kind: Klusterlet
metadata:
  name: klusterlet-secondary
spec:
  namespace: open-cluster-management-agent-secondary
  registrationImagePullSpec: quay.io/open-cluster-management/registration:latest
  workImagePullSpec: quay.io/open-cluster-management/work:latest
  clusterName: spoke2
  externalServerURLs:
  - url: https://spoke2-control-plane:6443
  hubApiServerHostAlias:
    ip: 172.19.0.3
    hostname: secondaryhub-control-plane
EOF

# 5. Accept spoke2 CSR on secondaryhub
clusteradm --context kind-secondaryhub accept --clusters spoke2 --skip-approve-check
```

### Step 4: Deploy Quorum Witness DaemonSet on `spoke2`
```bash
kubectl --context kind-spoke2 apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: witness-script
  namespace: kube-system
data:
  witness.sh: |
    #!/bin/sh
    PRIMARY_URL="https://172.19.0.2:6443/livez"
    while true; do
      if curl -sk --max-time 3 "$PRIMARY_URL" >/dev/null 2>&1; then
        STATUS="reachable"
      else
        STATUS="unreachable"
      fi
      printf "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n%s" "$STATUS" | \
        nc -l -p 9999 -q 1 2>/dev/null
      sleep 1
    done
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: hub-witness
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: hub-witness
  template:
    metadata:
      labels:
        app: hub-witness
    spec:
      hostNetwork: true
      tolerations:
      - effect: NoSchedule
        operator: Exists
      containers:
      - name: witness
        image: alpine:3.19
        command: ["/bin/sh", "-c", "apk add -q curl netcat-openbsd && /bin/sh /scripts/witness.sh"]
        ports:
        - containerPort: 9999
          hostPort: 9999
        volumeMounts:
        - name: script
          mountPath: /scripts
      volumes:
      - name: script
        configMap:
          name: witness-script
          defaultMode: 0755
EOF
```

### Step 5: Verify `spoke2` Availability on Both Hubs
```bash
kubectl --context kind-primaryhub get managedclusters spoke2
kubectl --context kind-secondaryhub get managedclusters spoke2
```

---

## Score-Based Multi-Cluster Workload Placement (`AddOnPlacementScore`)

Open Cluster Management (OCM) supports **Score-Based Placement** via `Placement` (`cluster.open-cluster-management.io/v1beta1`) and `AddOnPlacementScore` (`cluster.open-cluster-management.io/v1alpha1`).

Instead of static label matching, OCM evaluates cluster metrics/scores dynamically (e.g. available memory, CPU load, or custom metrics) and routes workload placement decisions to the cluster with the highest score.

### 1. Ensure `ManagedClusterSetBinding` is Active on Both Hubs

Bind the default `ManagedClusterSet` to the target namespace (`default`) on **both** `primaryhub` and `secondaryhub` (ensuring failover readiness if `secondaryhub` takes over):

```bash
# Apply on Primary Hub:
cat <<EOF | kubectl --context kind-primaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSetBinding
metadata:
  name: default
  namespace: default
spec:
  clusterSet: default
EOF

# Apply on Secondary Hub (for Active-Passive Failover Readiness):
cat <<EOF | kubectl --context kind-secondaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSetBinding
metadata:
  name: default
  namespace: default
spec:
  clusterSet: default
EOF
```

---

## Dynamic Score-Based Multi-Cluster Workload Placement (`AddOnPlacementScore`)

Open Cluster Management (OCM) supports **Dynamic Score-Based Placement** via `Placement` (`cluster.open-cluster-management.io/v1beta1`) and `AddOnPlacementScore` (`cluster.open-cluster-management.io/v1alpha1`).

Unlike static capacity metrics, **`AddOnPlacementScore`** enables dynamic live memory load-balancing. When a spoke cluster (`spoke1`) experiences high RAM usage, its published score decreases, causing OCM to automatically schedule new workloads (like Nginx) onto the cluster with the highest available score (`spoke2`).

> 💡 **Architecture Note: How Hubs Collect Spoke Memory Metrics (OCM vs. Cilium)**
>
> **Is Memory Tracking Handled by Cilium?**
> **No.** Cilium handles CNI networking, eBPF routing, WireGuard encryption, and ClusterMesh cross-cluster service discovery. It does **not** collect or report node resource metrics.
>
> **How Memory Metrics are Tracked by OCM:**
> 1. **Static Capacity & Allocatable RAM (OCM Klusterlet Agent)**:
>    - The `klusterlet-registration-agent` running on spoke clusters queries node stats (`kubectl get nodes`) and reports total capacity and allocatable memory (`status.allocatable.memory`) back to the Hub API server over HTTPS (`:6443`).
> 2. **Dynamic Live Memory Scoring (`AddOnPlacementScore`)**:
>    - A lightweight `memory-score-exporter` DaemonSet on each spoke evaluates live free memory (`free | awk '/Mem:/ {print int($7/$2 * 100)}'`) every 30 seconds.
>    - It updates `AddOnPlacementScore` CRD status on the Hub (`kubectl patch addonplacementscore memory-score -n <spoke-name>`).
>    - OCM `Placement` controllers evaluate these live scores to automatically route new workloads to the spoke cluster with the highest available memory.
>
> | Task / Metric | Component Responsible | Protocol |
> | :--- | :--- | :--- |
> | **Pod Overlay & WireGuard Encryption** | **Cilium & ClusterMesh** | eBPF + WireGuard UDP 51871 |
> | **Static Node Capacity & Status** | **OCM Klusterlet Agent** | HTTPS API (`:6443`) |
> | **Dynamic Live Memory Scoring** | **`AddOnPlacementScore` Exporter** | OCM CRD Status Update (`:6443`) |

---

### Step 1: Ensure `ManagedClusterSetBinding` is Active on Both Hubs

Bind the default `ManagedClusterSet` to namespace `default` on **both** `primaryhub` and `secondaryhub` (ensuring Active-Passive failover readiness):

```bash
# Apply on Primary Hub:
cat <<EOF | kubectl --context kind-primaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSetBinding
metadata:
  name: default
  namespace: default
spec:
  clusterSet: default
EOF

# Apply on Secondary Hub:
cat <<EOF | kubectl --context kind-secondaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSetBinding
metadata:
  name: default
  namespace: default
spec:
  clusterSet: default
EOF
```

---

### Step 2: Publish Live Memory Scores for `spoke1` and `spoke2`

Publish memory utilization scores (range `-100` to `100`) in each cluster's respective namespace on the Hub:

```bash
# 1. Publish score for spoke1 (e.g. score 10 = low available RAM due to high load):
cat <<EOF | kubectl --context kind-primaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1alpha1
kind: AddOnPlacementScore
metadata:
  name: memory-score
  namespace: spoke1
EOF
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":10}]}}'

# 2. Publish score for spoke2 (e.g. score 95 = high available RAM):
cat <<EOF | kubectl --context kind-primaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1alpha1
kind: AddOnPlacementScore
metadata:
  name: memory-score
  namespace: spoke2
EOF
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":95}]}}'
```

---

### Step 3: Deploy Dynamic Score-Based `Placement`

Deploy the `Placement` rule on `primaryhub` (and optionally `secondaryhub` for failover parity):

```bash
cat <<EOF | kubectl --context kind-primaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Placement
metadata:
  name: dynamic-memory-placement
  namespace: default
spec:
  numberOfClusters: 1
  sortBy: Score
  prioritizerPolicy:
    mode: Exact
    configurations:
      - scoreCoordinate:
          type: AddOn
          addOn:
            resourceName: memory-score
            scoreName: available-memory
        weight: 10
EOF
```

---

### Step 4: Verify Live OCM Placement Decision

Inspect the decision generated by OCM:

```bash
kubectl --context kind-primaryhub get placementdecisions -n default dynamic-memory-placement-decision-1 -o yaml
```

*Expected Output*: OCM detects that `spoke2` has a higher available memory score (`95` vs `10`) and routes placement to **`spoke2`**:
```yaml
status:
  decisions:
  - clusterName: spoke2
```

---

### Step 5: Test Automatic Re-Balancing (Simulate High Load on `spoke2`)

When `spoke2` runs low on RAM (e.g. score drops to `5`) and `spoke1` frees up RAM (e.g. score rises to `99`):

```bash
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":99}]}}'
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":5}]}}'
```

Re-check placement decisions:
```bash
kubectl --context kind-primaryhub get placementdecisions -n default dynamic-memory-placement-decision-1 -o yaml
```
*Result*: OCM automatically updates the decision to **`spoke1`**!

---

### Step 6: 100% Automated Workload Deployment (`ManifestWorkReplicaSet`)

To deploy Nginx **without hardcoding cluster names**, use `ManifestWorkReplicaSet`.

OCM reads the dynamic decision from `dynamic-memory-placement`, automatically creates the `ManifestWork` in the winning cluster's namespace, and provisions Nginx:

#### 1. Enable `ManifestWorkReplicaSet` Feature Gate on `primaryhub`:
```bash
kubectl --context kind-primaryhub patch clustermanager cluster-manager --type=merge -p '{"spec":{"workConfiguration":{"featureGates":[{"feature":"ManifestWorkReplicaSet","mode":"Enable"}]}}}'
```

#### 2. Deploy `ManifestWorkReplicaSet` bound to `dynamic-memory-placement`:
```bash
cat <<EOF | kubectl --context kind-primaryhub apply -f -
apiVersion: work.open-cluster-management.io/v1alpha1
kind: ManifestWorkReplicaSet
metadata:
  name: nginx-auto-placement
  namespace: default
spec:
  placementRefs:
    - name: dynamic-memory-placement
  cascadeDeletionPolicy: Background
  manifestWorkTemplate:
    workload:
      manifests:
        - apiVersion: apps/v1
          kind: Deployment
          metadata:
            name: nginx-demo
            namespace: default
          spec:
            replicas: 1
            selector:
              matchLabels:
                app: nginx-demo
            template:
              metadata:
                labels:
                  app: nginx-demo
              spec:
                containers:
                  - name: nginx
                    image: nginx:alpine
EOF
```

#### 3. Verify Automatic Placement:
* When `spoke1` score = `10` and `spoke2` score = `95`:
  OCM automatically generates `ManifestWork` in `spoke2` namespace $\rightarrow$ **Nginx running on `spoke2`** (`kubectl --context kind-spoke2 get pods`).
---

### Score Updating Options: Demo Mode vs. Production Mode

> 💡 **Comparison Overview**:
> - **Demo Mode (Manual Patching)**: Instantaneously simulates traffic spikes during live presentations using manual `kubectl patch` commands.
> - **Production Mode (Automated Telemetry Sync)**: Runs a background agent on spoke clusters (`spoke1`, `spoke2`) to continuously measure real hardware RAM usage (`free -m`) and auto-update score metrics on the Hub every 30 seconds.

---

### Option A: Live Demo / Presentation Mode (Manual Simulation Cheat Sheet)

Use these manual commands during live presentations to simulate cluster traffic spikes on demand:

#### Demo Scenario 1: Make `spoke2` Win (`spoke1` Overloaded / High RAM Usage)
```bash
# Set spoke1 score = 10 (High memory usage / Overloaded)
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":10}]}}'

# Set spoke2 score = 95 (Low memory usage / Healthy RAM)
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":95}]}}'
```

#### Demo Scenario 2: Make `spoke1` Win (`spoke2` Overloaded / High RAM Usage)
```bash
# Set spoke1 score = 99 (Low memory usage / Healthy RAM)
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":99}]}}'

# Set spoke2 score = 5 (High memory usage / Overloaded)
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":5}]}}'
```

#### Inspect Live Demo Scores & Decisions:
```bash
# View active cluster scores:
kubectl --context kind-primaryhub get addonplacementscore memory-score -A -o custom-columns="CLUSTER:.metadata.namespace,SCORE:.status.scores[0].value"

# View active winning cluster decision:
kubectl --context kind-primaryhub get placementdecisions -n default dynamic-memory-placement-decision-1 -o jsonpath='{.status.decisions[*].clusterName}'; echo ""
```

---

### Option B: Production Mode (Automated Telemetry Exporter)

In **Production**, you do **NOT** run manual `kubectl patch` commands or change your `Placement`/`ManifestWorkReplicaSet` YAMLs. Instead, deploy this automated telemetry DaemonSet onto `spoke1` and `spoke2`. It measures live free RAM and pushes metrics to the Hub automatically:

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: memory-score-exporter
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: memory-score-exporter
  template:
    metadata:
      labels:
        app: memory-score-exporter
    spec:
      hostNetwork: true
      tolerations:
      - effect: NoSchedule
        operator: Exists
      containers:
      - name: exporter
        image: bitnami/kubectl:latest
        command:
        - /bin/sh
        - -c
        - |
          CLUSTER_NAME=$(cat /etc/pod-info/cluster-name 2>/dev/null || echo "spoke1")
          while true; do
            # 1. Calculate live free memory percentage (0 to 100)
            FREE_MEM_PERCENT=$(free | awk '/Mem:/ {print int($7/$2 * 100)}')

            # 2. Automatically update AddOnPlacementScore status on Hub
            kubectl patch addonplacementscore memory-score -n ${CLUSTER_NAME} \
              --subresource=status --type=merge \
              -p "{\"status\":{\"scores\":[{\"name\":\"available-memory\",\"value\":${FREE_MEM_PERCENT}}]}}" 2>/dev/null || true

            sleep 30
          done
```

---

## Post-Reboot Cluster Resume (After PC Restart)

> 💡 **Persistence**: KinD clusters run inside persistent Docker containers on your local disk. Restarting or turning off your PC **does NOT delete** your clusters, OCM configurations, Cilium Mesh, or secrets.

If Docker containers are stopped after a PC reboot, resume all clusters with **1 command**:

```bash
# Start all KinD cluster nodes:
docker start primaryhub-control-plane secondaryhub-control-plane spoke1-control-plane spoke2-control-plane

# Verify all clusters are online:
kubectl --context kind-primaryhub get nodes
kubectl --context kind-secondaryhub get nodes
kubectl --context kind-spoke1 get nodes
kubectl --context kind-spoke2 get nodes
```

---

## Cleanup (Unjoin & Remove Old State) - Testing Purpose

Run these commands to remove all existing Klusterlet agents, old CSR certificates, and registered cluster resources before starting fresh.

### 1. Unjoin & Clean Up `spoke1`
```bash
# 1. Strip finalizer FIRST to prevent 'kubectl delete' from hanging
kubectl --context kind-spoke1 patch klusterlet klusterlet -p '{"metadata":{"finalizers":null}}' --type=merge

# 2. Delete klusterlet custom resource & namespaces
kubectl --context kind-spoke1 delete klusterlet klusterlet --ignore-not-found
clusteradm unjoin --context kind-spoke1 --cluster-name spoke1 || true
kubectl --context kind-spoke1 delete ns open-cluster-management-agent open-cluster-management --ignore-not-found
```

### 2. Remove `spoke1` & Old CSRs from `primaryhub`
```bash
kubectl --context kind-primaryhub delete managedcluster spoke1 --ignore-not-found
kubectl --context kind-primaryhub get csr -o name | grep spoke1 | xargs -r kubectl --context kind-primaryhub delete
```

### 3. Remove `spoke1` & Old CSRs from `secondaryhub`
```bash
kubectl --context kind-secondaryhub delete managedcluster spoke1 --ignore-not-found
kubectl --context kind-secondaryhub get csr -o name | grep spoke1 | xargs -r kubectl --context kind-secondaryhub delete
```

---

### 4. Complete Reset & Cleanup of `spoke2` (Prepare for Fresh Demo)

Run this block to completely wipe `spoke2` and its registered state from both hubs so you can re-create `spoke2` from scratch tomorrow:

```bash
# 1. Strip finalizers on spoke2 to prevent deletion hanging
kubectl --context kind-spoke2 patch klusterlet klusterlet-secondary -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
kubectl --context kind-spoke2 patch klusterlet klusterlet -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true

# 2. Delete ManagedCluster and CSRs from primaryhub
kubectl --context kind-primaryhub delete managedcluster spoke2 --ignore-not-found
kubectl --context kind-primaryhub get csr -o name | grep spoke2 | xargs -r kubectl --context kind-primaryhub delete
kubectl --context kind-primaryhub delete ns spoke2 --ignore-not-found
kubectl --context kind-primaryhub delete placement dynamic-memory-placement -n default --ignore-not-found
kubectl --context kind-primaryhub delete manifestworkreplicaset nginx-auto-placement -n default --ignore-not-found

# 3. Delete ManagedCluster and CSRs from secondaryhub
kubectl --context kind-secondaryhub delete managedcluster spoke2 --ignore-not-found
kubectl --context kind-secondaryhub get csr -o name | grep spoke2 | xargs -r kubectl --context kind-secondaryhub delete

# 4. Delete the KinD cluster spoke2
kind delete cluster --name spoke2
```

---

### 5. Complete Reset & Cleanup of `spoke3`

Run this block to completely wipe `spoke3` and its registered state from both hubs:

```bash
# 1. Strip finalizers on spoke3 to prevent deletion hanging
kubectl --context kind-spoke3 patch klusterlet klusterlet-secondary -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
kubectl --context kind-spoke3 patch klusterlet klusterlet -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true

# 2. Delete ManagedCluster, CSRs, and namespace from primaryhub
kubectl --context kind-primaryhub patch managedcluster spoke3 -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
kubectl --context kind-primaryhub delete managedcluster spoke3 --ignore-not-found
kubectl --context kind-primaryhub get csr -o name | grep spoke3 | xargs -r kubectl --context kind-primaryhub delete 2>/dev/null || true
kubectl --context kind-primaryhub delete ns spoke3 --ignore-not-found 2>/dev/null || true

# 3. Delete ManagedCluster, CSRs, and namespace from secondaryhub
kubectl --context kind-secondaryhub patch managedcluster spoke3 -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
kubectl --context kind-secondaryhub delete managedcluster spoke3 --ignore-not-found
kubectl --context kind-secondaryhub get csr -o name | grep spoke3 | xargs -r kubectl --context kind-secondaryhub delete 2>/dev/null || true
kubectl --context kind-secondaryhub delete ns spoke3 --ignore-not-found 2>/dev/null || true

# 4. Delete the KinD cluster spoke3
kind delete cluster --name spoke3
```

---

## Troubleshooting & Recovery After System / Docker Restart

### Symptom: `AVAILABLE: Unknown` in OCM & Cilium ClusterMesh Disconnected (`0/1 connected`)

When the host machine or Docker daemon restarts, KinD control-plane containers boot up and Docker re-assigns IP addresses on the bridge network (`172.19.0.0/16`). If containers start up in a different order, their Docker IP addresses swap:

* **Expected Initial IPs**:
  * `primaryhub-control-plane` ➔ `172.19.0.2`
  * `secondaryhub-control-plane` ➔ `172.19.0.3`
  * `spoke1-control-plane` ➔ `172.19.0.4`
* **If Docker starts `secondaryhub` first after reboot**:
  * `secondaryhub-control-plane` ➔ `172.19.0.2` (Swapped)
  * `primaryhub-control-plane` ➔ `172.19.0.3` (Swapped)

#### Why This Causes Failures:
1. **OCM (`kubectl get managedclusters` showing `Unknown`)**: Spoke1's Klusterlet registration agent for `primaryhub` connects to `https://172.19.0.2:6443`. When `secondaryhub` holds `172.19.0.2`, TLS verification fails with `crypto/rsa: verification error`. Heartbeat lease updates stop, and OCM sets `AVAILABLE: Unknown`.
2. **Cilium ClusterMesh (`0/1 connected`)**: ClusterMesh connects to `172.19.0.3:32379`. With swapped IPs, `primaryhub` attempts to mesh with itself, leading to TLS authority mismatch errors (`Cilium CA verification error`).

---

### Step-by-Step Recovery Procedure

#### Step 1: Check Current Node IP Assignments
```bash
kubectl --context kind-primaryhub get nodes -o wide
kubectl --context kind-secondaryhub get nodes -o wide
kubectl --context kind-spoke1 get nodes -o wide
```
If `primaryhub` is **not** `172.19.0.2` or `secondaryhub` is **not** `172.19.0.3`, proceed to Step 2.

#### Step 2: Restart Containers in Original Order
Stop all KinD containers and start them in the exact original sequence:

```bash
# 1. Stop all KinD node containers
docker stop spoke1-control-plane secondaryhub-control-plane primaryhub-control-plane

# 2. Start them in sequential order (primaryhub FIRST)
docker start primaryhub-control-plane
docker start secondaryhub-control-plane
docker start spoke1-control-plane
```

#### Step 3: Verify IP Recovery & Connectivity
```bash
# Verify IPs match expectations
kubectl --context kind-primaryhub get nodes -o wide      # Must be 172.19.0.2
kubectl --context kind-secondaryhub get nodes -o wide    # Must be 172.19.0.3
kubectl --context kind-spoke1 get nodes -o wide         # Must be 172.19.0.4

# Verify Cilium ClusterMesh connection
cilium clustermesh status --context kind-primaryhub
cilium clustermesh status --context kind-secondaryhub

# Verify OCM ManagedCluster status (Wait ~1-2 minutes for leader election & lease update)
kubectl --context kind-primaryhub get managedclusters
kubectl --context kind-secondaryhub get managedclusters
```

Both `AVAILABLE` columns will return `True` once Klusterlet agents re-acquire leader locks.

---

### Symptom: `JOINED` or `AVAILABLE` Column Blank After `clusteradm accept`

When running `kubectl get managedclusters`, `HUB ACCEPTED` shows `true`, but `JOINED` or `AVAILABLE` columns remain blank or empty:

```text
NAME     HUB ACCEPTED   MANAGED CLUSTER URLS                JOINED   AVAILABLE   AGE
spoke3   true           https://spoke3-control-plane:6443                        5m
```

#### Why This Happens:
1. **New Pending CSR Needs Approval**: When the Klusterlet registration agent pod restarts, re-configures, or updates its bootstrap secret, it generates a **new Certificate Signing Request (CSR)** on the Hub API server. Older approved CSRs are superseded. If the Hub admin has not accepted the newest CSR, the Hub cannot issue the client certificate.
2. **Leader Lock Acquisition Delay**: After pod startup, the Klusterlet registration agent takes 15–30 seconds to acquire the Kubernetes leader lock (`registration-agent-lock`).
3. **Lease Heartbeat Pending**: The `AVAILABLE` column turns `True` only after the agent sends its first `managed-cluster-lease` heartbeat to the Hub API server.

---

### Step-by-Step Resolution Guide

#### Step 1: Check for Pending CSRs on the Hub
```bash
kubectl --context kind-secondaryhub get csr | grep <spoke-name>
# (Or kind-primaryhub)
```
If you see any CSR with status **`Pending`** (e.g., `spoke3-xwg7j`), re-run `clusteradm accept`:

```bash
clusteradm --context kind-secondaryhub accept --clusters <spoke-name> --skip-approve-check
```

#### Step 2: Inspect Agent Logs on Spoke
```bash
# View Primary Agent Logs:
kubectl --context kind-spoke1 -n open-cluster-management-agent logs deployment/klusterlet-registration-agent --tail=30

# View Secondary Agent Logs (Dual-Hub Setup):
kubectl --context kind-spoke1 -n open-cluster-management-agent-secondary logs deployment/klusterlet-secondary-registration-agent --tail=30
```
Look for:
* `INFO: Client config for hub is ready.`
* `INFO: Start to update lease "managed-cluster-lease"`

#### Step 3: Verify Status Recovery
```bash
kubectl --context kind-primaryhub get managedclusters
kubectl --context kind-secondaryhub get managedclusters
```
Both `JOINED` and `AVAILABLE` will return **`True`**.
