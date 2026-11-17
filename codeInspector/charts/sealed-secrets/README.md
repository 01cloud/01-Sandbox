# Sealed Secrets Helm Chart (`sealed-secrets`)

## 1. Overview & Problem Statement

Modern cloud-native engineering relies heavily on **GitOps** workflows—storing all infrastructure and application declarations in Git repositories (such as GitHub or GitLab) and automatically synchronizing them into Kubernetes clusters.

However, standard Kubernetes `Secret` objects store credentials using simple base64 encoding (e.g., `echo "password" | base64`), which is **obfuscation, not encryption**. Anyone with read access to the Git repository can decode base64 strings in seconds. Committing plaintext secrets to Git introduces severe security risks and violates compliance standards (SOC2, ISO27001, HIPAA).

**Bitnami Sealed Secrets** solves this dilemma through **Asymmetric Public-Key Cryptography**:
1. The controller runs inside the target Kubernetes cluster and holds a unique, protected **Private Key**.
2. Developers and CI/CD pipelines use the public key and the `kubeseal` CLI tool to encrypt plaintext secrets into a custom resource called a **`SealedSecret`**.
3. Because the `SealedSecret` is encrypted with asymmetric encryption, it is completely safe to commit to public or private Git repositories.
4. When the `SealedSecret` is applied to the cluster, the controller intercepts it, decrypts it using its internal private key, and generates a standard Kubernetes `Secret` in memory.

```
       [ Developer Workstation / CI Pipeline ]
                         │
                         │ Plaintext Secret + Cluster Public Key
                         ▼
                     kubeseal CLI
                         │
                         ▼ (Asymmetric Encryption)
                ┌──────────────────┐
                │   SealedSecret   │  <-- Safe to commit to Git!
                │ (Encrypted YAML) │
                └────────┬─────────┘
                         │
                         │ git push -> git pull / kubectl apply
                         ▼
      ┌────────────────────────────────────────────────────────┐
      │ Kubernetes Cluster                                     │
      │                                                        │
      │   ┌────────────────────────────────────────────────┐   │
      │   │ Sealed Secrets Controller Pod                  │   │
      │   │ (Holds Cluster Private Key)                    │   │
      │   └───────────────────────┬────────────────────────┘   │
      │                           │ Decrypts SealedSecret      │
      │                           ▼                            │
      │   ┌────────────────────────────────────────────────┐   │
      │   │ Native Kubernetes Secret (v1)                  │   │
      │   │ (Consumed by apiServer, PostgreSQL, etc.)      │   │
      │   └────────────────────────────────────────────────┘   │
      └────────────────────────────────────────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **Asymmetric Cryptography:** Uses a key pair:
  - **Public Key:** Safe to distribute globally; used by developers to encrypt secrets.
  - **Private Key:** Stored strictly inside the cluster; used by the controller to decrypt secrets.
- **Custom Resource Definition (`SealedSecret`):** Defines the schema (`bitnami.com/v1alpha1/SealedSecret`) allowing Kubernetes to accept encrypted manifests containing encrypted data blocks (`encryptedData`).
- **Scoping Modes in Sealed Secrets:**
  - `strict` (Default): The secret must be decrypted into the exact same name and namespace.
  - `namespace-wide`: Can be renamed, but must remain in the same namespace.
  - `cluster-wide`: Can be applied under any name in any namespace.
- **Unsealing & Self-Healing:** The controller watches for `SealedSecret` changes. If a user accidentally deletes the decrypted Kubernetes Secret, the controller automatically regenerates it from the `SealedSecret`.

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/sealed-secrets/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/) deploy:

### 3.1. [deployment.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/deployment.yaml)
- **Kind:** `Deployment` (`apps/v1`)
- **Metadata:** Name `sealed-secrets-controller` in `.Release.Namespace` (default: `kube-system`).
- **Functionality:**
  - Deploys the controller container (`docker.io/bitnamicharts/sealed-secrets`).
  - Reads/writes private encryption keys stored as Kubernetes TLS Secrets.
  - Automatically re-encrypts or generates new key pairs upon key expiration.
  - Exposes port 8080 for public certificate retrieval and health checks.

### 3.2. RBAC & Identity
- [clusterrole.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/clusterrole.yaml) & [clusterrolebinding.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/clusterrolebinding.yaml): Grants the controller permissions to create, update, and watch native `Secrets` across all cluster namespaces.
- [role.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/role.yaml) & [rolebinding.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/rolebinding.yaml): Restricts key-pair management permissions strictly to the controller's own namespace.
- [serviceaccount.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/serviceaccount.yaml): The identity under which the controller pod runs.

### 3.3. Networking & Monitoring
- [service.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/service.yaml): Exposes port 8080 (ClusterIP) so developers can fetch the public certificate via `kubeseal --fetch-cert`.
- [metrics-svc.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/metrics-svc.yaml) & [servicemonitor.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/servicemonitor.yaml): Exposes Prometheus metrics (total unseal operations, unseal errors, decryption latency).
- [networkpolicy.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/templates/networkpolicy.yaml): Enforces network isolation, allowing only Kubernetes API server traffic to reach the controller.

### 3.4. [crds/sealed-secrets.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/sealed-secrets/crds/sealed-secrets.yaml)
- Registers the custom resource definition `sealedsecrets.bitnami.com`.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fullnameOverride` | string | `""` | Overrides resource names for all chart objects. |
| `namespaceOverride` | string | `""` | Overrides the deployment namespace (defaults to `kube-system` or release namespace). |
| `secretName` | string | `""` | Name of an existing TLS secret containing a pre-existing key to use instead of auto-generating one. |
| `containerPorts.metrics` | integer | `8081` | Container port exposing Prometheus metrics. |
| `service.ports.http` | integer | `8080` | Service port for kubeseal public cert retrieval. |
| `metrics.enabled` | boolean | (chart default) | Enables the separate metrics Service for Prometheus scraping. |
| `metrics.serviceMonitor.enabled` | boolean | `false` | Enables Prometheus Operator `ServiceMonitor` for auto-discovery. |
| `networkPolicy.enabled` | boolean | `false` | Enables `NetworkPolicy` resource restricting controller ingress. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **GitOps Security Compliance:** In 01-Sandbox, database passwords, JWT private keys, SendGrid tokens, and E2B API keys are never stored in plaintext. Charts like `apiServer` support rendering `SealedSecret` resources, ensuring all repository artifacts are completely safe for version control.
- **Multi-Cluster Key Synchronization:** By exporting the sealed secrets private key and mirroring it across PrimaryHub and SecondaryHub, the same sealed secret manifest can be decrypted seamlessly across both AWS regions during disaster recovery.

