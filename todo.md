# Multi-Cluster Infrastructure & Security Improvements: Action Plan (TODO)

This document tracks all security vulnerabilities, Kubernetes-native architecture gaps, and configuration drift discovered during the live SSM audit of the 5 production EC2 multi-cluster nodes on **November 16, 2026**.

---

## 1. Executive Summary & Audit Overview

### 1.1. Audited EC2 Instance Fleet

| Cluster Role | Region | Instance ID | Private IPv4 | K8s Cluster | OS / Runtime |
|---|---|---|---|---|---|
| **PrimaryHub** | `us-east-1` | `i-00677ba8b39a8f2a3` | `10.20.136.172` | `primaryhub` (KinD) | Amazon Linux 2023 / Docker 25 |
| **SecondaryHub** | `eu-west-1` | `i-04ef27857a07da12d` | `10.22.128.144` | `secondaryhub` (KinD) | Amazon Linux 2023 / Docker 25 |
| **Spoke 1** | `eu-central-1` | `i-04fc5a21d1cd92edf` | `10.23.142.252` | `spoke1` (KinD) | Amazon Linux 2023 / Docker 25 |
| **Spoke 2** | `ap-south-1` | `i-05e51d0264a061001` | `10.21.134.191` | `spoke2` (KinD) | Amazon Linux 2023 / Docker 25 |
| **Cortex** | `us-east-1` | `i-02d48970ea239f81d` | `10.20.156.135` | `cortex` (KinD) | Amazon Linux 2023 / Docker 25 |

---

## 2. Priority & Implementation Roadmap

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Phase 0: P0 - Intelligent Capacity-Aware Workload Distribution (Active)    │
│  - OCM ClusterSet Registration & Prioritizer Scoring Policies              │
│  - Real-Time Spoke Capacity & Active Sandbox Workload Scheduler Provider   │
│  - Autonomous Cluster Onboarding & Label Replication for Future Spokes     │
└─────────────────────────────────────┬──────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 1: P0 - Critical Security & Cloud Configuration Fixes (Immediate)    │
│  - Restrict AWS IMDSv2 Hop Limit to 1 (Block Pod IAM Credential Theft)     │
│  - Fix SPOKE2 IP Configuration Drift in prod.env                           │
│  - Restrict KinD Docker Database Port Bindings from 0.0.0.0 to 10.99.0.x   │
└─────────────────────────────────────┬──────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 2: P1 - Kubernetes-Native Zero-Trust & Workload Isolation             │
│  - Deploy Default-Deny NetworkPolicies for Sandbox Workloads               │
│  - Enforce Pod Security Standards (PSA: restricted) on Workload Namespaces │
│  - Disable AutomountServiceAccountToken on Untrusted Workloads             │
└─────────────────────────────────────┬──────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 3: P2 - Storage Durability & Disk Pressure Hardening                 │
│  - Resize Root EBS Volumes (Prevent 20GB Disk Saturation from 25Gi PVC)    │
│  - Plan Transition from local-path-provisioner to AWS EBS CSI (gp3)        │
└─────────────────────────────────────┬──────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 4: P2 - Kubernetes-Native Mesh Networking Evolution                  │
│  - Migrate Host Cron sync-wireguard-peers.sh to a K8s DaemonSet / Operator │
│  - Or Evaluate Cilium ClusterMesh with Transparent WireGuard Encryption    │
└─────────────────────────────────────┬──────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 5: P3 - Codebase Hygiene, Image Pre-Baking & Deduplication           │
│  - Deduplicate Sealed-Secrets Controllers (kube-system vs opensandbox)     │
│  - Pre-bake WireGuard & runsc into KinD node image (Stop runtime apt-get)  │
│  - Secure File Permissions on .sandbox-state/wg/ (chmod 600)               │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Action Items by Phase

### Phase 0: P0 — Intelligent Capacity-Aware Workload Distribution Across Spoke Clusters

