# Deep Technical Guide: Zero-Trust EC2 Access via AWS SSM Session Manager (`ssm.md`)

> **Target Audience**: Infrastructure Engineers, Cloud Architects, Security Engineers, and Operators.
> **Scope**: AWS Systems Manager (SSM) Session Manager | Terraform Compute Implementation | SSH-over-SSM ProxyCommand | Zero-Inbound Private Subnet Access | Port Forwarding

---

## 1. Architectural Overview & Security Model

In this infrastructure, all EC2 instances (`primaryhub`, `secondaryhub`, `spoke1`, `spoke2`) run in **isolated private subnets with no public IPv4 addresses (`assign_public_ip = false`)**.

Traditional bastion hosts (jump boxes) and inbound SSH access (TCP port 22) are **completely eliminated**. Inbound security group rules allow **zero inbound administrative traffic** from the internet. Instead, all interactive terminal access, automated script orchestration, and Kubernetes API tunneling are powered by **AWS Systems Manager (SSM) Session Manager**.

```
  OPERATOR WORKSTATION
 ┌────────────────────────────────────────────────────────┐
 │ Operator CLI / Scripts / kubectl                      │
 │   • aws ssm start-session                             │
 │   • ssh -o ProxyCommand="aws ssm start-session ..."   │
 │   • session-manager-plugin (Local WebSocket Client)   │
 └───────────────────────────┬────────────────────────────┘
                             │
                             │ HTTPS / TLS (TCP Port 443)
                             │ IAM Authenticated & Authorized
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │            AWS Systems Manager Control Plane           │
 │  • ssm.<region>.amazonaws.com                          │
 │  • ssmmessages.<region>.amazonaws.com (Message Gateway)│
 └───────────────────────────┬────────────────────────────┘
                             │
                             │ Outbound Long-Polling / WebSockets
                             │ (Over NAT Gateway / Private Routes)
                             ▼
  AWS CLOUD: PRIVATE SUBNET (No Public IP | Zero Inbound Open Ports)
 ┌────────────────────────────────────────────────────────────────────────┐
 │ EC2 Instance (primaryhub / secondaryhub / spoke1 / spoke2)            │
 │                                                                        │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │ Security Group (aws_security_group.spot)                         │  │
 │  │  • Inbound: 0 rules from internet (Port 22 closed)               │  │
 │  │  • Outbound: 0.0.0.0/0 (HTTPS 443 via NAT Gateway)               │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 │                                                                        │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │ amazon-ssm-agent (Daemon)                                        │  │
 │  │  • Initiates outbound TLS connection to ssmmessages              │  │
 │  │  • Assumes Instance Profile: AmazonSSMManagedInstanceCore        │  │
 │  │  • Multiplexes terminal PTY / SSH raw stream into local socket   │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 │                                                                        │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │ Operating System Users & KinD Kubernetes Clusters                │  │
 │  │  • ec2-user (Authorized fleet key in ~/.ssh/authorized_keys)    │  │
 │  │  • ssm-user (Spawned by SSM daemon with sudo privileges)         │  │
 │  │  • Docker & KinD control plane (Port 6443 bridged via tunnel)   │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 └────────────────────────────────────────────────────────────────────────┘
```

### Core Security Guarantees:
1. **Zero Attack Surface (No Inbound Listening Ports)**: The instance exposes no open ports to the internet or outside VPCs for administrative access. Scanners, botnets, and brute-force SSH attacks are neutralized.
2. **No Bastion Maintenance**: Eliminates bastion EC2 compute costs, public IP costs, OS patching, and bastion SSH key rotation.
3. **IAM-Governed Access**: Access is controlled exclusively through AWS IAM policies rather than static SSH keys. MFA, temporary credentials, and role assumption are enforced.
4. **Complete Auditability**: Every session start, resume, and terminate event is logged in **AWS CloudTrail**. Session input and output streams can be recorded to encrypted **Amazon S3** buckets or **Amazon CloudWatch Logs**.

---

## 2. Terraform Codebase Implementation: Where and How SSM is Used in Compute

