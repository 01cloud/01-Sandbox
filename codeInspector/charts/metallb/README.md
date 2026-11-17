# MetalLB Load Balancer Helm Chart (`metallb`)

## 1. Overview & Problem Statement

When deploying Kubernetes on managed cloud platforms (such as AWS EKS, Google GKE, or Azure AKS), creating a Service with `type: LoadBalancer` automatically calls cloud provider APIs to spin up an external load balancer (such as an AWS Network Load Balancer).

However, on **bare-metal servers, edge nodes, on-premise hardware, or KinD (Kubernetes in Docker) development clusters**, Kubernetes does not include a built-in network load balancer implementation. As a result, any Service configured with `type: LoadBalancer` remains permanently stuck in `<pending>` state with no external IP assigned.

**MetalLB** solves this fundamental problem. It provides a software-defined network load balancer implementation that hooks into standard Kubernetes Services, allocating virtual IP addresses from a predefined pool and advertising them over the local physical network using standard routing protocols (Layer 2 ARP/NDP or BGP).

```
                      [ External Client Network Traffic ]
                                      │
                                      ▼
                        ARP Request for 172.30.0.201
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │ MetalLB Speaker DaemonSet     │ (Responds with node's MAC addr)
                      │ (Leader node on Layer 2 Mesh) │
                      └───────────────┬───────────────┘
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │ Kubernetes Service (Port 80)  │
                      │ Type: LoadBalancer            │
                      │ Assigned IP: 172.30.0.201     │
                      └───────────────┬───────────────┘
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │   Target Backend Pods         │
                      │   (AgentGateway Proxy)        │
                      └───────────────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **Service Types:**
  - `ClusterIP` (Default): Only reachable from within the Kubernetes cluster network.
  - `NodePort`: Opens a high port (30000-32767) on all node IP addresses.
  - `LoadBalancer`: Allocates a dedicated, single external IP address directly mapped to standard ports (like 80 or 443).
- **MetalLB Controller (Deployment):** Watches the cluster for newly created Services of type `LoadBalancer`. When detected, it allocates an available IP from the configured `IPAddressPool` and assigns it to `service.status.loadBalancer.ingress`.
- **MetalLB Speaker (DaemonSet):** Runs on every node in the cluster. In Layer 2 mode, one speaker node is elected as the "leader" for an allocated IP and responds to ARP (IPv4) or NDP (IPv6) requests from the local network switch.
- **Custom Resource Definitions (CRDs):**
  - `IPAddressPool` (`metallb.io/v1beta1`): Defines a contiguous block or CIDR of IPv4/IPv6 addresses available for assignment.
  - `L2Advertisement` (`metallb.io/v1beta1`): Instructs the speaker nodes to broadcast the assigned IPs over Layer 2 (ARP).

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/metallb/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/metallb/templates/) deploy:

### 3.1. [metallb-manifests.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/metallb/templates/metallb-manifests.yaml)
- **Kind:** Multiple core resources:
  - `ServiceAccount`: `controller` and `speaker` in namespace `metallb-system`.
  - `ClusterRole` & `RoleBinding`: Grants controller permissions to read/update Services, and speaker permissions to query Endpoints and nodes.
  - `Deployment`: `metallb-controller` managing IP allocations.
  - `DaemonSet`: `metallb-speaker` running with host networking to handle ARP broadcasts.
  - `Secret`: Memberlist encryption key for speaker cluster consensus.

### 3.2. [ipaddresspool.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/metallb/templates/ipaddresspool.yaml)
- **Kind:** `IPAddressPool` (`metallb.io/v1beta1`)
- **Metadata:** Name `example` in namespace `metallb-system`.
- **Functionality:**
  - Declares the range of IPs allocated to LoadBalancer services, populated from `.Values.ipAddressPool.addresses` (default: `172.30.0.200-172.30.0.220`).

### 3.3. [l2advertisement.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/metallb/templates/l2advertisement.yaml)
- **Kind:** `L2Advertisement` (`metallb.io/v1beta1`)
- **Metadata:** Name `empty`.
- **Functionality:**
  - Instructs the speaker daemonset to advertise all IPAddressPools using Layer 2 ARP emulation across the node network interface.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | string | `"metallb-system"` | Namespace where MetalLB is installed. |
| `ipAddressPool.addresses` | list | `["172.30.0.200-172.30.0.220"]` | Range of IPs available for LoadBalancer assignment. |
| `l2Advertisement.enabled` | boolean | `true` | Enables Layer 2 ARP broadcasting mode. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Edge IP Assignment in Local & KinD Environments:** During automated testing, local development, or staging setups running on EC2 bare-metal instances, MetalLB assigns stable private IPs to the `AgentGateway` proxy or `Ingress` controllers without needing AWS Elastic Load Balancers.
- **Predictable Network Testing:** Allows WireGuard mesh endpoints and DNS forwarders to target deterministic IPs (such as `172.30.0.200`) across test environments.

---

## 6. Practical Debugging & Troubleshooting

### Check MetalLB Controller & Speaker Status
```bash
# Check running pods in metallb-system
kubectl get pods -n metallb-system -o wide

# Check controller logs for IP allocation events
kubectl logs -n metallb-system -l app=metallb,component=controller -f --tail=100

# Check speaker logs for ARP announcements
kubectl logs -n metallb-system -l app=metallb,component=speaker -f --tail=100
```

### Inspect IP Allocation on Services
```bash
# Verify that a LoadBalancer Service received an external IP
kubectl get svc -A | grep LoadBalancer

# Describe the service to see MetalLB events
kubectl describe svc -n agentgateway-system agentgateway-proxy
```

### Common Failure Modes
1. **Service Remains in `<pending>` External IP:** The IP pool is exhausted or does not match the requested IP family (IPv4 vs. IPv6). Check `kubectl describe ipaddresspool -n metallb-system`.
2. **ARP Collision / Duplicate IP on Network:** The range configured in `ipAddressPool.addresses` overlaps with existing physical network devices or DHCP ranges. Adjust the pool range to an unused subnet slice.
3. **Speaker Cannot Broadcast (Blocked ARP):** Cloud hypervisors or security groups may block promiscuous mode or foreign MAC addresses. In AWS VPCs, use Layer 2 only within the internal Docker/KinD bridge network, not across native AWS ENIs.