- [x] **0.1. Audit & Fix OCM PlacementDecision Failure (`AllManagedClusterSetsEmpty`)** *(Completed Nov 17, 2026)*
  - **Root Cause:** Spoke clusters (`spoke1`, `spoke2`) lacked the label `cluster.open-cluster-management.io/clusterset: sandbox-spokes`. As a result, `Placement` returned 0 decisions, and OpenSandbox server defaulted to hardcoded `DEFAULT_FALLBACK_SPOKE = "spoke1"`, sending 100% of workloads to `spoke1` (8 ManifestWorks on `spoke1`, 0 on `spoke2`).
  - **Remediation:** Labeled `spoke1` and `spoke2` with `clusterset=sandbox-spokes`, `sandbox-workload-capable=true`, `runtime.gvisor=true`, `runtime.kata=true`. Both clusters immediately bound to `PlacementDecision` on `primaryhub`.

- [x] **0.2. Native OCM Placement Prioritization Policy** *(Completed Nov 17, 2026)*
  - **File:** [`codeInspector/templates/ocm-placement.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/templates/ocm-placement.yaml)
  - **Enhancement:** Removed `numberOfClusters: 1` bottleneck and added multi-dimensional score prioritizers:
    - `ResourceAllocatableCPU` (weight: 3)
    - `ResourceAllocatableMemory` (weight: 3)
    - `Balance` (weight: 4)

- [x] **0.3. Dynamic Capacity-Aware Spoke Workload Dispatcher** *(Completed & Verified Nov 17, 2026)*
  - **File:** [`opensandbox-server/docker-build/src/services/k8s/ocm_provider.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/ocm_provider.py)
  - **Enhancement:** Implemented `_select_spoke_by_capacity()`:
    1. Evaluates all candidate clusters returned in `PlacementDecision`.
    2. Inspects `ManagedCluster` conditions to ensure `ManagedClusterConditionAvailable == True`.
    3. Reads real-time `allocatable.pods` capacity (default 110 pods).
    4. Queries active `ManifestWork` count in the candidate spoke namespace (`app.kubernetes.io/part-of=01sandbox`).
    5. Computes utilization ratio $\text{load} = \frac{\text{active ManifestWorks}}{\text{allocatable pods}}$ and automatically selects the candidate with the highest capacity headroom.
  - **Live Verification:** Tested live inside `opensandbox-server` on `primaryhub`:
    - Evaluated candidate clusters `['spoke1', 'spoke2']`.
    - `spoke1`: 8 active sandboxes / 110 pods (utilization: 7.27%).
    - `spoke2`: 0 active sandboxes / 110 pods (utilization: 0.00%).
    - Capacity scheduler chose optimal spoke cluster: `spoke2`! Workloads are dynamically diverted away from busy spokes to idle spokes. Mounted declaratively via ConfigMap `opensandbox-ocm-override` across both `primaryhub` and `secondaryhub`.

