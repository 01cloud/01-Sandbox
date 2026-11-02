# Live Demo Walkthrough: Push-Based Prometheus Secret Rotation via OCM

This guide provides an end-to-end operational script to demonstrate automated, zero-downtime secret rotation across the multi-cluster Prometheus fleet.

---

## Architecture Recap (What is Happening Behind the Scenes)

1. **Trigger**: An on-demand or cron-scheduled Kubernetes Job (`prometheus-secret-rotator`) starts.
2. **Phase 1 (Dual-Auth Activation)**: Generates a new credential pair (`prom-rw-<timestamp>` and random 24-character password with standard Bcrypt `$2b$` hash). Patches PrimaryHub's `secret/prometheus-web-config` so **both old and new credentials are simultaneously valid**. PrimaryHub Prometheus reloads in `< 15ms` via SIGHUP without restarting.
3. **Phase 2 (Autonomous Edge Distribution via OCM)**: Discovers all registered spoke clusters (`spoke1`...`spoke5`) and applies an Open Cluster Management (OCM) `ManifestWork` (`spoke-prometheus-auth`) containing the secret payload over WireGuard.
4. **Phase 3 (Grace Period & Edge Cutover)**: The spoke `klusterlet` applies the secret to the spoke's `monitoring` namespace. The edge scraper hot-reloads and cuts over to the new credentials with **zero 401 errors and zero dropped metrics**.
5. **Phase 4 (Legacy Revocation)**: After a 10-minute overlap window (`GRACE_PERIOD_SECONDS=600`), the rotator prunes the old credential from `web.yml` on PrimaryHub and triggers a final SIGHUP reload.

---

## Live Demo Script (Step-by-Step)

### Step 1: Inspect the Baseline (Before Rotation)
Show your audience the current single active user on PrimaryHub:

```bash
kubectl -n opensandbox-system get secret prometheus-web-config \
  -o jsonpath='{.data.web\.yml}' | base64 -d
```
> **Talking Point:** *"Notice that PrimaryHub Prometheus currently has one active basic auth user. Now, we are going to rotate this credential across all edge clusters without restarting Prometheus or dropping any metrics."*

---

### Step 2: Trigger the Automated Rotation Job
Launch a new rotation job from the CronJob template:

```bash
kubectl -n opensandbox-system create job \
  --from=cronjob/prometheus-secret-rotator \
  demo-rotation-$(date +%s)
```
> **Talking Point:** *"This creates an ephemeral Kubernetes Job using our CronJob template. In production, this executes automatically every 30 days at midnight UTC, but it can also be triggered instantly during an on-call response."*

---

### Step 3: Stream the Live Execution Logs
Watch the rotator execute in real time:

```bash
kubectl -n opensandbox-system logs -l app=prometheus-secret-rotator -f
```
**Look for these lines in the terminal:**
1. `[*] Generated new credential ID: prom-rw-...`
2. `[+] PrimaryHub secret patched with dual users.`
3. `[+] PrimaryHub Prometheus reloaded successfully via SIGHUP. Dual-auth is now LIVE.`
4. `[*] Phase 2: Distributing new credentials to edge spoke clusters via OCM...`
5. `    -> Updating Spoke: spoke1... [OK] Applied ManifestWork to spoke1.`
6. `[*] Phase 3: Entering Grace Period (600s)...`

> **Talking Point:** *"Within 5 seconds, the rotator generated a cryptographically secure 24-character credential, computed its Bcrypt hash, activated Dual-Auth on PrimaryHub, and hot-reloaded Prometheus in under 15 milliseconds without terminating any active connections."*

---

### Step 4: Prove Dual-Authentication is Active on PrimaryHub
Inspect `web.yml` in the PrimaryHub secret:

```bash
kubectl -n opensandbox-system get secret prometheus-web-config \
  -o jsonpath='{.data.web\.yml}' | base64 -d
```
**Expected Output:**
```yaml
basic_auth_users:
  <PREVIOUS_USER>: "$2b$12$..."
  prom-rw-<NEW_TIMESTAMP>: "$2b$12$..."
```
> **Talking Point:** *"Notice that BOTH users are present in the secret. This is our zero-downtime safety net: incoming remote write batches signed with either the old password or the new password are accepted immediately."*

---

### Step 5: Verify Edge Spokes Received the Secret via OCM
Verify that Open Cluster Management delivered the secret across the WireGuard mesh to `spoke1`:

