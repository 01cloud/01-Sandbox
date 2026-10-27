# AWS Infrastructure & Security Group Configuration Guide

This document provides a complete guide for the AWS EC2 fleet running the **01-Sandbox Multi-Region Multi-Cluster** infrastructure. It details the required AWS Security Group rules, port mappings, inter-cluster WireGuard overlay networking, and how external traffic reaches services such as the **Swagger API (`/docs`)**.

---

## 1. Fleet Architecture & Regional Topology

The fleet consists of 4 EC2 instances distributed across four distinct AWS regions connected via a full-mesh WireGuard overlay (`10.99.0.0/24`):

| Cluster Role | AWS Region | Host IP (Example) | WireGuard IP | Role Description |
| :--- | :--- | :--- | :--- | :--- |
| **PrimaryHub** | `us-east-1` (N. Virginia) | `34.204.88.237` | `10.99.0.1` | Active Hub, OCM Hub, Envoy Gateway, CodeInspector API |
| **SecondaryHub** | `eu-west-1` (Ireland) | `63.35.228.246` | `10.99.0.2` | Standby Hub, CNPG Standby, Failover Target |
| **Spoke1** | `eu-central-1` (Frankfurt) | `18.185.62.7` | `10.99.0.3` | OCM Managed Workload Cluster (Kata/QEMU) |
| **Spoke2** | `ap-south-1` (Mumbai) | `13.233.13.15` | `10.99.0.4` | OCM Managed Workload Cluster (Kata/QEMU) |

---

## 2. Security Group Specifications

### A. PrimaryHub Security Group (`01sandbox-primaryhub-sg`)
**Region:** `us-east-1`
PrimaryHub hosts the public Envoy Gateway entry point, the OCM Control Plane, and CodeInspector services.

#### Inbound Rules:
| Type | Port Range | Protocol | Source | Description / Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **HTTP** | `80` | `TCP` | `0.0.0.0/0` (or Admin IP) | **Swagger API UI (`/docs`)** and Web Traffic via Envoy Gateway |
| **HTTPS** | `443` | `TCP` | `0.0.0.0/0` (or Admin IP) | Secure Web Gateway Ingress (if TLS terminated) |
| **SSH** | `22` | `TCP` | `Admin IP/32` | Automated orchestration scripts & administration |
| **Custom UDP** | `51820` | `UDP` | `0.0.0.0/0` (or Peer IPs) | **WireGuard Overlay Mesh** (Inter-cluster communication) |
| **Custom TCP** | `6443` | `TCP` | `Admin IP/32` | Direct Kubernetes API Server access (Optional, can tunnel via SSH) |

#### Outbound Rules:
- All traffic (`-1` / all ports) to `0.0.0.0/0`.

---

### B. SecondaryHub Security Group (`01sandbox-secondaryhub-sg`)
**Region:** `eu-west-1`
SecondaryHub acts as the standby failover hub and replication target.

#### Inbound Rules:
| Type | Port Range | Protocol | Source | Description / Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **HTTP** | `80` | `TCP` | `0.0.0.0/0` (or Admin IP) | Standby / Failover Gateway access |
| **HTTPS** | `443` | `TCP` | `0.0.0.0/0` (or Admin IP) | Standby / Failover HTTPS |
| **SSH** | `22` | `TCP` | `Admin IP/32` | Remote execution & orchestration |
| **Custom UDP** | `51820` | `UDP` | `0.0.0.0/0` (or Peer IPs) | **WireGuard Overlay Mesh** |
| **Custom TCP** | `6443` | `TCP` | `Admin IP/32` | Kubernetes API Server (Optional) |

#### Outbound Rules:
- All traffic (`-1` / all ports) to `0.0.0.0/0`.

---

### C. Spoke1 & Spoke2 Security Groups (`01sandbox-spoke1-sg` & `01sandbox-spoke2-sg`)
**Regions:** `eu-central-1` (Frankfurt) & `ap-south-1` (Mumbai)
Spokes are worker clusters running sandboxed workloads under Kata Containers. They do not expose public HTTP endpoints.

#### Inbound Rules:
| Type | Port Range | Protocol | Source | Description / Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **SSH** | `22` | `TCP` | `Admin IP/32` | Remote management from orchestrator |
| **Custom UDP** | `51820` | `UDP` | `0.0.0.0/0` (or Peer IPs) | **WireGuard Overlay Mesh** to Hubs and Spokes |
| **Custom TCP** | `6443` | `TCP` | `Admin IP/32` | Kubernetes API Server (Optional, tunneled by default) |

#### Outbound Rules:
- All traffic (`-1` / all ports) to `0.0.0.0/0`.

---

## 3. Swagger API (`/docs`) Architecture & Flow

