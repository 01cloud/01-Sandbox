variable "ssh_key_name" {
  description = "Name of the existing AWS Key Pair to attach to EC2 instances"
  type        = string
  default     = "01sandbox-prod-key"
}

variable "ssh_private_key_path" {
  description = "Local path to the SSH private key corresponding to ssh_key_name"
  type        = string
  default     = "~/.ssh/id_rsa"
}

variable "admin_ip_cidr" {
  description = "CIDR block permitted for SSH (port 22) administration (default: all)"
  type        = string
  default     = "0.0.0.0/0"
}

# ── Regions for the 4 Clusters ────────────────────────────────────────────────
variable "region_primaryhub" {
  description = "AWS Region for PrimaryHub + Envoy Gateway (Region A)"
  type        = string
  default     = "us-east-1"
}

variable "region_secondaryhub" {
  description = "AWS Region for SecondaryHub (Region B)"
  type        = string
  default     = "eu-west-1"
}

variable "region_spoke1" {
  description = "AWS Region for Spoke1 Workloads (Region C)"
  type        = string
  default     = "ap-southeast-1"
}

variable "region_spoke2" {
  description = "AWS Region for Spoke2 Workloads (Region D)"
  type        = string
  default     = "us-west-2"
}

# ── Instance Types (Max 8 GiB RAM Capped) ──────────────────────────────────────
variable "instance_type_hubs" {
  description = "Instance type for PrimaryHub and SecondaryHub (Standard EC2, no KVM needed, 8 GiB RAM)"
  type        = string
  default     = "t3.large" # 2 vCPU, 8 GiB RAM
}

variable "instance_type_spokes" {
  description = "Instance type for Spokes with Nitro Nested Virtualization enabled (8 GiB RAM)"
  type        = string
  default     = "m7i-flex.large" # 2 vCPU, 8 GiB RAM (or c7i-flex.xlarge: 4 vCPU, 8 GiB RAM)
}