```bash
# 1. Check ManifestWork status on PrimaryHub:
kubectl -n spoke1 get manifestwork spoke-prometheus-auth

# 2. Check the condition details (must show Applied=True and Available=True):
kubectl -n spoke1 get manifestwork spoke-prometheus-auth \
  -o jsonpath='{"Status: "}{.status.conditions[0].message}{" | "}{.status.conditions[1].message}{"\n"}'

# 3. View the new username and password payload delivered to the spoke:
kubectl -n spoke1 get manifestwork spoke-prometheus-auth \
  -o jsonpath='{.spec.workload.manifests[0].stringData}{"\n"}'
```
**Expected Output:**
```text
Status: Apply manifest work complete | All resources are available
{"password":"...","username":"prom-rw-..."}
```
> **Talking Point:** *"We don't need SSH, custom scripts, or external Lambdas to reach our remote edge clusters. The Open Cluster Management agent on Spoke 1 autonomously pulls the ManifestWork over the WireGuard VPN mesh, commits `secret/prometheus-remote-write-auth` into Spoke 1's local `monitoring` namespace, and reloads the local scraper."*

---

### Step 6: Verify Continuous Metric Ingestion During Rotation
Confirm that metrics from `spoke1` are still actively streaming and being indexed into PrimaryHub's TSDB:

```bash
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=up%7Bcluster%3D%22spoke1%22%7D" | jq .data.result
```
**Expected Output:**
Shows active targets from `spoke1` returning `value: [ <timestamp>, "1" ]`.

> **Talking Point:** *"Throughout the entire rotation process, metric delivery never stopped. Zero HTTP 401 Unauthorized errors occurred, zero samples were dropped, and Grafana dashboards remain live."*

---

## 3. Cryptographic Deep-Dive: Plaintext vs. One-Way Bcrypt Verification

### Why Can't the Password in `web.yml` Be Decrypted?
* **On Edge Spokes (`prometheus-remote-write-auth`)**:
  The spoke scraper requires the **plaintext password** to sign outbound HTTP requests with the standard `Authorization: Basic <base64(username:password)>` header. In Kubernetes, Secret `data` fields are merely base64-encoded, not encrypted. Decoding base64 yields the exact plaintext password.
* **On PrimaryHub (`prometheus-web-config`)**:
  Prometheus stores a **one-way cryptographic Bcrypt hash** (e.g. `$2b$12$ZBXxtlux1lwexuILj1wNhukCxmL0ZFzIZ/ltbSpoYn.wlxKmUPXFC`). By cryptographic design, hashing is irreversible—it **cannot be decrypted**. This prevents credential theft if the configuration is ever compromised.

### How Prometheus Verifies the Password
When Spoke 1 transmits metrics, Prometheus passes the incoming plaintext password through the Bcrypt algorithm using the salt embedded in the stored hash. If the computed hash matches the stored hash, authentication succeeds (`HTTP 204 No Content`).

### How You Can Verify the Match Manually

#### Method A: Python Bcrypt Salt Matching Test
Extract the rotated password and stored hash directly from your cluster and verify the cryptographic match:

```bash
# 1. Dynamically read the username and password delivered to Spoke 1:
PASSWORD=$(kubectl -n spoke1 get manifestwork spoke-prometheus-auth -o jsonpath='{.spec.workload.manifests[0].stringData.password}')
USERNAME=$(kubectl -n spoke1 get manifestwork spoke-prometheus-auth -o jsonpath='{.spec.workload.manifests[0].stringData.username}')

# 2. Run the Bcrypt verification using the salt stored in PrimaryHub:
python3 -c "
import crypt, subprocess

password = '$PASSWORD'
username = '$USERNAME'

cmd = 'kubectl -n opensandbox-system get secret prometheus-web-config -o jsonpath=\"{.data.web\\\\.yml}\" | base64 -d'
web_yml = subprocess.check_output(cmd, shell=True, text=True)
stored_hash = [line.split(':', 1)[1].strip().strip('\"') for line in web_yml.splitlines() if username in line][0]

computed_hash = crypt.crypt(password, stored_hash)

print(f'User          : {username}')
print(f'Computed Hash : {computed_hash}')
print(f'Stored Hash   : {stored_hash}')
print(f'Cryptographic Match: {computed_hash == stored_hash}')
"
```
**Expected Output:**
```text
User          : prom-rw-...
Computed Hash : $2b$12$...
Stored Hash   : $2b$12$...
Cryptographic Match: True
```

#### Method B: Direct API Validation Against PrimaryHub
Encode the dynamically extracted credentials into an HTTP Basic Authorization header and query Prometheus directly:

```bash
PASSWORD=$(kubectl -n spoke1 get manifestwork spoke-prometheus-auth -o jsonpath='{.spec.workload.manifests[0].stringData.password}')
USERNAME=$(kubectl -n spoke1 get manifestwork spoke-prometheus-auth -o jsonpath='{.spec.workload.manifests[0].stringData.username}')
AUTH="Authorization: Basic $(echo -n "${USERNAME}:${PASSWORD}" | base64)"

kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- --header="$AUTH" "http://localhost:9090/api/v1/query?query=up" | jq .status
```
**Expected Output:**
```json
"success"
```
