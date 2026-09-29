# The Failover Front Door: Role of Envoy Gateway in 01-Sandbox

Envoy Gateway is the **failover front door** for the multi-hub architecture — a single, fixed address that external clients, `kubectl`, and OCM spoke clusters talk to, which transparently and instantly routes to whichever management hub is currently alive.

---

## 1. High-Level Architecture: How the Pieces Fit Together

Spoke clusters and external operators never connect directly to the underlying Kubernetes API or ingress endpoints of the individual hubs. Instead, all traffic targets the **High-Availability Virtual IP (`VIP: 10.99.0.100`)** exposed on the WireGuard overlay (`wg0`).

```
┌─────────────────────────────────────────────────────────────┐
│          CLIENTS, KUBECTL & SPOKE CLUSTERS                  │
│    (klusterlets configured with: https://10.99.0.100:6443)  │
└──────────────────────────────┬──────────────────────────────┘
                               │ WireGuard Mesh (10.99.0.0/24)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│               ENVOY GATEWAY (VIP: 10.99.0.100)              │
│       - Plain Layer-4 TCP Proxy (tcp_proxy)                 │
│       - Listens on 0.0.0.0 (all host/container interfaces)  │
│       - Active TCP Health Checks every 1s                   │
│       - Connection recycling & instant drop on failure      │
└──────────────┬──────────────────────────────┬───────────────┘
               │ Priority 0 (Active)          │ Priority 1 (Failover)
               ▼                              ▼
┌──────────────────────────────┐┌─────────────────────────────┐
│    PRIMARYHUB                ││   SECONDARYHUB              │
│  - Kube-API :6443 (10.99.0.1)││  - Kube-API :6443 (10.99.0.2)│
│  - MetalLB VIP: 172.30.0.200 ││  - MetalLB VIP: 172.30.0.201│
│       │ (Port 80)            ││       │ (Port 80)           │
│       ▼                      ││       ▼                     │
│    AgentGateway Proxy        ││    AgentGateway Proxy       │
│    (Rate Limit 7 req/min)    ││    (Rate Limit 7 req/min)   │
│       │                      ││       │                     │
│       ▼                      ││       ▼                     │
│    sandbox-api (ClusterIP)   ││    sandbox-api (ClusterIP)  │
└──────────────────────────────┘└─────────────────────────────┘
```

---

## 2. What Envoy Actually Does (Layer-4 TCP Proxying)

Envoy runs as a **pure Layer-4 TCP proxy** (using Envoy’s `tcp_proxy` network filter, completely protocol-agnostic and not HTTP-aware). It listens on `0.0.0.0` so it accepts traffic arriving on any interface reachable on the host or container (including physical interfaces, transit subnets, and `wg0`).

It terminates downstream TCP connections and proxies them across two dedicated ports:

1. **Port `80` (Application Ingress Traffic -> MetalLB -> AgentGateway)**:
   - Forwards to the active hub's MetalLB LoadBalancer VIP on port `80` (`172.30.0.200:80` for PrimaryHub, failing over to `172.30.0.201:80` for SecondaryHub).
   - MetalLB routes traffic to `agentgateway-proxy`, which evaluates L7 policies (rate limiting, auth, /metrics deny) before forwarding to `sandbox-api-service` (ClusterIP).

2. **Port `6443` (Kubernetes API Server Traffic)**:
   - Forwards to whichever hub is healthy on port `6443`.
   - This is the endpoint that `kubectl`, internal services, and spoke cluster `klusterlet` agents connect to.

### Priority-Based Backend Pools (Clusters)
Each listener routes to its own Envoy cluster (backend pool) defining two endpoints differentiated by priority:
- **`priority: 0`**: `primaryhub` (`10.99.0.1`) — Primary / Active endpoint.
- **`priority: 1`**: `secondaryhub` (`10.99.0.2`) — Standby / Failover endpoint.

Envoy’s priority-based load balancing guarantees:
- **100% of traffic routes to Priority 0 (`primaryhub`)** as long as it is passing active health checks.
- **Failover to Priority 1 (`secondaryhub`) occurs only if all Priority 0 endpoints become unhealthy.**
- When `primaryhub` recovers and passes health checks, traffic automatically and seamlessly falls back to Priority 0.

---

## 3. How Envoy Knows a Hub Is Down (Sub-Second Active Health Checks)