- [x] **0.4. Automatic Cluster Onboarding & Label Replication for Future Spokes** *(Completed Nov 17, 2026)*
  - **Files Updated:**
    - [`accounts/sandbox/us-east-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/templates/user_data.sh.tftpl)
    - [`accounts/sandbox/eu-west-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-west-1/compute/templates/user_data.sh.tftpl)
    - [`accounts/sandbox/eu-central-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-central-1/compute/templates/user_data.sh.tftpl)
    - [`accounts/sandbox/ap-south-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/ap-south-1/compute/templates/user_data.sh.tftpl)
    - [`script/prod-docker-multi-script/spoke-autonomous-join.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/spoke-autonomous-join.md)
  - **Capability:** When new spoke clusters (e.g. `spoke3`, `spoke4`) are provisioned via Terraform in `/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox`:
    1. The hub's auto-accept background timer automatically approves the CSR and applies the `clusterset=sandbox-spokes` and runtime tags.
    2. The primary hub mirrors the new spoke and its labels to the secondary standby hub.
    3. The OCM placement policy immediately schedules the new spoke into `PlacementDecision`.
    4. The capacity-aware scheduler detects 0 active sandboxes on the new spoke and automatically begins balancing incoming workloads to it.

---

### Phase 1: P0 — Critical Security & Cloud Configuration Fixes (Immediate)

- [x] **1.1. Block AWS IMDSv2 Access from Sandbox Containers (Hop Limit = 1)** *(Completed Nov 16, 2026)*
  - **Risk:** `CRITICAL`. KinD containers and user pods could fetch AWS IAM temporary credentials from `http://169.254.169.254/latest/meta-data/iam/security-credentials/` because `HttpPutResponseHopLimit` was set to `4` (PrimaryHub, Cortex) and `2` (Spokes). Any code running inside a sandbox could assume the host EC2 IAM roles (`bb-sandbox-spot-ssm`, etc.) with full S3, SSM, and EBS permissions.
  - **Execution:**
    - Applied `modify-instance-metadata-options --http-put-response-hop-limit 1 --http-tokens required` via EC2 CLI across all 5 live instances in their respective regions (`us-east-1`, `eu-west-1`, `eu-central-1`, `ap-south-1`).
    - Updated Terraform Launch Templates across all 4 sandbox compute regions in [`/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/) (`us-east-1`, `eu-west-1`, `eu-central-1`, `ap-south-1`) to lock `http_put_response_hop_limit = 1`, preventing IaC drift or future Spot instance re-creations from re-introducing the vulnerability.
  - **Verification:**
    Verified live via SSM on all 5 nodes:
    - Host OS SSM agent reaches IMDS (`200 OK` token generated).
    - KinD containers (`primaryhub-control-plane`, `cortex-control-plane`, `secondaryhub-control-plane`, `spoke1-control-plane`, `spoke2-control-plane`) are **BLOCKED** from fetching tokens (`curl -m 2` connection timed out).

- [x] **1.2. Correct SPOKE2 Private IPv4 Configuration Drift** *(Completed Nov 16, 2026)*
  - **Risk:** `HIGH`. In [`script/prod-docker-multi-script/prod.env`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/prod.env#L37), line 37 declared stale IP `SPOKE2_PRIVATE_IP="10.21.138.35"`. However, instance `i-05e51d0264a061001` in `ap-south-1` is actually assigned `10.21.134.191`. Re-running scripts broke WireGuard endpoint peer resolution.
  - **File Updated:** [`script/prod-docker-multi-script/prod.env`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/prod.env#L37)
  - **Remediation & Verification:**
    Updated line 37 to `SPOKE2_PRIVATE_IP="10.21.134.191"`. Verified no other stale references exist in deployment scripts. WireGuard peer resolution correctly resolves to `10.21.134.191:51820`.

- [x] **1.3. Restrict KinD Docker Database Port Bindings from `0.0.0.0`** *(Completed Nov 16, 2026)*
  - **Risk:** `HIGH`. Docker proxy on PrimaryHub exposed `0.0.0.0:30432` (PostgreSQL NodePort), `0.0.0.0:30379` (Valkey NodePort), and `0.0.0.0:30900` (Prometheus) on all host network interfaces. Any host in peered VPCs could hit these database ports directly.
  - **Implementation Strategy:**
    Because KinD starts before WireGuard interface `wg0` is created, binding KinD's `listenAddress` directly to `10.99.0.1` causes Docker startup failure (`Cannot assign requested address`). Instead, the Docker-native firewall solution is enforced in the `DOCKER-USER` iptables chain:
    ```bash
    iptables -I DOCKER-USER 1 -i wg0 -p tcp -m multiport --dports 30432,30379,30900 -j ACCEPT
    iptables -I DOCKER-USER 2 -i lo -p tcp -m multiport --dports 30432,30379,30900 -j ACCEPT
    iptables -I DOCKER-USER 3 -p tcp -m multiport --dports 30432,30379,30900 -j DROP
    ```
  - **Codebase Persistence:**
    - Updated [`script/prod-docker-multi-script/lib/clusters.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/lib/clusters.sh#L217) to enforce these rules immediately upon cluster creation.
    - Updated [`script/prod-docker-multi-script/lib/network.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/lib/network.sh#L154) to enforce rules during WireGuard setup.
    - Updated `/usr/local/bin/sync-wireguard-peers.sh` on live hub instances and in [`script/prod-docker-multi-script/spoke-autonomous-join.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/spoke-autonomous-join.md#L834) so the 20-second systemd timer continuously guarantees rule persistence.
  - **Verification:**
    - Verified from SecondaryHub: WireGuard connections to PrimaryHub (`10.99.0.1:30432` and `10.99.0.1:30379`) **succeed** immediately.
    - Verified from Cortex: VPC IP connections to PrimaryHub (`10.20.136.172:30432` and `10.20.136.172:30379`) are **BLOCKED** and time out.

---

### Phase 2: P1 — Kubernetes-Native Zero-Trust & Workload Isolation

- [ ] **2.1. Deploy Default-Deny Kubernetes NetworkPolicies for Sandbox Workloads**
  - **Risk:** `HIGH`. Audit showed **0 NetworkPolicies** across all 5 clusters. Untrusted user code executed in `opensandbox-workloads` has direct uninhibited layer-3 access to internal databases, RabbitMQ, etcd, and Kubernetes API servers.
  - **Files to update:** [`codeInspector/charts/opensandbox/templates/`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandbox/templates/)
  - **Manifest to add (`networkpolicy.yaml`):**
    ```yaml
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata:
      name: sandbox-workloads-default-deny
      namespace: opensandbox-workloads
    spec:
      podSelector: {}
      policyTypes:
        - Ingress
        - Egress
      egress:
        # Allow DNS resolution only
        - to:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: kube-system
          ports:
            - protocol: UDP
              port: 53
            - protocol: TCP
              port: 53
        # Explicitly deny 169.254.169.254 (IMDS) and internal VPC CIDRs (10.0.0.0/8, 172.16.0.0/12)
    ```

- [ ] **2.2. Enforce Pod Security Admission (PSA) Restricted on Workload Namespaces**
  - **Risk:** `HIGH`. All namespaces currently have `<none>` for `enforce`, `audit`, and `warn`.
  - **Files to update:** Root namespace templates or deployment scripts creating namespaces.
  - **Remediation:**
    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: opensandbox-workloads
      labels:
        pod-security.kubernetes.io/enforce: restricted
        pod-security.kubernetes.io/enforce-version: latest
        pod-security.kubernetes.io/warn: restricted
    ```

- [ ] **2.3. Disable `automountServiceAccountToken` on Untrusted Workload Pods**
  - **Risk:** `MEDIUM`. By default, Kubernetes mounts API server tokens at `/var/run/secrets/kubernetes.io/serviceaccount`. Sandbox runner pods do not require Kubernetes API access.
  - **File:** [`codeInspector/charts/opensandbox/templates/server.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandbox/templates/server.yaml) (`example.batchsandbox-template.yaml` block).
  - **Remediation:** Ensure `automountServiceAccountToken: false` is set on the BatchSandbox pod template specification.

---

### Phase 3: P2 — Storage Durability & Disk Pressure Hardening

- [ ] **3.1. Resize Root EBS Volumes to Prevent Node Disk Saturation**
  - **Risk:** `HIGH`. EC2 root disk `/dev/nvme0n1p1` is currently **20GB** on Hub and Spokes.
    - `prometheus-pvc`: requested **25Gi**
    - `scan-pvc`: requested **10Gi**
    - `postgresql-primary-1`: requested **5Gi**
    Total PVC requests (40Gi) exceed physical disk capacity (20Gi). As Prometheus scrapes and WAL segments write to `/var/lib/cortex` and `/prometheus`, the host will enter `NodeHasDiskPressure` and crash pods.
  - **Action:** Increase EBS volume sizes in AWS to at least **80Gi–100Gi** (`gp3`), followed by `growpart` and `xfs_growfs` / `resize2fs`.

- [ ] **3.2. Plan Transition from `local-path-provisioner` to Persistent AWS EBS Volumes**
  - **Risk:** `MEDIUM`. Data stored via `local-path-provisioner` resides inside Docker/KinD ephemeral paths on the root disk. If an EC2 spot instance terminates, database data and Prometheus historical metrics are lost permanently.
  - **Recommendation:** Mount a secondary dedicated EBS volume on the EC2 host (e.g. at `/data/k8s-storage`), or deploy the **AWS EBS CSI Driver** with local CSI fallback.

---

### Phase 4: P2 — Kubernetes-Native Networking Evolution

- [ ] **4.1. Replace Host Cron WireGuard Synchronization with a Kubernetes-Native DaemonSet**
  - **Current State:** A host-level root cron job (`/etc/cron.d/wireguard-sync`) executes `/usr/local/bin/sync-wireguard-peers.sh` every minute. It parses `/sandbox/clusters` from AWS SSM, executes AWS CLI commands on the host, and calls `wg syncconf`.
  - **Kubernetes-Native Target State:**
    - Package the sync logic into a Kubernetes **DaemonSet** or custom Operator.
    - Store WireGuard peer states as Kubernetes Custom Resources (`WireGuardPeer`).
    - The controller watches the CRDs and applies WireGuard configurations via Netlink directly, making peer discovery and key rotation fully declarative and auditable via `kubectl`.

- [ ] **4.2. Long-Term Evaluation: Cilium ClusterMesh with Transparent WireGuard**
  - **Recommendation:** Evaluate replacing `kindnet` + host WireGuard bash scripts with **Cilium CNI**. Cilium supports native multi-cluster ClusterMesh with kernel-level WireGuard encryption, native Kubernetes NetworkPolicies (L3/L4/L7), and integrated Cilium Tetragon eBPF security observability out of the box.

---

### Phase 5: P3 — Codebase Hygiene, Image Pre-Baking & Deduplication

- [ ] **5.1. Deduplicate Sealed-Secrets Controllers on PrimaryHub**
  - **Finding:** PrimaryHub has two SealedSecrets controllers running simultaneously:
    1. `sealed-secrets-controller` in namespace `kube-system`
    2. `codeinspector-sealed-secrets` in namespace `opensandbox-system`
  - **File:** [`codeInspector/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/values.yaml)
  - **Remediation:** In `values.yaml`, disable the subchart controller if `kube-system` is the designated cluster-wide controller:
    ```yaml
    sealed-secrets:
      enabled: false # Avoid running duplicate decryption controllers
    ```

- [ ] **5.2. Pre-Bake KinD Node Container Image (Stop Runtime `apt-get`)**
  - **Finding:** In [`script/prod-docker-multi-script/lib/clusters.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/lib/clusters.sh#L256), line 256 executes `apt-get update && apt-get install -y wireguard-tools iptables` dynamically inside running KinD containers during bootstrap.
  - **Issue:** Runtime package installations fail if Debian package repositories have intermittent network timeouts or rate-limiting.
  - **Remediation:** Build and publish a pre-baked Docker image `01community/kind-node:v1.32.2-sandboxed` that includes `wireguard-tools`, `iptables`, and `runsc` pre-installed.

- [ ] **5.3. Restrict Permissions on Local State Keys (`.sandbox-state/wg/`)**
  - **Finding:** Key files (`gateway.key`, `primaryhub.key`, `spoke1.key`, etc.) in `script/prod-docker-multi-script/.sandbox-state/wg/` have default `0664` permissions.
  - **File:** [`script/prod-docker-multi-script/lib/network.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/lib/network.sh)
  - **Remediation:** Enforce `umask 077` and `chmod 600` on all private key generation routines:
    ```bash
    chmod 600 "${WG_DIR}"/*.key
    ```

---

## 4. Verification & Validation Recipes

Use these one-line commands to audit and verify completion of the action items above:

```bash
# 1. Verify IMDSv2 is blocked from inside KinD:
docker exec primaryhub-control-plane curl -m 2 -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60"
# (Must time out / return non-zero)

# 2. Verify NetworkPolicies are active:
docker exec primaryhub-control-plane kubectl get netpol -A

# 3. Verify Pod Security Admission labels:
docker exec primaryhub-control-plane kubectl get ns -o custom-columns=NAME:.metadata.name,ENFORCE:.metadata.labels."pod-security\.kubernetes\.io/enforce"

# 4. Verify disk space on nodes:
df -h / /var/lib/docker

# 5. Verify only 1 Sealed Secrets controller is active:
docker exec primaryhub-control-plane kubectl get deploy -A | grep sealed-secrets
```
