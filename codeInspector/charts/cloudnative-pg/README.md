# CloudNativePG Operator Helm Chart (`cloudnative-pg`)

## 1. Overview & Problem Statement

Running enterprise stateful relational databases like **PostgreSQL** in containerized environments presents complex operational challenges:
- High availability (HA) requires continuous streaming replication between a primary node and multiple standby read replicas.
- Hardware or node failures require automated leader election and failover within seconds without data loss (split-brain prevention).
- Point-in-time recovery (PITR) requires continuous Write-Ahead Log (WAL) archiving to object storage (like AWS S3).
- Routine maintenance (minor version upgrades, vacuuming, rolling restarts) must execute without downtime.

The **CloudNativePG (CNPG)** operator solves these challenges by embedding PostgreSQL domain knowledge directly into Kubernetes via the **Kubernetes Operator Pattern**. It manages the full lifecycle of PostgreSQL database clusters natively, treating Postgres clusters as declarative Kubernetes Custom Resources (`Cluster`, `Backup`, `Pooler`).

```
┌────────────────────────────────────────────────────────┐
│ Kubernetes Control Plane                               │
│  - Custom Resource: postgresql.cnpg.io/v1/Cluster      │
└───────────────────────────┬────────────────────────────┘
                            │ Watches & Reconciles
                            ▼
┌────────────────────────────────────────────────────────┐
│ CloudNativePG Operator Pod                             │
│  - Validating & Mutating Admission Webhooks            │
│  - Automated Leader Election / Failover Logic          │
│  - Backup & WAL Archiving Coordinator                  │
└───────────────┬────────────────────────┬───────────────┘
                │ Controls               │ Controls
                ▼                        ▼
     ┌──────────────────────┐ ┌──────────────────────┐
     │  PostgreSQL Pod 1    │ │  PostgreSQL Pod 2    │
     │  (Primary / Leader)  │ │  (Standby Replica)   │
     └──────────┬───────────┘ └──────────▲───────────┘
                │ Streaming Replication  │
                └────────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **The Operator Pattern:** While standard Kubernetes controllers manage basic objects (like Deployments and Services), an *Operator* is an application-specific controller. CNPG extends the Kubernetes API with PostgreSQL-specific intelligence (handling failovers, replication lag, and pg_rewind).
- **Custom Resource Definitions (CRDs):** Registers new API endpoints:
  - `clusters.postgresql.cnpg.io`: Defines the database cluster topology, instance count, storage classes, and PostgreSQL configuration parameters.
  - `backups.postgresql.cnpg.io`: Triggers on-demand or scheduled database snapshots.
  - `poolers.postgresql.cnpg.io`: Deploys PgBouncer connection pooling instances.
- **Admission Webhooks (Mutating & Validating):**
  - *Mutating Webhook:* Injects default values, sidecars, and environment variables into database pods before they are created.
  - *Validating Webhook:* Rejects invalid configurations (e.g., trying to scale to 0 instances or setting conflicting replication modes) before etcd accepts the manifest.
- **PodMonitor (`monitoring.coreos.com`):** A Prometheus Operator primitive that automatically discovers and scrapes PostgreSQL performance metrics exported natively by CNPG pods.

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/cloudnative-pg/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cloudnative-pg/templates/) deploy:

### 3.1. [deployment.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cloudnative-pg/templates/deployment.yaml)
- **Kind:** `Deployment` (`apps/v1`)
- **Metadata:** Name `cnpg-cloudnative-pg` in the operator namespace (e.g., `cnpg-system`).
- **Functionality:**
  - Runs the operator controller manager (`ghcr.io/cloudnative-pg/cloudnative-pg:1.23.0`).
  - Sets up leader election across multiple operator replicas to ensure high availability.
  - Uses `--webhook-port={{ .Values.webhook.port }}` (default: `9443`) to serve admission webhooks.
  - Mounts webhook TLS certificates at `/run/secrets/cnpg.io/webhook`.
  - Configures three probe types (liveness, readiness, startup) all via HTTP GET `/readyz` on the `webhook-server` port (`9443`).

### 3.2. [rbac.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cloudnative-pg/templates/rbac.yaml)
- **Kind:** `ClusterRole`, `ClusterRoleBinding`, and `ServiceAccount`
- **Functionality:**
  - Grants the operator deep cluster permissions: managing Pods, PersistentVolumeClaims, Services, Endpoints, ConfigMaps, Secrets, and Custom Resources across all namespaces where PostgreSQL clusters are deployed.

### 3.3. Admission Webhooks
- [mutatingwebhookconfiguration.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cloudnative-pg/templates/mutatingwebhookconfiguration.yaml): Intercepts `Cluster` and `Backup` creation/modification to apply smart defaults.
- [validatingwebhookconfiguration.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cloudnative-pg/templates/validatingwebhookconfiguration.yaml): Validates database specifications and blocks catastrophic configuration errors.

### 3.4. Monitoring & Telemetry
- [monitoring-configmap.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cloudnative-pg/templates/monitoring-configmap.yaml):
  - Injects default Prometheus SQL metric queries (query duration, transaction counters, active locks, replication lag).
- [podmonitor.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cloudnative-pg/templates/podmonitor.yaml):
  - Tells Prometheus to scrape PostgreSQL metrics on port 9187 directly from managed database pods.

### 3.5. [crds/crds.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cloudnative-pg/templates/crds/crds.yaml)
- Registers all custom schemas (`postgresql.cnpg.io`) required by the operator into the Kubernetes cluster.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `replicaCount` | integer | `1` | Number of operator manager pod replicas. |
| `image.repository` | string | `"ghcr.io/cloudnative-pg/cloudnative-pg"` | Container image repository for the operator. |
| `image.tag` | string | `"1.23.0"` | CNPG operator version tag. |
| `crds.create` | boolean | `false` | When true, installs CRDs via Helm templates (usually managed via pre-install hooks or CRD directories). |
| `config.data` | object | `{}` | Global operator settings (e.g., custom webhook cert providers, default image catalogs). |
| `webhook.port` | integer | `9443` | Port the webhook server listens on. Used for `--webhook-port` and health probes. |
| `monitoring.podMonitorEnabled` | boolean | `false` | Enables automatic deployment of Prometheus `PodMonitor`. |
| `resources` | object | `{}` | CPU and memory resource requests/limits for the operator pod. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Backend Persistence:** In 01-Sandbox, CNPG provisions and manages the database cluster used by the `apiServer` to store user authentication keys, quota reservations, and sandbox execution logs.
- **Cross-Region Replication:** In the multi-region topology:
  - **PrimaryHub (us-east-1):** Runs the CNPG primary cluster with instances serving write traffic.
  - **SecondaryHub (eu-west-1):** Runs a CNPG standby replica cluster synchronizing WAL logs over the WireGuard mesh (`10.99.0.x`). If PrimaryHub suffers an AWS outage, SecondaryHub is promoted to primary.
- **Observability:** Metrics collected by the CNPG operator are scraped by Prometheus and forwarded to Cortex for long-term audit storage.

---

## 6. Practical Debugging & Troubleshooting

### Check Operator Status
```bash
# Check if the operator deployment is healthy
kubectl get deployment -n cnpg-system
kubectl logs -n cnpg-system -l app.kubernetes.io/name=cloudnative-pg -f --tail=100
```

### Inspect Database Clusters
```bash
# View all PostgreSQL clusters across namespaces
kubectl get clusters.postgresql.cnpg.io -A

# Get detailed cluster status (Primary node, Standby nodes, replication lag)
kubectl get cluster postgresql-primary -n sandbox -o wide
kubectl describe cluster postgresql-primary -n sandbox
```

### Check Database Pod Logs & Replication
```bash
# View database server logs on the primary instance
kubectl logs -n sandbox postgresql-primary-1 -c postgres -f --tail=100

# Verify streaming replication lag via cnpg status
kubectl cnpg status postgresql-primary -n sandbox
```

### Common Failure Modes
1. **Webhook Timeout / Connection Refused:** The API server cannot reach the CNPG webhook on port 9443. Check that network policies allow traffic from the control plane to the operator pod, or verify webhook certificates.
2. **Cluster In "Pending" State:** Pods cannot be scheduled due to storage constraints. Ensure a matching `StorageClass` is available and has sufficient volume capacity.
3. **Replication Broken / Out of Sync:** Check network routing over WireGuard. Ensure port 5432 is open between primary and standby clusters and user credentials match.