Rather than relying on passive error rates or waiting for client TCP timeouts, each Envoy cluster executes an active TCP health check:

```yaml
health_checks:
- timeout: 1s
  interval: 1s
  unhealthy_threshold: 2
  healthy_threshold: 2
  tcp_health_check: {}
```

- **Health Check Mechanism**: Envoy initiates a raw TCP connection handshake every second (`interval: 1s`) with a 1-second timeout (`timeout: 1s`).
- **Failure Threshold (`unhealthy_threshold: 2`)**: Two consecutive failed connection attempts immediately mark the endpoint as unhealthy (~2 seconds total detection time).
- **Recovery Threshold (`healthy_threshold: 2`)**: Two consecutive successful connection attempts mark an endpoint healthy again.
- **Instant Connection Killing (`close_connections_on_host_health_failure: true`)**:
  The moment a backend host is marked unhealthy, Envoy **actively terminates and kills all existing in-flight downstream connections** routed to that host.
  - *Why this matters*: Without this directive, existing TCP connections would hang indefinitely or wait for OS TCP keepalive timeouts (often minutes). Killing them immediately forces downstream clients to experience an immediate disconnect and trigger an instant reconnect to the failover target (`secondaryhub`).

---

## 4. Why Port 6443 Has Extra Settings the HTTP Listener Doesn't

In the `ingress_kube_api_listener` (port 6443), two critical timeout parameters are configured that are absent from the HTTP listener:

```yaml
- name: ingress_kube_api_listener
  filter_chains:
  - filters:
    - name: envoy.filters.network.tcp_proxy
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.filters.network.tcp_proxy.v3.TcpProxy
        stat_prefix: ingress_kube_api
        cluster: ingress_kube_api_cluster
        idle_timeout: 5s
        max_downstream_connection_duration: 60s
```

### The Kubernetes "Long-Lived Watch" Problem
Kubernetes API clients (`kubectl`, controllers, and OCM spoke agents) make extensive use of **HTTP/2 long-lived streaming connections and Informer watches**. By design, a watch connection stays open indefinitely as long as packets flow.

Without bounded connection durations:
- If `primaryhub` fails or becomes partially degraded, clients that established connections prior to the event might hang on stale TCP sessions and never trigger a DNS re-lookup or new TCP handshake.
- The client would remain blind to the fact that `secondaryhub` has taken over.

### The Solution: Periodic Connection Recycling
- **`max_downstream_connection_duration: 60s`**: Forces every client-to-Envoy TCP connection to gracefully terminate and recycle at least once every 60 seconds.
- **`idle_timeout: 5s`**: Closes connections that have been inactive for 5 seconds.

These constraints force Kubernetes API clients and spoke agents to periodically reopen their TCP connections against the VIP. If a failover occurred during that window, the subsequent connection immediately resolves to the new active hub (`secondaryhub`), ensuring swift and automatic discovery of the failover event. *(Note: This setting is also critical during spoke bootstrap to prevent OCM `clusteradm join` registration timeouts).*

---

## 5. Deployment Flavors: Production VM vs. KinD Lab Setup

The Envoy gateway architecture is implemented symmetrically across two deployment environments:

### A. Bare-Metal / Standalone Gateway VM (`codeInspector/gateway/deploy-gateway.sh`)
In multi-VM environments, Envoy runs on a dedicated gateway host (`gateway-vm`):
- **Lifecycle Management**: Managed as a systemd unit (`envoy-gateway.service`) with `Restart=always` and `RestartSec=3` (automatic relaunch on crashes or host reboot).
- **Host Networking (`--net=host`)**:
  Envoy binds directly to the VM's network stack rather than a container bridge network. This is mandatory because the Virtual IP (`10.99.0.100/32`) and the WireGuard interface (`wg0`) exist in the host network namespace.
