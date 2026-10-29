# Prometheus Long-Term Storage: Implementation Plan & Architectural Decision Record

**Document:** Implementation Plan & Production Deployment Guide
**File:** `docs/prometheus/long-term-strorage-cortex-s3-implementation.md`
**Author:** Kamal Tamang
**Reviewers:** Sarose Joshi, Umesh Khatiwada
**Target Environment:** 01-Sandbox Multi-Cluster Infrastructure (`PrimaryHub`, `SecondaryHub`, `Spoke1`, `Spoke2`) & 01 Cloud
**Status:** Approved Architectural Decision & Execution Plan
**Date:** October 2026

---

## 1. Executive Verdict: The Best & Recommended Approach

Following technical, financial, and architectural analysis across 01-Sandbox and 01 Cloud, the recommended strategy is the **Hybrid Dual-Tier Observability Architecture**:

1. **Tier 1 (Core Storage in 01-Sandbox): Prometheus + Thanos Sidecar S3 Block Shipping**
   - **Why it is the winning choice**:
     - Replaces expensive **25 GiB EBS gp3 volumes ($0.08–$0.10/GB-month)** with infinite **AWS S3 object storage ($0.023/GB-month)** and S3 Glacier Instant Retrieval ($0.004/GB-month), yielding a **71%–95% raw storage cost reduction**.
     - **Extremely Low Compute Footprint**: The Thanos Sidecar adds only **< 50m CPU and < 64Mi RAM** to the existing Prometheus pod. It runs easily inside our existing container cgroup limits (`500m / 512Mi`).
     - **Non-Invasive to Existing KinD Setup**: No complex distributed consensus rings, memberlist gossip networks, or dedicated caching tiers need to run inside the PrimaryHub KinD cluster.
     - **Standard TSDB Block Format**: Metrics in S3 are standard Prometheus TSDB blocks compatible with any Prometheus/Thanos tooling.
2. **Tier 2 (Enterprise Federation to 01 Cloud): Selective `remote_write` to 01 Cloud Cortex**
   - 01-Sandbox forwards aggregated cluster telemetry and security scanner execution metrics to 01 Cloud’s established Cortex cluster using `remote_write` with tenant header `X-Scope-OrgID: "01-sandbox"`.
   - **Zero Cortex microservices are deployed inside 01-Sandbox**. 01 Cloud handles cross-platform multi-tenancy and global executive dashboards.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        01-SANDBOX MULTI-CLUSTER INFRASTRUCTURE                         │
│                                                                                        │
│   [Spoke 1 (eu-central-1)]    [Spoke 2 (ap-south-1)]    [SecondaryHub (us-west-2)]     │
│   gVisor / Kata Pods          gVisor / Kata Pods        Standby Workloads              │
└──────────────┬──────────────────────────┬──────────────────────────┬───────────────────┘
               │                          │                          │
               │ WireGuard (10.99.0.0/24) │                          │
               └──────────────────────────┼──────────────────────────┘
                                          ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ PrimaryHub (us-east-1) - KinD Cluster (`primaryhub-control-plane`)                     │
│                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Prometheus Pod (`opensandbox-system`)                                            │  │
│  │                                                                                  │  │
│  │  ┌─────────────────────────┐               ┌──────────────────────────────────┐  │  │
│  │  │ Prometheus (v2.54.1)    │               │ Thanos Sidecar (v0.36.1)         │  │  │
│  │  │ - 4-6h local retention  │               │ - Ships 2h TSDB blocks to S3     │  │  │
│  │  │ - Downsized EBS: 10 GiB │──────────────►│ - CPU: < 50m, RAM: < 64Mi        │  │  │
│  │  └───────────┬─────────────┘ (shared volume│ - Provides gRPC StoreAPI         │  │  │
│  │              │               /prometheus)  └─────────────────┬────────────────┘  │  │
│  └──────────────┼───────────────────────────────────────────────┼───────────────────┘  │
│                 │                                               │                      │
│                 ▼                                               ▼                      │
│        ┌─────────────────┐                             ┌──────────────────┐            │
│        │ Local Grafana   │                             │ AWS S3 Bucket    │            │
│        │ (Live Queries)  │                             │ (01-sandbox-     │            │
│        └─────────────────┘                             │  prometheus-     │            │
│                 │                                      │  longterm)       │            │
│                 │                                      └────────┬─────────┘            │
│                 │ (Tier 2: remote_write)                        │                      │
└─────────────────┼───────────────────────────────────────────────┼──────────────────────┘
                  ▼                                               ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 01 CLOUD ENTERPRISE INFRASTRUCTURE                                                    │
