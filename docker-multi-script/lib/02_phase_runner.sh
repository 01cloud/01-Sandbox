#!/usr/bin/env bash
# ==============================================================================
# lib/02_phase_runner.sh – Idempotent phase execution framework
#
# run_phase <num> <title> <check_fn> <do_fn>
#   • Calls <check_fn>; if it returns 0 AND --force was NOT passed, the phase
#     is marked ✓ ALREADY CONFIGURED and skipped.
#   • Otherwise runs <do_fn> then re-runs <check_fn> to confirm success.
# ==============================================================================

run_phase() {
  local num="$1" title="$2" check_fn="$3" do_fn="$4"

  echo -e "\n${CYAN}${BOLD}[Phase ${num}]${NC} ${title}"
  echo -e "${CYAN}$(printf '─%.0s' {1..70})${NC}"

  # ── Idempotency check ──────────────────────────────────────────────────────
  if [ "$FORCE_RECONFIGURE" != "true" ] && $check_fn 2>/dev/null; then
    log_success "Phase ${num} – already configured. Skipping."
    return 0
  fi

  # ── Execute phase ──────────────────────────────────────────────────────────
  log_info "Running phase ${num}: ${title}..."
  if $do_fn; then
    log_success "Phase ${num} complete."
  else
    log_error "Phase ${num} (${title}) encountered errors."
    return 1
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Shared idempotency predicates used by multiple phase checks
# ─────────────────────────────────────────────────────────────────────────────

# Returns 0 if wg0 is UP inside <container> and its address matches <expected-ip>.
# Args: <container> <expected-ip>
_check_wg_active() {
  local container="$1" expected_ip="$2"
  docker exec "$container" ip addr show wg0 2>/dev/null | grep -q "$expected_ip"
}

# Returns 0 if the standard CRD bundle is Established on a given context.
# Used by phase-04 and phase-15 idempotency checks (hubs and spokes share the
# same CRD bundles).
# Args: <context>
_check_hub_crds_established() {
  local ctx="$1"
  local crds=(
    agentgatewaypolicies.agentgateway.dev
    agentgatewaybackends.agentgateway.dev
    tcproutes.gateway.networking.k8s.io
    httproutes.gateway.networking.k8s.io
    gateways.gateway.networking.k8s.io
    clusters.postgresql.cnpg.io
    batchsandboxes.sandbox.opensandbox.io
    pools.sandbox.opensandbox.io
    ipaddresspools.metallb.io
  )
  local c
  for c in "${crds[@]}"; do
    kubectl --context "$ctx" get crd "$c" >/dev/null 2>&1 || return 1
  done
  return 0
}
