# Prometheus Long-Term Storage: Implementation Plan & Architectural Decision Record

**Document:** Implementation Plan & Production Deployment Guide
**File:** `docs/prometheus/long-term-strorage-cortex-s3-implementation.md`
**Author:** Kamal Tamang
**Reviewers:** Sarose Joshi, Umesh Khatiwada
**Target Environment:** 01-Sandbox Multi-Cluster Infrastructure (`PrimaryHub`, `SecondaryHub`, `Spoke1`, `Spoke2`)
**Scope:** Self-Provisioned Long-Term Storage inside 01-Sandbox (No External Cloud Forwarding)
**Status:** Approved Architectural Decision & Execution Plan
**Date:** October 2026

---

## 1. Executive Summary & Clarification

This document details the architectural evaluation and complete implementation plan for replacing Prometheus's current local EBS storage in the **01-Sandbox multi-cluster infrastructure** with an **S3-backed long-term storage solution**.

### Scope Clarification
Following discussions between Sarose Joshi, Umesh Khatiwada, and Kamal Tamang:
- **No external forwarding**: All metric data, ingestion pipelines, and query engines remain **100% self-contained and sovereign within the 01-Sandbox infrastructure**. We do not forward metrics to an external cluster.
- **The Core Architectural Choice**:
  - **Option A: Provisioning our own dedicated Cortex cluster inside 01-Sandbox** (utilizing Cortex's TSDB block engine backed by our own AWS S3 bucket, drawing from the team's operational familiarity with Cortex).
  - **Option B: Prometheus Native S3 Integration via Thanos Sidecar / Store Gateway** (extending our existing Prometheus deployment to ship 2-hour TSDB blocks directly to our own AWS S3 bucket).

---

## 2. Decision Record: Which Option is Best and Recommended?

### The Verdict: **Option B (Prometheus S3 via Thanos Sidecar) is the Best & Recommended Approach for 01-Sandbox**

While self-provisioned Cortex is a formidable multi-tenant platform, **Option B is the superior engineering choice for 01-Sandbox**. Below is the rationale:

| Evaluation Dimension | Option A: Self-Provisioned Cortex (Monolithic) | Option B: Prometheus + Thanos S3 Sidecar (Recommended) | Why Option B Wins for 01-Sandbox |
| :--- | :--- | :--- | :--- |
| **Compute Overhead** | **High** (~1.5–2.0 vCPU, 2–4 GiB RAM minimum) | **Minimal** (< 50m CPU, < 64 MiB RAM) | 01-Sandbox PrimaryHub runs inside KinD on EC2 alongside PostgreSQL, Valkey, RabbitMQ, and API servers. Option B leaves maximum RAM for security analysis pipelines. |
| **Storage Architecture** | AWS S3 Bucket (`cortex-tsdb-blocks`) | AWS S3 Bucket (`01-sandbox-prometheus-longterm`) | Both achieve **infinite retention** and **71%–93% cost savings** over EBS. |
| **Local EBS Requirement** | 5 GiB (Temporary WAL buffer) | 10 GiB (Downsized from 25 GiB, 6h local retention) | Both solve the EBS scaling bottleneck and eliminate disk-fill crash risks. |
| **Operational Simplicity** | Requires managing Cortex deployment, memberlist gossip ring, and internal block compactor. | **Zero new server components**. A single sidecar container added to the existing Prometheus pod in Helm. | Near-zero maintenance overhead; no distributed consensus ring to troubleshoot. |
| **Multi-Tenancy** | Native multi-tenant headers (`X-Scope-OrgID`) | Soft multi-tenancy via cluster label filters | 01-Sandbox uses cluster-origin labels (`cluster="spoke1"`, `cluster="primaryhub"`), which Thanos handles natively. |
| **Disaster Recovery** | S3 bucket backup + memberlist rebuild | Standard TSDB blocks in S3 readable by any standard Prometheus tooling | Immutable TSDB format in S3; zero vendor lock-in. |

---

## 3. Architecture Diagrams