│                                                                                        │
│        ┌────────────────────────────────┐       Query Historical Blocks                │
│        │ 01 Cloud Cortex Cluster        │◄─────────────────────────────────────────────┘
│        │ (Multi-Tenant, Central Auth)   │
│        └────────────────────────────────┘
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Infrastructure Prerequisites & AWS Setup

### 2.1 AWS S3 Bucket Creation
Create a private S3 bucket in `us-east-1` (same region as PrimaryHub) with Server-Side Encryption and strict access blocking:

```bash
# Bucket name
BUCKET_NAME="01-sandbox-prometheus-longterm"
REGION="us-east-1"

# Create bucket
aws s3api create-bucket \
  --bucket "$BUCKET_NAME" \
  --region "$REGION"

# Enable Default Encryption (SSE-S3)
aws s3api put-bucket-encryption \
  --bucket "$BUCKET_NAME" \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'

# Block all public access
aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration '{
    "BlockPublicAcls": true,
    "IgnorePublicAcls": true,
    "BlockPublicPolicy": true,
    "RestrictPublicBuckets": true
  }'
```

### 2.2 S3 Lifecycle Policy (Automated Cost Optimization)
Transition older TSDB blocks to colder storage tiers to drive storage cost down to **$0.004/GB-month**:

```bash
cat << 'EOF' > /tmp/s3-lifecycle.json
{
  "Rules": [
    {
      "ID": "PrometheusTSDBLifecycle",
      "Status": "Enabled",
      "Filter": {
        "Prefix": ""
      },
      "Transitions": [
        {
          "Days": 30,
          "StorageClass": "STANDARD_IA"
        },
        {
          "Days": 90,
          "StorageClass": "GLACIER_IR"
        }
      ],
      "Expiration": {
        "Days": 730
      }
    }
  ]
}
EOF

aws s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET_NAME" \
  --lifecycle-configuration file:///tmp/s3-lifecycle.json
```

### 2.3 IAM Policy for PrimaryHub EC2 Instance Profile
PrimaryHub already runs with an IAM Instance Profile. Attach the following least-privilege policy to allow the Thanos Sidecar to upload TSDB blocks:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowPrometheusS3LongTermStorage",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::01-sandbox-prometheus-longterm",
        "arn:aws:s3:::01-sandbox-prometheus-longterm/*"
      ]
    }
  ]
}
```

---

## 3. Step-by-Step Codebase Implementation

### Step 3.1: Update Prometheus Values File
In [`codeInspector/charts/prometheus/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/values.yaml), configure Thanos Sidecar, downsize the EBS PVC, and adjust retention:

```yaml
namespace: opensandbox-system
clusterName: "primaryhub"
clusterRole: "hub"

# ── Local TSDB Retention ───────────────────────────────────────────────────────
# Local disk holds only 6 hours of recent samples.
# Thanos Sidecar flushes immutable 2-hour blocks directly to S3.
retention: "6h"

# ── Local Storage Downsizing ──────────────────────────────────────────────────
# Downsized from 25Gi to 10Gi because historical data lives in S3
persistence:
  enabled: true
  pvcName: "prometheus-pvc"
  storageClass: "standard"
  size: "10Gi"

# ── Thanos S3 Sidecar Integration ─────────────────────────────────────────────
thanos:
  enabled: true
  image:
    repository: quay.io/thanos/thanos
    tag: "v0.36.1"
    pullPolicy: IfNotPresent
  objstoreSecretName: "thanos-objstore-secret"
  bucket: "01-sandbox-prometheus-longterm"
  region: "us-east-1"
  resources:
    requests:
      cpu: "30m"
      memory: "64Mi"
    limits:
      cpu: "100m"
      memory: "128Mi"

# ── Tier 2: 01 Cloud Cortex Forwarding ─────────────────────────────────────────
remoteWrite:
  enabled: false # Toggle true when federating to 01 Cloud
  url: "https://cortex.01cloud.com/api/v1/push"
  tenantId: "01-sandbox"
  secretName: "cortex-remote-write-auth"
```

---

### Step 3.2: Create Thanos Object Store Secret Template
Create a new Helm template at [`codeInspector/charts/prometheus/templates/secret-thanos-objstore.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/secret-thanos-objstore.yaml):

```yaml
{{- if .Values.thanos.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.thanos.objstoreSecretName }}
  namespace: {{ .Values.namespace }}
  labels:
    app: prometheus
    tier: monitoring
type: Opaque
stringData:
  thanos.yaml: |
    type: S3
    config:
      bucket: {{ .Values.thanos.bucket }}
      endpoint: s3.{{ .Values.thanos.region }}.amazonaws.com
      region: {{ .Values.thanos.region }}
      insecure: false
      sse_config:
        type: "SSE-S3"
{{- end }}
```

---

### Step 3.3: Update Prometheus Deployment Template
In [`codeInspector/charts/prometheus/templates/deployment.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/deployment.yaml), add the required TSDB 2-hour block flags to Prometheus and inject the Thanos Sidecar container:

```yaml
# ── Inside Prometheus Container Args ──
args:
  - --config.file=/etc/prometheus/prometheus.yml
  - --storage.tsdb.path=/prometheus
  - --storage.tsdb.retention.time={{ .Values.retention | default "6h" }}
  - --storage.tsdb.min-block-duration=2h
  - --storage.tsdb.max-block-duration=2h
  - --web.enable-lifecycle
  - --web.enable-remote-write-receiver
  - --web.config.file=/etc/prometheus/web.yml

# ── Sidecar Container Definition ──
{{- if .Values.thanos.enabled }}
- name: thanos-sidecar
  image: "{{ .Values.thanos.image.repository }}:{{ .Values.thanos.image.tag }}"
  imagePullPolicy: {{ .Values.thanos.image.pullPolicy }}
  args:
    - sidecar
    - --tsdb.path=/prometheus
    - --prometheus.url=http://127.0.0.1:9090
    - --objstore.config-file=/etc/thanos/thanos.yaml
    - --http-address=0.0.0.0:10902
    - --grpc-address=0.0.0.0:10901
  ports:
    - name: http-sidecar
      containerPort: 10902
    - name: grpc-sidecar
      containerPort: 10901
  resources:
    {{- toYaml .Values.thanos.resources | nindent 4 }}
  volumeMounts:
    - name: storage
      mountPath: /prometheus
    - name: thanos-objstore
      mountPath: /etc/thanos
      readOnly: true
{{- end }}

# ── Volume Definition in Pod Spec ──
volumes:
  - name: storage
    persistentVolumeClaim:
      claimName: {{ .Values.persistence.pvcName }}
  {{- if .Values.thanos.enabled }}
  - name: thanos-objstore
    secret:
      secretName: {{ .Values.thanos.objstoreSecretName }}
  {{- end }}
```

---

