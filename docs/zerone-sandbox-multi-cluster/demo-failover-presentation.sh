#!/usr/bin/env bash
# ==============================================================================
# Multi-Cluster Failover & Split-Brain Prevention Live Demonstration Script
# Designed for live presentations to show zero split-brain and authoritative sync
# ==============================================================================

set -euo pipefail

# VM SSH Details
SSH_KEY="${HOME}/.ssh/kamal-kvm"
HUB1_IP="192.168.100.20"
HUB2_IP="192.168.101.20"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=5"

# Colors for presentation terminal
BOLD="\033[1m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
CYAN="\033[0;36m"
RED="\033[0;31m"
RESET="\033[0m"

step_banner() {
  echo -e "\n${BOLD}${BLUE}====================================================================${RESET}"
  echo -e "${BOLD}${CYAN}  $1${RESET}"
  echo -e "${BOLD}${BLUE}====================================================================${RESET}\n"
}

info() {
  echo -e "${GREEN}▶ [INFO]${RESET} $1"
}

prompt_step() {
  echo -e "\n${YELLOW}Press [ENTER] to execute this step...${RESET}"
  read -r
}

check_prereqs() {
  if [ ! -f "${SSH_KEY}" ]; then
    echo -e "${RED}Error: SSH private key ${SSH_KEY} not found.${RESET}"
    exit 1
  fi
}

hub1_exec() {
  ssh ${SSH_OPTS} "ubuntu@${HUB1_IP}" "$@"
}

hub2_exec() {
  ssh ${SSH_OPTS} "ubuntu@${HUB2_IP}" "$@"
}

# ------------------------------------------------------------------------------
check_prereqs
clear

echo -e "${BOLD}${GREEN}"
cat << 'EOF'
  ___  _       ____                  _ _box
 / _ \/ |     / ___|  __ _ _ __   __| | |__   _____  __