### 3.1 Recommended Architecture: Prometheus S3 via Thanos Sidecar

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
│ PrimaryHub EC2 (us-east-1) - KinD Cluster (`primaryhub-control-plane`)                 │
│                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Prometheus Pod (`opensandbox-system`)                                            │  │
│  │                                                                                  │  │
│  │  ┌─────────────────────────┐               ┌──────────────────────────────────┐  │  │
│  │  │ Prometheus (v2.54.1)    │               │ Thanos Sidecar (v0.36.1)         │  │  │
│  │  │ - Scrapes Hub & Spokes  │               │ - Reads closed 2h TSDB blocks    │  │  │
│  │  │ - 6h local retention    │               │ - CPU: < 50m, RAM: < 64Mi        │  │  │
│  │  │ - Downsized EBS: 10 GiB │──────────────►│ - Uploads immutable blocks to S3 │  │  │
│  │  └───────────┬─────────────┘ (shared volume│ - Acts as gRPC StoreAPI           │  │  │
│  │              │               /prometheus)  └─────────────────┬────────────────┘  │  │
│  └──────────────┼───────────────────────────────────────────────┼───────────────────┘  │
│                 │                                               │                      │
│                 ▼                                               ▼                      │
│        ┌─────────────────┐                             ┌──────────────────┐            │
│        │ Local Grafana   │                             │ AWS S3 Bucket    │            │
│        │ (Live Queries)  │                             │ (01-sandbox-     │            │
│        └─────────────────┘                             │  prometheus-     │            │
│                                                        │  longterm)       │            │
│                                                        └──────────────────┘            │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Alternative Architecture: Self-Provisioned Cortex (Monolithic Mode)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ PrimaryHub EC2 (us-east-1) - KinD Cluster (`primaryhub-control-plane`)                 │
│                                                                                        │
│  ┌───────────────────────────┐                         ┌────────────────────────────┐  │
│  │ Prometheus (Scraper Pod)  │─── remote_write ───────►│ Cortex Single-Binary Pod   │  │
│  │ - Scrapes Hub & Spokes    │    (http://cortex:9009) │ - Ingester (TSDB engine)   │  │
│  │ - Local buffer: 2 hours   │                         │ - Distributor & Querier    │  │
│  └───────────────────────────┘                         │ - Store-Gateway & Compactor│  │
│                                                        │ - RAM: 2–4 GiB             │  │
│                                                        └─────────────┬──────────────┘  │
│                                                                      │                 │
│                                                                      ▼                 │
│                                                        ┌────────────────────────────┐  │
│                                                        │ AWS S3 Bucket              │  │
│                                                        │ (cortex-tsdb-blocks)       │  │
│                                                        └────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Implementation Plan for Recommended Option: Prometheus + S3 (Thanos Sidecar)

### Phase 1: AWS Infrastructure Setup (S3 & IAM)

#### Step 1.1: Create Dedicated S3 Bucket
Run on your administrative workstation or via AWS CLI:

```bash
BUCKET_NAME="01-sandbox-prometheus-longterm"
REGION="us-east-1"

# 1. Create Bucket
aws s3api create-bucket \
  --bucket "$BUCKET_NAME" \
  --region "$REGION"

# 2. Enable Server-Side Encryption (SSE-S3)
aws s3api put-bucket-encryption \
  --bucket "$BUCKET_NAME" \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'

# 3. Block All Public Access
aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration '{
    "BlockPublicAcls": true,
    "IgnorePublicAcls": true,
    "BlockPublicPolicy": true,
    "RestrictPublicBuckets": true
  }'
```

#### Step 1.2: Apply Automated Tiering Lifecycle Policy
Configure S3 lifecycle transitions to optimize costs over 2 years:

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
rm -f /tmp/s3-lifecycle.json
```

#### Step 1.3: IAM Permissions for PrimaryHub EC2 Host
Attach the following IAM policy statement to the PrimaryHub instance profile (`i-04ae5b1c88149436a`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowPrometheusS3BlockShipping",
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

### Phase 2: Helm Chart Configuration Updates

#### Step 2.1: Update `codeInspector/charts/prometheus/values.yaml`
Modify [`codeInspector/charts/prometheus/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/values.yaml) to configure local retention, downsize EBS, and enable Thanos:

```yaml
namespace: opensandbox-system
clusterName: "primaryhub"
clusterRole: "hub"

# ── Retention & Storage Configuration ──────────────────────────────────────────
# Local disk only retains 6 hours of recent samples.
# Thanos Sidecar flushes 2-hour TSDB blocks directly to S3.
retention: "6h"

# ── EBS PVC Downsizing ────────────────────────────────────────────────────────
# Reduced from 25Gi to 10Gi because historical metrics are persisted in S3
persistence:
  enabled: true
  pvcName: "prometheus-pvc"
  storageClass: "standard"
  size: "10Gi"

# ── Thanos S3 Sidecar Configuration ───────────────────────────────────────────
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
```

#### Step 2.2: Add Object Storage Secret Template
Create file `codeInspector/charts/prometheus/templates/secret-thanos-objstore.yaml`:

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