- **Dynamic Templating**:
  [`deploy-gateway.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/gateway/deploy-gateway.sh) dynamically injects the primary and secondary hub IPs from CLI flags, environment files (`cluster.env`), or defaults into `envoy.yaml.template`, pushing the configuration and systemd service via SSH/SCP.

### B. Containerized KinD Multi-Cluster (`docker-multi-cluster.sh` / `phase_06_envoy.sh`)
In the local development environment:
- **Container**: `envoy-gateway` running custom image `01sandbox-envoy:v1`.
- **Pre-Baked Offline Image**:
  Standard Envoy Docker images (`envoyproxy/envoy:v1.31-latest`) lack kernel networking tools. `docker-multi-cluster.sh` builds `01sandbox-envoy:v1` with `wireguard-tools`, `iproute2`, `iptables`, `curl`, and `procps` pre-installed.
- **Sub-Second Boot**:
  On startup, the container executes:
  ```bash
  wg-quick up wg0 && envoy -c /etc/envoy/envoy.yaml
  ```
  It establishes `wg0` and initializes the proxy in **under 1 second** completely offline, without needing runtime `apt-get` downloads.

Both implementations share the identical YAML shape, health-checking rules, priority configurations, and WireGuard VIP topology.

---

## 6. Evolution: Why Envoy Replaced Legacy Watchdogs & iptables DNAT

Prior to introducing Envoy, the sandbox relied on a custom shell-based watchdog service (`ocm-vip-watchdog.service`) and raw kernel `iptables` DNAT rules.

[`deploy-gateway.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/gateway/deploy-gateway.sh#L109-L122) explicitly tears down this legacy infrastructure during deployment:
```bash
# Disable legacy watchdog
sudo systemctl disable --now ocm-vip-watchdog.service 2>/dev/null || true

# Flush legacy iptables DNAT rules for ports 80 and 6443
for PORT in 80 6443; do
  sudo iptables -t nat -D PREROUTING -d "${GATEWAY_IP}" -p tcp --dport ${PORT} -j DNAT --to-destination "${PRIMARY_HUB_IP}:${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D PREROUTING -d "${VIP}"        -p tcp --dport ${PORT} -j DNAT --to-destination "${PRIMARY_HUB_IP}:${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D OUTPUT     -d "${GATEWAY_IP}" -p tcp --dport ${PORT} -j DNAT --to-destination "${PRIMARY_HUB_IP}:${PORT}" 2>/dev/null || true
  sudo iptables -t nat -D OUTPUT     -d "${VIP}"        -p tcp --dport ${PORT} -j DNAT --to-destination "${PRIMARY_HUB_IP}:${PORT}" 2>/dev/null || true
done
```

### Why iptables DNAT Was Replaced:
1. **Zero Health Awareness**:
   Kernel `iptables` DNAT rules blindly rewrite destination IP addresses in packet headers. If the target hub goes down, iptables continues silently routing packets into a black hole with no capability to detect failure or reroute.
2. **Brittle Shell Watchdog**:
   `ocm-vip-watchdog.service` ran coarse bash loops (`curl` or `ping`) to detect outages and execute shell commands to rewrite iptables rules. This introduced race conditions, zombie rules, and multi-second outages.
3. **No Connection Draining**:
   Rewriting iptables rules does not clean up Linux conntrack tables, leading to stalled or reset TCP connections.
4. **Envoy's Core Improvement**:
   Envoy solves this natively in user-space with **active TCP health checks**, **priority-tiered backends**, and **instant connection termination** (`close_connections_on_host_health_failure`), delivering clean, sub-second, automated failover.

---

## 7. Diagnostics & Operational Verification

### Check Envoy Health & Upstream Status
```bash
# On the Gateway VM (or via host port 10000 on KinD):
curl -s http://127.0.0.1:9901/clusters | grep -E "health_flags|priority"

# Example output when PrimaryHub is active:
# ingress_kube_api_cluster::10.99.0.1:6443::health_flags::healthy
# ingress_kube_api_cluster::10.99.0.1:6443::priority::0
# ingress_kube_api_cluster::10.99.0.2:6443::health_flags::healthy
# ingress_kube_api_cluster::10.99.0.2:6443::priority::1
```

### Test Failover In Real Time
1. Stop the active primary hub:
   ```bash
   docker stop primaryhub-control-plane  # (or systemctl stop on VM)
   ```
2. Inspect Envoy cluster status:
   ```bash
   curl -s http://127.0.0.1:9901/clusters | grep ingress_kube_api_cluster
   # Notice 10.99.0.1:6443 transitions to failed_active_hc within ~2 seconds.
   ```
3. Verify client connectivity:
   ```bash
   kubectl --server=https://10.99.0.100:6443 get nodes
   # Seamlessly responds from secondaryhub.
   ```
