# Public IP & Networking Configuration

This document explains how the backend API (running inside a Proxmox VM Kubernetes cluster) is exposed to the internet and how it connects to the external frontend website (`sandbox.01security.com`). 

With the assignment of a dedicated public IP (`148.113.4.247`) to the Proxmox VM, the cluster uses a native Kubernetes approach via **MetalLB** and an **Ingress Controller**, replacing the previous requirement for Cloudflare Tunnels (though tunnels remain a viable alternative).

## Option 1: Native Kubernetes Networking (MetalLB + Ingress) - *Recommended*

This method uses the public IP directly, offering lower latency and native integration with the Kubernetes networking stack.

### 1. MetalLB Configuration
MetalLB is a load-balancer implementation for bare metal Kubernetes clusters. It is responsible for assigning the public IP to your Ingress Controller.

In your `values.yaml`, MetalLB is configured with a single IP Address Pool targeting the `/32` CIDR of your public IP:

```yaml
metallb:
  enabled: true
  namespace: metallb-system
  ipAddressPool:
    addresses:
      - 148.113.4.247/32
  l2Advertisement:
    enabled: true
```

With `l2Advertisement` enabled, MetalLB responds to ARP requests on your local network, declaring that the MAC address of the Kubernetes node owns the IP `148.113.4.247`.

### 2. Ingress Controller
When your Nginx Ingress Controller (or API Gateway) creates a `Service` of `type: LoadBalancer`, MetalLB automatically provisions the public IP for it. 

Any internet traffic hitting `148.113.4.247` on ports 80/443 is now natively routed to the Nginx Ingress Controller, which then forwards the traffic to your backend API services based on the Ingress rules.

### 3. DNS Configuration
To connect the frontend website to the backend:
1. Go to your DNS provider (e.g., Cloudflare, Route53).
2. Create an **A Record** pointing your API domain (e.g., `api.01security.com`) to `148.113.4.247`.

### 4. Updating the Frontend/Dashboard
Update the `DASHBOARD_BACKENDS_JSON` and your Gateway `hostnames` in your Helm configuration to point to the new direct API domain instead of the old trycloudflare tunnel URL.

```yaml
agentgateway:
  httproute:
    hostnames:
      - api.01security.com # Your new domain
```

---

## Option 2: Cloudflare Zero Trust Tunnels (Alternative)

If you prefer not to expose the Proxmox VM directly to the internet (keeping firewall ports 80/443 closed), you can still use Cloudflare Tunnels even with a public IP.

### How it Works
1. A `cloudflared` pod runs inside your Kubernetes cluster.
2. It establishes an outbound, encrypted connection to Cloudflare's edge network.
3. Your DNS records point to the Tunnel UUID (CNAME record).
4. Cloudflare routes traffic from the user, through the tunnel, directly into your cluster.

### Pros of Tunnels
- **Security:** No inbound ports need to be opened on the Proxmox firewall. The public IP `148.113.4.247` remains hidden.
- **SSL Certificates:** Cloudflare handles HTTPS out of the box without needing `cert-manager` inside the cluster.
- **DDoS Protection:** Traffic is scrubbed at Cloudflare's edge before ever reaching your server.

### Cons of Tunnels
- Slightly higher latency due to the routing overhead.
- Dependency on Cloudflare's Zero Trust infrastructure.

## Summary

By configuring **MetalLB** with `148.113.4.247/32`, you successfully unblocked native communication between your externally hosted website and your Proxmox-hosted backend. The frontend simply needs its `.env` or configuration updated to send requests to the domain resolving to this new public IP.
