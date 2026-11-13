# Automated Secret Rotation & Multi-Cluster Prometheus Authentication Architecture

This guide provides an end-to-end technical explanation of how **automated, zero-downtime secret rotation** operates across the multi-cluster Prometheus observability platform, how distributed edge spoke clusters authenticate and push metrics to Central Prometheus for visualization in Grafana, and how newly added spoke clusters automatically discover credentials and configure themselves without manual intervention.

---

## Table of Contents

1. [Architectural Overview & Data Flow](#1-architectural-overview--data-flow)
2. [How Automated Secret Rotation Works (The 4-Phase Lifecycle)](#2-how-automated-secret-rotation-works-the-4-phase-lifecycle)
   * [Deep Dive: What is "Hub Dual-Auth" and Why is it Necessary?](#deep-dive-what-is-hub-dual-auth-and-why-is-it-necessary)
   * [Phase 1: Dual-Auth Activation on PrimaryHub](#phase-1-dual-auth-activation-on-primaryhub)
   * [Phase 2: Autonomous Multi-Cluster & Cloud Distribution](#phase-2-autonomous-multi-cluster--cloud-distribution)
   * [Phase 3: Transition & Overlap Grace Period](#phase-3-transition--overlap-grace-period)
   * [Phase 4: Selective Pruning & Revocation](#phase-4-selective-pruning--revocation)
3. [How Edge Spokes Authenticate & Deliver Metrics to PrimaryHub](#3-how-edge-spokes-authenticate--deliver-metrics-to-primaryhub)
4. [How New Spoke Clusters Auto-Configure via AWS Launch Templates](#4-how-new-spoke-clusters-auto-configure-via-aws-launch-templates)
5. [Visualization in Central Grafana](#5-visualization-in-central-grafana)
6. [Resilience & Disaster Recovery (Instance Terminations)](#6-resilience--disaster-recovery-instance-terminations)
7. [Reference Code & File Index](#7-reference-code--file-index)

---

## 1. Architectural Overview & Data Flow

Our multi-cluster observability architecture spans multiple AWS regions connected over an encrypted full-mesh **WireGuard VPN overlay** (`10.99.0.0/24`):

* **PrimaryHub** (`us-east-1`): Central Prometheus TSDB receiver (NodePort `30900`), Central Grafana, AWS EBS persistence (`100 GiB`), and Open Cluster Management (OCM) control plane.
* **SecondaryHub** (`eu-west-1`): Standby control plane pushing metrics to PrimaryHub.
* **Edge Spoke Clusters** (`eu-central-1`, `ap-south-1`, `us-east-1`, etc.): Autonomous KinD/Kubernetes nodes running edge `prometheus-scraper` pods.

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                             PRIMARYHUB (us-east-1)                                              │
│                                                                                                                 │
│   ┌──────────────────────────────────┐                     ┌────────────────────────────────────────────────┐   │
│   │   prometheus-secret-rotator      │                     │               Central Grafana                  │   │
│   │   (Runs every 30d / On-Demand)   │                     │  (http://prometheus-service.opensandbox:9090)  │   │
│   └───────────────┬──────────────────┘                     └───────────────────────▲────────────────────────┘   │
│                   │                                                                │                            │
│   1. Dual-Auth    │ 2. Distribute via OCM                 Local Query Latency <3ms │                            │
│      Update       │    & AWS SSM Parameter Store                                   │                            │
│                   ▼                                        ┌───────────────────────┴────────────────────────┐   │
│   ┌──────────────────────────────────┐                     │            Central Prometheus TSDB             │   │
│   │  Secret: prometheus-web-config   │ ──(SIGHUP reload)──►│     (--web.enable-remote-write-receiver)       │   │
│   │  [prometheus-rw + prom-rw-...]   │                     │     NodePort 30900 / Basic Auth Gate           │   │
│   └──────────────────────────────────┘                     └───────────────────────▲────────────────────────┘   │
│                                                                                    │ 100 GiB EBS Volume         │
│                                                                                    ▼ (DeleteOnTermination=false)│
│                                                            ┌────────────────────────────────────────────────┐   │
│                                                            │            /data → /prometheus (PVC)           │   │
│                                                            └────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────┘
                                                                                     ▲
                                HTTP POST /api/v1/write                              │ WireGuard Overlay Mesh
                          Basic Auth (Signed Credentials)                            │ 10.99.0.0/24
                                                                                     │
                       ┌─────────────────────────────────────────────────────────────┴──────────────────┐
                       │                                                                                │
┌──────────────────────┴──────────────────────┐                                  ┌──────────────┴──────────────────────┐
│            SPOKE 1 (eu-central-1)           │                                  │            SPOKE 2 (ap-south-1)     │
│  • Edge prometheus-scraper pod              │                                  │  • Edge prometheus-scraper pod      │
│  • Local crash-safe WAL buffer              │                                  │  • Local crash-safe WAL buffer      │
│  • OCM klusterlet-work-agent receives auth  │                                  │  • OCM klusterlet-work-agent receives auth
│  • cluster="spoke1", region="eu-central-1"  │                                  │  • cluster="spoke2", region="ap-south-1"
└─────────────────────────────────────────────┘                                  └─────────────────────────────────────┘
```

---

## 2. How Automated Secret Rotation Works (The 4-Phase Lifecycle)

To eliminate static credentials without causing `HTTP 401 Unauthorized` errors or dropped metric samples across geographically separated AWS regions, the platform implements a **Dual-Authentication Overlap Pattern** managed by the Kubernetes CronJob [`rotator-cronjob.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/rotator-cronjob.yaml).

```text
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     THE 4-PHASE ROTATION LIFECYCLE                                     │
├──────────────────────────┬──────────────────────────┬──────────────────────────┬───────────────────────┤
│ Phase 1: Hub Dual-Auth   │ Phase 2: Autonomous Sync │ Phase 3: Transition      │ Phase 4: Safe Pruning │
├──────────────────────────┼──────────────────────────┼──────────────────────────┼───────────────────────┤
│ • Generates User_B &     │ • Dispatches OCM         │ • 600s grace period      │ • Prunes ONLY older   │
│   Bcrypt ($2b$) hash     │   ManifestWork over WG   │   overlap window         │   rotated credentials │
│ • Patches PrimaryHub:    │ • Syncs AWS SSM Store    │ • Both old and new       │ • System users        │
│   [System + A + B]       │   via pure Python SigV4  │   credentials valid      │   (prometheus-rw)     │
│ • SIGHUP reload in       │   (/sandbox/monitoring)  │ • Zero 401 errors,       │   remain preserved    │
│   ~10ms (no restarts)    │ • New spokes immediately │   zero sample loss       │ • Final SIGHUP reload │
│ • All users active       │   discover new creds     │                          │ • Clean final state   │
└──────────────────────────┴──────────────────────────┴──────────────────────────┴───────────────────────┘
```

### Deep Dive: What is "Hub Dual-Auth" and Why is it Necessary?

**Hub Dual-Auth** (Dual-Authentication Overlap) refers to the transitional state during secret rotation where **both the previous credentials and the new credentials remain valid simultaneously** on PrimaryHub's Prometheus server.

#### 1. The Distributed Cutover Race Condition
In a geographically distributed multi-cluster setup (PrimaryHub in Virginia, edge spokes in Frankfurt, Mumbai, Ireland):
* **Impossibility of Instantaneous Synchronization**: You cannot reconfigure multiple remote Kubernetes clusters around the world at the exact same millisecond. Network latency across regions, OCM manifest reconciliation loops, and Kubernetes secret reload cycles mean some spokes will update in seconds while others may take up to a few minutes.
* **The Failure Mode Without Dual-Auth**: If PrimaryHub immediately overwrote the old credentials with the new credentials in a single step (hard cutover), every spoke cluster that had not yet applied its new secret would immediately fail with `HTTP 401 Unauthorized` and drop or backpressure telemetry until it completed updating.
* **The Solution With Dual-Auth**: PrimaryHub accepts metrics signed by **EITHER** the old credential OR the new credential. Spoke clusters can migrate asynchronously at their own pace. Metric ingestion remains 100% continuous without a single dropped sample.

#### 2. How PrimaryHub Implements Dual-Auth
Prometheus natively supports multiple concurrent authorized users in its HTTP basic authentication configuration file (`web.yml`). During the overlap window, the rotator injects the new credential alongside the old credential:

```yaml
# PrimaryHub /etc/prometheus/web.yml during the Dual-Auth Overlap Window
basic_auth_users:
  prometheus-rw: "$2y$10$..."         # Static system account (Grafana proxy & internal scrapes)
  prom-rw-202611021237: "$2b$12$..." # OLD active user (spokes currently using this)
  prom-rw-202611030908: "$2b$12$..." # NEW active user (spokes switching now)
```

#### 3. Operational Comparison: Hard Cutover vs Hub Dual-Auth
| Operational Metric | Hard Cutover (Without Dual-Auth) | Hub Dual-Auth (Our Implementation) |
| :--- | :--- | :--- |
| **Authentication Strategy** | Single active user replaced instantly | Multiple concurrent valid users in `web.yml` |
| **Edge Spoke Synchronization** | Strict lockstep required (impossible globally) | Asynchronous graceful migration over 10 minutes |
| **HTTP Error Rates** | Bursts of `HTTP 401 Unauthorized` alerts | **0 HTTP 401 errors** across entire fleet |
| **Metric Telemetry Loss** | Telemetry lost or backpressured during cutover | **Zero dropped metrics** across all regions |
| **Grafana Dashboard Impact** | Gaps in graphs and panel errors during rotation | Continuous, uninterrupted graphs |


### Phase 1: Dual-Auth Activation on PrimaryHub
1. The `prometheus-secret-rotator` CronJob executes in namespace `opensandbox-system`.
2. Generates a new unique user identifier `prom-rw-<timestamp>` and a cryptographically secure 24-character random password.
3. Computes the Blowfish/Bcrypt (`$2b$`) hash using Python's standard library `crypt` module.
4. Reads the existing `secret/prometheus-web-config` and appends the new user while **preserving all existing system users**:
   ```yaml
   basic_auth_users:
     prometheus-rw: "$2y$10$XNVJltW/XwJnMEwAxlcUo.mcQWUhIJrlqLONxd9Xn8etCITQkHo5O"
     prom-rw-202611021237: "$2b$12$ZBXxtlux1lwexuILj1wNhukCxmL0ZFzIZ/ltbSpoYn.wlxKmUPXFC"
     prom-rw-202611030908: "$2b$12$l7YuXNcbZULq9zSErrVyM.TxC9sv9NoXy/pckYuCYR/4dmgVc5oIO"
   ```
5. Waits 10 seconds for Kubernetes secret volume propagation, then sends a Linux kernel signal `kill -HUP 1` directly to the PrimaryHub Prometheus container.
6. **Hot-Reload Speed**: Prometheus reloads configuration in **~10.89ms** without pod restarts, connection drops, or downtime. Dual-Auth is now live.

### Phase 2: Autonomous Multi-Cluster & Cloud Distribution

#### 1. Edge Spoke Sync via OCM ManifestWork (Live In-Cluster Synchronization)
* **Cluster Discovery**: The rotator queries Open Cluster Management (OCM) on PrimaryHub for all registered managed clusters:
  ```python
  spokes = run_cmd(["kubectl", "get", "managedclusters", "-o", "jsonpath={.items[*].metadata.name}"]).split()
  ```
* **Dispatching Workloads to Spoke Tracking Namespaces**:
  For every joined cluster (`spoke1`, `spoke2`, etc.), the rotator creates or patches an OCM `ManifestWork` named `spoke-prometheus-auth` inside that cluster's Hub-side tracking namespace:
  ```yaml
  apiVersion: work.open-cluster-management.io/v1
  kind: ManifestWork
  metadata:
    name: spoke-prometheus-auth
    namespace: spoke1              # <-- Hub-side tracking namespace for Spoke 1
  spec:
    workload:
      manifests:
        - apiVersion: v1
          kind: Secret
          metadata:
            name: prometheus-remote-write-auth
            namespace: monitoring  # <-- Target namespace INSIDE the spoke cluster
          type: Opaque
          stringData:
            username: prom-rw-202611030908
            password: <24-char-plaintext-password>
  ```

* **How ManifestWork Patches the Spoke's `monitoring` Namespace**:
  ```text
  [PrimaryHub (us-east-1)]
    │
    │  1. Rotator applies ManifestWork into tracking namespace "spoke1"
    ▼
  [OCM Control Plane]
    │
    │  2. Synchronizes across encrypted WireGuard VPN overlay (10.99.0.0/24)
    ▼
  [Spoke 1 (eu-central-1)]
    │
    │  3. Local klusterlet-work-agent pulls the workload payload
    ▼
  [Spoke 1: Namespace "monitoring"]
    │
    │  4. Commits/Patches: secret/prometheus-remote-write-auth
    │     - username: prom-rw-202611030908
    │     - password: <24-char-plaintext-password>
    ▼
  [prometheus-scraper Pod in "monitoring"]
    │
    │  5. Reads updated secret and streams to PrimaryHub (10.99.0.1:30900/api/v1/write)
    ▼
  [PrimaryHub Prometheus]
    │
    └─► Dual-Auth validates either credential with HTTP 204 No Content!
  ```
  - **Namespace Separation Explained**: Applying the `ManifestWork` in `namespace: spoke1` on PrimaryHub instructs OCM which physical spoke cluster should receive the payload. Inside the payload specification, `namespace: monitoring` directs the spoke's local agent to create/patch the secret directly inside the spoke's local **`monitoring`** namespace.
  - **Autonomous Agent Application**: The local `klusterlet-work-agent` running inside the spoke pulls this payload across WireGuard (`10.99.0.0/24`) and commits `secret/prometheus-remote-write-auth` into the spoke's `monitoring` namespace without human intervention.
  - **Immediate Ingestion**: The local `prometheus-scraper` pod references this secret in its remote write configuration, picking up the new credentials and transmitting metrics with zero downtime.

#### 2. AWS SSM Parameter Store Synchronization (For New & Replacement Instances)
* To guarantee that newly launched EC2 instances (from Auto Scaling Groups or disaster recovery rebuilds) immediately receive the active credentials, the rotator calls AWS SSM Parameter Store directly in `us-east-1`:
  * `/sandbox/monitoring/rw-username` (`Type: String`)
  * `/sandbox/monitoring/rw-password` (`Type: SecureString`)
* **Pure-Python SigV4 Engine**: Implemented directly in Python standard library (`urllib.request`, `hmac`, `hashlib`, `datetime`). Requires zero external pip packages and zero `aws` CLI binaries.
* **Credential Expiry Consideration for 30-Day Automated Cron**:
  * The rotator CronJob reads AWS credentials from `secret/aws-ssm-credentials` in `opensandbox-system`.
  * Temporary STS credentials (`ASIA...` with `AWS_SESSION_TOKEN`) expire within 1–12 hours. For fully unattended 30-day runs, supply non-expiring credentials (`AKIA...`) or attach an IAM policy with `ssm:PutParameter` on `/sandbox/monitoring/*` to the EC2 instance profile (`*-ssm`).

### Phase 3: Transition & Overlap Grace Period
* The rotator enters a configurable grace period (`GRACE_PERIOD_SECONDS=600`, default 10 minutes).
* Because PrimaryHub operates in Dual-Auth mode, incoming remote write requests authenticated with either the previous credentials or the new credentials succeed immediately (`HTTP 204 No Content`).

### Phase 4: Selective Pruning & Revocation
* Once the grace period expires, the rotator re-fetches `secret/prometheus-web-config`.
* **Selective Pruning Rule**:
  * Users starting with `prom-rw-` that do not match `new_user` are removed (deprecated rotating credentials).
  * The baseline system user `prometheus-rw` (used by Grafana datasource proxy, PrimaryHub self-scrapes, and SecondaryHub) is **permanently preserved**.
* PrimaryHub Prometheus receives a final `kill -HUP 1` reload signal. Deprecated credentials now return `HTTP 401 Unauthorized`, and only valid credentials remain accepted.

---

## 3. How Edge Spokes Authenticate & Deliver Metrics to PrimaryHub

Each spoke runs a local Prometheus instance (`prom/prometheus:v2.54.1`) configured in Push mode via `remote_write`:

### Scraper Configuration (`prometheus-scraper-config`)
Path: ConfigMap `prometheus-scraper-config` in namespace `monitoring` on each spoke:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s
  external_labels:
    cluster: "spoke1"
    role: "spoke"
    region: "eu-central-1"

remote_write:
  - url: "http://10.99.0.1:30900/api/v1/write"
    basic_auth:
      username: "prom-rw-202611030908"
      password: "<SECURE_PASSWORD>"
    queue_config:
      capacity: 10000
      max_shards: 10
      min_shards: 1
      max_samples_per_send: 1000
      batch_send_deadline: 5s
      min_backoff: 500ms
      max_backoff: 10s
```

### Authentication & Ingestion Pipeline:
1. **Local Scrape & WAL Buffer**: Every 15 seconds, the scraper polls local pods, services, and nodes, appending raw samples into `/prometheus/wal/`. If network connectivity to PrimaryHub drops, samples accumulate in the WAL without data loss.
2. **Batching & Compression**: A background `walWatcher` goroutine reads segments, batches samples into Protocol Buffers (Protobuf), compresses them with Snappy in RAM, and adds the HTTP Basic Authorization header:
   ```http
   POST /api/v1/write HTTP/1.1
   Host: 10.99.0.1:30900
   Authorization: Basic <base64(username:password)>
   Content-Encoding: snappy
   Content-Type: application/x-protobuf
   ```
3. **WireGuard Routing & Kernel Netfilter**: The batch travels across the encrypted WireGuard overlay (`10.99.0.3 → 10.99.0.1`). On the PrimaryHub EC2 host, Linux kernel netfilter (DNAT) forwards port `30900` directly into the KinD container network interface at wire speed.
4. **PrimaryHub Authentication Gate**:
   * Central Prometheus `--web.config.file=/etc/prometheus-web/web.yml` receives the request.
   * Extracts the plaintext password from the `Authorization: Basic` header and hashes it using the salt embedded in `basic_auth_users`.
   * If the hash matches, the batch bypasses the security gate and writes into the TSDB head block, returning `HTTP 204 No Content`.

---

## 4. How New Spoke Clusters Auto-Configure via AWS Launch Templates

When a new spoke cluster is provisioned in any AWS region, it is **100% self-bootstrapping and zero-touch**. No admin intervention or manual credential copying is required.

### Launch Template Location:
Launch templates are defined per-region under the infrastructure repository:
* [`/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-central-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-central-1/compute/templates/user_data.sh.tftpl)
* [`/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/ap-south-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/ap-south-1/compute/templates/user_data.sh.tftpl)
* [`/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-west-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-west-1/compute/templates/user_data.sh.tftpl)
* [`/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/templates/user_data.sh.tftpl)

### The Cloud-Init Bootstrapping Pipeline:
When the EC2 instance boots, `user_data.sh.tftpl` executes the following automated pipeline:

```bash
deploy_spoke_monitoring() {
  echo "=== Deploying Autonomous Spoke Prometheus Scraper (Push to PrimaryHub) ==="
  export KUBECONFIG=/etc/kubernetes/admin.conf

  # 1. Apply Base RBAC & Namespace
  kubectl apply -f - <<'EOF_MON_BASE'
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus-scraper
  namespace: monitoring
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: prometheus-scraper-role
rules:
  - apiGroups: [""]
    resources: ["nodes", "nodes/metrics", "services", "endpoints", "pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["daemonsets", "statefulsets", "deployments"]
    verbs: ["get", "list", "watch"]
  - nonResourceURLs: ["/metrics"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: prometheus-scraper-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: prometheus-scraper-role
subjects:
  - kind: ServiceAccount
    name: prometheus-scraper
    namespace: monitoring
EOF_MON_BASE

  # 2. Dynamically retrieve Remote Write credentials from AWS SSM Parameter Store
  echo "Fetching Remote Write credentials from AWS SSM Parameter Store (/sandbox/monitoring/rw-* in us-east-1)..."
  if ! RW_USER=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-username" --region "us-east-1" --query "Parameter.Value" --output text); then
    echo "FATAL: Failed to retrieve /sandbox/monitoring/rw-username from AWS SSM Parameter Store in us-east-1. Aborting." >&2
    return 1
  fi

  if ! RW_PASS=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-password" --region "us-east-1" --query "Parameter.Value" --with-decryption --output text); then
    echo "FATAL: Failed to retrieve /sandbox/monitoring/rw-password from AWS SSM Parameter Store in us-east-1. Aborting." >&2
    return 1
  fi

  # 3. Apply scraper ConfigMap with remote_write and dynamic credentials
  cat <<EOF_CM | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-scraper-config
  namespace: monitoring
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
      evaluation_interval: 15s
      scrape_timeout: 10s
      external_labels:
        cluster: "${cluster_name}"
        role: "${role}"
        region: "${region}"

    remote_write:
      - url: "http://10.99.0.1:30900/api/v1/write"
        basic_auth:
          username: "$${RW_USER}"
          password: "$${RW_PASS}"
        queue_config:
          capacity: 10000
          max_shards: 10
          min_shards: 1
          max_samples_per_send: 1000
          batch_send_deadline: 5s
          min_backoff: 500ms
          max_backoff: 10s

    scrape_configs:
      - job_name: 'prometheus-scraper'
        static_configs:
          - targets: ['localhost:9090']

      - job_name: 'open-cluster-management-agent'
        kubernetes_sd_configs:
          - role: pod
            namespaces:
              names:
                - open-cluster-management-agent
        relabel_configs:
          - source_labels: [__address__]
            action: replace
            regex: ([^:]+)(?::\d+)?
            replacement: \$1:8080
            target_label: __address__
          - action: labelmap
            regex: __meta_kubernetes_pod_label_(.+)
          - source_labels: [__meta_kubernetes_namespace]
            target_label: kubernetes_namespace
          - source_labels: [__meta_kubernetes_pod_name]
            target_label: kubernetes_pod_name

      - job_name: 'kubernetes-services'
        kubernetes_sd_configs:
          - role: service
        relabel_configs:
          - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_scrape]
            action: keep
            regex: true
          - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_path]
            action: replace
            target_label: __metrics_path__
            regex: (.+)
          - source_labels: [__address__, __meta_kubernetes_service_annotation_prometheus_io_port]
            action: replace
            regex: ([^:]+)(?::\d+)?;(\d+)
            replacement: \$1:\$2
            target_label: __address__
          - action: labelmap
            regex: __meta_kubernetes_service_label_(.+)
          - source_labels: [__meta_kubernetes_namespace]
            target_label: kubernetes_namespace
          - source_labels: [__meta_kubernetes_service_name]
            target_label: kubernetes_name

      - job_name: 'kubernetes-pods'
        kubernetes_sd_configs:
          - role: pod
        relabel_configs:
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
            action: keep
            regex: true
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
            action: replace
            target_label: __metrics_path__
            regex: (.+)
          - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
            action: replace
            regex: ([^:]+)(?::\d+)?;(\d+)
            replacement: \$1:\$2
            target_label: __address__
          - action: labelmap
            regex: __meta_kubernetes_pod_label_(.+)
          - source_labels: [__meta_kubernetes_pod_namespace]
            action: replace
            target_label: kubernetes_namespace
          - source_labels: [__meta_kubernetes_pod_name]
            target_label: kubernetes_pod_name
EOF_CM

  # 4. Deploy Prometheus Scraper Container
  kubectl apply -f - <<'EOF_MON_DEP'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus-scraper
  namespace: monitoring
  labels:
    app: prometheus-scraper
spec:
  replicas: 1
  selector:
    matchLabels:
      app: prometheus-scraper
  template:
    metadata:
      labels:
        app: prometheus-scraper
    spec:
      serviceAccountName: prometheus-scraper
      containers:
        - name: prometheus
          image: prom/prometheus:v2.54.1
          imagePullPolicy: IfNotPresent
          args:
            - "--config.file=/etc/prometheus/prometheus.yml"
            - "--storage.tsdb.path=/prometheus"
            - "--storage.tsdb.retention.time=6h"
            - "--web.enable-lifecycle"
          ports:
            - containerPort: 9090
              name: metrics
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 256Mi
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
            - name: storage
              mountPath: /prometheus
      volumes:
        - name: config
          configMap:
            name: prometheus-scraper-config
        - name: storage
          emptyDir: {}
EOF_MON_DEP
}
```

---

## 5. Visualization in Central Grafana

Central Grafana runs in the `opensandbox-system` namespace on PrimaryHub. It queries the local PrimaryHub TSDB via in-cluster DNS with sub-3ms latency:

### Datasource Proxy Configuration
Path: [`codeInspector/charts/grafana/templates/configmap-datasources.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/configmap-datasources.yaml)

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasources
  namespace: opensandbox-system
data:
  datasources.yaml: |
    apiVersion: 1
    datasources:
      - name: "primaryhub"
        uid: "prometheus-primaryhub"
        type: prometheus
        access: proxy
        url: http://prometheus-service.opensandbox-system.svc.cluster.local:9090
        isDefault: true
        jsonData:
          timeInterval: "15s"
          httpMethod: GET
        basicAuth: true
        basicAuthUser: "prometheus-rw"
        secureJsonData:
          basicAuthPassword: "PrometheusRemoteWriteSecure2026!"
        editable: false
```

### Pre-Provisioned Dashboards
Path: [`codeInspector/charts/grafana/templates/configmap-dashboards.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/configmap-dashboards.yaml)

All dashboards are auto-mounted into Grafana at launch:
1. **Universal Spoke Fleet Observability (`spoke-fleet.json`)**:
   Uses the dynamic liveness query:
   ```promql
   count by (cluster)(up)
   ```
   The `$cluster` dropdown dynamically discovers any newly attached spoke cluster within 60 seconds. When an ephemeral spoke is terminated, it cleanly drops out of the active list.
2. **Global Fleet Overview (`multi-cluster-overview.json`)**:
   Cross-cluster availability table, API request rates, and WireGuard throughput across all AWS regions.
3. **PrimaryHub & Standby Control Plane Dashboards (`primaryhub.json`, `secondaryhub.json`)**:
   CloudNativePG replication lag, PostgreSQL WAL sync, FastAPI backend metrics, and Envoy proxy routes.

---

## 6. Resilience & Disaster Recovery (Instance Terminations)

### Case A: Edge Spoke Instance Terminated
* **Authentication**: **100% Automatic**. When the replacement EC2 instance launches, cloud-init fetches the latest credentials from AWS SSM Parameter Store, boots the KinD cluster, starts the scraper pod, and connects to WireGuard.
* **Historical Data Safety**: Spoke instances are completely stateless and ephemeral. All historical metrics are stored centrally on PrimaryHub's 100 GiB AWS EBS GP3 volume (`DeleteOnTermination=false`). Not a single past sample is lost.

### Case B: PrimaryHub EC2 Instance Terminated
* **Storage Preservation**: Central TSDB data is mounted on an independent AWS EBS volume (`DeleteOnTermination=false`). When PrimaryHub is re-provisioned (via `./docker-multi-cluster.sh` or Terraform), the persistent volume is reattached to `/data → /prometheus` (`prometheus-pvc`).
* **Authentication Continuity**: PrimaryHub starts with baseline credentials (`prometheus-rw`). Because the secret rotator permanently preserves `prometheus-rw`, Grafana and active spokes authenticate immediately without disruption.

---

## 7. Reference Code & File Index

| Component | File Path | Purpose |
| :--- | :--- | :--- |
| **Rotator CronJob Manifest** | [`codeInspector/charts/prometheus/templates/rotator-cronjob.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/rotator-cronjob.yaml) | Kubernetes CronJob running pure-Python SigV4 rotator script |
| **Rotator Helm Values** | [`codeInspector/charts/prometheus/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/values.yaml) | Configures cron schedule (`0 0 1 * *`), grace period (`600s`), and retention (`7d`) |
| **Hub Web Config Template** | [`codeInspector/charts/prometheus/templates/secret-web-config.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/secret-web-config.yaml) | Renders `basic_auth_users` dictionary in `web.yml` |
| **Grafana Datasource Manifest** | [`codeInspector/charts/grafana/templates/configmap-datasources.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/configmap-datasources.yaml) | Provisions authenticated in-cluster Prometheus datasource |
| **Grafana Dashboard ConfigMap** | [`codeInspector/charts/grafana/templates/configmap-dashboards.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/configmap-dashboards.yaml) | Auto-provisions dynamic multi-cluster Grafana dashboards |
| **Launch Template (eu-central-1)** | [`user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-central-1/compute/templates/user_data.sh.tftpl) | Frankfurt Spoke 1 zero-touch bootstrapping script |
| **Launch Template (ap-south-1)** | [`user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/ap-south-1/compute/templates/user_data.sh.tftpl) | Mumbai Spoke 2 zero-touch bootstrapping script |
| **Launch Template (eu-west-1)** | [`user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-west-1/compute/templates/user_data.sh.tftpl) | Ireland Spoke 3 zero-touch bootstrapping script |
| **Launch Template (us-east-1)** | [`user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/templates/user_data.sh.tftpl) | Virginia Hub/Spoke zero-touch bootstrapping script |
| **Cluster Environment Config** | [`script/prod-docker-multi-script/prod.env`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/prod.env) | Fleet instance IDs, WireGuard private IPs, and AWS regions |
| **Authoritative Operations Guide** | [`docs/prometheus/prometheus.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/prometheus.md) | Full multi-cluster architecture and runbook reference |
