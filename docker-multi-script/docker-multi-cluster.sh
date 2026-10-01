#!/usr/bin/env bash
# ==============================================================================
# docker-multi-cluster.sh – Multi-Cluster Platform Setup Entry Point
#
# Usage:
#   ./docker-multi-cluster.sh             # Full one-shot setup (idempotent)
#   ./docker-multi-cluster.sh --force     # Re-run every step unconditionally
#   ./docker-multi-cluster.sh --clean     # Tear down all clusters and networks
#   ./docker-multi-cluster.sh --verify    # Health check an existing deployment
#
# All logic lives in lib/*.sh. This file sources them in the correct order
# and dispatches to the appropriate action.
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

# Source library modules in dependency order
source "${LIB_DIR}/globals.sh"    # config, logging, phase runner, arg parsing
source "${LIB_DIR}/preflight.sh"  # system helpers, host toolchain check
source "${LIB_DIR}/network.sh"    # transit net, WireGuard, PKI, Envoy
source "${LIB_DIR}/clusters.sh"   # KinD clusters, CRDs, OCM init, images
source "${LIB_DIR}/deploy.sh"     # Helm: primaryhub + secondaryhub stacks
source "${LIB_DIR}/kata.sh"       # Kata Containers + Firecracker runtime
source "${LIB_DIR}/ocm.sh"        # OCM join (MultipleHubs) + registration sync
source "${LIB_DIR}/main.sh"       # teardown, verification, main() orchestrator

# Entry point
case "$ACTION" in
  clean)  teardown_environment ;;
  verify) run_verification     ;;
  *)      main                 ;;
esac