### Step 3.4: Configure Tier 2 (01 Cloud Cortex Federation)
When ready to stream metrics into 01 Cloud Cortex, append the remote-write block to the Prometheus configuration template in [`codeInspector/charts/prometheus/templates/configmap.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/configmap.yaml):

```yaml
{{- if .Values.remoteWrite.enabled }}
remote_write:
  - url: "{{ .Values.remoteWrite.url }}"
    headers:
      X-Scope-OrgID: "{{ .Values.remoteWrite.tenantId }}"
    basic_auth:
      username: "{{ .Values.remoteWrite.username }}"
      password: "{{ .Values.remoteWrite.password }}"
    queue_config:
      max_samples_per_send: 1000
      batch_send_deadline: 5s
      max_shards: 5
      min_shards: 1
{{- end }}
```

---

## 4. Rollout & Upgrade Execution Procedure

Deploy the upgraded chart safely using the repository's dedicated upgrade script:

```bash
cd /home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script

# Step 1: Ensure active AWS credentials are set
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."

# Step 2: Apply the safe Helm upgrade to PrimaryHub
./upgrade-helm.sh primaryhub
```

---

## 5. Verification & Testing Playbook

### 5.1 Verification Checklist
| Step | Action | Expected Result |
| :---: | :--- | :--- |
| **1** | `kubectl -n opensandbox-system get pods -l app=prometheus` | Pod shows `2/2` Ready (`prometheus` + `thanos-sidecar`). |
| **2** | `kubectl -n opensandbox-system logs deploy/prometheus -c thanos-sidecar` | Logs show `sidecar started` and `configured objstore S3 bucket`. |
| **3** | Wait for 2 hours (or check initial TSDB block closure) | Sidecar logs `uploaded block <ULID> to S3 bucket`. |
| **4** | `aws s3 ls s3://01-sandbox-prometheus-longterm/` | TSDB block directories containing `meta.json` and chunk files. |
| **5** | Query Grafana Dashboard | Recent metrics (< 2h) load instantly from memory; historical metrics load seamlessly. |

### 5.2 Verification Commands
```bash
# Check pod status and containers
docker exec primaryhub-control-plane kubectl -n opensandbox-system get pod -l app=prometheus

# Inspect sidecar logs for S3 block uploads
docker exec primaryhub-control-plane kubectl -n opensandbox-system logs -l app=prometheus -c thanos-sidecar --tail=50

# Inspect S3 bucket contents from host
aws s3 ls s3://01-sandbox-prometheus-longterm/ --recursive --human-readable
```

---

## 6. Financial Impact & Resource Sizing

### 6.1 Storage Cost Comparison (Monthly)

| Metric Volume / Retention | Current EBS (`gp3`) Cost | Proposed S3 + Glacier Cost | Net Monthly Savings | % Reduction |
| :--- | :--- | :--- | :--- | :---: |
| **500 GiB (30 Days)** | $40.00 | $11.50 | **$28.50** | **71.2%** |
| **1.5 TiB (90 Days)** | $120.00 | $18.75 | **$101.25** | **84.4%** |
| **3.0 TiB (180 Days)** | $240.00 | $26.25 | **$213.75** | **89.1%** |
| **6.0 TiB (365 Days)** | $480.00 | $35.00 | **$445.00** | **92.7%** |

### 6.2 PrimaryHub Node Resource Delta
- **CPU**: `+30m` (negligible impact on EC2 host).
- **RAM**: `+64Mi` (Prometheus retention lowered from 7d to 6h reduces local memory footprint by ~150Mi, resulting in a **net negative memory delta**).
- **EBS Volume**: Reduced from **25 GiB down to 10 GiB**, eliminating disk-fill crash risks.

---

## 7. Rollback Plan

If unexpected S3 network throttles or sidecar issues occur, rollback is simple:
1. Set `thanos.enabled: false` and `retention: "7d"` in [`values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/values.yaml).
2. Re-run `./upgrade-helm.sh primaryhub`.
3. The pod restarts as a single-container Prometheus instance without altering the persistent volume.
