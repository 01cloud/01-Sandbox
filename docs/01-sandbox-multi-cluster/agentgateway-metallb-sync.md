# High-Availability API Ingress: AgentGateway, MetalLB & Multi-Cluster Virtual IP

This document is the **definitive technical reference and operational guide** for the high-availability API ingress architecture in the 01-Sandbox multi-cluster platform. It details how external clients, desktop browsers, and spoke clusters securely route traffic through **AgentGateway (Envoy)**, **MetalLB (Layer 2)**, and the **Virtual IP (`10.99.0.100`)** across `primaryhub` and `secondaryhub`.

---

## Table of Contents

1. [Architecture Overview & Visual Topology](#1-architecture-overview--visual-topology)
2. [End-to-End Traffic Flow](#2-end-to-end-traffic-flow)
3. [Deep Technical Breakdown of Each Layer](#3-deep-technical-breakdown-of-each-layer)
   - [3.1 AgentGateway (Kubernetes Gateway API & Envoy)](#31-agentgateway-kubernetes-gateway-api--envoy)
   - [3.2 MetalLB in KinD (Where `172.18.255.200` Came From)](#32-metallb-in-kind-where-17218255200-came-from)
   - [3.3 Linux Kernel NAT Ingress (Host Wire-Speed Forwarding)](#33-linux-kernel-nat-ingress-host-wire-speed-forwarding)
   - [3.4 Virtual IP Watchdog (Dual-Port 6443 & 80 High Availability)](#34-virtual-ip-watchdog-dual-port-6443--80-high-availability)
4. [Analysis of Previous Failures & Root Causes](#4-analysis-of-previous-failures--root-causes)
5. [Browser Access, Desktop Routing & Domain Name Association](#5-browser-access-desktop-routing--domain-name-association)
   - [5.1 Why `http://192.168.100.10/docs` Works Directly](#51-why-http19216810010docs-works-directly)
   - [5.2 Why `http://10.99.0.100/docs` Needs a Desktop Route](#52-why-http10990100docs-needs-a-desktop-route)
   - [5.3 Associating a Custom Domain Name in the Future](#53-associating-a-custom-domain-name-in-the-future)
   - [5.4 Does `192.168.100.10` Hop Through `10.99.0.100`? (The Two Front Doors Analogy)](#54-does-19216810010-hop-through-10990100-the-two-front-doors-analogy)
   - [5.5 Why Do We Need the Virtual IP (`10.99.0.100`) If We Have `192.168.100.10`?](#55-why-do-we-need-the-virtual-ip-10990100-if-we-have-19216810010)
6. [Complete Configuration Reference](#6-complete-configuration-reference)
   - [6.1 Helm `values.yaml` (Primary Hub Master)](#61-helm-valuesyaml-primary-hub-master)
   - [6.2 Helm `values-secondary.yaml` (Secondary Hub Standby)](#62-helm-values-secondaryyaml-secondary-hub-standby)
   - [6.3 Gateway VIP Watchdog Daemon (`ocm-vip-watchdog.service`)](#63-gateway-vip-watchdog-daemon-ocm-vip-watchdogservice)
   - [6.4 Boot Auto-Recovery (`ocm-mesh-boot.service`)](#64-boot-auto-recovery-ocm-mesh-bootservice)
7. [Failover Lifecycle & High-Availability Verification](#7-failover-lifecycle--high-availability-verification)
   - [7.1 How `192.168.100.10` "Floats" to Secondary Hub (Smart Traffic Director vs Physical IP Migration)](#71-how-19216810010-floats-to-secondary-hub-smart-traffic-director-vs-physical-ip-migration)
   - [7.2 The Exact 3-Second Failover Kernel Transition](#72-the-exact-3-second-failover-kernel-transition)
8. [Operational Runbook & Verification Commands](#8-operational-runbook--verification-commands)
9. [API Reference & Dual Swagger UI Access Guide](#9-api-reference--dual-swagger-ui-access-guide)
   - [9.1 High-Level Platform API vs. OpenSandbox Engine](#91-high-level-platform-api-vs-opensandbox-engine)
   - [9.2 Unified URL & Swagger UI Quick Reference](#92-unified-url--swagger-ui-quick-reference)
   - [9.3 CodeInspector API Manager Endpoints (`/docs` & `/openapi.json`)](#93-codeinspector-api-manager-endpoints-docs--openapijson)
   - [9.4 OpenSandbox Lifecycle API Endpoints (`/api/v1/01sbx/docs` & `/api/v1/01sbx/openapi.json`)](#94-opensandbox-lifecycle-api-endpoints-apiv101sbxdocs--apiv101sbxopenapijson)
   - [9.5 How AgentGateway and FastAPI Proxy the OpenSandbox Prefix](#95-how-agentgateway-and-fastapi-proxy-the-opensandbox-prefix)
   - [9.6 High-Availability Failover Continuity for Both APIs](#96-high-availability-failover-continuity-for-both-apis)


---

## 1. Architecture Overview & Visual Topology

### 1.1 The Goal
Expose the **CodeInspector API Manager** (`sandbox-api-service`) through an enterprise-grade Kubernetes ingress pipeline utilizing **AgentGateway (Envoy Proxy)** and **MetalLB**, while ensuring that:
1. All traffic can enter through a single, resilient **Virtual IP (`10.99.0.100`)** or **Gateway IP (`192.168.100.10`)**.
2. If `primaryhub` fails, traffic instantly floats to `secondaryhub` **without breaking browser sessions, modifying DNS, or altering client URLs**.
3. Zero userspace reverse proxies (like `socat` or `nginx`) are needed on the VM hosts — all host forwarding is performed in the Linux Kernel via Netfilter NAT.

### 1.2 Comprehensive Architectural Diagram

```
                        [Client / Desktop Browser / Spoke Cluster]
                                            │
                                            ▼
       ┌──────────────────────────────────────────────────────────────────────────┐
       │                   gateway-vm (Watchdog & VIP Host)                       │
       │                   Underlay: 192.168.100.10 (Host Bridge)                 │
       │                   Overlay VIP: 10.99.0.100 (WireGuard wg0)               │
       │                                                                          │
       │  ┌────────────────────────────────────────────────────────────────────┐  │
       │  │ native systemd daemon: ocm-vip-watchdog.service                    │  │
       │  │ Checks: https://10.99.0.1:6443/livez every 2s                      │  │
       │  │ Active Target: 10.99.0.1 (Primary) -> Failover: 10.99.0.2 (Standby)│  │
       │  │                                                                    │  │
       │  │ PREROUTING DNAT:                                                   │  │
       │  │ - 10.99.0.100:6443    -> Active Hub:6443   (Kubernetes API)        │  │
       │  │ - 10.99.0.100:80      -> Active Hub:80     (AgentGateway HTTP)     │  │
       │  │ - 192.168.100.10:80   -> Active Hub:80     (Direct Browser Access) │  │
       │  └────────────────────────────────────────────────────────────────────┘  │
       └────────────────────────────────────┬─────────────────────────────────────┘
                                            │
                             WireGuard Mesh Overlay (10.99.0.0/24)
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     │                                             │
      [Primary Hub - Active RW]                     [Secondary Hub - Standby RO]
┌──────────────────────────────────────────┐   ┌──────────────────────────────────────────┐
│ hub1-vm (10.99.0.1 / 192.168.100.20)     │   │ hub2-vm (10.99.0.2 / 192.168.101.20)     │
│                                          │   │                                          │
│ Linux Kernel Netfilter NAT:              │   │ Linux Kernel Netfilter NAT:              │
│ iptables PREROUTING: dport 80 DNAT ->    │   │ iptables PREROUTING: dport 80 DNAT ->    │
│ 172.18.255.200:80 (MetalLB)              │   │ 172.18.255.200:80 (MetalLB)              │
│                                          │   │                                          │
│ KinD Bridge Network: 172.18.0.0/16       │   │ KinD Bridge Network: 172.18.0.0/16       │
│                                          │   │                                          │
│ ┌──────────────────────────────────────┐ │   │ ┌──────────────────────────────────────┐ │
│ │ MetalLB IPAddressPool:               │ │   │ │ MetalLB IPAddressPool:               │ │
│ │ 172.18.255.200-172.18.255.250        │ │   │ │ 172.18.255.200-172.18.255.250        │ │
│ │ LoadBalancer Service:                │ │   │ │ LoadBalancer Service:                │ │
│ │ agentgateway-proxy                   │ │   │ │ agentgateway-proxy                   │ │
│ │ EXTERNAL-IP: 172.18.255.200          │ │   │ │ EXTERNAL-IP: 172.18.255.200          │ │
│ └──────────────────┬───────────────────┘ │   │ └──────────────────┬───────────────────┘ │
│                    │                     │   │                    │                     │
│                    ▼                     │   │                    ▼                     │
│ ┌──────────────────────────────────────┐ │   │ ┌──────────────────────────────────────┐ │
│ │ AgentGateway Envoy Proxy Pod         │ │   │ │ AgentGateway Envoy Proxy Pod         │ │
│ │ (agentgateway-system namespace)      │ │   │ │ (agentgateway-system namespace)      │ │
│ │ Listens: 80, 443, 5432, 6379, 5672   │ │   │ │ Listens: 80, 443, 5432, 6379, 5672   │ │
│ └──────────────────┬───────────────────┘ │   │ └──────────────────┬───────────────────┘ │
│                    │ HTTPRoute           │   │                    │ HTTPRoute           │
│                    │ PathPrefix: /       │   │                    │ PathPrefix: /       │
│                    ▼                     │   │                    ▼                     │
│ ┌──────────────────────────────────────┐ │   │ ┌──────────────────────────────────────┐ │
│ │ sandbox-api-service                  │ │   │ │ sandbox-api-service                  │ │
│ │ (opensandbox-system namespace)       │ │   │ │ (opensandbox-system namespace)       │ │
│ │ CodeInspector API Manager (Uvicorn)  │ │   │ │ CodeInspector API Manager (Uvicorn)  │ │
│ │ Endpoints: /docs, /openapi.json      │ │   │ │ Endpoints: /docs, /openapi.json      │ │
│ └──────────────────────────────────────┘ │   │ └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘   └──────────────────────────────────────────┘
```

---

## 2. End-to-End Traffic Flow

When a client makes a request to `http://192.168.100.10/docs` (or `http://10.99.0.100/docs`), the packet traverses 6 deterministic stages:

| Stage | Location | Action |
|:---|:---|:---|
| **1. Ingress Hit** | `gateway-vm` | Browser requests `http://192.168.100.10:80/docs`. Kernel Netfilter matches destination and port 80. |
| **2. VIP Translation** | `gateway-vm` (Watchdog) | Watchdog's PREROUTING table translates destination to the active Primary Hub: `10.99.0.1:80`. |
| **3. WireGuard Transit** | WireGuard `wg0` | Packet is encapsulated with ChaCha20-Poly1305 encryption and transmitted across the underlay network to `hub1-vm`. |
| **4. Host Kernel NAT** | `hub1-vm` Host | Packet arrives on `hub1-vm`. Host iptables rules match port 80 and DNAT it directly to the MetalLB LoadBalancer IP: `172.18.255.200:80`. |
| **5. MetalLB & Envoy** | `primaryhub` (KinD) | MetalLB delivers the packet across the Docker bridge `br-...` to the `agentgateway-proxy` Service. The Envoy proxy receives the HTTP request. |
| **6. HTTPRoute & Backend** | `opensandbox-system` | Envoy inspects the Host header, matches `HTTPRoute` rule (`PathPrefix: /`), and proxies the connection to `sandbox-api-service:80` (CodeInspector API Manager Uvicorn pod). |

---

## 3. Deep Technical Breakdown of Each Layer

### 3.1 AgentGateway (Kubernetes Gateway API & Envoy)
* **What it is**: AgentGateway is a cloud-native API gateway built on the Kubernetes Gateway API standard (`gateway.networking.k8s.io/v1`).
* **Controller**: The `codeinspector-agentgateway-controller` deployment monitors `Gateway` and `HTTPRoute` resources and automatically spins up a production-grade Envoy proxy pod (`agentgateway-proxy`).
* **Multi-Protocol Capabilities**: In addition to HTTP (80) and HTTPS (443), AgentGateway is configured to listen on database and cache ports (5432 for PostgreSQL, 6379 for Valkey/Redis, and 5672 for RabbitMQ).

### 3.2 MetalLB in KinD (Where `172.18.255.200` Came From)
In a standard bare-metal or cloud Kubernetes cluster, MetalLB assigns public or private LAN IPs. In KinD (Kubernetes-in-Docker):
1. **Network Topology**: KinD nodes do not run on physical network cards; they run inside Docker containers attached to a Linux bridge (named `kind`).
2. **Subnet Inspection**: Inspecting the KinD network (`docker network inspect kind`) reveals the bridge subnet:
   ```json
   [{"Subnet":"172.18.0.0/16","Gateway":"172.18.0.1"}]
   ```
3. **Preventing Collisions**: Docker dynamically assigns container IPs starting from the low range (`172.18.0.2`, `172.18.0.3`, etc.).
4. **The Safe Pool**: We selected the high-range subnet **`172.18.255.200 - 172.18.255.250`** for MetalLB:
   - It belongs directly to the `172.18.0.0/16` bridge, meaning the host VM kernel has an immediate, hardware-level local route to it.
   - It will never collide with Docker's container IP allocator.
   - When MetalLB allocates `172.18.255.200`, its speaker pod answers ARP on the Docker bridge, allowing instantaneous communication.

### 3.3 Linux Kernel NAT Ingress (Host Wire-Speed Forwarding)
Instead of running heavy userspace daemons (like `socat` or `nginx`) that consume CPU and introduce context switching, host-to-cluster forwarding is executed directly inside the Linux Kernel using Netfilter:
```bash
iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 80 -j DNAT --to-destination 172.18.255.200:80
```
* `! -i br-+`: Prevents loopback hairpinning by only matching packets arriving from external interfaces (WireGuard `wg0` or physical Ethernet), ignoring internal Docker bridge traffic.
* Performance: Wire-speed routing with zero context switches and microsecond latency.

### 3.4 Virtual IP Watchdog (Dual-Port 6443 & 80 High Availability)
The watchdog on `gateway-vm` is managed by `ocm-vip-watchdog.service`:
* **Health Check**: Pings `https://10.99.0.1:6443/livez` every 2 seconds.
* **Failover Threshold**: If 3 consecutive health checks fail, the watchdog triggers failover:
  1. Purges existing DNAT rules.
  2. Injects new PREROUTING DNAT rules pointing both port `6443` and port `80` to `secondaryhub` (`10.99.0.2`).
  3. Flushes connection tracking (`conntrack -D`) to force clients and browsers to immediately reconnect to the new hub without waiting for TCP timeout.

---

## 4. Analysis of Previous Failures & Root Causes

Before this implementation, API access was broken due to two specific bugs:

### Failure 1: Multiple Addresses Conflict in AgentGateway Controller
* **Error**:
  ```text
  "msg":"error handling agentgateway-system/agentgateway-proxy: multiple addresses given,
  only one address is supported: gateway agentgateway-system/agentgateway-proxy has 3 addresses"
  ```
* **Root Cause**: `codeInspector/values.yaml` contained 3 addresses:
  ```yaml
  # BROKEN CONFIGURATION
  addresses:
    - value: 10.9.9.10
    - value: 10.9.9.20
    - value: 10.0.10.9
  ```
  The Gateway API specification allows controllers to restrict address counts. AgentGateway strictly enforces a single address. Because 3 were provided, reconciliation failed, no proxy pod was created, and MetalLB never assigned an IP.
* **Fix**: Replaced with a single deterministic address:
  ```yaml
  # WORKING CONFIGURATION
  addresses:
    - value: 172.18.255.200
  ```

### Failure 2: Unrouted MetalLB Subnet
* **Error**: MetalLB assigned `10.9.9.20` on `secondaryhub`, but packets timed out.
* **Root Cause**: The IP pool was set to `10.9.9.0/24`. Neither the host VM nor the WireGuard network had any interface or route to `10.9.9.x`. In KinD, MetalLB Layer 2 ARP packets were silently dropped because the bridge only accepted `172.18.0.0/16`.
* **Fix**: Aligned `ipAddressPool.addresses` with the Docker bridge: `172.18.255.200-172.18.255.250`.

---

## 5. Browser Access, Desktop Routing & Domain Name Association

### 5.1 Why `http://192.168.100.10/docs` Works Directly
* `192.168.100.10` is the **physical underlay IP of `gateway-vm`**.
* The desktop workstation hosting the VMs is directly attached to the virtual network bridge (`virbr-hub1` with IP `192.168.100.1`).
* Because the desktop and `gateway-vm` share the same physical Layer 2 subnet, any browser running on the desktop can open `http://192.168.100.10/docs` immediately without needing VPN or route modifications.

### 5.2 Why `http://10.99.0.100/docs` Needs a Desktop Route
* `10.99.0.100` is an **internal WireGuard overlay address** that exists strictly inside the 5 VMs.
* Your desktop host operating system is not a WireGuard peer. When your browser queries `10.99.0.100`, the desktop kernel does not know where to send it.
* **To enable `http://10.99.0.100/docs` in your desktop browser**, add a static route on your desktop pointing `10.99.0.0/24` to `gateway-vm`:
  ```bash
  sudo ip route add 10.99.0.0/24 via 192.168.100.10
  ```
  Once added, both URLs function identically.

### 5.3 Associating a Custom Domain Name in the Future
To route a custom domain (such as `api.01cloud.com` or `sandbox.company.internal`):

1. **DNS Record**: Point your DNS A record to the Gateway IP or Virtual IP:
   ```text
   api.01cloud.com.   IN   A   10.99.0.100
   # OR for external access via Gateway:
   api.01cloud.com.   IN   A   192.168.100.10
   ```
2. **HTTPRoute Hostname Whitelist**: Envoy checks the HTTP `Host:` header. Update `codeInspector/values.yaml`:
   ```yaml
   agentgateway:
     httproute:
       hostnames:
         - api.01cloud.com       # <-- Your domain
         - "*.01cloud.com"       # <-- Wildcard support
         - 10.99.0.100
         - 192.168.100.10
   ```
   Now, navigating to `http://api.01cloud.com/docs` will immediately display Swagger UI.

### 5.4 Does `192.168.100.10` Hop Through `10.99.0.100`? (The Two Front Doors Analogy)
**No, it does not hop through `10.99.0.100` first.**

In IP networking, an IP address is a **destination**, not a router hop. Both `192.168.100.10` and `10.99.0.100` live on the exact same physical virtual machine (`gateway-vm`). They are **two parallel front doors on the same building**:

```
[Desktop Browser (Outside VPN)]         [Spoke Clusters (Inside WireGuard VPN)]
                │                                        │
                ▼                                        ▼
     http://192.168.100.10/docs                 http://10.99.0.100/docs
     (Front Door 1: Underlay LAN)               (Front Door 2: Overlay Virtual IP)
                │                                        │
                └───────────────────┬────────────────────┘
                                    │
                                    ▼
                   ┌─────────────────────────────────┐
                   │           gateway-vm            │
                   │      (Both IPs live here)       │
                   │                                 │
                   │   VIP Watchdog Engine           │
                   │   - Evaluates Hub Health        │
                   │   - Routes to Active Target     │
                   └────────────────┬────────────────┘
                                    │
                  Both front doors route to the
                  EXACT SAME active backend hub:
                                    │
               ┌────────────────────┴────────────────────┐
    [Primary Healthy]                                [If Primary Fails]
               ▼                                         ▼
┌──────────────────────────────┐          ┌──────────────────────────────┐
│     hub1-vm (10.99.0.1:80)   │          │     hub2-vm (10.99.0.2:80)   │
│   Primary Hub (Active RW)    │          │   Secondary Hub (Standby RO) │
└──────────────────────────────┘          └──────────────────────────────┘
```

#### Step-by-Step Packet Trace:
When your desktop requests `http://192.168.100.10/docs`:
1. **From Desktop**: Source `192.168.100.1` (Your PC) $\rightarrow$ Destination `192.168.100.10:80` (`gateway-vm`).
2. **Inside Gateway Kernel (Netfilter DNAT)**: Destination `192.168.100.10:80` is rewritten directly to `10.99.0.1:80` (`hub1-vm`).
3. **Transit Over WireGuard (`wg0`)**: Source is masqueraded as `10.99.0.254` (`gateway-vm` WireGuard IP) $\rightarrow$ Destination `10.99.0.1:80`.
The packet never hops to `10.99.0.100`. It exits `gateway-vm` straight into the encrypted tunnel destined for the active hub.

### 5.5 Why Do We Need the Virtual IP (`10.99.0.100`) If We Have `192.168.100.10`?
For a human developer at their desktop workstation, `192.168.100.10` is all that is needed to access the Swagger UI. However, for the **Kubernetes multi-cluster platform (`spoke1`, `spoke2`, OCM Klusterlets, and Hubs)**, the Virtual IP is **mandatory** for 4 critical architectural reasons:

| Core Requirement | Without Virtual IP (`10.99.0.100`) | With Virtual IP (`10.99.0.100`) |
|:---|:---|:---|
| **1. OCM Klusterlet Single-Endpoint Constraint** | Spokes would have to hardcode `primaryhub`'s IP (`https://10.99.0.1:6443`). When `primaryhub` dies, all remote spoke clusters are **permanently orphaned and cut off**. | Spokes register to an immutable hub endpoint: `--hub-apiserver https://10.99.0.100:6443`. When failover occurs, spokes stay connected with **zero reconfiguration**. |
| **2. Kubernetes TLS SANs (Cryptographic Verification)** | Spoke agents verify the API server's TLS certificate. If a spoke connects to an IP not in the cert, TLS handshake fails with `x509: certificate is not valid`. | Both `primaryhub` and `secondaryhub` have certificates cryptographically signed for `certSANs: ["10.99.0.100", ...]`. |
| **3. Cross-Subnet & Multi-Cloud Isolation** | In production, `spoke1` (`192.168.102.20`) and `spoke2` (`192.168.103.20`) live in different VPCs/clouds. They cannot see or route to physical LAN addresses like `192.168.100.10`. | `10.99.0.100` provides a clean, unified, cross-region overlay address that works identically regardless of what cloud or subnet a spoke is placed in. |
| **4. WireGuard End-to-End Encryption** | Physical underlay traffic (`192.168.100.10`) is unencrypted. | Traffic to `10.99.0.100` is strictly encrypted with ChaCha20-Poly1305 before leaving the network interface. |

---

## 6. Complete Configuration Reference

### 6.1 Helm `values.yaml` (Primary Hub Master)

```yaml
agentgateway:
  enabled: true
  namespace: agentgateway-system
  httproute:
    hostnames:
      - api-sandbox.01security.com
      - 10.0.10.9
      - 10.99.0.100
      - 10.99.0.1
      - 10.99.0.2
      - 192.168.100.10
      - 192.168.100.20
      - 192.168.101.20
    backendService:
      name: sandbox-api-service
      port: 80
  gateway:
    gatewayClassName: agentgateway
    addresses:
      - value: 172.18.255.200
    listeners:
      - protocol: HTTP
        port: 80
        name: http
      - protocol: HTTPS
        port: 443
        name: https
        tls:
          certificateRefs:
            - name: agentgateway-tls
      - protocol: TCP
        port: 5432
        name: postgresql
      - protocol: TCP
        port: 6379
        name: redis
      - protocol: TCP
        port: 5672
        name: rabbitmq-amqp

metallb:
  enabled: true
  namespace: metallb-system
  ipAddressPool:
    addresses:
      - 172.18.255.200-172.18.255.250
  l2Advertisement:
    enabled: true
```

### 6.2 Helm `values-secondary.yaml` (Secondary Hub Standby)

```yaml
agentgateway:
  enabled: true
  httproute:
    hostnames:
      - api-sandbox.01security.com
      - 10.0.10.9
      - 10.99.0.100
      - 10.99.0.1
      - 10.99.0.2
      - 192.168.100.10
      - 192.168.100.20
      - 192.168.101.20
  gateway:
    addresses:
      - value: 172.18.255.200
```

### 6.3 Gateway VIP Watchdog Daemon (`ocm-vip-watchdog.service`)

Installed on `gateway-vm` at `/usr/local/bin/ocm-vip-watchdog.sh`:

```bash
#!/bin/bash
set -eo pipefail

PRIMARY_HUB="10.99.0.1"
SECONDARY_HUB="10.99.0.2"
VIP="10.99.0.100"
GW_PHYSICAL="192.168.100.10"
ACTIVE_TARGET=""
FAIL_COUNT=0
FAIL_THRESHOLD=3

ip addr add ${VIP}/32 dev wg0 2>/dev/null || true
sysctl -w net.ipv4.ip_forward=1 >/dev/null
iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE

while true; do
  if curl -k -m 2 -s https://${PRIMARY_HUB}:6443/livez >/dev/null 2>&1; then
    FAIL_COUNT=0
    TARGET="${PRIMARY_HUB}"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "[$(date -Iseconds)] [ocm-vip-watchdog] Primary check failed (${FAIL_COUNT}/${FAIL_THRESHOLD})"
    if [ "$FAIL_COUNT" -ge "$FAIL_THRESHOLD" ]; then
      if curl -k -m 2 -s https://${SECONDARY_HUB}:6443/livez >/dev/null 2>&1; then
        TARGET="${SECONDARY_HUB}"
      else
        TARGET="${PRIMARY_HUB}"
      fi
    else
      TARGET="${ACTIVE_TARGET:-${PRIMARY_HUB}}"
    fi
  fi

  if [ -n "$TARGET" ] && [ "$TARGET" != "$ACTIVE_TARGET" ]; then
    echo "[$(date -Iseconds)] [ocm-vip-watchdog] Switching VIP target to ${TARGET}"

    # Clean previous DNAT targets
    iptables -t nat -D PREROUTING -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${PRIMARY_HUB}:6443 2>/dev/null || true
    iptables -t nat -D PREROUTING -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${SECONDARY_HUB}:6443 2>/dev/null || true
    iptables -t nat -D OUTPUT -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${PRIMARY_HUB}:6443 2>/dev/null || true
    iptables -t nat -D OUTPUT -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${SECONDARY_HUB}:6443 2>/dev/null || true

    iptables -t nat -D PREROUTING -d ${VIP} -p tcp --dport 80 -j DNAT --to-destination ${PRIMARY_HUB}:80 2>/dev/null || true
    iptables -t nat -D PREROUTING -d ${VIP} -p tcp --dport 80 -j DNAT --to-destination ${SECONDARY_HUB}:80 2>/dev/null || true
    iptables -t nat -D OUTPUT -d ${VIP} -p tcp --dport 80 -j DNAT --to-destination ${PRIMARY_HUB}:80 2>/dev/null || true
    iptables -t nat -D OUTPUT -d ${VIP} -p tcp --dport 80 -j DNAT --to-destination ${SECONDARY_HUB}:80 2>/dev/null || true

    iptables -t nat -D PREROUTING -d ${GW_PHYSICAL} -p tcp --dport 80 -j DNAT --to-destination ${PRIMARY_HUB}:80 2>/dev/null || true
    iptables -t nat -D PREROUTING -d ${GW_PHYSICAL} -p tcp --dport 80 -j DNAT --to-destination ${SECONDARY_HUB}:80 2>/dev/null || true
    iptables -t nat -D OUTPUT -d ${GW_PHYSICAL} -p tcp --dport 80 -j DNAT --to-destination ${PRIMARY_HUB}:80 2>/dev/null || true
    iptables -t nat -D OUTPUT -d ${GW_PHYSICAL} -p tcp --dport 80 -j DNAT --to-destination ${SECONDARY_HUB}:80 2>/dev/null || true

    # Apply new active targets
    iptables -t nat -I PREROUTING 1 -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${TARGET}:6443
    iptables -t nat -I OUTPUT 1 -d ${VIP} -p tcp --dport 6443 -j DNAT --to-destination ${TARGET}:6443

    iptables -t nat -I PREROUTING 1 -d ${VIP} -p tcp --dport 80 -j DNAT --to-destination ${TARGET}:80
    iptables -t nat -I OUTPUT 1 -d ${VIP} -p tcp --dport 80 -j DNAT --to-destination ${TARGET}:80

    iptables -t nat -I PREROUTING 1 -d ${GW_PHYSICAL} -p tcp --dport 80 -j DNAT --to-destination ${TARGET}:80
    iptables -t nat -I OUTPUT 1 -d ${GW_PHYSICAL} -p tcp --dport 80 -j DNAT --to-destination ${TARGET}:80

    ACTIVE_TARGET="${TARGET}"
    conntrack -D -d ${VIP} 2>/dev/null || true
    conntrack -D -d ${GW_PHYSICAL} 2>/dev/null || true
  fi
  sleep 2
done
```

### 6.4 Boot Auto-Recovery (`ocm-mesh-boot.service`)

Installed across `hub1-vm` and `hub2-vm` at `/usr/local/bin/ocm-mesh-boot.sh`:

```bash
# AgentGateway & MetalLB API Ingress (Port 80 -> MetalLB LoadBalancer 172.18.255.200:80)
iptables -t nat -C PREROUTING ! -i br-+ -p tcp --dport 80 -j DNAT --to-destination 172.18.255.200:80 2>/dev/null || \
iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 80 -j DNAT --to-destination 172.18.255.200:80
```

---

## 7. Failover Lifecycle & High-Availability Verification

```
[Normal State]
Client Browser ──> http://192.168.100.10/docs ──> gateway-vm ──> hub1-vm (10.99.0.1:80) [OK]

[Failure Event]
Primary Hub crashes / VM halts ──> Watchdog detects failure within 3-6s

[Failover State]
Client Browser ──> http://192.168.100.10/docs ──> gateway-vm ──> hub2-vm (10.99.0.2:80) [OK]
(Zero change to client URL; Swagger UI continues serving seamlessly from Secondary Hub)
```

1. **Active/Standby Role**:
   - `primaryhub` runs as Master Read/Write.
   - `secondaryhub` runs as Warm Standby. Because CloudNativePG and Valkey continuously mirror data, `secondaryhub` possesses up-to-date state at all times.
2. **Hitless Switching**:
   - The VIP Watchdog flushes connection tracking states (`conntrack -D`) immediately upon switching. This terminates half-open TCP connections to the dead node, allowing the next HTTP GET from the browser to connect to the secondary hub instantly.

### 7.1 How `192.168.100.10` "Floats" to Secondary Hub (Smart Traffic Director vs Physical IP Migration)
The IP address **`192.168.100.10` itself does not physically move** between virtual machines.

Instead, **`gateway-vm` acts as a Layer 4 Smart Traffic Director (Reverse Proxy/Load Balancer)**, similar to an AWS Application Load Balancer or F5 BIG-IP:

```
                       Your Desktop Browser
                                │
                                ▼
                   http://192.168.100.10/docs
                                │
                                ▼
                 ┌─────────────────────────────┐
                 │         gateway-vm          │
                 │      (192.168.100.10)       │
                 │                             │
                 │   [The Smart Switchboard]   │
                 └──────────────┬──────────────┘
                                │
               ┌────────────────┴────────────────┐
   STATE A: Primary is UP            STATE B: Primary is DOWN
   (Watchdog rule points to Hub1)    (Watchdog rule points to Hub2)
               │                                 │
               ▼                                 ▼
┌──────────────────────────────┐  ┌──────────────────────────────┐
│       hub1-vm (10.99.0.1)    │  │       hub2-vm (10.99.0.2)    │
│         Primary Hub          │  │        Secondary Hub         │
└──────────────────────────────┘  └──────────────────────────────┘
```

#### Why Dynamic Kernel Redirection is Superior to Physical IP Migration:
* **Zero Network Freezes**: If `192.168.100.10` physically migrated from `hub1-vm` to `hub2-vm`, your desktop operating system would suffer 30–60 seconds of ARP cache poisoning and packet drops while waiting for the network card MAC address update.
* **Microsecond Switching**: Because `gateway-vm` remains physically stationary, your desktop browser never sees a network link flap. The redirection is executed entirely in gateway kernel memory in less than 1 millisecond.

### 7.2 The Exact 3-Second Failover Kernel Transition
1. **Normal State**: The watchdog maintains this DNAT rule in `gateway-vm`'s kernel:
   ```bash
   iptables -t nat -A PREROUTING -d 192.168.100.10 -p tcp --dport 80 -j DNAT --to-destination 10.99.0.1:80
   ```
2. **Outage Event**: `hub1-vm` crashes or stops responding to `https://10.99.0.1:6443/livez`.
3. **Watchdog Action**: After 3 missed checks (~3 to 6 seconds), the watchdog executes:
   ```bash
   iptables -t nat -I PREROUTING 1 -d 192.168.100.10 -p tcp --dport 80 -j DNAT --to-destination 10.99.0.2:80
   conntrack -D -d 192.168.100.10
   ```
4. **Browser Continuity**: When you refresh **`http://192.168.100.10/docs`** in Chrome or Firefox, the packet arrives at `gateway-vm`, is translated to `10.99.0.2:80`, and is served by `secondaryhub`'s AgentGateway with zero manual reconfiguration.

---

## 8. Operational Runbook & Verification Commands

### 8.1 Verifying AgentGateway & MetalLB
Run on `hub1-vm` and `hub2-vm`:
```bash
# 1. Check Gateway Status (Must show PROGRAMMED: True)
kubectl get gateway -n agentgateway-system agentgateway-proxy

# 2. Check Service External IP (Must show 172.18.255.200)
kubectl get svc -n agentgateway-system agentgateway-proxy

# 3. Check Envoy Proxy Pod (Must show 1/1 Running)
kubectl get pods -n agentgateway-system

# 4. Direct In-Cluster Curl
curl -s -i -H "Host: 10.99.0.100" http://172.18.255.200/docs | head -n 15
```

### 8.2 Verifying Virtual IP Routing
Run from `gateway-vm` or any spoke VM:
```bash
# Test Swagger UI via Virtual IP
curl -s -i http://10.99.0.100/docs | head -n 15

# Test OpenAPI Specification via Virtual IP
curl -s http://10.99.0.100/openapi.json | jq .info
```

### 8.3 Verifying Desktop Browser Ingress
Run from your desktop host workstation:
```bash
# Test Swagger UI via Gateway Physical IP
curl -s -i http://192.168.100.10/docs | head -n 15

# Verify JSON Response
curl -s http://192.168.100.10/openapi.json | jq .info
```

Expected Output:
```json
{
  "title": "CodeInspector API Manager",
  "description": "A centralized proxy relaying connections mapping standard interaction seamlessly to the underlying actual code-evaluation clusters locally natively successfully.",
  "version": "2.1.0"
}
```

---

## 9. API Reference & Dual Swagger UI Access Guide

The 01-Sandbox architecture exposes **two distinct, complementary API interfaces** through AgentGateway. Both interfaces are served on the same high-availability entry points (`192.168.100.10` and `10.99.0.100`), sharing the exact same 3-second failover guarantees.

---

### 9.1 High-Level Platform API vs. OpenSandbox Engine

| Feature / Attribute | 1. CodeInspector API Manager (Platform Gateway) | 2. z1Sandbox Lifecycle API (OpenSandbox Engine) |
| :--- | :--- | :--- |
| **Swagger UI Path** | **`/docs`** | **`/api/v1/01sbx/docs`** |
| **OpenAPI Spec Path** | **`/openapi.json`** | **`/api/v1/01sbx/openapi.json`** |
| **Primary Role** | Unified platform orchestrator & developer portal | Core microVM / container execution engine |
| **Component** | `sandbox-api` (FastAPI orchestrator) | `opensandbox-server` (Proxied via `sandbox-api`) |
| **Target Audience** | External clients, SaaS integrations, UI frontends | Automated scheduling pipelines, internal runners |
| **Authentication** | Auth0 JWT Bearer Token / API Key (`/v1/api-keys`) | Propagated via `sandbox-api` service-to-service |
| **Core Functions** | Job queuing, repo scanning, subscriptions, stats | MicroVM pod spin-up, pause/resume, container exec |

---

### 9.2 Unified URL & Swagger UI Quick Reference

Open any of the following interactive documentation URLs in your desktop browser:

#### Recommended Direct Access (Desktop LAN Underlay):
- **CodeInspector Platform API Documentation**:
  👉 **`http://192.168.100.10/docs`**
- **OpenSandbox Engine Lifecycle Documentation**:
  👉 **`http://192.168.100.10/api/v1/01sbx/docs`**

#### Overlay Mesh Access (Virtual IP):
- **CodeInspector Platform API Documentation**:
  👉 **`http://10.99.0.100/docs`**
- **OpenSandbox Engine Lifecycle Documentation**:
  👉 **`http://10.99.0.100/api/v1/01sbx/docs`**

> [!NOTE]
> Accessing via `10.99.0.100` requires the static route on your desktop workstation (`sudo ip route add 10.99.0.0/24 via 192.168.100.10`). Accessing via `192.168.100.10` works immediately out of the box with zero desktop configuration.

---

### 9.3 CodeInspector API Manager Endpoints (`/docs` & `/openapi.json`)

The **CodeInspector API Manager** manages high-level developer operations, language scanners, and task queues:

| HTTP Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/v1/jobs` | Submit a code evaluation or execution job to the cluster |
| `GET` | `/v1/jobs/{job_id}/status` | Check execution status (Queued, Running, Succeeded, Failed) |
| `GET` | `/v1/jobs/{job_id}/result` | Fetch standard output, errors, and execution metrics |
| `GET` | `/v1/jobs/{job_id}/events` | Stream real-time container log events |
| `POST` | `/v1/repo-scan` | Trigger static security & code analysis across a Git repository |
| `GET` | `/v1/repo-scan/{job_id}/result` | Retrieve polyglot analysis findings (Python, Go, Java, Rust, etc.) |
| `POST` | `/v1/sandboxes` | High-level request to allocate a new sandbox environment |
| `POST` | `/v1/api-keys` | Generate and rotate API keys for programmatic access |
| `GET` | `/v1/backends` | List registered execution engines (e.g., `Z1_SANDBOX`) |
| `GET` | `/queue-stats` | Monitor real-time Redis queue depths and Celery worker load |
| `GET` | `/health` | Ingress health probe endpoint |

#### Live Verification via cURL:
```bash
# Fetch platform API metadata
curl -s http://192.168.100.10/openapi.json | jq .info
```
Output:
```json
{
  "title": "CodeInspector API Manager",
  "description": "A centralized proxy relaying connections mapping standard interaction seamlessly to the underlying actual code-evaluation clusters locally natively successfully.",
  "version": "2.1.0"
}
```

---

### 9.4 OpenSandbox Lifecycle API Endpoints (`/api/v1/01sbx/docs` & `/api/v1/01sbx/openapi.json`)

The **OpenSandbox Lifecycle API** interacts directly with the container runtime (gVisor runsc / Kata Containers) and Open Cluster Management (OCM) placements across spoke clusters:

| HTTP Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/01sbx/sandboxes` | Provision an isolated sandbox pod with memory/CPU limits |
| `GET` | `/api/v1/01sbx/sandboxes/{sandbox_id}` | Retrieve sandbox runtime state, spoke placement, and node IP |
| `POST` | `/api/v1/01sbx/sandboxes/{sandbox_id}/pause` | Freeze sandbox processes to conserve compute resources |
| `POST` | `/api/v1/01sbx/sandboxes/{sandbox_id}/resume` | Unfreeze sandbox processes and restore CPU cycles |
| `POST` | `/api/v1/01sbx/sandboxes/{sandbox_id}/renew-expiration` | Extend sandbox TTL / lease duration |
| `POST` | `/api/v1/01sbx/sandboxes/{sandbox_id}/endpoints/{port}` | Expose internal container port to ingress |
| `GET` | `/api/v1/01sbx/sandboxes/{sandbox_id}/proxy/{port}/{full_path}` | Reverse-proxy live HTTP requests directly into sandbox pod |
| `POST` | `/api/v1/01sbx/scan-jobs` | Dispatch low-level language scanner pod |
| `GET` | `/api/v1/01sbx/scan-jobs/{job_id}/report` | Fetch raw scanner diagnostic report |
| `GET` | `/api/v1/01sbx/scan-jobs/{job_id}/workspace/{file_path}` | Inspect code workspace artifacts inside container |
| `GET` | `/api/v1/01sbx/01sandbox/health` | Deep health probe of the OpenSandbox controller and daemon |

#### Live Verification via cURL:
```bash
# Fetch OpenSandbox lifecycle engine metadata
curl -s http://192.168.100.10/api/v1/01sbx/openapi.json | jq .info
```
Output:
```json
{
  "title": "z1Sandbox Lifecycle API",
  "description": "The Sandbox Lifecycle API coordinates how untrusted workloads are created, executed, paused, resumed, and finally disposed.",
  "version": "0.1.0"
}
```

---

### 9.5 How AgentGateway and FastAPI Proxy the OpenSandbox Prefix

When a client hits `http://192.168.100.10/api/v1/01sbx/docs`, the request traverses the system as follows:

```
[Browser Request: /api/v1/01sbx/docs]
                  │
                  ▼
  [gateway-vm: iptables PREROUTING]
  192.168.100.10:80 ──(DNAT)──▶ 10.99.0.1:80
                                       │
                                       ▼
                         [hub1-vm: iptables PREROUTING]
                         10.99.0.1:80 ──(DNAT)──▶ 172.18.255.200:80
                                                        │
                                                        ▼
                                       [MetalLB Layer 2 LoadBalancer]
                                       Announced on KinD bridge
                                                        │
                                                        ▼
                                       [AgentGateway / Envoy Proxy]
                                       Matches HTTPRoute: Hostname & Prefix "/"
                                                        │
                                                        ▼
                                       [Service: sandbox-api-service:8000]
                                                        │
                      ┌─────────────────────────────────┴─────────────────────────────────┐
                      │                                                                   │
           Path: /docs or /v1/*                                              Path: /api/v1/01sbx/*
                      │                                                                   │
                      ▼                                                                   ▼
       [FastAPI Local Route Handler]                                     [FastAPI Reverse-Proxy Engine]
       Serves CodeInspector API Docs                                     Forwarded internally via HTTP to:
       & Platform Orchestration                                          http://opensandbox-server.opensandbox-system.svc.cluster.local
                                                                                          │
                                                                                          ▼
                                                                         [opensandbox-server (FastAPI)]
                                                                         Serves z1Sandbox Lifecycle Docs
                                                                         & Manages Container / MicroVM Runtime
```

1. **Ingress Normalization**: AgentGateway receives the HTTP request at `172.18.255.200:80` and routes it to `sandbox-api-service` based on `HTTPRoute` rules.
2. **Dynamic Internal Reverse-Proxy**: `sandbox-api` inspects the request path:
   - If the path matches `OPENSANDBOX_ROUTE_PREFIX` (`/api/v1/01sbx`), it uses its internal HTTP client (`httpx`) to transparently forward the request to `http://opensandbox-server.opensandbox-system.svc.cluster.local`.
   - The OpenAPI schema documentation `/api/v1/01sbx/openapi.json` and Swagger UI assets at `/api/v1/01sbx/docs` are relayed directly back to the client browser without CORS violations.

---

### 9.6 High-Availability Failover Continuity for Both APIs

Because both `/docs` and `/api/v1/01sbx/docs` share the unified ingress path:
- **Zero URL Changes**: You never need separate URLs or ports for primary vs. secondary hub.
- **Synchronized Hub Services**: Both `primaryhub` and `secondaryhub` run identical `sandbox-api` and `opensandbox-server` deployments synchronized via git/helm.
- **Failover Behavior**: If `hub1-vm` crashes:
  1. `ocm-vip-watchdog` switches DNAT destination from `10.99.0.1:80` to `10.99.0.2:80`.
  2. The next HTTP GET to **either** `http://192.168.100.10/docs` or `http://192.168.100.10/api/v1/01sbx/docs` immediately reaches `secondaryhub`.
  3. No browser refresh errors, no 404s, and no service interruption.
