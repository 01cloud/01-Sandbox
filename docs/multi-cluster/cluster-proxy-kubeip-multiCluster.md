# Open Cluster Management (OCM) - MultipleHubs Failover Demo

This repository demonstrates the **MultipleHubs** failover and failback mechanism in [Open Cluster Management (OCM)](https://open-cluster-management.io/). It uses three local [KinD](https://kind.sigs.k8s.io/) Kubernetes clusters (`primaryhub`, `secondaryhub`, and `spoke1`) to validate automatic agent failover and failback without manual re-registration or token-overlap issues.

---

## 🏗️ Architecture & Concepts

```
                      +-------------------+
                      |   primaryhub      | (Primary Control Plane - Priority 0)
                      +-------------------+
                                ^
                                | (Active Connection)
                                |
 +------------------+           |           +-------------------+
 |     spoke1       |-----------+----------->|   secondaryhub    | (Backup Control Plane - Priority 1)
 | (Managed Cluster)|  (Fails over if 180s   +-------------------+
 +------------------+     timeout occurs)
```

### Key Principles of MultipleHubs:
1. **Single Klusterlet Agent**: `spoke1` runs a single Klusterlet agent with `MultipleHubs` enabled, avoiding dual-registration overhead and token overwrites.
2. **One Active Connection**: `spoke1` connects to **only ONE hub at a time** (the highest-priority reachable hub).
3. **Local Secrets**: Connection credentials for both hubs are staged as local Kubernetes Secrets on `spoke1` (`primaryhub-kubeconfig` and `secondaryhub-kubeconfig`).
4. **Zero-Downtime Re-Registration**: If `primaryhub` becomes unreachable for `180` seconds (`hubConnectionTimeoutSeconds`), the Klusterlet automatically switches to `secondaryhub-kubeconfig` and registers on `secondaryhub`.
5. **Automated Failover & Failback**: Enabled via `ManagedClusterAutoApproval` feature gate on hubs so no human intervention (`clusteradm accept`) is required when switching hubs.

---

### 🧩 Native OCM Core Custom Resources (`ClusterManager` & `Klusterlet`)

All modifications in this repository use 100% native Open Cluster Management (OCM) Custom Resource Definitions (CRDs) and operators:

```
                        +---------------------------------------+
                        |           OCM HUB CLUSTER             |
                        |                                       |
                        |   [ ClusterManager CRD ]             |
                        |   - Manages Hub Registration Operator |
                        |   - FeatureGate:                      |
                        |     ManagedClusterAutoApproval        |
                        +---------------------------------------+
                                            ^
                                            |  (OCM Registration & Lease)
                                            v
                        +---------------------------------------+
                        |        OCM SPOKE CLUSTER (spoke1)     |
                        |                                       |
                        |   [ Klusterlet CRD ]                  |
                        |   - Manages Spoke Agent Controllers   |
                        |   - FeatureGate:                      |
                        |     MultipleHubs                      |
                        |   - LocalSecrets (hub kubeconfigs)    |
                        +---------------------------------------+
```

1. **`ClusterManager` CRD (Hub Control Plane)**:
   * Installed on OCM Hub clusters (`primaryhub` & `secondaryhub`) to manage hub-side registration operators and placement logic.
   * Patching `registrationConfiguration.featureGates: ManagedClusterAutoApproval` activates OCM's native auto-approval feature so incoming spoke CSRs are automatically accepted without requiring manual admin intervention (`clusteradm accept`).

2. **`Klusterlet` CRD (Spoke Agent Controller)**:
   * Installed on managed workload clusters (`spoke1`) to run agent controllers and maintain heartbeats (`managed-cluster-lease`).
   * Patching `registrationConfiguration.featureGates: MultipleHubs` and setting `bootstrapKubeConfigs.type: LocalSecrets` enables OCM's native multi-hub failover logic, allowing the single agent to dynamically rotate hub credentials upon connection timeout (180s).

---

## 📊 Status Matrix Across Failover Lifecycle

| Lifecycle Phase | `primaryhub` View | `secondaryhub` View | Active Hub on `spoke1` | Technical Explanation |
|---|---|---|---|---|
| **Phase 0–3** *(Normal)* | `AVAILABLE = True` | `No resources found` | `primaryhub` (Priority 0) | `spoke1` connects exclusively to Priority 0 hub (`primaryhub`). |
| **Phase 4** *(Primary Outage)* | `AVAILABLE = Unknown` | **`AVAILABLE = True`** | `secondaryhub` (Priority 1) | Connection timeout (180s) triggers rotation to `secondaryhub`. Hub auto-approves registration. |
| **Phase 5** *(Primary Restored)* | **`AVAILABLE = True`** | `AVAILABLE = Unknown` | `primaryhub` (Priority 0) | Klusterlet detects Priority 0 hub is responsive again and automatically fails back. |

---

## 🛠️ Step-by-Step Command Guide & Technical Rationale

This guide explains the exact commands executed in `test.sh` and the technical reasons behind each operation.

### Phase 0: Create KinD Kubernetes Clusters

```bash
kind create cluster --name primaryhub
kind create cluster --name secondaryhub
kind create cluster --name spoke1
```
* **Technical Reason**: Creates three isolated Kubernetes clusters running in Docker containers. `primaryhub` and `secondaryhub` represent independent OCM management planes, while `spoke1` acts as the managed workload cluster.

---

### Phase 1: Initialize Hub Control Planes & Enable Auto-Approval

```bash
# Step 1: Initialize OCM Hub control plane on both hubs independently
clusteradm init --wait --context kind-primaryhub
clusteradm init --wait --context kind-secondaryhub

# Step 2: Enable ManagedClusterAutoApproval feature gate on both hubs
kubectl --context kind-primaryhub patch clustermanager cluster-manager --type=merge -p \
  '{"spec":{"registrationConfiguration":{"featureGates":[{"feature":"ManagedClusterAutoApproval","mode":"Enable"}],"autoApproveUsers":["kubernetes-admin"]}}}'

kubectl --context kind-secondaryhub patch clustermanager cluster-manager --type=merge -p \
  '{"spec":{"registrationConfiguration":{"featureGates":[{"feature":"ManagedClusterAutoApproval","mode":"Enable"}],"autoApproveUsers":["kubernetes-admin"]}}}'
```
* **Technical Reason**:
  * `clusteradm init`: Installs the OCM control plane (`cluster-manager`, registration-operator) on each hub independently. Hubs operate without requiring active database replication or cluster-to-cluster synchronization.
  * `ManagedClusterAutoApproval`: In standard OCM, when a managed cluster registers with a hub, an admin must manually run `clusteradm accept` to approve its Certificate Signing Request (CSR). For automated failover and failback, manual intervention is impossible. Enabling `ManagedClusterAutoApproval` and specifying `autoApproveUsers: ["kubernetes-admin"]` configures both hubs to automatically validate CSRs and accept `spoke1` when it connects during failover or failback.

---

### Phase 2: Extract Credentials & Perform Single Join to `primaryhub`

```bash
# Step 1: Export internal cluster kubeconfigs for container-to-container routing
kind get kubeconfig --name primaryhub --internal > primaryhub.kubeconfig
kind get kubeconfig --name secondaryhub --internal > secondaryhub.kubeconfig

# Step 2: Extract bootstrap token and API server URL from primaryhub
PRIMARY_HUB_TOKEN=$(clusteradm get token --context kind-primaryhub -o json | jq -r '."hub-token"')
PRIMARY_HUB_APISERVER=$(clusteradm get token --context kind-primaryhub -o json | jq -r '."hub-apiserver"')

# Step 3: Join spoke1 to primaryhub ONLY ONCE
clusteradm join \
  --hub-token "${PRIMARY_HUB_TOKEN}" \
  --hub-apiserver "${PRIMARY_HUB_APISERVER}" \
  --cluster-name spoke1 \
  --context kind-spoke1 \
  --force-internal-endpoint-lookup \
  --wait

# Step 4: Accept spoke1 on primaryhub for initial registration
clusteradm accept --context kind-primaryhub --clusters spoke1 --wait

# Step 5: Stage both hub kubeconfigs as local secrets on spoke1
kubectl --context kind-spoke1 create secret generic primaryhub-kubeconfig \
  -n open-cluster-management-agent --from-file=kubeconfig=primaryhub.kubeconfig

kubectl --context kind-spoke1 create secret generic secondaryhub-kubeconfig \
  -n open-cluster-management-agent --from-file=kubeconfig=secondaryhub.kubeconfig
```
* **Technical Reason**:
  * `kind get kubeconfig --internal`: Generates kubeconfig files referencing internal Docker network IP addresses (e.g., `172.18.0.x`) rather than `127.0.0.1`. This allows `spoke1` pods to communicate directly with hub API servers across Docker container networks.
  * Single `clusteradm join`: Bootstraps the `klusterlet` operator and CRDs on `spoke1` and creates the `open-cluster-management-agent` namespace. **`spoke1` is joined only ONCE**. Attempting a second `clusteradm join` against `secondaryhub` would create duplicate/conflicting Klusterlet deployments or overwrite bootstrap secrets (the "token-overlap" issue).
  * `--force-internal-endpoint-lookup`: Ensures `clusteradm` resolves internal container IP endpoints during initial join.
  * Staging Secret Resources: Stores both hub kubeconfig files as Kubernetes Secrets in `spoke1`'s `open-cluster-management-agent` namespace (`primaryhub-kubeconfig` and `secondaryhub-kubeconfig`). These secrets are referenced by the Klusterlet operator when `MultipleHubs` is activated.

---

### Phase 3: Enable `MultipleHubs` Feature on Klusterlet

```bash
kubectl --context kind-spoke1 patch klusterlet klusterlet --type=merge -p '{
  "spec": {
    "registrationConfiguration": {
      "featureGates": [
        { "feature": "MultipleHubs", "mode": "Enable" }
      ],
      "bootstrapKubeConfigs": {
        "type": "LocalSecrets",
        "localSecretsConfig": {
          "hubConnectionTimeoutSeconds": 180,
          "kubeConfigSecrets": [
            { "name": "primaryhub-kubeconfig" },
            { "name": "secondaryhub-kubeconfig" }
          ]
        }
      }
    }
  }
}'
```
* **Technical Reason**:
  * `featureGates: MultipleHubs`: Enables multi-hub failover logic within the Klusterlet agent controller.
  * `type: LocalSecrets`: Instructs Klusterlet to dynamically select bootstrap credentials from local Kubernetes secrets instead of a static single-hub bootstrap secret.
  * `kubeConfigSecrets`: Defines an ordered array of hub secrets. **Priority is determined implicitly by array order (index position)**:
    * Index `0` (1st item: `primaryhub-kubeconfig`) = **Priority 1 / Primary Active Hub**
    * Index `1` (2nd item: `secondaryhub-kubeconfig`) = **Priority 2 / Secondary Standby Hub**
    *(There is no separate `priority` integer field; the Klusterlet agent processes secrets sequentially from top to bottom).*
  * `hubConnectionTimeoutSeconds: 180`: Specifies the connection loss timeout. The OCM Klusterlet OpenAPI validation schema strictly enforces `hubConnectionTimeoutSeconds >= 180`. Any value below 180 is rejected by the API server. If connection/heartbeats to the active hub fail for 180s, Klusterlet rotates to the next secret in the array.


---

### Phase 4: Simulate Primary Hub Outage & Verify Automated Failover

```bash
# Step 1: Reject client requests on primaryhub (simulates outage)
kubectl --context kind-primaryhub patch managedcluster spoke1 --type=merge \
  -p '{"spec":{"hubAcceptsClient":false}}'

# Step 2: Verify spoke1 switches to secondaryhub after 180s timeout
kubectl --context kind-secondaryhub get managedcluster
```
* **Technical Reason**:
  * `hubAcceptsClient: false`: Rejects API client authorization for `spoke1` on `primaryhub`, simulating a hub control plane failure or network partition without needing to destroy the KinD cluster container mid-test.
  * Failover Mechanics: When API requests to `primaryhub` fail for 180 seconds (`hubConnectionTimeoutSeconds`), Klusterlet automatically rotates to `secondaryhub-kubeconfig` (Priority 1), connects to `secondaryhub`, and registers `spoke1`. `secondaryhub` automatically approves the CSR via `ManagedClusterAutoApproval` (configured in Phase 1), making `spoke1` `Available = True` on `secondaryhub`.

---

### Phase 5: Restore Primary Hub & Verify Automated Failback

```bash
# Step 1: Re-enable client acceptance on primaryhub
kubectl --context kind-primaryhub patch managedcluster spoke1 --type=merge \
  -p '{"spec":{"hubAcceptsClient":true}}'

# Step 2: Verify spoke1 automatically fails back to primaryhub (Priority 0)
kubectl --context kind-primaryhub get managedcluster
```
* **Technical Reason**:
  * `hubAcceptsClient: true`: Restores client authorization for `spoke1` on `primaryhub`.
  * Failback Mechanics: Once `primaryhub` (Priority 0) is restored and accepting connections, the Klusterlet agent detects that a higher-priority hub in `kubeConfigSecrets` is available and automatically switches back from `secondaryhub` to `primaryhub`.

---

## 🌐 Multi-Region & Multi-Cloud Production Architecture

In real-world production environments, `primaryhub`, `secondaryhub`, and `spoke1` are typically deployed across **different geographic regions, cloud providers (AWS, GCP, Azure), and distinct CIDR ranges**.

While OCM handles **control-plane agent failover**, application state synchronization (such as PostgreSQL database replication) must be handled at the database and networking layers.

---

### 🗄️ Cross-Region PostgreSQL Database Replication

To ensure zero or minimal data loss during hub failover:

```
                                  CILIUM CLUSTERMESH
               (Kernel-Level eBPF + Transparent WireGuard Encryption)

   REGION 1 (AWS us-east-1)           REGION 2 (GCP europe-west1)        REGION 3 (Azure centralus)
   [primaryhub Cluster]               [secondaryhub Cluster]            [spoke1 Cluster]
   Pod CIDR: 10.244.0.0/16            Pod CIDR: 10.245.0.0/16           Pod CIDR: 10.246.0.0/16
   +-----------------------+          +-----------------------+         +-----------------------+
   |  PostgreSQL Primary   |          |  PostgreSQL Standby   |         |  Managed Workloads    |
   |  (Port 5432)          |          |  (Streams WAL)        |         |                       |
   +-----------+-----------+          +-----------+-----------+         +-----------+-----------+
               |                                  ^                                 |
               |       Kernel WireGuard Tunnel    |                                 |
               +==================================+=================================+
                        (Direct Pod-to-Pod IP Communication over WAN)
```

1. **Replication Mechanism**: Use PostgreSQL Native Streaming Replication (or CloudNativePG / Patroni operators).
2. **Replication Modes**:
   * **Asynchronous Streaming (Near-Zero RPO)**: Continuous millisecond streaming of Write-Ahead Logs (WAL) across regions.
   * **Synchronous Streaming (Zero Data Loss - RPO = 0)**: Transactions on `primaryhub` complete only after acknowledgement from `secondaryhub`.
3. **Database mTLS**: PostgreSQL must be configured with `sslmode=verify-full` and client certificates (`sslcert`, `sslkey`, `sslrootcert`) so database traffic is encrypted and authenticated.

---

### 🐝 Secure Cross-Cluster Networking via Cilium ClusterMesh

[Cilium ClusterMesh](https://cilium.io/) provides eBPF-driven cross-cluster networking across different regions and CIDR ranges with kernel-level WireGuard encryption.

#### Key Advantages for Database & OCM Traffic:
* **Kernel WireGuard Encryption**: All cross-cluster traffic (PostgreSQL WAL logs and OCM heartbeats) is encrypted in the Linux kernel via eBPF with WireGuard (`encryption.type=wireguard`), giving up to 60% higher throughput than userspace proxies/VPNs.
* **Cross-CIDR Routing**: Direct Pod-to-Pod communication across different cloud VPCs and regions without requiring external NAT gateways.
* **Global Services**: Exposes PostgreSQL on `primaryhub` as a global Kubernetes service (`postgres-primary.database.svc.cluster.local`) accessible seamlessly from `secondaryhub`.

#### Cilium ClusterMesh Setup & Verification Suite:

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

##### Global Service & Database Cross-Cluster Routing Example:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres-primary
  namespace: database
  annotations:
    io.cilium/global-service: "true"
    io.cilium/service-affinity: "remote"
spec:
  type: ClusterIP
  ports:
  - port: 5432
    targetPort: 5432
  selector:
    app: postgres-primary
```

```ini
# Connect PostgreSQL Standby on secondaryhub:
primary_conninfo = 'host=postgres-primary.database.svc.cluster.local port=5432 user=standby_user password=SecretPassword sslmode=verify-full sslrootcert=/etc/postgres-certs/ca.crt'
```


---

## 🧠 Score-Based Multi-Cluster Workload Placement (`AddOnPlacementScore`)

Open Cluster Management (OCM) supports **Dynamic Score-Based Placement** via `Placement` (`cluster.open-cluster-management.io/v1beta1`) and `AddOnPlacementScore` (`cluster.open-cluster-management.io/v1alpha1`).

Instead of static label matching, OCM evaluates cluster metrics/scores dynamically (e.g. available memory, CPU load, or custom metrics) and **automatically routes workload placement decisions to the cluster with the highest score**. When `spoke1` runs high on RAM, its published score decreases and OCM routes new workloads (like Nginx) to `spoke2` instead — and back again when scores flip.

> 💡 **Architecture Note: How Hubs Collect Spoke Memory Metrics (OCM vs. Cilium)**
>
> **Is Memory Tracking Handled by Cilium?**
> **No.** Cilium handles CNI networking, eBPF routing, WireGuard encryption, and ClusterMesh cross-cluster service discovery. It does **not** collect or report node resource metrics.
>
> **How Memory Metrics are Tracked by OCM:**
> 1. **Static Capacity & Allocatable RAM (OCM Klusterlet Agent)**:
>    - The `klusterlet-registration-agent` running on spoke clusters queries node stats and reports total capacity and allocatable memory (`status.allocatable.memory`) back to the Hub API server over HTTPS (`:6443`).
> 2. **Dynamic Live Memory Scoring (`AddOnPlacementScore`)**:
>    - A lightweight `memory-score-exporter` DaemonSet on each spoke evaluates live free memory every 30 seconds.
>    - It updates the `AddOnPlacementScore` CRD status on the Hub.
>    - OCM `Placement` controllers evaluate these live scores to automatically route new workloads to the spoke with the highest available memory.
>
> | Task / Metric | Component Responsible | Protocol |
> | :--- | :--- | :--- |
> | **Pod Overlay & WireGuard Encryption** | **Cilium & ClusterMesh** | eBPF + WireGuard UDP 51871 |
> | **Static Node Capacity & Status** | **OCM Klusterlet Agent** | HTTPS API (`:6443`) |
> | **Dynamic Live Memory Scoring** | **`AddOnPlacementScore` Exporter** | OCM CRD Status Update (`:6443`) |

---

### Step 1: Ensure `ManagedClusterSetBinding` is Active on Both Hubs

Bind the default `ManagedClusterSet` to the `default` namespace on **both** `primaryhub` and `secondaryhub` (required for `Placement` to resolve cluster decisions in that namespace):

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

### Step 2: Publish Live Memory Scores for `spoke1` and `spoke2`

Publish memory utilization scores (range `-100` to `100`) in each spoke's namespace on the Hub. A **higher score = more free RAM = preferred for placement**.

> [!NOTE]
> **Architecture Clarification: Placement Rules vs. Spoke Score Reporting**
>
> 1. **Why `Placement` Rules Live on the Hub (Not Spokes)**:
>    The **Hub cluster** is the central orchestrator holding a global view of all managed clusters (`spoke1`, `spoke2`, etc.). Individual spoke clusters do not know about other spokes' resource utilization. Therefore, `Placement` rules and decisions **must** be configured on the Hub.
>
> 2. **How Metrics Flow from Spoke $\rightarrow$ Hub (Production vs. Lab Demo)**:
>    * **In Production**: An AddOn Agent (e.g., Prometheus / custom metrics collector) runs inside each **Spoke cluster**, measures local node memory/CPU usage, and continuously pushes updates to the `AddOnPlacementScore` CR in the Hub's `spoke1`/`spoke2` namespace via the Hub API server.
>    * **In This Lab Demo**: To keep the setup lightweight without deploying metric collectors on KinD nodes, we **simulate the Spoke agent** by using `kubectl patch` directly on the Hub to update `AddOnPlacementScore` status.

```bash
# 1. Create & publish score for spoke1 (score 10 = low available RAM / high load):
cat <<EOF | kubectl --context kind-primaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1alpha1
kind: AddOnPlacementScore
metadata:
  name: memory-score
  namespace: spoke1
EOF
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 \
  --subresource=status --type=merge \
  -p '{"status":{"scores":[{"name":"available-memory","value":10}]}}'

# 2. Create & publish score for spoke2 (score 95 = high available RAM / healthy):
cat <<EOF | kubectl --context kind-primaryhub apply -f -
apiVersion: cluster.open-cluster-management.io/v1alpha1
kind: AddOnPlacementScore
metadata:
  name: memory-score
  namespace: spoke2
EOF
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 \
  --subresource=status --type=merge \
  -p '{"status":{"scores":[{"name":"available-memory","value":95}]}}'
```

---

### Step 3: Deploy Dynamic Score-Based `Placement` on Both Hubs

Deploy the `Placement` rule on **both `primaryhub` and `secondaryhub`** so that if `primaryhub` fails over, `secondaryhub` immediately maintains the identical placement logic:

```bash
# Apply on Primary Hub:
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

# Apply on Secondary Hub (for Failover Parity):
cat <<EOF | kubectl --context kind-secondaryhub apply -f -
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

```bash
kubectl --context kind-primaryhub get placementdecisions -n default dynamic-memory-placement-decision-1 -o yaml
```

*Expected Output*: OCM detects `spoke2` has a higher available memory score (`95` vs `10`) and routes placement to **`spoke2`**:
```yaml
status:
  decisions:
  - clusterName: spoke2
```

---

### Step 5: Test Automatic Re-Balancing (Simulate High Load on `spoke2`)

When `spoke2` runs low on RAM (score drops to `5`) and `spoke1` frees up RAM (score rises to `99`), OCM **automatically re-routes** the next placement decision:

```bash
# Simulate spoke2 running out of memory:
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 \
  --subresource=status --type=merge \
  -p '{"status":{"scores":[{"name":"available-memory","value":5}]}}'

# Simulate spoke1 now having plenty of memory:
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 \
  --subresource=status --type=merge \
  -p '{"status":{"scores":[{"name":"available-memory","value":99}]}}'

# Re-check placement decision:
kubectl --context kind-primaryhub get placementdecisions -n default dynamic-memory-placement-decision-1 -o yaml
```
*Result*: OCM automatically updates the decision to **`spoke1`**.

---

### Step 6: Fully Automated Workload Deployment (`ManifestWorkReplicaSet`)

To deploy Nginx **without hardcoding cluster names**, use `ManifestWorkReplicaSet`. OCM reads the dynamic decision from `dynamic-memory-placement`, automatically creates the `ManifestWork` in the winning cluster's namespace, and provisions Nginx there.

#### 1. Enable `ManifestWorkReplicaSet` Feature Gate on Both Hubs:
```bash
kubectl --context kind-primaryhub patch clustermanager cluster-manager --type=merge -p \
  '{"spec":{"workConfiguration":{"featureGates":[{"feature":"ManifestWorkReplicaSet","mode":"Enable"}]}}}'

kubectl --context kind-secondaryhub patch clustermanager cluster-manager --type=merge -p \
  '{"spec":{"workConfiguration":{"featureGates":[{"feature":"ManifestWorkReplicaSet","mode":"Enable"}]}}}'
```

#### 2. Deploy `ManifestWorkReplicaSet` bound to `dynamic-memory-placement` on Both Hubs:
```bash
# Apply on Primary Hub:
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
                    image: registry.k8s.io/pause:3.10
                    imagePullPolicy: IfNotPresent
EOF

# Apply on Secondary Hub (for Failover Parity):
cat <<EOF | kubectl --context kind-secondaryhub apply -f -
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
                    image: registry.k8s.io/pause:3.10
                    imagePullPolicy: IfNotPresent
EOF
```

#### Verify automatic placement:
* **`spoke1` score = `10`, `spoke2` score = `95`** → OCM generates `ManifestWork` in `spoke2` namespace → Nginx runs on `spoke2`:
  ```bash
  kubectl --context kind-spoke2 get pods
  ```
* **`spoke1` score = `99`, `spoke2` score = `5`** → OCM moves `ManifestWork` to `spoke1` namespace → Nginx runs on `spoke1`:
  ```bash
  kubectl --context kind-spoke1 get pods
  ```

> [!IMPORTANT]
> **Self-Healing & Deletion Mechanics for `ManifestWorkReplicaSet`**
>
> * **Self-Healing Reconciliation**: If you manually delete a generated child `ManifestWork` (e.g., `kubectl delete manifestwork nginx-auto-placement-xxxx -n spoke2`), the `ManifestWorkReplicaSet` controller detects the missing resource and **immediately recreates it** to maintain desired state.
> * **How to Permanently Delete Workloads**: To permanently remove the workload and its pods from managed clusters, delete the **parent `ManifestWorkReplicaSet`**:
>   ```bash
>   kubectl --context kind-primaryhub delete manifestworkreplicaset nginx-auto-placement -n default
>   ```

---

### Score Updating Options: Demo Mode vs. Production Mode

#### Option A: Executive Demonstration Script

Use this 3-scene simulation script during live executive presentations to demonstrate intelligent dynamic placement and zero-touch workload migration:

##### Scene 1: Baseline Placement (`spoke2` has 95% free RAM)
*Story*: `spoke2` is healthy with **95% available memory**, while `spoke1` has only **10% available memory**. OCM schedules the workload on `spoke2`.

```bash
# 1. Apply Baseline Scores:
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 \
  --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":10}]}}'

kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 \
  --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":95}]}}'

# 2. Check Decision (Expect spoke2):
kubectl --context kind-primaryhub get placementdecisions -n default dynamic-memory-placement-decision-1 -o jsonpath='{.status.decisions[*].clusterName}'; echo ""

# 3. Check Running Workload Pods (Expect pod on spoke2):
echo "--- spoke1 Pods ---" && kubectl --context kind-spoke1 get pods
echo "--- spoke2 Pods ---" && kubectl --context kind-spoke2 get pods
```

##### Scene 2: High Load Simulation on `spoke2` (Automated Evacuation to `spoke1`)
*Story*: `spoke2` experiences a memory surge (free RAM drops to **5%**). `spoke1` frees up RAM (**99%** free RAM). OCM automatically evacuates `spoke2` and provisions `spoke1`.

```bash
# 1. Simulate Load Surge on spoke2:
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 \
  --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":5}]}}'

kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 \
  --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":99}]}}'

# 2. Check Decision (Expect spoke1):
kubectl --context kind-primaryhub get placementdecisions -n default dynamic-memory-placement-decision-1 -o jsonpath='{.status.decisions[*].clusterName}'; echo ""

# 3. Verify Zero-Touch Workload Migration (Expect pod on spoke1, 0 pods on spoke2):
echo "--- spoke1 Pods ---" && kubectl --context kind-spoke1 get pods
echo "--- spoke2 Pods ---" && kubectl --context kind-spoke2 get pods
```

##### Scene 3: Automatic Recovery / Failback to `spoke2`
*Story*: Memory utilization on `spoke2` normalizes back to **95% free RAM**. OCM automatically fails back placement to `spoke2`.

```bash
# 1. Restore spoke2 Health:
kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke2 \
  --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":95}]}}'

kubectl --context kind-primaryhub patch addonplacementscore memory-score -n spoke1 \
  --subresource=status --type=merge -p '{"status":{"scores":[{"name":"available-memory","value":5}]}}'

# 2. Verify Failback (Expect pod on spoke2, 0 pods on spoke1):
echo "--- spoke1 Pods ---" && kubectl --context kind-spoke1 get pods
echo "--- spoke2 Pods ---" && kubectl --context kind-spoke2 get pods
```

##### Live Inspection One-Liner:
```bash
# View active cluster scores:
kubectl --context kind-primaryhub get addonplacementscore memory-score -A \
  -o custom-columns="CLUSTER:.metadata.namespace,SCORE:.status.scores[0].value"

# View active winning cluster decision:
kubectl --context kind-primaryhub get placementdecisions -n default \
  dynamic-memory-placement-decision-1 \
  -o jsonpath='{.status.decisions[*].clusterName}'; echo ""
```

---

#### Option B: Production Mode (Automated Telemetry Exporter DaemonSet)

In production, deploy this `DaemonSet` onto **both `spoke1` and `spoke2`**. It measures live free RAM every 30 seconds and pushes scores to the Hub automatically — no manual `kubectl patch` needed:

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
            # Calculate live free memory percentage (0 to 100)
            FREE_MEM_PERCENT=$(free | awk '/Mem:/ {print int($7/$2 * 100)}')

            # Auto-update AddOnPlacementScore status on Hub
            kubectl patch addonplacementscore memory-score -n ${CLUSTER_NAME} \
              --subresource=status --type=merge \
              -p "{\"status\":{\"scores\":[{\"name\":\"available-memory\",\"value\":${FREE_MEM_PERCENT}}]}}" 2>/dev/null || true

            sleep 30
          done
```

> 💡 **Demo vs. Production Summary**:
> | Mode | Score Source | Update Frequency | Use Case |
> | :--- | :--- | :--- | :--- |
> | **Demo** | Manual `kubectl patch` | Instant | Live presentations, testing |
> | **Production** | DaemonSet `free -m` | Every 30s | Real cluster load-balancing |

---


## 🏢 High-Availability Multi-Cluster Failover Architecture

This section documents a **production-grade, self-healing HA extension** built on top of the OCM MultipleHubs foundation. It eliminates the single hub as a point of failure: if `primaryhub` goes down, `secondaryhub` takes over all cluster management automatically within ~30 seconds, with zero human intervention.

> **Scope note:** The OCM `MultipleHubs` feature (documented above) handles *spoke agent re-registration* on hub failure. The components below handle *hub-level* concerns: Virtual IP failover for seamless client access, automated watchdog-triggered failover, and split-brain protection. These layers complement each other.

---

### 🗺️ HA Topology

#### Kind / Local (current environment)

kube-vip **works in kind**. All kind containers share the same Docker bridge network (`172.19.0.0/16`), which is a real Linux bridge — a genuine L2 broadcast domain. ARP is fully functional between containers (verified via the kernel ARP neighbor table). kube-vip's Gratuitous ARP mode works exactly as it does on bare-metal VMs, with two parameter differences: the interface is `eth0` (not `enp1s0`) and the VIP must be an unassigned IP within the Docker bridge subnet.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  CLIENT / kubectl / CI                                                   │
│  Connect via VIP: 172.19.0.100  (floats between hubs on same bridge)     │
└──────────────────────────────────────┬───────────────────────────────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │   Virtual IP 172.19.0.100  │
                         │   Lease held by one hub    │
                         └──────────┬────────┬────────┘
                                    │        │
              ┌─────────────────────▼──┐  ┌──▼─────────────────────┐
              │  primaryhub-control-   │  │  secondaryhub-control- │
              │  plane  172.19.0.4     │  │  plane  172.19.0.5     │
              │  (ACTIVE — holds VIP)  │  │  (STANDBY)             │
              └────────────────────────┘  └────────────────────────┘
                         │                        │
              ┌──────────▼────────────────────────▼──────────┐
              │  spoke1  172.19.0.2   spoke2  172.19.0.3     │
              │  (Managed Workloads — same Docker bridge)     │
              └──────────────────────────────────────────────┘

  Docker bridge: 172.19.0.0/16   interface inside containers: eth0
  ClusterMesh (Cilium v1.18.0): primaryhub(id=1) ←→ secondaryhub(id=2)
                                 NodePort 32379 on each hub node IP
```

> **How VIP failover works here:** When `primaryhub` goes down, the kube-vip pod on `secondaryhub` wins the Kubernetes Lease (`plndr-cp-lock`), adds `172.19.0.100/32` to its `eth0`, and sends a Gratuitous ARP. All other containers on the Docker bridge (including spoke clusters) update their ARP caches instantly and route to `secondaryhub`. When `primaryhub` recovers, it reclaims the lease and the VIP returns.

---

### ⚙️ The 4 Components That Make Failover Possible

#### Component 1: 🌐 kube-vip — Virtual IP Manager *(works on both kind and production VMs)*

**What it is:** A lightweight service/pod running on the hubs to manage a shared Virtual IP (`172.19.0.100`).

**Why it matters:** Spoke clusters, your `kubectl`, and any CI/CD pipeline always connect to one stable IP (`172.19.0.100`). The active hub running underneath is transparent.

> 💡 **Important Multi-Cluster Concept: Single-Cluster vs Multi-Cluster Leases**
> - **Single Cluster (Standard kube-vip):** Multiple master nodes in *one* cluster share the same `etcd` database. They all fight for the same `plndr-cp-lock` Lease object. Node 1 wins, Node 2 & 3 stay standby.
> - **Two Separate Clusters (`primaryhub` + `secondaryhub`):** Each hub has its *own independent etcd database*. If `kube-vip` runs on both clusters simultaneously, `primaryhub`'s kube-vip wins the lease in `primaryhub`, AND `secondaryhub`'s kube-vip wins the lease in `secondaryhub`! Both would bind `172.19.0.100` at the same time.
> - **The Multi-Cluster Solution:** In a 2-cluster setup, `kube-vip` is active on `primaryhub` (holding `172.19.0.100`). On `secondaryhub`, the VIP is activated by the **Failover Controller watchdog** when `primaryhub` goes down (or `kube-vip` on `secondaryhub` is scaled to 1 replica upon confirmed outage). This prevents IP conflict while providing automatic VIP failover!

**RBAC (apply to both hubs):**

```bash
# Apply on primaryhub:
kubectl --context kind-primaryhub apply -f - <<'EOF'
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
- apiGroups: ["coordination.k8s.io"]
  resources: ["leases"]
  verbs: ["get", "create", "update", "patch"]
- apiGroups: [""]
  resources: ["nodes"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get"]
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
EOF

# Apply on secondaryhub:
kubectl --context kind-secondaryhub apply -f - <<'EOF'
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
- apiGroups: ["coordination.k8s.io"]
  resources: ["leases"]
  verbs: ["get", "create", "update", "patch"]
- apiGroups: [""]
  resources: ["nodes"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get"]
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
EOF
```

**DaemonSet — kind (current environment):**

Deploy `kube-vip` on `primaryhub` first so it binds VIP `172.19.0.100`. `secondaryhub` keeps its RBAC ready, and the `failover-controller` watchdog automatically deploys `kube-vip` to `secondaryhub` if `primaryhub` goes down!

```bash
# Step 1: Deploy kube-vip on primaryhub (Active Hub)
kubectl --context kind-primaryhub apply -f - <<'EOF'
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kube-vip
  namespace: kube-system
  labels:
    app: kube-vip
spec:
  selector:
    matchLabels:
      app: kube-vip
  template:
    metadata:
      labels:
        app: kube-vip
    spec:
      serviceAccountName: kube-vip
      hostNetwork: true
      tolerations:
      - effect: NoSchedule
        operator: Exists
      containers:
      - name: kube-vip
        image: ghcr.io/kube-vip/kube-vip:v0.8.2
        imagePullPolicy: IfNotPresent
        args:
        - manager
        - --controlplane
        - --arp              # L2 ARP — works on Docker bridge (same broadcast domain)
        - --interface
        - eth0               # Docker bridge NIC inside kind containers
        - --address
        - "172.19.0.100"     # free IP in Docker bridge 172.19.0.0/16
        - --leaderElection
        - --leaseDuration
        - "5"
        - --leaseRenewDuration
        - "3"
        - --leaseRetry
        - "1"
        securityContext:
          capabilities:
            add: ["NET_ADMIN", "NET_RAW", "SYS_TIME"]
EOF
```

**DaemonSet — production VMs:**

Replace `--interface` and `--address` with your actual NIC name and desired VIP.

```bash
# Apply on primaryhub:
kubectl --context primaryhub apply -f - <<'EOF'
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kube-vip
  namespace: kube-system
  labels:
    app: kube-vip
spec:
  selector:
    matchLabels:
      app: kube-vip
  template:
    metadata:
      labels:
        app: kube-vip
    spec:
      serviceAccountName: kube-vip
      hostNetwork: true
      tolerations:
      - effect: NoSchedule
        operator: Exists
      containers:
      - name: kube-vip
        image: ghcr.io/kube-vip/kube-vip:v0.8.2
        imagePullPolicy: IfNotPresent
        args:
        - manager
        - --controlplane
        - --arp
        - --interface
        - enp1s0             # adjust to your actual NIC name
        - --address
        - "192.168.122.230"  # shared VIP on your LAN
        - --leaderElection
        - --leaseDuration
        - "5"
        - --leaseRenewDuration
        - "3"
        - --leaseRetry
        - "1"
        securityContext:
          capabilities:
            add: ["NET_ADMIN", "NET_RAW", "SYS_TIME"]
EOF

# Apply on secondaryhub:
kubectl --context secondaryhub apply -f - <<'EOF'
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kube-vip
  namespace: kube-system
  labels:
    app: kube-vip
spec:
  selector:
    matchLabels:
      app: kube-vip
  template:
    metadata:
      labels:
        app: kube-vip
    spec:
      serviceAccountName: kube-vip
      hostNetwork: true
      tolerations:
      - effect: NoSchedule
        operator: Exists
      containers:
      - name: kube-vip
        image: ghcr.io/kube-vip/kube-vip:v0.8.2
        imagePullPolicy: IfNotPresent
        args:
        - manager
        - --controlplane
        - --arp
        - --interface
        - enp1s0
        - --address
        - "192.168.122.230"
        - --leaderElection
        - --leaseDuration
        - "5"
        - --leaseRenewDuration
        - "3"
        - --leaseRetry
        - "1"
        securityContext:
          capabilities:
            add: ["NET_ADMIN", "NET_RAW", "SYS_TIME"]
EOF
```

**Verify VIP ownership — kind:**

```bash
# Check which container holds the VIP:
docker exec primaryhub-control-plane ip addr show eth0 | grep 172.19.0.100
# Expected (while primary is active): inet 172.19.0.100/32 scope global eth0

docker exec secondaryhub-control-plane ip addr show eth0 | grep 172.19.0.100
# Expected: (empty — secondaryhub is standby)

# Check which hub holds the Kubernetes Lease (kind context):
kubectl --context kind-primaryhub get lease plndr-cp-lock -n kube-system \
  -o jsonpath='{.spec.holderIdentity}'

# Test VIP is reachable from the host or any spoke container:
curl -k https://172.19.0.100:6443/healthz
# Expected: ok
```

**Verify VIP ownership — production:**

```bash
ssh 192.168.122.225 "ip addr show enp1s0 | grep 192.168.122.230"
# Expected: inet 192.168.122.230/32 scope global enp1s0

ssh 192.168.122.143 "ip addr show enp1s0 | grep 192.168.122.230"
# Expected: (empty)

kubectl --context primaryhub get lease plndr-cp-lock -n kube-system -o yaml | grep holderIdentity
```

---

### 🤖 100% Automated Cross-Cluster VIP Failover

In a single cluster, `kube-vip` handles failover automatically. Across **two separate clusters**, we make VIP failover **100% automatic** using a lightweight **Automated Watchdog** deployed on `secondaryhub`.

#### 📊 Cluster & IP Architecture Summary

| Machine / Node | Real IP | Virtual IP (VIP) Status |
| :--- | :--- | :--- |
| **`primaryhub-control-plane`** | `172.19.0.4` | **Holds `172.19.0.100` (Active)** |
| **`secondaryhub-control-plane`** | `172.19.0.5` | **Standby (Binds `172.19.0.100` ONLY when primary fails)** |
| **`spoke1-control-plane`** | `172.19.0.2` | Connects via VIP `172.19.0.100` |
| **`spoke2-control-plane`** | `172.19.0.3` | Connects via VIP `172.19.0.100` |

#### How it Works

```
┌──────────────────────────────────────────────────────────────────┐
│                   SECONDARY HUB WATCHDOG                         │
│                                                                  │
│  1. Pings primaryhub API (172.19.0.4:6443) every 5 seconds.      │
│  2. If primaryhub goes DOWN (3 consecutive missed checks):       │
│     👉 Automatically deploys kube-vip on secondaryhub!           │
│        (secondaryhub binds 172.19.0.100)                         │
│  3. When primaryhub RECOVERS:                                    │
│     👉 Automatically deletes kube-vip from secondaryhub!         │
│        (VIP returns to primaryhub)                               │
└──────────────────────────────────────────────────────────────────┘
```

#### Step-by-Step Setup

**Deploy the Automated VIP Watchdog on `secondaryhub`:**

```bash
kubectl --context kind-secondaryhub apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: vip-watchdog-script
  namespace: kube-system
data:
  kube-vip-ds.yaml: |
    apiVersion: apps/v1
    kind: DaemonSet
    metadata:
      name: kube-vip
      namespace: kube-system
      labels:
        app: kube-vip
    spec:
      selector:
        matchLabels:
          app: kube-vip
      template:
        metadata:
          labels:
            app: kube-vip
        spec:
          serviceAccountName: kube-vip
          hostNetwork: true
          tolerations:
          - effect: NoSchedule
            operator: Exists
          containers:
          - name: kube-vip
            image: ghcr.io/kube-vip/kube-vip:v0.8.2
            imagePullPolicy: IfNotPresent
            args:
            - manager
            - --controlplane
            - --arp
            - --interface
            - eth0
            - --address
            - "172.19.0.100"
            - --leaderElection
            - --leaseDuration
            - "5"
            - --leaseRenewDuration
            - "3"
            - --leaseRetry
            - "1"
            securityContext:
              capabilities:
                add: ["NET_ADMIN", "NET_RAW", "SYS_TIME"]
  watchdog.sh: |
    #!/bin/sh
    PRIMARY_IP="172.19.0.4"
    FAIL_COUNT=0
    THRESHOLD=3
    VIP_ACTIVE=false

    echo "[vip-watchdog] Starting automated VIP watchdog monitoring primaryhub (${PRIMARY_IP})..."

    while true; do
      sleep 5
      if (echo > /dev/tcp/${PRIMARY_IP}/6443) >/dev/null 2>&1; then
        # Primary is UP
        if [ "$VIP_ACTIVE" = "true" ]; then
          echo "[vip-watchdog] primaryhub RECOVERED! Removing VIP from secondaryhub (failback)..."
          kubectl delete ds kube-vip -n kube-system 2>/dev/null || true
          VIP_ACTIVE=false
        elif [ "$FAIL_COUNT" -gt 0 ]; then
          echo "[vip-watchdog] primaryhub responded — resetting fail counter."
        fi
        FAIL_COUNT=0
      else
        # Primary is DOWN
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "[vip-watchdog] primaryhub UNREACHABLE ($FAIL_COUNT/$THRESHOLD)"
        if [ "$FAIL_COUNT" -ge "$THRESHOLD" ] && [ "$VIP_ACTIVE" = "false" ]; then
          echo "[vip-watchdog] THRESHOLD REACHED! Activating VIP on secondaryhub..."
          kubectl apply -f /scripts/kube-vip-ds.yaml
          VIP_ACTIVE=true
          echo "[vip-watchdog] VIP 172.19.0.100 successfully activated on secondaryhub!"
        fi
      fi
    done
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: vip-watchdog
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: vip-watchdog-role
rules:
- apiGroups: ["apps"]
  resources: ["daemonsets"]
  verbs: ["get", "create", "delete", "list", "watch"]
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: vip-watchdog-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: vip-watchdog-role
subjects:
- kind: ServiceAccount
  name: vip-watchdog
  namespace: kube-system
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vip-watchdog
  namespace: kube-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vip-watchdog
  template:
    metadata:
      labels:
        app: vip-watchdog
    spec:
      serviceAccountName: vip-watchdog
      containers:
      - name: watchdog
        image: bitnami/kubectl:latest
        command: ["/bin/sh", "/scripts/watchdog.sh"]
        volumeMounts:
        - name: script
          mountPath: /scripts
      volumes:
      - name: script
        configMap:
          name: vip-watchdog-script
          defaultMode: 0755
EOF
```

#### Test 100% Automated Failover

```bash
# Terminal 1 — Watch the watchdog logs live:
kubectl --context kind-secondaryhub logs -n kube-system deploy/vip-watchdog -f

# Terminal 2 — Stop primaryhub container:
docker stop primaryhub-control-plane

# Observe: Watchdog detects outage after 15s and automatically deploys kube-vip on secondaryhub!
# Test VIP reachability:
curl -k https://172.19.0.100:6443/healthz

# Terminal 2 — Start primaryhub container (Failback):
docker start primaryhub-control-plane

# Observe: Watchdog detects recovery, automatically removes kube-vip from secondaryhub, and primaryhub reclaims VIP 172.19.0.100!
```

---

## 🌐 Scenario B — Access Nginx on Spoke Clusters via Hub VIP (OCM Cluster Proxy + Cluster Gateway)

This section implements the **full production path**: access the Nginx pod running inside a spoke cluster by making an HTTP/HTTPS request that passes **only through the Hub VIP** (`172.19.0.100`). Your browser or `curl` never has a direct route to `spoke1` or `spoke2` — all traffic is tunnelled through the hub.

### 🔭 Why This Matters

Standard OCM only requires the spoke to reach the hub (outbound). The hub itself has **no direct network path back into the spoke's pod network**. OCM Cluster Proxy solves this by having each spoke's proxy-agent establish a persistent **mTLS gRPC tunnel outbound to the hub**. The hub then sends traffic *down* that already-established tunnel. No firewall rules, no VPN, no inbound ports on the spoke side — exactly the same model your web browser uses to connect to websites.

---

### 🗺️ Full Architecture (kind environment)

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  Your Browser / curl                                                    │
  │  GET https://172.19.0.100:6443/apis/.../clustergateways/spoke2/proxy/  │
  └────────────────────────────────┬────────────────────────────────────────┘
                                   │  HTTPS (Docker bridge, same L2)
                                   ▼
            ┌──────── Hub VIP 172.19.0.100 (kube-vip floats) ────────┐
            │                                                          │
            │   cluster-gateway  (Aggregated API Server on hub)       │
            │   Routes /apis/cluster.core.oam.dev/...                 │
            │          │ calls spoke via konnectivity client           │
            │   cluster-proxy Proxy Server  (mTLS gRPC)               │
            │   Accepts tunnels from spoke proxy agents                │
            └──────────────────────────┬───────────────────────────────┘
                                       │  mTLS gRPC tunnel
                                       │  (established OUTBOUND by spoke agent)
                        ┌──────────────▼──────────────┐
                        │  spoke2-control-plane        │
                        │  Proxy Agent (dials out→hub) │
                        │  nginx-demo Service :80      │
                        │  nginx-demo Pod              │
                        └──────────────────────────────┘
```

**Traffic direction at a glance:**

| Leg | Direction | Protocol |
| :--- | :--- | :--- |
| Browser → Hub VIP | Inbound to hub | HTTPS `:6443` |
| cluster-gateway → Proxy Server | In-cluster on hub | Go konnectivity client |
| Proxy Server → spoke Proxy Agent | Hub → Spoke (via existing tunnel) | mTLS gRPC |
| Proxy Agent → nginx Service | In-cluster on spoke | TCP `:80` |

---

### 📋 Prerequisites Check

```bash
# Confirm spoke clusters are registered
kubectl --context kind-primaryhub get managedcluster

# Confirm helm is installed
helm version --short

# Confirm clusteradm is installed
clusteradm version
```

---

### 🪜 Step 1 — Add a ClusterIP Service to the Nginx ManifestWorkReplicaSet

The Nginx pod currently has **no Service** inside the spoke cluster. The cluster-proxy routes TCP to a stable `ClusterIP` Service DNS name — not a raw pod IP. We add a Service to the existing `ManifestWorkReplicaSet` so OCM propagates it to every spoke automatically.

```bash
kubectl --context kind-primaryhub apply -f - <<'EOF'
apiVersion: work.open-cluster-management.io/v1alpha1
kind: ManifestWorkReplicaSet
metadata:
  name: nginx-auto-placement
  namespace: default
spec:
  placementRefs:
  - name: dynamic-memory-placement
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
                ports:
                - containerPort: 80

      - apiVersion: v1
        kind: Service
        metadata:
          name: nginx-demo
          namespace: default
        spec:
          selector:
            app: nginx-demo
          ports:
          - name: http
            port: 80
            targetPort: 80
EOF
```

> 💡 **Why ClusterIP?** The proxy agent runs inside the spoke cluster, so it reaches the Service by its internal DNS name (`nginx-demo.default.svc.cluster.local:80`). `NodePort` or `LoadBalancer` are not needed here.

Verify the Service appeared on each spoke:

```bash
kubectl --context kind-spoke1 get svc nginx-demo -n default
kubectl --context kind-spoke2 get svc nginx-demo -n default
```

Expected:

```
NAME         TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)   AGE
nginx-demo   ClusterIP   10.x.x.x       <none>        80/TCP    30s
```

---

### 🪜 Step 2 — Install OCM Cluster Proxy on the Hub

The `cluster-proxy` Helm chart installs two things on the hub:

| Deployed object | What it does |
| :--- | :--- |
| `cluster-proxy` (3 replicas) | The **Proxy Server** — mTLS gRPC endpoint that spokes connect to |
| `cluster-proxy-addon-manager` | Watches `ManagedClusterAddOn` objects and pushes proxy agents into each spoke |

```bash
# Add the OCM Helm repository (only needed once)
helm repo add ocm https://open-cluster-management.io/helm-charts
helm repo update
```

```bash
# Install cluster-proxy on primaryhub and expose port 8091 on Hub VIP (172.19.0.100)
helm install \
  --kube-context kind-primaryhub \
  -n open-cluster-management-addon \
  --create-namespace \
  cluster-proxy ocm/cluster-proxy \
  --set proxyServer.entrypointAddress=172.19.0.100 \
  --set proxyServer.entrypointPort=8091

kubectl --context kind-primaryhub patch svc proxy-entrypoint -n open-cluster-management-addon \
  -p '{"spec":{"externalIPs":["172.19.0.100","172.19.0.4"]}}'
```

Verify hub-side pods are `Running`:

```bash
kubectl --context kind-primaryhub get deploy -n open-cluster-management-addon
```

Expected:

```
NAME                          READY   UP-TO-DATE   AVAILABLE   AGE
cluster-proxy                 3/3     3            3           1m
cluster-proxy-addon-manager   1/1     1            1           1m
```

#### 🔍 How to Verify or Update the Configured Entrypoint IP

**Verify currently configured IP:**

```bash
# Option A: Via Helm values
helm get values cluster-proxy -n open-cluster-management-addon --kube-context kind-primaryhub

# Option B: Via ManagedProxyConfiguration Custom Resource
kubectl --context kind-primaryhub get managedproxyconfiguration cluster-proxy -o jsonpath='{.spec.proxyServer.entrypoint.hostname.value}{"\n"}'
```

**Update deployed chart to use the VIP (`172.19.0.100`) if installed previously with a different IP:**

```bash
helm upgrade \
  --kube-context kind-primaryhub \
  -n open-cluster-management-addon \
  cluster-proxy ocm/cluster-proxy \
  --set proxyServer.entrypointAddress=172.19.0.100 \
  --set proxyServer.entrypointPort=8091
```

---

### 🪜 Step 3 — Enable the Proxy Agent on Each Spoke

Creating a `ManagedClusterAddOn` on the hub (one per spoke) tells the addon-manager to push the **Proxy Agent** into that spoke. The agent then automatically dials outbound to the hub's proxy server and keeps the mTLS tunnel alive permanently.

```bash
# Enable cluster-proxy on spoke1
kubectl --context kind-primaryhub apply -f - <<'EOF'
apiVersion: addon.open-cluster-management.io/v1alpha1
kind: ManagedClusterAddOn
metadata:
  name: cluster-proxy
  namespace: spoke1
spec:
  installNamespace: open-cluster-management-cluster-proxy
EOF
```

```bash
# Enable cluster-proxy on spoke2
kubectl --context kind-primaryhub apply -f - <<'EOF'
apiVersion: addon.open-cluster-management.io/v1alpha1
kind: ManagedClusterAddOn
metadata:
  name: cluster-proxy
  namespace: spoke2
spec:
  installNamespace: open-cluster-management-cluster-proxy
EOF
```

Verify agent pods appeared inside each spoke (OCM places them in namespace `open-cluster-management-cluster-proxy`):

```bash
kubectl --context kind-spoke1 get pods -n open-cluster-management-cluster-proxy
kubectl --context kind-spoke2 get pods -n open-cluster-management-cluster-proxy
```

Expected:

```
NAME                            READY   STATUS    RESTARTS   AGE
cluster-proxy-agent-<hash>      1/1     Running   0          1m
```

Check that tunnels are established:

```bash
kubectl --context kind-primaryhub get managedclusteraddon -A
```

Expected:

```
NAMESPACE   NAME            AVAILABLE   DEGRADED   PROGRESSING
spoke1      cluster-proxy   True
spoke2      cluster-proxy   True
```

Or use `clusteradm` for a latency-aware view (always pass `--context kind-primaryhub` so `clusteradm` targets the hub):

```bash
clusteradm proxy health --context kind-primaryhub
```

> 💡 **Tip**: If you omit `--context kind-primaryhub`, `clusteradm` uses your active shell context. If your shell context is currently pointed at a spoke (e.g., `kind-spoke2`), `clusteradm` will report `Cluster-Proxy addon is not installed.` because the addon manager runs on the **Hub**, not on spokes.

Expected:

```
CLUSTER NAME   INSTALLED   AVAILABLE   PROBED HEALTH   LATENCY
spoke1         True        True        True            ~7ms
spoke2         True        True        True            ~7ms
```

---

### 🪜 Step 4 — Install Cluster Gateway (Stable HTTPS URL on Hub)

`cluster-gateway` is an **aggregated Kubernetes API server** that runs on the hub. It registers itself as an extension at `/apis/cluster.core.oam.dev`, reusing the hub's existing port 6443. This means:

- **No new port or LoadBalancer** — it shares the kube-apiserver's existing address.
- **Standard RBAC** — the same tokens and kubeconfigs that work with the hub also gate spoke access.
- **One stable URL per cluster** — the URL pattern is fixed and does not change on hub failover.

```bash
# Install cluster-gateway on primaryhub
helm install \
  --kube-context kind-primaryhub \
  -n open-cluster-management-addon \
  cluster-gateway \
  ocm/cluster-gateway-addon-manager
```

Verify the aggregated API is registered:

```bash
kubectl --context kind-primaryhub api-resources | grep clustergateway
```

Expected:

```
clustergateways   ...   cluster.core.oam.dev/v1alpha1   false   ClusterGateway
```

Verify that `clustergateways` API resource is registered on the hub:

```bash
kubectl --context kind-primaryhub api-resources | grep clustergateway
```

Expected:

```
clustergateways                                                         cluster.core.oam.dev/v1alpha1                 false        ClusterGateway
clustergatewayconfigurations                                            proxy.open-cluster-management.io/v1alpha1     false        ClusterGatewayConfiguration
```

> 💡 **Note on `kubectl get clustergateway`**: By default, `kubectl get clustergateway` will return `No resources found` unless the optional `managed-serviceaccount` addon is enabled to auto-populate token secrets. The aggregated API path (`/apis/cluster.core.oam.dev/v1alpha1/...`) routes through the `cluster-proxy` mTLS tunnel to reach spoke clusters.

---

### 🪜 Step 5 — View Nginx Web Page in Browser via Hub VIP (`http://172.19.0.100:8080/`)

To view the **Nginx welcome web page** directly in your browser using **only the Hub VIP address** (`172.19.0.100`), deploy the forwarder using pure `kubectl apply` manifests on both Hubs:

```bash
# 1. Expose Nginx on spoke2 via NodePort 30080
kubectl --context kind-spoke2 patch svc nginx-demo -n default -p '{"spec":{"type":"NodePort","ports":[{"name":"http","port":80,"targetPort":80,"nodePort":30080}]}}'

# 2. Deploy HTTP forwarder on primaryhub via kubectl (listens on Hub VIP 172.19.0.100:8080)
kubectl --context kind-primaryhub apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hub-nginx-proxy
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: hub-nginx-proxy
  template:
    metadata:
      labels:
        app: hub-nginx-proxy
    spec:
      hostNetwork: true
      containers:
      - name: proxy
        image: alpine/socat
        args: ["TCP-LISTEN:8080,fork,reuseaddr", "TCP:172.19.0.3:30080"]
EOF

# 3. Deploy HTTP forwarder on secondaryhub via kubectl (standby hub)
kubectl --context kind-secondaryhub apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hub-nginx-proxy
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: hub-nginx-proxy
  template:
    metadata:
      labels:
        app: hub-nginx-proxy
    spec:
      hostNetwork: true
      containers:
      - name: proxy
        image: alpine/socat
        args: ["TCP-LISTEN:8080,fork,reuseaddr", "TCP:172.19.0.3:30080"]
EOF
```

---

#### 🌐 Open Nginx in your browser:

Navigate directly to the Hub VIP in Chrome/Firefox:
👉 **`http://172.19.0.100:8080/`**

You will see the **Nginx welcome page HTML** (`Welcome to nginx!`) rendered in your browser!

---

#### 🧪 Hub VIP Failover Test (Nginx Browser Edition)

1. Keep **`http://172.19.0.100:8080/`** loaded in your browser.
2. Stop `primaryhub` in your terminal:
   ```bash
   docker stop primaryhub-control-plane
   ```
3. Wait **~3 seconds** for `kube-vip` to transfer the VIP `172.19.0.100` to `secondaryhub`.
4. Refresh your browser at the **exact same URL** (`http://172.19.0.100:8080/`).
5. **Result**: The Nginx welcome page reloads successfully — now served seamlessly by **`secondaryhub`** over the VIP!
6. Bring `primaryhub` back online (Failback):
   ```bash
   docker start primaryhub-control-plane
   ```

---

#### 🧠 Deep-Dive Architectural & Technical Breakdown of Step 5

| Step / Technical Parameter | Why We Did It | Deep-Dive Mechanism |
| :--- | :--- | :--- |
| **1. `NodePort 30080` on `spoke2`** | Allow host-level / cross-cluster Docker reachability. | By default, `service/nginx-demo` is `ClusterIP` (`10.102.219.9`), isolated inside `spoke2`'s internal pod network. Exposing `type: NodePort` on port `30080` binds `spoke2-control-plane`'s container interface (`172.19.0.3:30080`), allowing any container or host on the `172.19.0.0/16` Docker bridge network to query Nginx directly. |
| **2. `hostNetwork: true` on Hub Pods** | Bind the proxy pod directly to the Hub's host network interface (`eth0`). | Standard Kubernetes pods sit in isolated pod networks (`10.96.x.x` / `10.97.x.x`). Setting `hostNetwork: true` attaches the pod directly to `eth0` on the Hub node, allowing it to capture incoming TCP traffic targeted at the floating Hub VIP (`172.19.0.100:8080`). |
| **3. Dual Deployment on Both Hubs** | Seamless HA failover without changing browser URLs. | Deploying `hub-nginx-proxy` on both `primaryhub` and `secondaryhub` ensures both control-plane nodes have a listening daemon on port 8080. When `kube-vip` shifts `172.19.0.100` to `secondaryhub` upon failover, `secondaryhub` immediately handles incoming requests on `http://172.19.0.100:8080/`. |

#### 📐 End-to-End Traffic Routing Diagram

```
[ Your Desktop Web Browser ]
             │
      ( http://172.19.0.100:8080/ )
             │
             ▼
   [ Floating Hub VIP: 172.19.0.100 ]
             │
  ┌──────────┴──────────┐
  │ Active Node:        │ (If Primary fails, VIP floats to Secondary)
  │ primaryhub          │
  └──────────┬──────────┘
             │
             ▼
   [ hub-nginx-proxy Pod ]  ( hostNetwork: true, port 8080 )
             │
             ▼
   ( Forwarded via socat over 172.19.0.0/16 Docker Bridge )
             │
             ▼
   [ spoke2-control-plane:30080 ]
             │
             ▼
   [ nginx-demo Pod inside spoke2 ]  ===> Renders "Welcome to nginx!" HTML Page!
```

---

### 🪜 Step 6 — Access Nginx Through the Hub VIP

> 💡 **Understanding Connectivity Paths**:
> - **Direct Spoke Access (`kubectl --context kind-spoke2`)**: Dials `spoke2-control-plane:6443` (`172.19.0.3`) directly. This bypasses both Hubs entirely — which is why it continues working even when both Hubs are stopped.
> - **Hub VIP Access (`kind-primaryhub`)**: Routes requests *through* the Hub (`primaryhub` at `172.19.0.100`), down the mTLS gRPC tunnel, and into the spoke. If `primaryhub` is stopped, this connection breaks immediately!

> ⚠️ **Note on `deploy/cluster-proxy` port 8090/8091**: Port 8090/8091 of `cluster-proxy` is a **binary gRPC / mTLS server** (Konnectivity protocol), not a standard HTTP web server. Opening `localhost:8889` in a browser or plain `curl` will produce `connection reset by peer` because gRPC expects TLS frames, not plain HTTP `GET /`.

#### Test 1 — Hub Dependence Test (Fails if Hub is stopped)

Run `kubectl proxy` targeting **`primaryhub`**:

```bash
# Start an authenticated proxy to primaryhub (Hub VIP 172.19.0.100)
kubectl --context kind-primaryhub proxy --port=8001
```

> 💡 **Port in use error?**: If you see `address already in use`, `kubectl proxy` is already running in the background. You can directly run the `curl` command below, or run `pkill -f "kubectl proxy"` to stop existing proxy instances.

In a new terminal:

```bash
curl http://localhost:8001/api/v1/namespaces/default/services
```

> 🧪 **Failover Test**: Stop `primaryhub` (`docker stop primaryhub-control-plane`). Notice `curl http://localhost:8001/...` fails immediately with `Connection refused` because it depends on `primaryhub`! Switch context to `kind-secondaryhub` (`kubectl --context kind-secondaryhub proxy --port=8001`) to resume Hub access on the standby hub.

---

#### Test 2 — Direct Spoke Access Test (Independent of Hubs)

```bash
# Direct port-forward to spoke2 (bypasses Hubs entirely)
kubectl --context kind-spoke2 port-forward svc/nginx-demo 8888:80 -n default
```

In a new terminal:

```bash
curl http://localhost:8888/
```

You should see the **Nginx welcome page HTML**.

---

#### Full Production — Stable HTTPS URL via Cluster Gateway

The URL pattern:

```
https://<hub-api-ip>:6443/apis/cluster.core.oam.dev/v1alpha1/clustergateways/<cluster>/proxy/api/v1/namespaces/<namespace>/services/<service>:<port>/proxy/
```

Generate a hub token:

```bash
TOKEN=$(kubectl --context kind-primaryhub create token default -n default)
```

Access nginx on spoke2 via the Hub VIP:

```bash
curl -k \
  --header "Authorization: Bearer $TOKEN" \
  "https://172.19.0.100:6443/apis/cluster.core.oam.dev/v1alpha1/clustergateways/spoke2/proxy/api/v1/namespaces/default/services/nginx-demo:80/proxy/"
```

You should see the **Nginx welcome page HTML** — served by the pod inside spoke2, proxied entirely through hub VIP `172.19.0.100`. The URL is identical regardless of which physical hub holds the VIP.

Access nginx on spoke1 (same pattern, different cluster name):

```bash
curl -k \
  --header "Authorization: Bearer $TOKEN" \
  "https://172.19.0.100:6443/apis/cluster.core.oam.dev/v1alpha1/clustergateways/spoke1/proxy/api/v1/namespaces/default/services/nginx-demo:80/proxy/"
```

---

### ✅ End-to-End Verification Checklist

```bash
# Layer 1: Nginx Service exists on each spoke
kubectl --context kind-spoke1 get svc nginx-demo -n default
kubectl --context kind-spoke2 get svc nginx-demo -n default

# Layer 2: Proxy server is healthy on the hub
kubectl --context kind-primaryhub get pods -n open-cluster-management-addon

# Layer 3: Proxy agents are healthy inside each spoke
kubectl --context kind-spoke1 get pods -n open-cluster-management-cluster-proxy
kubectl --context kind-spoke2 get pods -n open-cluster-management-cluster-proxy

# Layer 4: Tunnels are Available
kubectl --context kind-primaryhub get managedclusteraddon -A

# Layer 5: Tunnels are Available & Probed Healthy
clusteradm proxy health --context kind-primaryhub

# Layer 7: Full production URL returns HTTP 200
TOKEN=$(kubectl --context kind-primaryhub create token default -n default)
curl -sk -o /dev/null -w "HTTP %{http_code}\n" \
  --header "Authorization: Bearer $TOKEN" \
  "https://172.19.0.100:6443/apis/cluster.core.oam.dev/v1alpha1/clustergateways/spoke2/proxy/api/v1/namespaces/default/services/nginx-demo:80/proxy/"
# Expected: HTTP 200
```

---

### 🔁 Hub Failover Test — Does Nginx Stay Reachable?

**Goal:** Confirm that after `primaryhub` goes completely offline, the same URL still returns the Nginx page — now served via `secondaryhub` holding the VIP.

Before the test, install cluster-proxy and cluster-gateway on `secondaryhub`. Use the VIP as the entrypoint so spokes reconnect transparently:

```bash
# cluster-proxy on secondaryhub (installed & exposed on Hub VIP 172.19.0.100)
helm install \
  --kube-context kind-secondaryhub \
  -n open-cluster-management-addon \
  --create-namespace \
  cluster-proxy ocm/cluster-proxy \
  --set proxyServer.entrypointAddress=172.19.0.100 \
  --set proxyServer.entrypointPort=8091

kubectl --context kind-secondaryhub patch svc proxy-entrypoint -n open-cluster-management-addon \
  -p '{"spec":{"externalIPs":["172.19.0.100","172.19.0.5"]}}'
```

```bash
# cluster-gateway on secondaryhub
helm install \
  --kube-context kind-secondaryhub \
  -n open-cluster-management-addon \
  cluster-gateway \
  ocm/cluster-gateway-addon-manager
```

Run the failover test (two terminals):

```bash
# Terminal 1 — Poll nginx every 3 seconds via the VIP URL
TOKEN=$(kubectl --context kind-primaryhub create token default -n default)
while true; do
  STATUS=$(curl -sk -o /dev/null -w "%{http_code}" \
    --header "Authorization: Bearer $TOKEN" \
    "https://172.19.0.100:6443/apis/cluster.core.oam.dev/v1alpha1/clustergateways/spoke2/proxy/api/v1/namespaces/default/services/nginx-demo:80/proxy/")
  echo "$(date '+%H:%M:%S')  HTTP $STATUS"
  sleep 3
done
```

```bash
# Terminal 2 — Kill primaryhub
docker stop primaryhub-control-plane
```

What you will observe in Terminal 1:

```
03:00:00  HTTP 200    ← primaryhub is serving
03:00:03  HTTP 200
03:00:09  HTTP 000    ← primaryhub going down (brief gap ~15s)
03:00:12  HTTP 000
03:00:15  HTTP 200    ← secondaryhub picked up the VIP, serving again
03:00:18  HTTP 200    ← transparent to the client
```

```bash
# Terminal 2 — Restore primaryhub (failback test)
docker start primaryhub-control-plane
```

After ~30 seconds `primaryhub` reclaims the VIP and becomes the active hub again.

---

### 🗂️ Component Summary

| Component | Where it runs | What it does |
| :--- | :--- | :--- |
| **cluster-proxy Proxy Server** | Hub (`open-cluster-management-addon` ns) | mTLS gRPC server; terminates tunnels from spoke agents |
| **cluster-proxy Addon Manager** | Hub (`open-cluster-management-addon` ns) | Watches `ManagedClusterAddOn` objects; pushes agent manifests to spokes |
| **cluster-proxy Proxy Agent** | Each spoke (`open-cluster-management-agent-addon` ns) | Dials outbound to hub proxy server; keeps mTLS tunnel alive permanently |
| **cluster-gateway** | Hub (`open-cluster-management-addon` ns) | Aggregated API server; exposes `clustergateways/<cluster>/proxy/<path>` on port 6443 |
| **kube-vip** | Hub nodes (`kube-system` ns) | Manages floating VIP `172.19.0.100`; fails over between hubs in ~15–30 s |
| **nginx-demo Deployment + Service** | Each spoke (`default` ns) | The workload being proxied; `ClusterIP` Service gives a stable internal address |

---

### 📌 Key Concepts Explained Simply

**Why does the spoke agent dial outbound?**
The spoke clusters might be in a completely separate private network (different data centre, different country). You cannot add firewall rules to let the hub dial *in* to every private spoke network. Instead, each spoke's proxy-agent dials *out* to one well-known address (the hub entrypoint IP). This is the same model your browser uses — you always connect out to websites; the website never initiates a connection into your laptop.

**What is mTLS?**
Standard HTTPS (TLS) only checks the *server's* certificate. Mutual TLS (mTLS) means *both* sides present and verify certificates before the tunnel opens. This prevents a rogue process from impersonating a legitimate spoke agent and injecting or snooping on tunnel traffic.

**What is an aggregated API server (cluster-gateway)?**
Kubernetes lets you extend its own API with additional servers (`APIService` objects). `cluster-gateway` registers itself as an extension of the hub's kube-apiserver. Requests for `/apis/cluster.core.oam.dev` are automatically forwarded to `cluster-gateway`. You get a stable, RBAC-enforced URL on the hub's existing port — no extra ingress or LoadBalancer needed.

**What happens if the hub fails?**
1. kube-vip moves the VIP from `primaryhub` to `secondaryhub` (~15 s).
2. Spokes lose their gRPC tunnel connection briefly, then re-dial — they reconnect to `secondaryhub`'s proxy server (reachable at the same VIP address).
3. `cluster-gateway` on `secondaryhub` is already running and routes through the re-established tunnels.
4. Clients hitting `172.19.0.100:6443` see a brief gap (~15–30 s), then `HTTP 200` resumes — transparently.
