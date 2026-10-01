#!/usr/bin/env bash
# ==============================================================================
# docker-multi-cluster.sh – Multi-Cluster Platform Setup Entry Point
#
# Usage:
#   ./docker-multi-cluster.sh             # Full one-shot setup (idempotent)
#   ./docker-multi-cluster.sh --force     # Re-run every phase unconditionally
#   ./docker-multi-cluster.sh --clean     # Tear down all clusters and networks
#   ./docker-multi-cluster.sh --verify    # Health check an existing deployment
#
# All logic lives in lib/*.sh.  This file sources them in the correct order
# and dispatches to the appropriate action.
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

# ── Source library modules in dependency order ─────────────────────────────────
# shellcheck source=lib/00_globals.sh
source "${LIB_DIR}/00_globals.sh"       # colors, paths, IPs, CRD maps, arg parsing

# shellcheck source=lib/01_logging.sh
source "${LIB_DIR}/01_logging.sh"       # log_info / log_success / log_warn / log_error / log_step

# shellcheck source=lib/02_phase_runner.sh
source "${LIB_DIR}/02_phase_runner.sh"  # run_phase, _check_wg_active, _check_hub_crds_established

# shellcheck source=lib/03_system.sh
source "${LIB_DIR}/03_system.sh"        # ensure_kernel_inotify_limits, ensure_docker_access,
                                        # ensure_sandbox_repo, _relax_webhook_failure_policy

# shellcheck source=lib/04_cluster.sh
source "${LIB_DIR}/04_cluster.sh"       # _create_kind_cluster, _setup_wireguard, _wait_for_pod

# shellcheck source=lib/05_crds.sh
source "${LIB_DIR}/05_crds.sh"          # _sanitize_agentgateway_crds, _ensure_hub_crds, _install_crds

# shellcheck source=lib/06_teardown.sh
source "${LIB_DIR}/06_teardown.sh"      # cleanup_environment

# shellcheck source=lib/07_verification.sh
source "${LIB_DIR}/07_verification.sh"  # run_verification

# ── Source individual phase modules ────────────────────────────────────────────
source "${LIB_DIR}/phase_01_preflight.sh"
source "${LIB_DIR}/phase_02_network.sh"
source "${LIB_DIR}/phase_03_hubs.sh"
source "${LIB_DIR}/phase_04_hub_crds.sh"
source "${LIB_DIR}/phase_05_hub_wireguard.sh"
source "${LIB_DIR}/phase_06_envoy.sh"
source "${LIB_DIR}/phase_07_pki.sh"
source "${LIB_DIR}/phase_08_ocm.sh"
source "${LIB_DIR}/phase_09_namespaces.sh"
source "${LIB_DIR}/phase_10_image.sh"
source "${LIB_DIR}/phase_11_primaryhub.sh"
source "${LIB_DIR}/phase_12_secondaryhub.sh"
source "${LIB_DIR}/phase_13_spokes.sh"
source "${LIB_DIR}/phase_14_spoke_wireguard.sh"
source "${LIB_DIR}/phase_15_spoke_crds.sh"
source "${LIB_DIR}/phase_15b_kata_fc.sh"
source "${LIB_DIR}/phase_16_ocm_join.sh"
source "${LIB_DIR}/phase_17_sync.sh"
source "${LIB_DIR}/phase_18_summary.sh"

# shellcheck source=lib/99_main.sh
source "${LIB_DIR}/99_main.sh"          # main() – calls all 18 phases in order

# ── Entry point ────────────────────────────────────────────────────────────────
case "$ACTION" in
  clean)  cleanup_environment ;;
  verify) run_verification    ;;
  *)      main                ;;
esac
