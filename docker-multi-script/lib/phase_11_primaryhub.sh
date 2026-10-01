#!/usr/bin/env bash
# ==============================================================================
# lib/phase_11_primaryhub.sh – Deploy the full stack on PrimaryHub
#
# Two-step Helm deploy:
#   11a  CNPG operator + PostgreSQL primary pod (waits until Running)
#   11b  Full stack: agentgateway, apiServer, opensandbox, MetalLB, etc.
# ==============================================================================

_check_phase_11() {
  helm status codeinspector --kube-context kind-primaryhub -n opensandbox-system >/dev/null 2>&1 || return 1

  local d desired avail
  for d in valkey sandbox-api opensandbox-server; do
    desired=$(kubectl --context kind-primaryhub -n opensandbox-system get deployment "$d" -o jsonpath='{.spec.replicas}' 2>/dev/null)
    [ -n "$desired" ] || return 1
    avail=$(kubectl --context kind-primaryhub -n opensandbox-system get deployment "$d" -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0)
    [ "${avail:-0}" -ge "${desired:-1}" ] || return 1
  done

  kubectl --context kind-primaryhub -n opensandbox-system get pod postgresql-primary-1 \
    -o jsonpath='{.status.phase}' 2>/dev/null | grep -q "Running" || return 1

  return 0
}

_do_phase_11_primaryhub_deploy() {
  log_step "PHASE 11a: PrimaryHub – PostgreSQL (CNPG Primary) First"

  ensure_sandbox_repo
  local cmap_tmpl="${CODE_INSPECTOR_DIR}/charts/apiServer/templates/configmap.yaml"
  if [ -f "$cmap_tmpl" ]; then
    sed -i 's/tpl \$value \$/tpl (\$value | toString) \$/g' "$cmap_tmpl" 2>/dev/null || true
  fi

  _ensure_hub_crds "kind-primaryhub"
  _relax_webhook_failure_policy "kind-primaryhub"

  log_info "Using codeInspector directory: ${CODE_INSPECTOR_DIR}"
  log_info "Deploying CNPG operator + PostgreSQL primary on primaryhub..."
  helm upgrade --install codeinspector "${CODE_INSPECTOR_DIR}" \
    --skip-crds \
    --kube-context kind-primaryhub \
    --namespace opensandbox-system \
    --create-namespace \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --set global.ocm.enabled=false \
    --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
    --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
    --set agentgateway.enabled=false \
    --set "agentgateway-controller.enabled=false" \
    --set apiServer.enabled=true \
    --set apiServer.deployment.replicaCount=0 \
    --set apiServer.rabbitmq.enabled=false \
    --set apiServer.failoverController.enabled=false \
    --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
    --set opensandbox.enabled=false \
    --set metallb.enabled=false \
    --set "sealed-secrets.enabled=false" \
    --set prometheus.enabled=false \
    --set grafana.enabled=false \
    --set opensandboxResourcePool.enabled=false \
    --timeout 5m

  log_info "Waiting for postgresql-primary-1 to be Ready before deploying full stack..."
  _wait_for_pod "kind-primaryhub" "opensandbox-system" "postgresql-primary-1" 60 "postgresql-primary-1"

  log_step "PHASE 11b: PrimaryHub – Full Stack (agentgateway, apiServer, opensandbox, metallb…)"

  _ensure_hub_crds "kind-primaryhub"
  _relax_webhook_failure_policy "kind-primaryhub"
  if ! kubectl --context kind-primaryhub get crd agentgatewaypolicies.agentgateway.dev >/dev/null 2>&1; then
    log_warn "Explicitly applying agentgateway-crds.yaml on kind-primaryhub..."
    _sanitize_agentgateway_crds
    kubectl --context kind-primaryhub apply --server-side --force-conflicts --field-manager=crd-installer -f "${CODE_INSPECTOR_DIR}/crds/agentgateway-crds.yaml"
    kubectl --context kind-primaryhub wait --for condition=established --timeout=60s crd/agentgatewaypolicies.agentgateway.dev
  fi

  helm upgrade --install codeinspector "${CODE_INSPECTOR_DIR}" \
    --skip-crds \
    --kube-context kind-primaryhub \
    --namespace opensandbox-system \
    --create-namespace \
    --values "${CODE_INSPECTOR_DIR}/values.yaml" \
    --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
    --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
    --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
    --timeout 10m

  kubectl --context kind-primaryhub rollout status deployment/valkey \
    -n opensandbox-system --timeout=120s || true
  kubectl --context kind-primaryhub rollout status deployment/sandbox-api \
    -n opensandbox-system --timeout=120s || true
  kubectl --context kind-primaryhub rollout status deployment/opensandbox-server \
    -n opensandbox-system --timeout=120s || true

  log_success "PrimaryHub full stack deployed."
}

phase_11_primaryhub_deploy() {
  run_phase "11" "PrimaryHub – PostgreSQL First, then Full Stack" _check_phase_11 _do_phase_11_primaryhub_deploy
}