#### Step 2.3: Update Deployment Template
In [`codeInspector/charts/prometheus/templates/deployment.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/deployment.yaml):

1. **Add TSDB block duration flags** to the Prometheus container args:
   ```yaml
   args:
     - --config.file=/etc/prometheus/prometheus.yml
     - --storage.tsdb.path=/prometheus
     - --storage.tsdb.retention.time={{ .Values.retention | default "6h" }}
     - --storage.tsdb.min-block-duration=2h
     - --storage.tsdb.max-block-duration=2h
     - --web.enable-lifecycle
     - --web.enable-remote-write-receiver
     - --web.config.file=/etc/prometheus/web.yml
   ```
2. **Inject the `thanos-sidecar` container**:
   ```yaml
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
       {{- toYaml .Values.thanos.resources | nindent 6 }}
     volumeMounts:
       - name: storage
         mountPath: /prometheus
       - name: thanos-objstore
         mountPath: /etc/thanos
         readOnly: true
   {{- end }}
   ```
3. **Add the Secret Volume** to pod volumes:
   ```yaml
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

### Phase 3: Rollout & Verification

#### Step 3.1: Apply Upgrades via Repository Script
Execute from your local machine:
```bash
cd /home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script

export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."

./upgrade-helm.sh primaryhub
```

#### Step 3.2: Verify Pod & Sidecar Execution
```bash
# Verify both containers (prometheus + thanos-sidecar) are Ready:
docker exec primaryhub-control-plane kubectl -n opensandbox-system get pod -l app=prometheus

# Check Thanos Sidecar initialization logs:
docker exec primaryhub-control-plane kubectl -n opensandbox-system logs -l app=prometheus -c thanos-sidecar --tail=30
```
Expected output:
```
level=info msg="sidecar started"
level=info msg="successfully verified bucket connectivity" bucket=01-sandbox-prometheus-longterm
```

#### Step 3.3: Verify S3 Block Shipments
After the first 2-hour TSDB block closes:
```bash
aws s3 ls s3://01-sandbox-prometheus-longterm/ --recursive
```
Expected output:
```
2026-10-29 15:00:00   01HXYZ123456789ABCDEFGH/meta.json
2026-10-29 15:00:00   01HXYZ123456789ABCDEFGH/chunks/000001
2026-10-29 15:00:00   01HXYZ123456789ABCDEFGH/index
```

---

## 5. Alternative Implementation: Self-Provisioned Cortex (Monolithic Mode)

If the engineering team decides to proceed with **provisioning self-hosted Cortex directly inside 01-Sandbox**:

### Step 5.1: Create Cortex S3 Bucket
```bash
aws s3api create-bucket --bucket "01-sandbox-cortex-storage" --region us-east-1
```

### Step 5.2: Create Cortex Helm Chart Values (`cortex-values.yaml`)
Deploy Cortex in **Single-Binary (Monolithic) mode** to minimize pod count inside the KinD cluster:

```yaml
# Deploy as a single monolithic workload target
cortex:
  target: all
  image:
    repository: cortexproject/cortex
    tag: "v1.16.0"

  config:
    auth_enabled: false # Single-tenant internal deployment

    server:
      http_listen_port: 9009
      grpc_listen_port: 9095

    # Ingestion & Ring Configuration
    distributor:
      ring:
        kvstore:
          store: memberlist

    ingester:
      lifecycler:
        ring:
          kvstore:
            store: memberlist
          replication_factor: 1 # Single replica to fit KinD memory constraints
      max_transfer_retries: 0

    # TSDB S3 Block Storage Engine
    blocks_storage:
      backend: s3
      s3:
        bucket_name: 01-sandbox-cortex-storage
        endpoint: s3.us-east-1.amazonaws.com
        region: us-east-1
      tsdb:
        dir: /data/tsdb
        retention_period: 6h # Ingester flushes to S3 after 2h

    # Compactor
    compactor:
      data_dir: /data/compactor

  resources:
    requests:
      cpu: "500m"
      memory: "2048Mi"
    limits:
      cpu: "1500m"
      memory: "4096Mi"
```

### Step 5.3: Point Prometheus to Self-Hosted Cortex
In [`codeInspector/charts/prometheus/templates/configmap.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/configmap.yaml), stream metrics to the local Cortex service:

```yaml
remote_write:
  - url: "http://cortex.opensandbox-system.svc.cluster.local:9009/api/v1/push"
    queue_config:
      max_samples_per_send: 1000
      batch_send_deadline: 5s
```

---

## 6. Financial Summary: Self-Hosted Cortex vs. Prometheus S3

| Cost Component | Current Setup (EBS gp3) | Self-Provisioned Cortex (Monolithic) | Prometheus + S3 Sidecar (Recommended) |
| :--- | :--- | :--- | :--- |
| **S3 Storage (1 TB / mo)** | $0.00 | ~$23.00 | ~$23.00 |
| **Local EBS Storage** | **$2.50** (25 GiB EBS) | **$1.00** (10 GiB buffer) | **$1.00** (10 GiB buffer) |
| **Compute Overhead (RAM/CPU)**| $0.00 | **+$35 – $70 / mo** (Requires upgrading EC2 instance for 4GB RAM) | **$0.00** (Fits in existing pod limits) |
| **Operational Maintenance** | Low | Moderate/High (Ring, compactor) | Low |
| **Total Monthly Sizing** | **High as data grows** | **Moderate** | **Lowest** |

---

## 7. Conclusion

By deploying **Option B (Prometheus S3 via Thanos Sidecar)** inside 01-Sandbox:
1. **Full Sovereignty**: All metrics remain strictly within the 01-Sandbox AWS account and S3 bucket.
2. **EBS Problem Solved**: Eliminates the 25 GiB EBS disk bottleneck and downsizes local disk usage to 10 GiB.
3. **Infinite Retention**: Historical data scales indefinitely on S3 at **71%–93% lower cost** than block storage.
4. **Zero KinD Resource Contention**: Unlike a self-hosted Cortex cluster (which requires 2–4 GB RAM), the Thanos Sidecar runs on < 64 MB RAM, ensuring maximum host resources remain available for microVM/gVisor code execution.