In this repository, SSM is not an external third-party module; it is **natively integrated into the Compute stack** in each regional directory. This design ensures that each region (`us-east-1`, `eu-west-1`, `eu-central-1`, `ap-south-1`) independently provisions and manages its own IAM execution role, instance profile, and Auto Scaling Group lifecycle without cross-region coupling.

### Code Locations:
- **IAM Role & Instance Profile**: [`accounts/sandbox/<region>/compute/main.tf`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/main.tf#L237-L281)
- **Auto Scaling Group Launch Template Binding**: [`accounts/sandbox/<region>/compute/main.tf`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/main.tf#L376-L380)
- **Variables & Feature Flags**: [`accounts/sandbox/<region>/compute/variables.tf`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/variables.tf#L157-L173)
- **Outputs & Dynamic CLI Connect Commands**: [`accounts/sandbox/<region>/compute/outputs.tf`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/outputs.tf#L36-L61)

---

### Exact Terraform Implementation in `compute/main.tf`

#### 1. IAM Role with EC2 Trust Policy
Created when `var.enable_ssm = true`. Grants the EC2 service permission to assume the role:
```hcl
# File: accounts/sandbox/<region>/compute/main.tf

resource "aws_iam_role" "ssm" {
  count = var.enable_ssm ? 1 : 0

  name        = "${var.name}-ssm"
  description = "Lets the sandbox spot instances register with Systems Manager, so they can be reached without SSH or an open port."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  permissions_boundary = var.created_role_boundary_name == null ? null : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.created_role_boundary_name}"
  tags                 = local.common_tags
}
```

#### 2. Managed Policy Attachment (`AmazonSSMManagedInstanceCore`)
Attaches the gold-standard AWS managed policy providing the minimal permissions needed for the SSM agent to communicate with the SSM endpoints, perform health checks, and establish data channels:
```hcl
# File: accounts/sandbox/<region>/compute/main.tf

resource "aws_iam_role_policy_attachment" "ssm" {
  count = var.enable_ssm ? 1 : 0

  role       = aws_iam_role.ssm[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
```

#### 3. Instance Profile Declaration
Wraps the IAM role into an instance profile so it can be associated with EC2 launch templates:
```hcl
# File: accounts/sandbox/<region>/compute/main.tf

resource "aws_iam_instance_profile" "ssm" {
  count = var.enable_ssm ? 1 : 0

  name = "${var.name}-ssm"
  role = aws_iam_role.ssm[0].name
  tags = local.common_tags
}
```

#### 4. Auto Scaling Group Launch Template Binding
The instance profile is passed directly into the Auto Scaling Group module (`terraform-aws-modules/autoscaling/aws`). Key settings:
- `create_iam_instance_profile = false` (Uses our explicitly managed profile).
- `iam_instance_profile_name = var.enable_ssm ? aws_iam_instance_profile.ssm[0].name : null`.
- `key_name = null` by default (No SSH key pair attached).
- `security_groups = [aws_security_group.spot.id]` (Zero inbound ports open from the internet).

```hcl
# File: accounts/sandbox/<region>/compute/main.tf

module "asg" {
  source  = "terraform-aws-modules/autoscaling/aws"
  version = "8.1.0"
  for_each = var.instances

  # ...
  # IAM & SSM
  create_iam_instance_profile = false
  iam_instance_profile_name   = var.enable_ssm ? aws_iam_instance_profile.ssm[0].name : null

  # Key pair (optional, defaults to null for SSM session manager)
  key_name = var.key_pair_name != null ? aws_key_pair.fleet[0].key_name : null
  # ...
}
```

---

### Variable Definition in `compute/variables.tf`

```hcl
# File: accounts/sandbox/<region>/compute/variables.tf

variable "enable_ssm" {
  description = <<-EOT
    Attach an instance profile granting AmazonSSMManagedInstanceCore, so these can be
    reached with Session Manager.

    This is how access is intended to work here: the security group opens NO inbound
    ports and no SSH key is attached, so there is nothing to brute-force and no key to
    leak. The agent connects outbound through the NAT gateway, so no inbound path is
    needed and the instances keep no public IP.

    Turning this off leaves the instances with no access path at all -- which is a valid
    choice for a pure burn-in workload, but say so deliberately.
  EOT
  type        = bool
  default     = true
}
```

---

### Outputs in `compute/outputs.tf`

The stack automatically outputs formatted, copy-pasteable connection commands:

```hcl
# File: accounts/sandbox/<region>/compute/outputs.tf

output "ssm_instance_profile" {
  description = "Instance profile granting Session Manager access, or null when var.enable_ssm is false."
  value       = var.enable_ssm ? aws_iam_instance_profile.ssm[0].name : null
}

output "ssm_session_commands" {
  description = "AWS CLI commands to connect to the active in-service instance of each ASG via SSM."
  value = {
    for k, m in module.asg :
    k => "aws ssm start-session --target $(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names ${m.autoscaling_group_name} --region ${var.region} --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text) --region ${var.region}"
  }
}
```

---

## 3. Under-the-Hood Communication Mechanics

### How Session Manager Connects Without Inbound Ports

1. **Outbound Registration**:
   When an EC2 instance launches, the pre-installed `amazon-ssm-agent` starts. It fetches its IAM credentials from the local Instance Metadata Service (**IMDSv2** at `http://169.254.169.254/latest/meta-data/`).
2. **Control Channel Establishment**:
   The agent initiates an **outbound HTTPS connection** (TLS over TCP port 443) to the regional SSM endpoint:
   - `ssm.<region>.amazonaws.com` (Control plane API)
   - `ssmmessages.<region>.amazonaws.com` (Interactive data stream gateway)
   - `ec2messages.<region>.amazonaws.com` (Routing service)
   Because this is an outbound connection, stateful firewalls (NAT Gateways and Security Groups) permit the return traffic automatically.
3. **Client Initiation**:
   The operator runs `aws ssm start-session --target <instance-id>`.
   AWS IAM validates the caller's credentials and permissions. If authorized, the SSM service creates a WebSocket channel on `ssmmessages.<region>.amazonaws.com`.
4. **Data Multiplexing**:
   The SSM service notifies the instance agent over its existing polling channel. The agent establishes a bidirectional WebSocket stream to `ssmmessages`.
5. **Local Execution**:
   - The agent spawns a pseudo-terminal (`pty`) running `/bin/bash` under the local user `ssm-user`.
   - The local AWS CLI uses the `session-manager-plugin` binary to connect local `stdin`/`stdout` to the SSM WebSocket endpoint.

---

## 4. SSH and SCP over SSM (ProxyCommand)

While native interactive sessions use `aws ssm start-session`, tools like `git`, `ansible`, `scp`, `rsync`, and our multi-cluster orchestration scripts require standard SSH protocol semantics (key-based auth, file transfer, tunneling).

AWS provides the standard SSM document **`AWS-StartSSHSession`** to tunnel raw TCP streams (including SSH on port 22) through the SSM WebSocket data channel.

```
 [Operator: ssh client] ──(stdin/stdout)──► [session-manager-plugin]
                                                    │
                                                    │ TLS WebSocket (Port 443)
                                                    ▼
                                      [AWS SSM Messages Gateway]
                                                    │
                                                    │ TLS WebSocket (Port 443)
                                                    ▼
                                      [amazon-ssm-agent]
                                                    │
                                                    │ Raw TCP Stream (Port 22)
                                                    ▼
                                      [Local sshd daemon on EC2]
```

### The ProxyCommand Configuration
To connect directly via SSH using the instance ID:
```bash
ssh -i ~/.ssh/bb-sandbox-fleet \
  -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region us-east-1" \
  -o StrictHostKeyChecking=no \
  ec2-user@i-09a8a31d16ab0a4f5
```

### Transparent SSH Config (`~/.ssh/config`)
Add this entry to your workstation's `~/.ssh/config` for transparent access across all regions:

```sshconfig
# Catch-all for any AWS EC2 instance reached via SSM
Host i-* mi-*
    IdentityFile ~/.ssh/bb-sandbox-fleet
    User ec2-user
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    ProxyCommand aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region %r
```

With this config, connecting to any private instance is as simple as:
```bash
ssh ec2-user@i-09a8a31d16ab0a4f5
```

---

## 5. How the Multi-Cluster Orchestration Script Uses SSM

The orchestration script in [`prod-docker-multi-script`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/prod-docker-multi-script) interacts with all 4 remote clusters exclusively through SSM:

### 1. Automated Fleet Key Authorization (`ensure_node_ssm_access`)
In [`lib/globals.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/prod-docker-multi-script/lib/globals.sh):
1. Generates a local dedicated fleet key `~/.ssh/bb-sandbox-fleet` if one does not exist.
2. Checks if SSH over SSM connects.
3. If not yet authorized, uses `aws ssm send-command` (`AWS-RunShellScript`) to inject the public key directly into `/home/ec2-user/.ssh/authorized_keys` over the SSM control plane without requiring an open network port.

### 2. High-Performance File Transfers (`remote_copy_to` / `remote_copy_from`)
Transfers KinD configuration manifests, WireGuard configurations (`/etc/wireguard/wg0.conf`), PKI certificates (`ca.crt`, `sa.key`), and Envoy configurations via `scp` encapsulated through the SSM tunnel.

### 3. Container Image Streaming (`remote_pipe_to`)
Pipes multi-hundred-megabyte Docker container images directly from local Docker daemon into the remote container runtime:
```bash
docker save 01sandbox-opensandbox-server:latest | \
  remote_pipe_to "$target_host" "docker exec -i primaryhub-control-plane ctr -n k8s.io images import --local -"
```

### 4. Background SSH Tunnels for `kubectl` (`ensure_cluster_ssh_tunnel`)
In [`lib/clusters.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/prod-docker-multi-script/lib/clusters.sh):
Creates persistent local background tunnels forwarding local ports to the private KinD API servers:
- `127.0.0.1:16443` $\rightarrow$ `primaryhub` (us-east-1) KinD API server
- `127.0.0.1:16444` $\rightarrow$ `secondaryhub` (eu-west-1) KinD API server
- `127.0.0.1:16445` $\rightarrow$ `spoke1` (eu-central-1) KinD API server
- `127.0.0.1:16446` $\rightarrow$ `spoke2` (ap-south-1) KinD API server

Your local workstation can run `kubectl --context kind-primaryhub get pods -A` directly against the private Kubernetes clusters in AWS!

---

## 6. Workstation Prerequisites & Setup

### Step 1: Install the AWS CLI v2
Ensure AWS CLI v2 is installed:
```bash
aws --version
# Should return: aws-cli/2.x.x
```

### Step 2: Install the AWS Session Manager Plugin
The Session Manager plugin is required to handle WebSocket stream multiplexing on your local terminal.

#### Ubuntu / Debian:
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb"
sudo dpkg -i session-manager-plugin.deb
rm session-manager-plugin.deb
```

#### RHEL / Amazon Linux:
```bash
sudo dnf install -y https://s3.amazonaws.com/session-manager-downloads/plugin/latest/linux_64bit/session-manager-plugin.rpm
```

#### macOS:
```bash
brew install --cask session-manager-plugin
```

Verify installation:
```bash
session-manager-plugin
# Expected output: The Session Manager plugin was installed successfully. Use the AWS CLI to start a session.
```

---

## 7. Practical Operator Usage & Common Commands

### 1. Interactive Terminal Shell (Native SSM)
Start an interactive root/admin shell on any instance:
```bash
# Connect to PrimaryHub (us-east-1)
aws ssm start-session --target i-09a8a31d16ab0a4f5 --region us-east-1

# Connect to SecondaryHub (eu-west-1)
aws ssm start-session --target i-02bd225a89ca3f40c --region eu-west-1

# Connect to Spoke1 (eu-central-1)
aws ssm start-session --target i-07ccbbd1912b94450 --region eu-central-1

# Connect to Spoke2 (ap-south-1)
aws ssm start-session --target i-07995773a43fb55e7 --region ap-south-1
```
*Note: This logs in as `ssm-user`. To switch to root: `sudo -i` or `sudo su - ec2-user`.*

---

### 2. Run Non-Interactive Commands Remotely (SSM RunShellScript)
Execute commands directly without opening an interactive session:
```bash
aws ssm send-command \
  --instance-ids i-09a8a31d16ab0a4f5 \
  --region us-east-1 \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["docker ps", "ip a show wg0"]' \
  --output table
```

---

### 3. Port Forwarding (Local Port $\rightarrow$ Remote Instance)
Forward a remote port on the private EC2 instance to your local machine:
```bash
# Forward Envoy Gateway (Port 80 on PrimaryHub) to local port 8080
aws ssm start-session \
  --target i-09a8a31d16ab0a4f5 \
  --region us-east-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["80"],"localPortNumber":["8080"]}'
```
Now navigate to `http://localhost:8080` in your browser to access the remote private service.

---

### 4. Direct SCP File Copy over SSM
Copy files to/from the private EC2 instance without a public IP:
```bash
# Copy local file to remote EC2 instance
scp -i ~/.ssh/bb-sandbox-fleet \
  -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region us-east-1" \
  ./myfile.tar.gz ec2-user@i-09a8a31d16ab0a4f5:/tmp/

# Download remote file from EC2 instance
scp -i ~/.ssh/bb-sandbox-fleet \
  -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region us-east-1" \
  ec2-user@i-09a8a31d16ab0a4f5:/etc/wireguard/wg0.conf ./downloaded-wg0.conf
```

---

## 8. Required IAM Permissions Reference

To use Session Manager, the operator's IAM identity (user or role) requires the following permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:StartSession",
        "ssm:ResumeSession",
        "ssm:TerminateSession"
      ],
      "Resource": [
        "arn:aws:ec2:*:*:instance/*",
        "arn:aws:ssm:*:*:document/AWS-StartSSHSession",
        "arn:aws:ssm:*:*:document/AWS-StartPortForwardingSession"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssm:DescribeInstanceInformation",
        "ssm:GetConnectionStatus",
        "ssm:DescribeSessions",
        "ssm:GetDocument",
        "ec2:DescribeInstances"
      ],
      "Resource": "*"
    }
  ]
}
```

The EC2 instance itself only requires the AWS-managed policy attached to its instance profile:
- **`AmazonSSMManagedInstanceCore`** (Defined in `accounts/sandbox/<region>/compute/main.tf`).

---

## 9. Troubleshooting Playbook

### Issue 1: `TargetNotConnected` or instance does not appear in SSM
**Symptom**: `An error occurred (TargetNotConnected) when calling the StartSession operation.`

**Root Causes & Solutions**:
1. **SSM Agent Not Running**:
   On the instance, verify the service is active:
   ```bash
   sudo systemctl status amazon-ssm-agent
   ```
2. **Missing Instance Profile**:
   Ensure `iam_instance_profile_name` is attached to the instance with `AmazonSSMManagedInstanceCore`.
3. **Outbound Internet/Egress Blocked**:
   The instance in the private subnet must be able to reach `ssmmessages.<region>.amazonaws.com:443`.
   Check that the private route table has a route `0.0.0.0/0` $\rightarrow$ `nat-xxxx` (NAT Gateway).
4. **IMDSv2 Token Access**:
   If IMDSv2 is enforced (`http_tokens = "required"`), verify `http_put_response_hop_limit >= 2`.

---

### Issue 2: `Cannot run program "session-manager-plugin"`
**Symptom**:
```
SessionManagerPlugin is not found. Please refer to SessionManager Documentation here: http://docs.aws.amazon.com/console/systems-manager/session-manager-plugin-not-found
```
**Solution**:
The `session-manager-plugin` binary is not installed on your local machine or not in your `$PATH`. Install it as documented in **Section 6**.

---

### Issue 3: SSH over SSM hangs or exits immediately
**Symptom**: `ssh: Connection closed by remote host` or timeout.

**Root Causes & Solutions**:
1. Check that the `AWS-StartSSHSession` document exists in the specified AWS region.
2. Check that the public key is in `/home/ec2-user/.ssh/authorized_keys` with permissions `600` and owner `ec2-user:ec2-user`.
3. Test native SSM first (`aws ssm start-session --target <instance-id>`). If native SSM works, the issue is solely SSH key authentication on the instance.