---

## 6. Practical Debugging & Troubleshooting

### Fetch Public Certificate for Sealing
```bash
# Retrieve the cluster's public certificate using kubeseal
kubeseal --fetch-cert \
  --controller-name=sealed-secrets-controller \
  --controller-namespace=kube-system > pub-sealed-secrets.pem

# Encrypt a secret into a SealedSecret
kubectl create secret generic my-secret \
  --from-literal=password=SuperSecret123 \
  --dry-run=client -o yaml | \
  kubeseal --format=yaml --cert=pub-sealed-secrets.pem > my-sealed-secret.yaml
```

### Inspect Controller Logs & Unseal Status
```bash
# View controller logs to monitor decryption events
kubectl logs -n kube-system -l app.kubernetes.io/name=sealed-secrets -f --tail=100

# Inspect the status of a SealedSecret
kubectl describe sealedsecret my-sealed-secret -n sandbox
# Check that status indicates "Synced"
```

### Common Failure Modes
1. **Decryption Error (`Event: ErrUnsealFailed`):** The secret was sealed using a different public key or sealed with strict scope and renamed/moved to a different namespace. Re-seal using the current certificate.
2. **Missing Private Key After Cluster Rebuild:** If a cluster is re-installed without backing up the private key secret (`sealed-secrets-key*`), existing `SealedSecret` manifests cannot be decrypted. Always back up the active key secret to a secure vault.
3. **Controller Not Syncing:** Check if the controller's RBAC role allows creating secrets in the target namespace.
