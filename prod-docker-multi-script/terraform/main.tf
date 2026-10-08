terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.4"
    }
  }
}

# ── Regional Providers ────────────────────────────────────────────────────────
provider "aws" {
  alias  = "hub1"
  region = var.region_primaryhub
}

provider "aws" {
  alias  = "hub2"
  region = var.region_secondaryhub
}

provider "aws" {
  alias  = "spoke1"
  region = var.region_spoke1
}

provider "aws" {
  alias  = "spoke2"
  region = var.region_spoke2
}

# ── Ubuntu 24.04 LTS AMIs ──────────────────────────────────────────────────────
data "aws_ami" "ubuntu_hub1" {
  provider    = aws.hub1
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

data "aws_ami" "ubuntu_hub2" {
  provider    = aws.hub2
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

data "aws_ami" "ubuntu_spoke1" {
  provider    = aws.spoke1
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

data "aws_ami" "ubuntu_spoke2" {
  provider    = aws.spoke2
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

# ── Security Groups ───────────────────────────────────────────────────────────
# PrimaryHub + Envoy Security Group
resource "aws_security_group" "sg_primaryhub" {
  provider    = aws.hub1
  name        = "01sandbox-primaryhub-sg"
  description = "PrimaryHub and Envoy Gateway security group"

  # Public Ingress for Website & Gateway
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Public HTTP"
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Public HTTPS"
  }

  # WireGuard Overlay Mesh
  ingress {
    from_port   = 51820
    to_port     = 51820
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "WireGuard UDP Mesh"
  }

  # SSH Administration
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_cidr]
    description = "SSH Access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "01sandbox-primaryhub-sg" }
}

# SecondaryHub Security Group
resource "aws_security_group" "sg_secondaryhub" {
  provider    = aws.hub2
  name        = "01sandbox-secondaryhub-sg"
  description = "SecondaryHub security group"

  ingress {
    from_port   = 51820
    to_port     = 51820
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "WireGuard UDP Mesh"
  }
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_cidr]
    description = "SSH Access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "01sandbox-secondaryhub-sg" }
}

# Spoke1 Security Group
resource "aws_security_group" "sg_spoke1" {
  provider    = aws.spoke1
  name        = "01sandbox-spoke1-sg"
  description = "Spoke1 Workload Cluster security group"

  ingress {
    from_port   = 51820
    to_port     = 51820
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "WireGuard UDP Mesh"
  }
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_cidr]
    description = "SSH Access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "01sandbox-spoke1-sg" }
}

# Spoke2 Security Group
resource "aws_security_group" "sg_spoke2" {
  provider    = aws.spoke2
  name        = "01sandbox-spoke2-sg"
  description = "Spoke2 Workload Cluster security group"

  ingress {
    from_port   = 51820
    to_port     = 51820
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "WireGuard UDP Mesh"
  }
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_cidr]
    description = "SSH Access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "01sandbox-spoke2-sg" }
}

# ── EC2 Instances ─────────────────────────────────────────────────────────────
# 1. PrimaryHub + Envoy Gateway (Region A)
resource "aws_instance" "primaryhub" {
  provider      = aws.hub1
  ami           = data.aws_ami.ubuntu_hub1.id
  instance_type = var.instance_type_hubs
  key_name      = var.ssh_key_name

  vpc_security_group_ids = [aws_security_group.sg_primaryhub.id]

  root_block_device {
    volume_size           = 40
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name = "01sandbox-primaryhub"
    Role = "control-plane-primary"
  }
}

resource "aws_eip" "eip_primaryhub" {
  provider = aws.hub1
  instance = aws_instance.primaryhub.id
}

# 2. SecondaryHub Backup (Region B)
resource "aws_instance" "secondaryhub" {
  provider      = aws.hub2
  ami           = data.aws_ami.ubuntu_hub2.id
  instance_type = var.instance_type_hubs
  key_name      = var.ssh_key_name

  vpc_security_group_ids = [aws_security_group.sg_secondaryhub.id]

  root_block_device {
    volume_size           = 40
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name = "01sandbox-secondaryhub"
    Role = "control-plane-secondary"
  }
}

resource "aws_eip" "eip_secondaryhub" {
  provider = aws.hub2
  instance = aws_instance.secondaryhub.id
}

# 3. Spoke1 Workload Cluster (Region C - Nitro Nested Virtualization Enabled)
resource "aws_instance" "spoke1" {
  provider      = aws.spoke1
  ami           = data.aws_ami.ubuntu_spoke1.id
  instance_type = var.instance_type_spokes
  key_name      = var.ssh_key_name

  # Enable Intel VT-x hardware pass-through for Kata Firecracker /dev/kvm
  cpu_options {
    nested_virtualization = "enabled"
  }

  vpc_security_group_ids = [aws_security_group.sg_spoke1.id]

  root_block_device {
    volume_size           = 50
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name = "01sandbox-spoke1"
    Role = "worker-kata-fc"
  }
}

resource "aws_eip" "eip_spoke1" {
  provider = aws.spoke1
  instance = aws_instance.spoke1.id
}

# 4. Spoke2 Workload Cluster (Region D - Nitro Nested Virtualization Enabled)
resource "aws_instance" "spoke2" {
  provider      = aws.spoke2
  ami           = data.aws_ami.ubuntu_spoke2.id
  instance_type = var.instance_type_spokes
  key_name      = var.ssh_key_name

  # Enable Intel VT-x hardware pass-through for Kata Firecracker /dev/kvm
  cpu_options {
    nested_virtualization = "enabled"
  }

  vpc_security_group_ids = [aws_security_group.sg_spoke2.id]

  root_block_device {
    volume_size           = 50
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name = "01sandbox-spoke2"
    Role = "worker-kata-fc"
  }
}

resource "aws_eip" "eip_spoke2" {
  provider = aws.spoke2
  instance = aws_instance.spoke2.id
}