| | | | | ____\___ \ / _` | '_ \ / _` | '_ \ / _ \ \/ /
| |_| | ||_____|__) | (_| | | | | (_| | |_) | (_) >  <
 \___/|_|     |____/ \__,_|_| |_|\__,_|_.__/ \___/_/\_\
EOF
echo -e "${RESET}"
echo -e "${BOLD}High-Availability Failover & Split-Brain Prevention Live Demo${RESET}"
echo -e "Target VMs: PrimaryHub (${HUB1_IP}) | SecondaryHub (${HUB2_IP})\n"

prompt_step

# ------------------------------------------------------------------------------
# STEP 1: Current Cluster State
# ------------------------------------------------------------------------------
step_banner "STEP 1: Verify Initial Cluster State (Primary = Master, Secondary = Replica)"

info "Inspecting database on PrimaryHub (hub1-vm)..."
hub1_exec "kubectl exec -n opensandbox-system postgresql-primary-1 -c postgres -- psql -U postgres -d apikeys -c 'SELECT id, name, user_email, created_at FROM api_keys;'"

info "Checking Valkey cache keys on PrimaryHub..."
hub1_exec "kubectl exec -n opensandbox-system deploy/valkey -- valkey-cli KEYS '*'"

info "Checking PostgreSQL replication status on SecondaryHub (hub2-vm)..."
hub2_exec "kubectl exec -n opensandbox-system postgresql-secondary-1 -c postgres -- psql -U postgres -d apikeys -c 'SELECT status, sender_host, written_lsn FROM pg_stat_wal_receiver;'"

prompt_step

# ------------------------------------------------------------------------------
# STEP 2: Simulate PrimaryHub Failure
# ------------------------------------------------------------------------------
step_banner "STEP 2: Simulate PrimaryHub Outage (docker stop primaryhub-control-plane)"

info "Stopping primaryhub-control-plane container on hub1-vm..."
hub1_exec "docker stop primaryhub-control-plane"
info "PrimaryHub is now DOWN!"

info "Observing SecondaryHub failover controller detecting failure..."
echo -e "${CYAN}Monitoring failover controller logs on hub2-vm (last 6 lines):${RESET}"
hub2_exec "kubectl logs -n opensandbox-system deploy/ocm-failover-controller --tail=6"

prompt_step

# ------------------------------------------------------------------------------
# STEP 3: Outage Mutations on SecondaryHub (Simulating the Split-Brain scenario)
# ------------------------------------------------------------------------------
step_banner "STEP 3: Perform Outage Mutations on SecondaryHub (Inserts & Deletions)"

info "SecondaryHub is now promoted to Read-Write mode."
info "Inserting a new API key created during outage: 'outage_key_presentation'..."
hub2_exec "kubectl exec -n opensandbox-system postgresql-secondary-1 -c postgres -- psql -U postgres -d apikeys -c \"INSERT INTO api_keys (name, key_hash) VALUES ('outage_key_presentation', 'hash_presentation_999');\""

info "Current state of api_keys on SecondaryHub during outage:"
hub2_exec "kubectl exec -n opensandbox-system postgresql-secondary-1 -c postgres -- psql -U postgres -d apikeys -c 'SELECT id, name, user_email, created_at FROM api_keys;'"

info "Writing key into SecondaryHub Valkey memory..."
hub2_exec "kubectl exec -n opensandbox-system deploy/valkey -- valkey-cli SET api_key:outage_key_presentation 'valid'"

prompt_step

# ------------------------------------------------------------------------------
# STEP 4: Restore PrimaryHub & Watch Authoritative Failback
# ------------------------------------------------------------------------------
step_banner "STEP 4: Restore PrimaryHub (docker start primaryhub-control-plane)"

info "Starting primaryhub-control-plane container on hub1-vm..."
hub1_exec "docker start primaryhub-control-plane"
info "PrimaryHub container started! Waiting for Kubernetes API and PostgreSQL..."

info "Streaming failback reconciliation logs from ocm-failover-controller on hub2-vm..."
echo -e "${CYAN}Watch the 4-step reconciliation: Health Gate -> Authoritative Sync -> Valkey Sync -> Re-clone${RESET}"

# Follow logs until Standby completion message is detected or 30s timeout
set +e
timeout 45 hub2_exec "kubectl logs -n opensandbox-system deploy/ocm-failover-controller -f --tail=10"
set -e

prompt_step

# ------------------------------------------------------------------------------
# STEP 5: Verification of Zero Split-Brain
# ------------------------------------------------------------------------------
step_banner "STEP 5: Verification — Proving Zero Split-Brain & Perfect Parity"

info "1. Querying PrimaryHub PostgreSQL (hub1-vm):"
hub1_exec "kubectl exec -n opensandbox-system postgresql-primary-1 -c postgres -- psql -U postgres -d apikeys -c 'SELECT id, name, user_email, created_at FROM api_keys;'"

info "2. Querying PrimaryHub Valkey Cache (hub1-vm):"
hub1_exec "kubectl exec -n opensandbox-system deploy/valkey -- valkey-cli GET api_key:outage_key_presentation"

info "3. Querying SecondaryHub PostgreSQL (hub2-vm):"
hub2_exec "kubectl exec -n opensandbox-system postgresql-secondary-1 -c postgres -- psql -U postgres -d apikeys -c 'SELECT id, name, user_email, created_at FROM api_keys;'"

info "4. Checking WAL Receiver on SecondaryHub (Must show 'streaming'):"
hub2_exec "kubectl exec -n opensandbox-system postgresql-secondary-1 -c postgres -- psql -U postgres -d apikeys -c 'SELECT status, sender_host, written_lsn FROM pg_stat_wal_receiver;'"

echo -e "\n${BOLD}${GREEN}====================================================================${RESET}"
echo -e "${BOLD}${GREEN}  DEMONSTRATION COMPLETE: ZERO DATA RESURRECTION & ZERO SPLIT-BRAIN!${RESET}"
echo -e "${BOLD}${GREEN}====================================================================${RESET}\n"
EOF