### Why `http://<PRIMARYHUB_IP>/docs` Did Not Load Initially:
1. **Envoy Container User Privilege Issue:**
   - The official Docker image `envoyproxy/envoy:v1.31-latest` drops root privileges to user `envoy` (UID `101`) by default.
   - When run with `--net host`, non-root users cannot bind to privileged port `80` (`cannot bind '0.0.0.0:80': Permission denied`), causing Envoy to enter a crash-loop (`Restarting`).
   - **Resolution:** Launch Envoy with `-e ENVOY_UID=0` or `--cap-add=NET_BIND_SERVICE`.

2. **Upstream Target Misconfiguration:**
   - The original `ingress_http_cluster` in Envoy was mistakenly routed to port `30432` (which is PostgreSQL replication), rather than HTTP port `80`.
   - **Resolution:** Route Envoy's HTTP cluster to the active `agentgateway-proxy` LoadBalancer VIP:
     - Priority 0: `172.30.0.200:80` (PrimaryHub MetalLB VIP)
     - Priority 1: `172.30.0.201:80` (SecondaryHub MetalLB VIP)

3. **Host-to-KinD VIP Routing:**
   - On the EC2 host, the MetalLB VIP `172.30.0.200` resides inside the KinD container network.
   - The EC2 host requires a kernel route to send `172.30.0.200` to the KinD node:
     ```bash
     sudo ip route replace 172.30.0.200/32 via $(docker inspect primaryhub-control-plane -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
     ```

### End-to-End Request Flow:
```
[User Browser]
       │  (HTTP Port 80)
       ▼
[AWS Security Group (Port 80 Allowed)]
       │
       ▼
[PrimaryHub EC2 Host (34.204.88.237:80)]
       │
       ▼
[Envoy Gateway Container (--net host, Port 80)]
       │  (Proxy to 172.30.0.200:80)
       ▼
[MetalLB LoadBalancer VIP (agentgateway-proxy)]
       │
       ▼
[sandbox-api Pod (FastAPI / Uvicorn)]
       │  (Returns Swagger UI HTML & JSON schema)
       ▼
[200 OK: CodeInspector API Manager - Docs]
```

---

## 4. Why Database & Internal Ports Do NOT Need Public AWS SG Exposure

In this multi-region architecture:
- **PostgreSQL Replication (NodePort `30432`)**
- **Valkey / Redis Replication (NodePort `30379`)**
- **OCM Cluster Registration & Webhooks (`9443`, `8080`)**

All communicate **exclusively over the encrypted WireGuard overlay tunnel (`10.99.0.0/24`)**.
- WireGuard encapsulates all internal TCP traffic inside **UDP 51820** packets.
- Therefore, you **DO NOT** need to open ports `30432`, `30379`, or internal cluster ports to the public internet in AWS. Only **UDP 51820** and **TCP 80 / 22** are required on the host firewall.

---

## 5. Verification & Troubleshooting Commands

### Test 1: Verify Port 80 Inbound Reachability
From your local workstation:
```bash
curl -I http://34.204.88.237/docs
```
*Expected output:* `HTTP/1.1 200 OK`

### Test 2: Verify WireGuard Mesh Connectivity
On PrimaryHub:
```bash
ping -c 2 10.99.0.2   # SecondaryHub
ping -c 2 10.99.0.3   # Spoke1
ping -c 2 10.99.0.4   # Spoke2
```
*Expected output:* `0% packet loss`

### Test 3: Check Envoy Status & Logs
On PrimaryHub:
```bash
docker ps --filter name=envoy-gateway
docker logs --tail 20 envoy-gateway
```
*Expected output:* Container status is `Up` and logs show `listener 'ingress_http_listener' started`.

---

## 6. AWS CLI Commands to Update Security Groups (Optional)

If creating or updating security groups using the AWS CLI:

```bash
# Allow HTTP (Port 80) for Swagger / Web on PrimaryHub
aws ec2 authorize-security-group-ingress \
  --region us-east-1 \
  --group-id <PRIMARYHUB_SG_ID> \
  --protocol tcp \
  --port 80 \
  --cidr 0.0.0.0/0

# Allow WireGuard Mesh (UDP 51820) across all clusters
for region in us-east-1 eu-west-1 eu-central-1 ap-south-1; do
  aws ec2 authorize-security-group-ingress \
    --region "$region" \
    --group-id <SG_ID_FOR_REGION> \
    --protocol udp \
    --port 51820 \
    --cidr 0.0.0.0/0
done
```

---

## 7. Troubleshooting: AWS SSM Connection Failures During Orchestration

### Problem & Symptoms
When running `./docker-multi-cluster.sh` with private EC2 instances (`USE_SSM="true"`), Phase 01 preflight halts with the following error:

```text
[INFO]    Authorizing SSH key on i-09af7401f1fbdef5d (us-east-1) via AWS SSM RunCommand...
[INFO]    Connecting to remote node 'primaryhub' (i-09af7401f1fbdef5d) via AWS SSM Session Manager...
[ERROR]   Connection to primaryhub (i-09af7401f1fbdef5d) failed!
[ERROR]   Ensure AWS CLI credentials, session-manager-plugin, and SSM agent on the instance are active.
```

### Root Cause Analysis
The orchestrator connects to private EC2 instances without public IPs by tunneling SSH over AWS Systems Manager (`AWS-StartSSHSession` document):

1. **Missing or Expired AWS CLI Credentials in the Terminal Session:**
   - The SSH command relies on a `ProxyCommand`:
     ```bash
     aws ssm start-session --target <instance-id> --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region <region>
     ```
   - Prior to connecting, `ensure_node_ssm_access` runs `aws ssm send-command` to register the fleet SSH key (`~/.ssh/bb-sandbox-fleet.pub`) into `/home/<user>/.ssh/authorized_keys`.
   - If AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) are not exported in the active shell session, both the `send-command` and the SSH `ProxyCommand` fail with:
     ```text
     aws: [ERROR]: An error occurred (NoCredentials): Unable to locate credentials.
     ```
2. **Missing Local `session-manager-plugin`:**
   - AWS CLI requires the AWS Session Manager Plugin binary (`/usr/local/bin/session-manager-plugin`) to establish WebSocket tunnels for interactive sessions and SSH proxying.
3. **OS Username Mismatch:**
   - The user specified by `REMOTE_USER` and `SSH_USER` in `prod.env` must match the default AMI user:
     - Amazon Linux 2 / 2023: `ec2-user`
     - Ubuntu 22.04 / 24.04: `ubuntu`

### Solution

#### Step 1: Export Active AWS Credentials
Export your AWS credentials (or SSO temporary credentials) in the terminal session where you run the script:

```bash
export AWS_ACCESS_KEY_ID="<your-access-key-id>"
export AWS_SECRET_ACCESS_KEY="<your-secret-access-key>"
export AWS_SESSION_TOKEN="<your-session-token>"
```

Confirm that the AWS identity is active:
```bash
aws sts get-caller-identity
```

#### Step 2: Verify `session-manager-plugin` Installation
Ensure the Session Manager plugin is installed on your local workstation:
```bash
session-manager-plugin
```
*If missing on Ubuntu/Debian:*
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb"
sudo dpkg -i session-manager-plugin.deb
rm session-manager-plugin.deb
```

#### Step 3: Verify Remote EC2 SSM Agent Status
Confirm that the instance is reporting `Online` in SSM:
```bash
aws ssm describe-instance-information \
  --region us-east-1 \
  --query "InstanceInformationList[?InstanceId=='i-09af7401f1fbdef5d'].[InstanceId,PingStatus,PlatformName]" \
  --output table
```

#### Step 4: Test Interactive Session & Key Authorization
Test an interactive session directly:
```bash
aws ssm start-session --target i-09af7401f1fbdef5d --region us-east-1
```

#### Step 5: Verify OS User in `prod.env`
Check `prod.env` to ensure `REMOTE_USER` matches the instance OS:
```bash
# For Amazon Linux 2023:
REMOTE_USER="ec2-user"
SSH_USER="ec2-user"

# For Ubuntu:
# REMOTE_USER="ubuntu"
# SSH_USER="ubuntu"
```

#### Step 6: Handle EC2 Spot Replacements (`TargetNotConnected`)
If using Spot instances managed by Auto Scaling Groups, an instance may be interrupted or replaced while the cluster is running.
- **Symptom:** `aws: [ERROR]: An error occurred (TargetNotConnected) when calling the StartSession operation: <instance_id> is not connected.`
- **Cause:** The previous instance was terminated and replaced with a new instance ID.
- **Resolution:**
  1. Inspect running instances in the region:
     ```bash
     aws ec2 describe-instances --region <region> \
       --filters "Name=tag:Name,Values=<cluster-role>" "Name=instance-state-name,Values=running" \
       --query "Reservations[*].Instances[*].[InstanceId,State.Name,LaunchTime]" --output table
     ```
  2. The script's `lib/globals.sh` now automatically queries AWS EC2 by tag (`Name=secondaryhub`, `Name=spoke1`, etc.) if the configured instance ID is terminated. You can also update `prod.env` directly with the new active ID.

#### Step 7: Re-run Orchestration Script
Run the multi-cluster script in the authenticated shell:
```bash
./docker-multi-cluster.sh
```
