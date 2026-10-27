# docker-multi-script — Script Reference

> **Location:** `docker-multi-script/`
> **Entry point:** `docker-multi-cluster.sh`
> **Library scripts:** `lib/*.sh` (sourced in dependency order)

This document explains the purpose, internal structure, and every function of all scripts in the project. Read it top-to-bottom for a full mental model of how the multi-cluster platform is built, or jump to any section for a specific script.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [How the Scripts Fit Together](#2-how-the-scripts-fit-together)
3. [Entrypoint — `docker-multi-cluster.sh`](#3-entrypoint--docker-multi-clustersh)
4. [lib/globals.sh — Configuration, Logging & Phase Runner](#4-libglobalssh--configuration-logging--phase-runner)
5. [lib/preflight.sh — Host Toolchain & Environment Setup](#5-libpreflightsh--host-toolchain--environment-setup)
6. [lib/network.sh — Transit Network, WireGuard & Envoy Gateway](#6-libnetworksh--transit-network-wireguard--envoy-gateway)
7. [lib/clusters.sh — KinD Cluster Lifecycle, CRDs & OCM Init](#7-libclusterssh--kind-cluster-lifecycle-crds--ocm-init)
8. [lib/deploy.sh — Helm Deployments for Hub Clusters](#8-libdeploysh--helm-deployments-for-hub-clusters)
9. [lib/kata.sh — Kata Containers + Firecracker Runtime](#9-libkatash--kata-containers--firecracker-runtime)
10. [lib/gvisor.sh — gVisor (runsc) Runtime](#10-libgvisorsh--gvisor-runsc-runtime)
11. [lib/ocm.sh — OCM Spoke Join & Registration Sync](#11-libocmsh--ocm-spoke-join--registration-sync)
12. [lib/main.sh — Orchestrator, Teardown & Verification](#12-libmainsh--orchestrator-teardown--verification)
13. [Full Provisioning Sequence (All 17 Phases)](#13-full-provisioning-sequence-all-17-phases)
14. [Idempotency Design — How `run_phase` Works](#14-idempotency-design--how-run_phase-works)
15. [The `.sandbox-state` Directory](#15-the-sandbox-state-directory)

---

## 1. Project Overview

The `docker-multi-script` project fully automates the creation of a **four-cluster Kubernetes platform** on a single Linux host using [KinD](https://kind.sigs.k8s.io/) (Kubernetes-in-Docker). The topology is:

```
┌──────────────────────────────────────────────────────────┐
│  Transit Docker Network  (172.30.0.0/24)                 │
│                                                          │
│  ┌──────────┐   ┌─────────────┐   ┌───────────────────┐ │
│  │ PrimaryHub│   │SecondaryHub │   │  Envoy Gateway    │ │
│  │ (hub1)   │   │ (hub2)      │   │  VIP 10.99.0.100  │ │
│  └──────────┘   └─────────────┘   └───────────────────┘ │
│       │                │                     │           │
│  ┌──────────┐   ┌─────────────┐              │ WireGuard │
│  │  Spoke1  │   │   Spoke2    │◄─────────────┘  Overlay │
│  └──────────┘   └─────────────┘       10.99.0.0/24      │
└──────────────────────────────────────────────────────────┘
```

| Cluster | Role | WireGuard IP |
|---|---|---|
| `primaryhub` | Active OCM hub, PostgreSQL primary, API ingress | `10.99.0.1` |
| `secondaryhub` | Warm standby OCM hub, PostgreSQL standby (WAL), failover controller | `10.99.0.2` |
| `spoke1` | Workload cluster; Kata/Firecracker + gVisor runtimes | `10.99.0.3` |
| `spoke2` | Workload cluster; Kata/Firecracker + gVisor runtimes | `10.99.0.4` |

---

## 2. How the Scripts Fit Together

```
docker-multi-cluster.sh          ← Entry point, sources all libs, dispatches action
│
├── lib/globals.sh               ← Sourced FIRST: all variables, arg parsing, logging, run_phase
├── lib/preflight.sh             ← Host OS checks, auto-installs tools, locates/clones repo
├── lib/network.sh               ← Docker transit network, WireGuard keys, Envoy gateway
├── lib/clusters.sh              ← KinD cluster creation, CRD install, OCM init, images, namespaces
├── lib/deploy.sh                ← Helm chart deployments on primaryhub & secondaryhub
├── lib/kata.sh                  ← Kata Containers + Firecracker MicroVM runtime on spokes
├── lib/gvisor.sh                ← gVisor (runsc) sandbox runtime on spokes
├── lib/ocm.sh                   ← Spoke join to OCM, MultipleHubs feature, CSR approval
└── lib/main.sh                  ← main() orchestrator, teardown_environment, run_verification
```

Each library script **only defines functions**. The actual calls happen in `main()` inside `main.sh`, keeping execution flow in one easy-to-read place.

---

## 3. Entrypoint — `docker-multi-cluster.sh`

**File:** `docker-multi-cluster.sh`

### Purpose
The thin entry point that sources all library modules in the correct dependency order, then dispatches to the right action based on the `$ACTION` variable (which is set by argument parsing in `globals.sh`).

### Key Details

```bash
set -eo pipefail
```
Strict error mode — any unhandled non-zero exit causes the script to abort immediately. This prevents silent failures in a long provisioning sequence.

### Source Order (Matters)
```bash
source globals.sh    # MUST be first — sets variables that all others depend on
source preflight.sh
source network.sh
source clusters.sh
source deploy.sh
source kata.sh
source gvisor.sh
source ocm.sh
source main.sh       # MUST be last — main() calls functions from all others
```

### Action Dispatch

| CLI Argument | `$ACTION` value | Function called |
|---|---|---|
| *(none)* | `deploy` | `main()` |
| `--clean` or `clean` | `clean` | `teardown_environment()` |
| `--verify` | `verify` | `run_verification()` |
| `--force` | `deploy` | `main()` (all phase checks bypassed) |

---

## 4. lib/globals.sh — Configuration, Logging & Phase Runner

**File:** `lib/globals.sh`

### Purpose
The configuration backbone of the entire project. Every other script relies on variables and functions defined here. It is always sourced first.

### Section 1 — Terminal Colors
```bash
RED GREEN YELLOW BLUE CYAN BOLD NC
```
ANSI escape codes used by the logging functions to color output. `NC` resets to normal.

### Section 2 — Repository & State Paths

| Variable | Value / Meaning |
|---|---|
| `SCRIPT_DIR` | Absolute path to the `docker-multi-script/` directory |
| `ROOT_DIR` | Same as `SCRIPT_DIR` |
| `SANDBOX_REPO_DIR` | Detected path to the `01-Sandbox` source repository |
| `CODE_INSPECTOR_DIR` | `$SANDBOX_REPO_DIR/codeInspector` — Helm chart source |
| `OPENSANDBOX_BUILD_DIR` | Path to the `opensandbox-server` Docker build context |

**Repo Detection Logic:** The script checks four candidate locations for the `codeInspector` directory in order of priority:
1. One level above `SCRIPT_DIR` (the script is inside the repo)
2. `$ROOT_DIR/01-Sandbox/codeInspector` (repo is a sibling folder)
3. `$ROOT_DIR/codeInspector` (repo is the script root)
4. `$(pwd)/codeInspector` (current working directory)

This makes the script runnable from any working directory without manual path configuration.

**State Directories** (all under `.sandbox-state/`):

| Variable | Path | Contents |
|---|---|---|
| `STATE_DIR` | `.sandbox-state/` | Root of all runtime state |
| `PKI_DIR` | `.sandbox-state/pki/` | Shared Kubernetes CA certs & SA keys |
| `WG_DIR` | `.sandbox-state/wg/` | WireGuard keypairs per entity |
| `ENVOY_DIR` | `.sandbox-state/envoy/` | Envoy config + WireGuard wg0.conf |
| `SEC_DIR` | `.sandbox-state/sec/` | Pre-rendered CNPG re-clone manifest |
| `KATA_CACHE_DIR` | `.sandbox-state/kata-assets/` | Kata static release tarball & binaries |
| `GVISOR_CACHE_DIR` | `.sandbox-state/gvisor-assets/` | gVisor binaries |

All these directories are created with `mkdir -p` at source time so no phase ever fails due to a missing directory.

### Section 3 — Network Configuration

**Transit Docker Network (layer-3 bridge):**

| Variable | Value | Meaning |
|---|---|---|
| `TRANSIT_NET_NAME` | `01sandbox-transit` | Docker network name |
| `TRANSIT_SUBNET` | `172.30.0.0/24` | Docker network subnet |
| `GW_TRANSIT_IP` | `172.30.0.10` | Envoy gateway container transit IP |
| `HUB1_TRANSIT_IP` | `172.30.0.20` | PrimaryHub transit IP |
| `HUB2_TRANSIT_IP` | `172.30.0.21` | SecondaryHub transit IP |
| `SPOKE1_TRANSIT_IP` | `172.30.0.30` | Spoke1 transit IP |
| `SPOKE2_TRANSIT_IP` | `172.30.0.31` | Spoke2 transit IP |
| `HUB1_METALLB_IP` | `172.30.0.200` | MetalLB VIP on primaryhub |
| `HUB2_METALLB_IP` | `172.30.0.201` | MetalLB VIP on secondaryhub |

**WireGuard Encrypted Overlay (layer-3 VPN):**

| Variable | Value | Meaning |
|---|---|---|
| `WG_SUBNET_PREFIX` | `10.99.0` | Overlay subnet prefix |
| `WG_GATEWAY_IP` | `10.99.0.254` | Envoy gateway WireGuard address |
| `WG_VIP` | `10.99.0.100` | Virtual IP — single entry point for API and HTTP |
| `WG_HUB1_IP` | `10.99.0.1` | PrimaryHub WireGuard IP |
| `WG_HUB2_IP` | `10.99.0.2` | SecondaryHub WireGuard IP |
| `WG_SPOKE1_IP` | `10.99.0.3` | Spoke1 WireGuard IP |
| `WG_SPOKE2_IP` | `10.99.0.4` | Spoke2 WireGuard IP |

`WG_PRIV` and `WG_PUB` are declared as associative arrays, populated in Phase 02 and carried through all subsequent phases.

### Section 4 — CRD Metadata

Three associative arrays map CRD bundle filenames to human-readable names, included CRD resource names, and upstream URLs. These are consumed by `_install_crds()` in `clusters.sh` to print a rich install table and support automatic upstream-first fetching with local fallback.

### Section 5 — Kata / Firecracker Versions

```bash
KATA_VERSION="${KATA_VERSION:-3.18.0}"
FIRECRACKER_VERSION="${FIRECRACKER_VERSION:-v1.11.1}"
```
Both are overridable via environment variables, so you can test with a different version without modifying the script.

### Section 6 — Argument Parsing

Processes `$@` before any other library code runs. Recognizes:
- `--clean` / `clean` / `--destroy` / `destroy` → teardown
- `--verify` → health check only
- `--force` → set `FORCE_RECONFIGURE=true` (bypass all idempotency checks)
- `-h` / `--help` → print usage and exit

### Section 7 — Logging Functions

| Function | Color | Stream | Purpose |
|---|---|---|---|
| `log_info` | Blue | stdout | Normal progress messages |
| `log_success` | Green | stdout | Success confirmations |
| `log_warn` | Yellow | stderr | Non-fatal warnings |
| `log_error` | Red | stderr | Errors (fatal or not) |
| `log_step` | Cyan block | stdout | Major phase headers |

### Section 8 — Phase Runner (`run_phase`)

```bash
run_phase <num> <title> <check_fn> <do_fn>
```

This is the **idempotency engine** of the entire project. Before running `<do_fn>`, it calls `<check_fn>`. If `<check_fn>` exits 0 **and** `--force` was not passed, the phase is skipped with a "already configured" message. This makes re-running the script safe — already-completed phases are skipped, and incomplete phases continue from where they left off.

See [Section 14](#14-idempotency-design--how-run_phase-works) for the full explanation.

**Shared predicates** also defined here:
- `_check_wg_active <container> <expected-ip>` — verifies `wg0` is up and has the given IP inside a container.
- `_check_hub_crds_established <context>` — verifies that all 9 required CRDs are registered on the given cluster context.

---

## 5. lib/preflight.sh — Host Toolchain & Environment Setup

**File:** `lib/preflight.sh`
**Phase:** Phase 01

### Purpose
Ensures the host machine has every required tool before any cluster work begins. If a tool is missing, it attempts automatic installation. Also clones the `01-Sandbox` repository if it is not already present.

### Functions

#### `ensure_kernel_inotify_limits()`
Running 4 KinD clusters simultaneously exhausts the Linux kernel's default inotify watch budget (8,192 watches, 128 instances). This function:
1. Reads current values from `/proc/sys/fs/inotify/`
2. If below thresholds (`max_user_watches = 524288`, `max_user_instances = 8192`), uses `sysctl -w` to raise them live.
3. Writes a persistent config to `/etc/sysctl.d/99-kind-inotify.conf` so the values survive reboots.

Without this, the 3rd or 4th KinD cluster creation fails with a cryptic `EMFILE` error or hangs waiting for `Reached target Multi-User System`.

#### `ensure_docker_access()`
Checks if the Docker daemon is accessible (`docker info`). If not:
1. Starts the daemon via `systemctl` or `service`
2. Waits up to 10 seconds for `/var/run/docker.sock` to appear
3. Adds the current user to the `docker` group permanently
4. Grants immediate `chmod 666` + `setfacl` access for the current session (avoids needing to log out and back in)

#### `_apply_repo_paths <repo-root>`
A helper that updates `SANDBOX_REPO_DIR`, `CODE_INSPECTOR_DIR`, and `OPENSANDBOX_BUILD_DIR` to point at a confirmed repository root. Also:
- Switches the repo to the `feat/production` branch if it is not already on it
- Patches `configmap.yaml` to coerce boolean values to strings using `tpl ($value | toString)`, fixing a Helm template rendering issue

#### `ensure_sandbox_repo()`
Locates or clones the `01-Sandbox` source repository, which contains all Helm charts and CRD manifests.

**Search order:**
1. Already configured (`$OPENSANDBOX_BUILD_DIR` exists with a Dockerfile) → done immediately
2. Check 5 well-known candidate paths relative to the script and `$(pwd)`
3. Recursive `find` up to 4 levels deep under `$ROOT_DIR` and `$HOME`
4. Clone via SSH from `git@github.com:01cloud/01-Sandbox.git` into `$ROOT_DIR/01-Sandbox` (or `$STATE_DIR/01-Sandbox` if that path is occupied)

SSH cloning uses `StrictHostKeyChecking=accept-new` to avoid interactive prompts on first connection. Falls back to cloning without a branch specifier if the target branch is not found.

#### `_relax_webhook_failure_policy <context>`
Sets all OCM and CNPG admission webhooks to `failurePolicy: Ignore` using `kubectl … | jq … | kubectl apply`. This prevents a circular deadlock where Kubernetes refuses all API calls because the webhook pods are not yet running. Applies to:
- 3 OCM validating webhooks (ManagedClusterSet, ManagedCluster, ManifestWork)
- 1 CNPG mutating webhook
- 1 CNPG validating webhook

#### `_check_phase_01()`
The idempotency check for Phase 01. Returns 0 only if every required tool (`git`, `docker`, `kind`, `kubectl`, `helm`, `clusteradm`, `jq`, `curl`, `wg`) is on PATH, Docker daemon is running, and inotify limits are already at or above the required thresholds.

#### `_do_phase_01_preflight()`
The actual work function for Phase 01. Contains per-tool installer sub-functions:

| Sub-function | Tool | Install Method |
|---|---|---|
| `_install_git` | git | apt / dnf / yum |
| `_install_docker` | docker | Official Docker apt/yum repo + GPG key |
| `_install_kind` | kind | GitHub releases binary (latest version auto-detected) |
| `_install_kubectl` | kubectl | `dl.k8s.io` stable release binary |
| `_install_helm` | helm | Official `get-helm-3` installer script |
| `_install_clusteradm` | clusteradm | OCM installer script, falls back to GitHub tarball |
| `_install_jq` | jq | apt / dnf / GitHub binary fallback |
| `_install_curl` | curl | apt / dnf |
| `_install_wireguard` | wg | `wireguard` + `wireguard-tools` package |

A `_check_and_install <tool> <installer_fn>` dispatcher runs each pair and collects failures. If any tool could not be installed, the script exits with a clear list of what failed. After all tools are confirmed:
1. `ensure_docker_access` is called again as a final check
2. `ensure_sandbox_repo` is called to pre-position the repository
3. `modprobe wireguard` is attempted (non-fatal, may be built-in)
4. `ensure_kernel_inotify_limits` is called to guarantee KinD can run 4 clusters

#### `phase_01_preflight()`
Wrapper that calls `run_phase "01" "Checking Host Toolchain (Auto-Install if Missing)" _check_phase_01 _do_phase_01_preflight`.

---

## 6. lib/network.sh — Transit Network, WireGuard & Envoy Gateway

**File:** `lib/network.sh`
**Phases:** 02, 05, 06, 07

### Purpose
Establishes the Docker transit network that connects all KinD containers, generates WireGuard keypairs for all participants, configures an encrypted WireGuard overlay mesh, and deploys the Envoy proxy as the single cluster entry point.

### Functions

#### `_check_phase_02()` / `_do_phase_02_transit_network_and_wg_keys()`
**Phase 02 — Transit Network & WireGuard Key Generation**

The check verifies:
- The `01sandbox-transit` Docker network exists
- All 5 WireGuard key files (`gateway`, `primaryhub`, `secondaryhub`, `spoke1`, `spoke2`) exist and are non-empty in `$WG_DIR`
- If check passes, it loads keypairs into the `WG_PRIV[]` / `WG_PUB[]` arrays for later phases (critical: even if Phase 02 is skipped, the keys must be in memory)

The do function:
1. Creates the Docker bridge network `01sandbox-transit` with subnet `172.30.0.0/24` and bridge name `br-01transit` (if it does not exist)
2. For each of the 5 entities, generates an X25519 Curve25519 keypair using Python's `cryptography` library (base64-encoded, compatible with WireGuard's format), and saves to `$WG_DIR/<entity>.key` and `$WG_DIR/<entity>.pub`
3. Loads all keypairs into `WG_PRIV[]` and `WG_PUB[]` arrays

Why Python for keygen? The `wg genkey` command from WireGuard tools encodes keys in a slightly different base64 format; Python's `cryptography` library produces raw bytes that directly match WireGuard's wire format.

#### `_do_phase_05_wireguard_on_hubs()`
**Phase 05 — WireGuard on Hub Clusters**

Configures `wg0` on `primaryhub-control-plane` and `secondaryhub-control-plane` only. Spoke clusters are not yet created at this point, so spoke peers are not yet included — they are added in Phase 14.

#### `_setup_wireguard <container> <entity> <wg-ip>`
Shared helper called by both Phase 05 and Phase 14. Steps inside the Docker container:
1. Installs `wireguard-tools` if not present via `apt-get`
2. Writes `/etc/wireguard/wg0.conf` with:
   - The interface `Address` (WireGuard IP)
   - The entity's private key (from `WG_PRIV[]`)
   - A `[Peer]` block for every other entity (gateway, both hubs, both spokes) with their public keys, allowed IPs, transit endpoints, and `PersistentKeepalive = 25`
3. Brings down any existing `wg0`, enables the `wg-quick@wg0` systemd unit, and starts it
4. Enables IPv4 forwarding (`net.ipv4.ip_forward=1`)

#### `_do_phase_06_envoy_gateway()`
**Phase 06 — Envoy Gateway (VIP: 10.99.0.100)**

The Envoy gateway container is the single access point for both HTTP traffic and the Kubernetes API over the WireGuard overlay.

Steps:
1. **Writes `$ENVOY_DIR/envoy.yaml`** — a static Envoy v3 configuration with:
   - `ingress_http_listener` on port 80: HTTP connection manager routing to `ingress_http_cluster`, which round-robins between `HUB1_METALLB_IP:80` (priority 0) and `HUB2_METALLB_IP:80` (priority 1). The priority system means Envoy only sends HTTP to secondaryhub when primaryhub is unhealthy.
   - `ingress_kube_api_listener` on port 6443: TCP proxy routing to `ingress_kube_api_cluster`, which round-robins between `WG_HUB1_IP:6443` and `WG_HUB2_IP:6443`.
   - All clusters have TCP health checks (1s interval, 2 threshold) so unhealthy backends are removed automatically.
2. **Writes `$ENVOY_DIR/wg0.conf`** — the gateway's WireGuard config, assigning it both `WG_GATEWAY_IP/24` and `WG_VIP/32` (the virtual IP), with peers for all 4 clusters.
3. **Builds `01sandbox-envoy:v1`** — a custom Docker image based on `envoyproxy/envoy:v1.31-latest` with `wireguard-tools` and `iptables` added.
4. **Starts the container** with port mapping `-p 80:80`, `--privileged`, transit IP `172.30.0.10`, both config files bind-mounted read-only, and entrypoint: `wg-quick up wg0 && envoy -c /etc/envoy/envoy.yaml`
5. **Verifies VIP reachability** by pinging `10.99.0.100` from inside `primaryhub-control-plane` (up to 20 attempts).

#### `_do_phase_07_verify_root_ca_and_vip()`
**Phase 07 — Verifying Shared Root CA & VIP TLS SANs**

1. Computes SHA256 of `/etc/kubernetes/pki/ca.crt` inside both hub containers. Aborts if they differ — mismatched CAs would cause TLS verification failures across clusters.
2. Checks ServiceAccount key parity (`sa.pub`) and syncs from primaryhub to secondaryhub via `$PKI_DIR` if they differ. SA keys must match so tokens signed on primaryhub are valid on secondaryhub during failover.
3. Updates the `cluster-info` ConfigMap in `kube-public` on both hubs to advertise `https://10.99.0.100:6443` (the VIP) as the API server address.

---

## 7. lib/clusters.sh — KinD Cluster Lifecycle, CRDs & OCM Init

**File:** `lib/clusters.sh`
**Phases:** 03, 04, 08, 09, 10, 13, 14, 15

### Purpose
Manages the full lifecycle of all four KinD clusters, installs Custom Resource Definitions, initializes Open Cluster Management on the hubs, creates namespaces, and loads the custom opensandbox-server Docker image.

### Helper Functions

#### `_create_kind_cluster <name> <pod-subnet> <svc-subnet> <transit-ip> [use-shared-ca]`
The core KinD cluster factory. Steps:
1. Calls `ensure_kernel_inotify_limits` to guarantee host limits are adequate
2. If the cluster already exists, exports kubeconfig and returns
3. Builds a `kind: Cluster` YAML config in `/tmp/kind-<name>.yaml` with:
   - Custom `podSubnet` and `serviceSubnet` (unique per cluster to avoid IP conflicts)
   - `certSANs` including all WireGuard IPs and the VIP (needed for API server TLS)
   - `extraPortMappings` for PostgreSQL (30432) and Valkey/Redis (30379) NodePorts
   - `extraMounts` if `use_shared_ca=true`: bind-mounts the shared CA cert/key and SA keys from `$PKI_DIR` into the secondaryhub container before the API server starts
   - `extraMounts` for spoke clusters: bind-mounts `/dev/kvm` and `/dev/net/tun` for Firecracker hardware virtualization
4. Runs `kind create cluster`
5. Sets `docker update --restart=always` on the container (survives host reboots)
6. Connects the cluster container to the transit network at the specified IP
7. Waits for the control-plane node and CoreDNS to be Ready

#### `_setup_wireguard <container> <entity> <wg-ip> [extra-ips]`
Shared helper used in both Phase 05 (hubs only) and Phase 14 (all clusters). Defined in `clusters.sh` because it directly pairs with the cluster creation logic.

#### `_wait_for_pod <context> <namespace> <pod-name-pattern> [max] [label]`
Polls `kubectl get pod` every 5 seconds until a pod whose name matches the grep pattern is `1/1 Running`. Used to gate sequential Helm deployments. Times out gracefully with a warning.

#### `_sanitize_agentgateway_crds()`
The AgentGateway CRDs include `x-kubernetes-validations` CEL rules that exceed Kubernetes 1.30's CEL cost budget. This function uses Python's `yaml` library to strip all `x-kubernetes-validations` keys from the CRD YAML in-place before applying it.

#### `_ensure_hub_crds <context>`
A lightweight, idempotent CRD guarantor. Checks each of the 5 CRD bundles individually and only applies those that are missing. After applying, waits for each CRD to reach `Established` condition. Called at multiple points to ensure CRDs are always present.

#### `_install_crds <context>`
Full CRD installation with rich logging. For each bundle:
1. Tries to download the upstream YAML (5s connect timeout)
2. Applies with `--server-side --force-conflicts`
3. Falls back to the local copy from `$CODE_INSPECTOR_DIR/crds/` if upstream unavailable
4. Prints a formatted table of all established CRDs at the end

### Phase Functions

#### Phase 03 — `phase_03_create_hub_clusters()`
1. Creates `primaryhub` with `use_shared_ca=false` — it generates its own CA
2. Extracts the CA cert/key and SA keys from `primaryhub-control-plane` into `$PKI_DIR`
3. Creates `secondaryhub` with `use_shared_ca=true` — the CA files are bind-mounted before the API server generates any certificates

This ensures both hubs have **identical Root CAs** from the very first boot, which is the foundation for cross-cluster trust and seamless failover.

#### Phase 04 — `phase_04_install_crds_on_hubs()`
Runs `_install_crds` on both `kind-primaryhub` and `kind-secondaryhub`.

#### Phase 08 — `phase_08_ocm_init()`
Initializes Open Cluster Management on both hubs using `clusteradm init`. Also deploys the `ocm-auto-acceptor`, waits for the registration webhook, and calls `_relax_webhook_failure_policy` on both hubs.

#### Phase 09 — `phase_09_create_namespaces()`
Creates `opensandbox-system`, `metallb-system`, and `agentgateway-system` namespaces on both hub clusters using idempotent `--dry-run=client -o yaml | kubectl apply`.

#### Phase 10 — `phase_10_load_custom_image()`
1. Builds `01community/01sandbox-opensandbox-server:v0.7.10-ocm` from the `opensandbox-server/docker-build/` Dockerfile if not already built locally
2. Loads the image into both hub clusters via `kind load docker-image`
3. Triggers a rolling restart of the `opensandbox-server` deployment to pick up the new image

#### Phase 13 — `phase_13_create_spoke_clusters()`
Creates `spoke1` and `spoke2`. Immediately removes the `NoSchedule` taint from both control-plane nodes — KinD single-node clusters taint the control-plane by default, which would permanently block all pod scheduling since there are no worker nodes.

#### Phase 14 — `phase_14_wireguard_on_all_clusters()`
1. Configures `wg0` on `spoke1-control-plane` and `spoke2-control-plane`
2. **Re-applies WireGuard on both hubs** — now that spoke public keys are known, the hub `wg0.conf` files are updated to include spoke `[Peer]` entries. Without this re-apply, hubs cannot route traffic to spokes over the overlay.

#### Phase 15 — `phase_15_install_crds_on_spokes()`
Runs `_install_crds` on `kind-spoke1` and `kind-spoke2`.

---

## 8. lib/deploy.sh — Helm Deployments for Hub Clusters

**File:** `lib/deploy.sh`
**Phases:** 11, 12

### Purpose
Deploys the full application stack onto both hub clusters using Helm. Uses a deliberate **two-step approach**: deploy PostgreSQL first and wait for it to be Running, then deploy everything else.

### Phase 11 — PrimaryHub Deploy

#### `_do_phase_11_primaryhub_deploy()`
**Step 11a — PostgreSQL Primary First:**
1. Patches the `configmap.yaml` Helm template for boolean-to-string coercion
2. Ensures CRDs and relaxes admission webhooks
3. Detects and clears stale PVCs if `postgresql-primary-1-initdb` has an `Error` state
4. Runs `helm upgrade --install codeinspector` with most services **disabled** — only CNPG operator and PostgreSQL cluster are active
5. Waits up to 5 minutes for `postgresql-primary-1` to become `1/1 Running`
6. If `initdb` fails due to `PGData directories already exist`, purges the stale PVC and retries

**Step 11b — Full Stack:**
1. Re-ensures CRDs; explicitly applies AgentGateway CRDs if missing
2. Runs `helm upgrade --install codeinspector` again with everything enabled
3. Waits for `valkey`, `sandbox-api`, and `opensandbox-server` rollouts to complete

### Phase 12 — SecondaryHub Deploy

#### `_do_phase_12_secondaryhub_deploy()`
**Step 12a — PostgreSQL Standby First:**
Deploys CNPG with `values-secondary.yaml` overrides. Key Helm values:
- `apiServer.cnpg.replication.primaryHost: WG_HUB1_IP` — points WAL streaming at primaryhub
- `apiServer.cnpg.replication.primaryPort: 30432` — the NodePort mapped during cluster creation
- `apiServer.valkey.replication.primaryHost/Port` — Valkey replication from primaryhub

This makes secondaryhub PostgreSQL a **physical streaming standby** (WAL receiver) of primaryhub.

**Step 12b — Full Stack + Failover Controller:**
1. Deploys everything including `ocm-failover-controller`
2. Pre-renders the CNPG re-clone manifest using `helm template` → `$SEC_DIR/postgresql-secondary-cluster.yaml` and copies it into the secondaryhub container at `/root/` (used by the failover controller after a failover event)

---

## 9. lib/kata.sh — Kata Containers + Firecracker Runtime

**File:** `lib/kata.sh`
**Phase:** 15b

### Purpose
Installs [Kata Containers](https://katacontainers.io/) 3.x with the Firecracker VMM on spoke clusters. Pods can request `runtimeClassName: kata-fc` to run inside isolated Firecracker MicroVMs.

### Functions

#### `_ensure_kata_host_assets()`
1. Reuses existing `/opt/kata` on the host if complete (no download needed)
2. Otherwise downloads the Kata static release tarball from GitHub releases into `$KATA_CACHE_DIR`
3. Downloads the Firecracker binary separately (packaged in its own release tarball)
4. All downloads are cached — only performed if files are absent

#### `_configure_spoke_kata_fc <spoke>`
Full installation sequence per spoke:

| Step | What happens |
|---|---|
| 1 | Installs `lvm2` and `thin-provisioning-tools` inside the container (required for devmapper) |
| 2 | Copies the host's devmapper-enabled `containerd` binary into the container |
| 3 | Copies Kata binaries (`containerd-shim-kata-v2`, `firecracker`, `jailer`, `kata-runtime`, `kata-ctl`) and kernel/firmware assets into `/opt/kata/` |
| 4 | Copies or generates `/etc/kata-containers/configuration.toml` (Firecracker-specific config) |
| 5 | Creates symlinks in `/usr/local/bin/` for all Kata binaries |
| 6 | Installs a `dmsetup` wrapper that recreates `/dev/mapper/*` device nodes after every `dmsetup` operation (necessary in containers where `udev` is absent) |
| 7 | Creates a 15GB loop-backed LVM volume group and a 12GB thin-pool inside the container |
| 8 | Installs `init-containerd-devmapper.sh` startup script and `containerd-devmapper.service` systemd unit that re-attaches loop devices on container restart |
| 9 | Appends the `kata-fc` runtime handler and `devmapper` snapshotter plugin to containerd's `config.toml`, restarts containerd only if config changed |
| 10 | Creates Kubernetes `RuntimeClass kata-fc` and the `opensandbox-workloads` namespace with klusterlet RBAC |

#### `_smoke_test_kata_fc <spoke>`
Schedules a test pod with `runtimeClassName: kata-fc` running `busybox`. Confirms the pod runs and logs show a MicroVM guest kernel via `uname -a`. Cleans up afterward.

#### `_do_phase_15b_setup_kata_firecracker()`
1. Warns if `/dev/kvm` is missing (Firecracker needs hardware virtualization)
2. Calls `_ensure_kata_host_assets` once (shared between both spokes)
3. Checks 3 idempotency conditions per spoke; skips if already configured
4. Calls `_configure_spoke_kata_fc` for any spoke that needs setup

---

## 10. lib/gvisor.sh — gVisor (runsc) Runtime

**File:** `lib/gvisor.sh`
**Phase:** 15c

### Purpose
Installs [gVisor](https://gvisor.dev/) (`runsc`) on spoke clusters alongside Kata. gVisor provides lightweight user-space kernel isolation. Pods can request `runtimeClassName: gvisor`.

### Functions

#### `_ensure_gvisor_host_assets()`
If `runsc`, `containerd-shim-runsc-v1`, and `gvisor-bin/` are all present in `$GVISOR_CACHE_DIR`, reuses cached assets. Otherwise downloads `gvisor.tar.zstd` from `storage.googleapis.com/gvisor/releases/release/latest/x86_64/`, extracts it, and sets execute permissions.

#### `_install_gvisor_on_spoke <spoke>`
1. Copies `runsc` and `containerd-shim-runsc-v1` into the spoke container at `/usr/local/bin/` (skips if already present)
2. Appends the `runsc` and `gvisor` runtime handler configuration blocks to `/etc/containerd/config.toml` (idempotent — checks with `grep` first, restarts containerd only if config changed)
3. Creates the Kubernetes `RuntimeClass gvisor` resource (handler `runsc`)

#### `_smoke_test_gvisor <spoke>`
Schedules a `busybox` pod with `runtimeClassName: gvisor` and checks the pod logs for the `Starting gVisor` dmesg line, which confirms the gVisor kernel is running.

#### `_do_phase_15c_setup_gvisor()`
Downloads assets once, then for each spoke checks 4 idempotency conditions, calls `_install_gvisor_on_spoke` and `_smoke_test_gvisor` for any spoke needing setup.

---

## 11. lib/ocm.sh — OCM Spoke Join & Registration Sync

**File:** `lib/ocm.sh`
**Phases:** 16, 17

### Purpose
Joins the spoke clusters to Open Cluster Management using the **MultipleHubs** feature, which allows each Klusterlet to know about both hubs and automatically fail over to the secondary hub if the primary becomes unavailable.

### Functions

#### `_write_hub_bootstrap_kubeconfig <out-file> <server-url> <token> <hub-node-name>`
Writes a kubeconfig to `<out-file>` that a Klusterlet uses to bootstrap its connection to a hub. The CA certificate is extracted live from the hub container (`docker exec … cat /etc/kubernetes/pki/ca.crt | base64`). File written with `chmod 600` to protect the bootstrap token.

#### `_get_hub_token <context>`
Extracts a long-lived (10-year) bootstrap token from a hub cluster. Priority:
1. `kubectl create token agent-registration-bootstrap --duration=87600h` (preferred)
2. Falls back to `clusteradm get token`

Retries up to 20 times with 5-second back-off.

#### `_dump_klusterlet_debug <context>`
Dumps diagnostics to stderr when a spoke fails to join: pod list, secret list, registration agent logs (last 30 lines), and Klusterlet status conditions.

#### `_ensure_hub_apiserver_sans <cluster> <svc-cidr>`
Checks if the hub's API server TLS certificate already includes the WireGuard IPs as Subject Alternative Names. If not:
1. Patches `/kind/kubeadm.conf` to persist SANs across reboots
2. Backs up `apiserver.crt` and `apiserver.key`
3. Uses `kubeadm init phase certs apiserver` to regenerate the certificate with WG IPs in SANs
4. Kills `kube-apiserver` to force a restart with the new certificate
5. Polls up to 2 minutes for the API server to become healthy

This is necessary because KinD generates API server certs before WireGuard IPs are known. Without this, Klusterlets connecting via WG IPs receive a TLS certificate that does not cover those IPs and refuse to connect.

#### `_do_phase_16_join_spokes_to_ocm()`
The most complex phase. For each spoke (`spoke1`, `spoke2`):

1. **Cert rotation:** Calls `_ensure_hub_apiserver_sans` on both hubs
2. **Tokens:** Obtains bootstrap tokens from both hubs
3. **Bootstrap kubeconfigs:** Writes `primaryhub-bootstrap.kubeconfig` and `secondaryhub-bootstrap.kubeconfig` to `$STATE_DIR`
4. **Taint removal:** Removes `NoSchedule` taint if still present
5. **Reachability check:** Pings both hub API servers from inside the spoke container
6. **Klusterlet install:** Copies `clusteradm` into the spoke container and runs `clusteradm join --hub-token ... --hub-apiserver ... --cluster-name <spoke>` inside it
7. **Bootstrap secrets:** Creates 3 Kubernetes Secrets in `open-cluster-management-agent`: `primaryhub-kubeconfig`, `secondaryhub-kubeconfig`, and `bootstrap-hub-kubeconfig`
8. **MultipleHubs patch:** Patches the `klusterlet` CR to enable the `MultipleHubs` feature gate, set `bootstrapKubeConfigs.type: LocalSecrets`, and list both hub secrets with a 60-second failover timeout
9. **Agent restart:** Deletes the `klusterlet-registration-agent` pod so it picks up the new config immediately
10. **CSR approval:** Polls `clusteradm accept` on primaryhub; polls `ManagedClusterConditionAvailable=True`; best-effort pre-approves on secondaryhub

#### `_do_phase_17_sync_spokes_to_secondaryhub()`
SecondaryHub starts without knowledge of the spoke clusters. This phase syncs registration state:
1. Copies spoke Namespaces, ClusterRoles, ClusterRoleBindings, and RoleBindings from primaryhub to secondaryhub
2. Copies the `ManagedCluster` object (stripping server-side metadata) with `hubAcceptsClient=false`
3. Syncs the `sandbox-spokes` ManagedClusterSet and ManagedClusterSetBinding
4. Labels spokes on both hubs with capability labels: `sandbox-workload-capable=true`, `runtime.gvisor=true`, `runtime.kata=true`, `runtime.kata-fc=true`, `wireguard-ip=<WG_SPOKE_IP>`

---

## 12. lib/main.sh — Orchestrator, Teardown & Verification

**File:** `lib/main.sh`

### Purpose
Contains the top-level `main()` orchestrator, the `teardown_environment()` cleanup function, the `run_verification()` health checker, and the `_print_summary()` final output.

### Functions

#### `teardown_environment()`
Complete cleanup — removes everything the script created:
1. Stops and removes the `envoy-gateway` container
2. Deletes all 4 KinD clusters and their kubeconfig entries
3. Removes the transit Docker network (disconnects all containers first)
4. Removes the `kind` Docker network if no KinD clusters remain
5. Deletes `$STATE_DIR`, `/tmp/01sandbox-*`, `/tmp/kind-*.yaml`, `/tmp/spoke*-*`
6. Prunes unused Docker volumes

All commands use `|| true` to continue even if a specific resource does not exist.

#### `run_verification()`
Runs 4 health checks on an existing deployment (also called at the end of `main()`):

| Check | What it verifies |
|---|---|
| [1a] OCM PrimaryHub | `kubectl get managedclusters` — spokes are registered |
| [1b] OCM SecondaryHub | Same check on standby hub |
| [2] PostgreSQL Primary | `pg_stat_replication` — WAL streaming sender is active |
| [3] PostgreSQL Standby | `pg_stat_wal_receiver` — WAL receiver is connected to primaryhub |
| [4] Valkey Replication | `valkey-cli info replication` — secondary is replicating from primaryhub |

#### `_print_summary()`
Detects the host's public IP (`ip route get 1.1.1.1`) and prints a formatted summary box with cluster IPs, VIP, database replication topology, and frontend integration instructions. Also automatically updates `z1sandbox-website/.env` with the correct `VITE_API_BASE_URL` if the file exists.

#### `main()`
The full provisioning sequence — calls all 17 phase functions in order, then `run_verification()` and `_print_summary()`.

---

## 13. Full Provisioning Sequence (All 17 Phases)

| Phase | Script | What it does |
|---|---|---|
| **01** | preflight.sh | Install/verify all host tools; clone repo; tune kernel inotify limits |
| **02** | network.sh | Create transit Docker network; generate WireGuard X25519 keypairs |
| **03** | clusters.sh | Create primaryhub (generates CA); create secondaryhub (uses shared CA via bind-mount) |
| **04** | clusters.sh | Install all CRDs on both hubs |
| **05** | network.sh | Configure WireGuard `wg0` on hub containers |
| **06** | network.sh | Build & deploy Envoy gateway container with WireGuard + dual-hub proxy |
| **07** | network.sh | Verify shared Root CA hash; sync SA keys; update `cluster-info` to advertise VIP |
| **08** | clusters.sh | `clusteradm init` on both hubs; deploy OCM auto-acceptor; relax admission webhooks |
| **09** | clusters.sh | Create `opensandbox-system`, `metallb-system`, `agentgateway-system` on hubs |
| **10** | clusters.sh | Build `opensandbox-server` Docker image; load into hub clusters |
| **11** | deploy.sh | Helm deploy primaryhub: PostgreSQL primary first, then full stack |
| **12** | deploy.sh | Helm deploy secondaryhub: PostgreSQL WAL standby first, then full stack + failover controller; pre-render re-clone manifest |
| **13** | clusters.sh | Create spoke1 and spoke2; remove `NoSchedule` control-plane taint |
| **14** | clusters.sh | Configure WireGuard on spokes; re-apply on hubs to include spoke peers |
| **15** | clusters.sh | Install all CRDs on spoke clusters |
| **15b** | kata.sh | Install Kata Containers + Firecracker + LVM devmapper on spokes |
| **15c** | gvisor.sh | Install gVisor (runsc) runtime on spokes |
| **16** | ocm.sh | Regenerate hub API server certs with WG SANs; join spokes to OCM with MultipleHubs; approve CSRs |
| **17** | ocm.sh | Sync spoke registration to secondaryhub; label spokes with runtime capability labels |

---

## 14. Idempotency Design — How `run_phase` Works

Every phase follows this pattern:

```
run_phase "<num>" "<title>" <check_function> <do_function>
```

```
           ┌──────────────┐
           │  check_fn()  │  exit 0?
           └──────┬───────┘
         yes      │         no
     (skip) <─────┤────────> run do_fn()
                  │
         --force passed?
         yes ─────> always run do_fn()
```

- **Check functions** (`_check_phase_NN`) are fast, read-only predicates. They inspect existing infrastructure state and return 0 if the phase result is already present.
- **Do functions** (`_do_phase_NN_*`) perform the actual work and are safe to run on partially-completed environments.
- **`--force`** bypasses all checks and re-runs every phase unconditionally. Useful when a phase completed but produced wrong output.

This means you can **safely interrupt the script at any point and re-run it** — already-completed phases are instantly skipped, and only the remaining work continues.

---

## 15. The `.sandbox-state` Directory

All runtime state is stored under `.sandbox-state/` next to the script directory. Created at source time, destroyed during teardown.

```
.sandbox-state/
├── pki/
│   ├── ca.crt                              # Shared Kubernetes Root CA (copied from primaryhub)
│   ├── ca.key                              # Root CA private key
│   ├── sa.key                              # Service Account signing key
│   └── sa.pub                              # Service Account public key
├── wg/
│   ├── gateway.key / gateway.pub           # Envoy gateway WireGuard keypair
│   ├── primaryhub.key / primaryhub.pub
│   ├── secondaryhub.key / secondaryhub.pub
│   ├── spoke1.key / spoke1.pub
│   └── spoke2.key / spoke2.pub
├── envoy/
│   ├── envoy.yaml                          # Envoy proxy static configuration (Phase 06)
│   └── wg0.conf                            # WireGuard config for the Envoy gateway container
├── sec/
│   └── postgresql-secondary-cluster.yaml  # Pre-rendered CNPG re-clone manifest (Phase 12)
├── kata-assets/
│   ├── kata-static-<ver>-amd64.tar.xz     # Cached Kata release tarball
│   ├── opt/kata/                           # Extracted Kata binaries and kernel assets
│   └── release-<ver>-x86_64/              # Extracted Firecracker release
├── gvisor-assets/
│   ├── runsc                               # gVisor sandbox kernel binary
│   ├── containerd-shim-runsc-v1            # containerd shim for gVisor
│   └── gvisor-bin/                         # Additional gVisor utilities
├── primaryhub-bootstrap.kubeconfig         # Bootstrap kubeconfig: spoke → primaryhub (Phase 16)
└── secondaryhub-bootstrap.kubeconfig       # Bootstrap kubeconfig: spoke → secondaryhub (Phase 16)
```

**Why a hidden dot-directory?**
- Keeps the project directory clean (hidden by default in `ls`)
- Clearly separates generated runtime state from source code
- Single `rm -rf "$STATE_DIR"` in teardown wipes all state cleanly
- Caching heavy downloads (`kata-assets`, `gvisor-assets`) makes repeated runs drastically faster — Kata static binaries alone are ~500MB
