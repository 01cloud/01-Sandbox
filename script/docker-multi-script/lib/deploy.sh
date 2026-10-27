#!/usr/bin/env bash
# ==============================================================================
# lib/deploy.sh – Helm deployments for PrimaryHub and SecondaryHub
#
# Functions:
#   deploy_primaryhub    – two-step Helm deploy: CNPG primary first, then full stack
#   deploy_secondaryhub  – two-step Helm deploy: CNPG standby first, then full stack
#                          + failover controller + pre-render re-clone manifest
# ==============================================================================
#
# Two-step Helm deploy:
#   11a  CNPG operator + PostgreSQL primary pod (waits until Running)
#   11b  Full stack: agentgateway, apiServer, opensandbox, MetalLB, etc.

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

  local p_ready
  p_ready=$(kubectl --context kind-primaryhub -n opensandbox-system get pod postgresql-primary-1 -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")

  if [ "$p_ready" = "True" ] && [ "$FORCE_RECONFIGURE" != "true" ]; then
    log_info "PostgreSQL primary (postgresql-primary-1) is already Running and Ready on primaryhub – skipping Step 11a."
  else
    # Check if an existing orphaned PVC is causing initdb to fail with "PGData already exists"
    if kubectl --context kind-primaryhub -n opensandbox-system get pods 2>/dev/null | grep -q "postgresql-primary-1-initdb.*Error"; then
      log_warn "Detected failing initdb due to stale PGData on an orphaned volume. Clearing stale PVC..."
      kubectl --context kind-primaryhub -n opensandbox-system delete cluster postgresql-primary --wait=false 2>/dev/null || true
      kubectl --context kind-primaryhub -n opensandbox-system delete pvc -l cnpg.io/cluster=postgresql-primary 2>/dev/null || true
      kubectl --context kind-primaryhub -n opensandbox-system delete pods -l cnpg.io/cluster=postgresql-primary --force --grace-period=0 2>/dev/null || true
      sleep 3
    fi

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
    if ! _wait_for_pod "kind-primaryhub" "opensandbox-system" "postgresql-primary-1" 30 "postgresql-primary-1"; then
      # Check if initdb is blocked on stale PGData
      local init_logs
      init_logs=$(kubectl --context kind-primaryhub -n opensandbox-system logs -l cnpg.io/cluster=postgresql-primary -c initdb --tail=20 2>/dev/null || true)
      if echo "$init_logs" | grep -q "PGData directories already exist"; then
        log_warn "initdb failed: PGData directories already exist. Purging orphaned PVC and re-provisioning fresh volume..."
        kubectl --context kind-primaryhub -n opensandbox-system delete cluster postgresql-primary --wait=false 2>/dev/null || true
        kubectl --context kind-primaryhub -n opensandbox-system delete pvc -l cnpg.io/cluster=postgresql-primary 2>/dev/null || true
        kubectl --context kind-primaryhub -n opensandbox-system delete pods -l cnpg.io/cluster=postgresql-primary --force --grace-period=0 2>/dev/null || true
        sleep 3
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
        _wait_for_pod "kind-primaryhub" "opensandbox-system" "postgresql-primary-1" 60 "postgresql-primary-1"
      fi
    fi
  fi

  log_step "PHASE 11b: PrimaryHub – Full Stack (agentgateway, apiServer, opensandbox, metallb…)"

  _ensure_hub_crds "kind-primaryhub"
  _relax_webhook_failure_policy "kind-primaryhub"
  if ! kubectl --context kind-primaryhub get crd agentgatewaypolicies.agentgateway.dev >/dev/null 2>&1; then
    log_warn "Explicitly applying agentgateway-crds.yaml on kind-primaryhub..."
    _sanitize_agentgateway_crds
    kubectl --context kind-primaryhub apply --server-side --force-conflicts --field-manager=crd-installer -f "${CODE_INSPECTOR_DIR}/crds/agentgateway-crds.yaml"
    kubectl --context kind-primaryhub wait --for condition=established --timeout=60s crd/agentgatewaypolicies.agentgateway.dev
  fi

  local p11_ready=true
  for d in valkey sandbox-api opensandbox-server; do
    local avail desired
    desired=$(kubectl --context kind-primaryhub -n opensandbox-system get deployment "$d" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
    avail=$(kubectl --context kind-primaryhub -n opensandbox-system get deployment "$d" -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo "0")
    if [ "${avail:-0}" -lt "${desired:-1}" ]; then
      p11_ready=false
      break
    fi
  done

  if [ "$p11_ready" = "true" ] && [ "$FORCE_RECONFIGURE" != "true" ] && helm status codeinspector --kube-context kind-primaryhub -n opensandbox-system >/dev/null 2>&1; then
    log_info "PrimaryHub full stack is already deployed and healthy – skipping helm upgrade in Step 11b."
  else
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
  fi

  log_success "PrimaryHub full stack deployed."
}

phase_11_primaryhub_deploy() {
  run_phase "11" "PrimaryHub – PostgreSQL First, then Full Stack" _check_phase_11 _do_phase_11_primaryhub_deploy
}
#
# Two-step Helm deploy:
#   12a  CNPG standby (WAL from PrimaryHub at WG_HUB1_IP:30432)
#   12b  Full stack + ocm-failover-controller; pre-render re-clone manifest

_check_phase_12() {
  helm status codeinspector --kube-context kind-secondaryhub -n opensandbox-system >/dev/null 2>&1 || return 1

  local d desired avail
  for d in valkey sandbox-api opensandbox-server ocm-failover-controller; do
    desired=$(kubectl --context kind-secondaryhub -n opensandbox-system get deployment "$d" -o jsonpath='{.spec.replicas}' 2>/dev/null)
    [ -n "$desired" ] || return 1
    avail=$(kubectl --context kind-secondaryhub -n opensandbox-system get deployment "$d" -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0)
    [ "${avail:-0}" -ge "${desired:-1}" ] || return 1
  done

  kubectl --context kind-secondaryhub -n opensandbox-system get pod postgresql-secondary-1 \
    -o jsonpath='{.status.phase}' 2>/dev/null | grep -q "Running" || return 1

  [ -s "${SEC_DIR}/postgresql-secondary-cluster.yaml" ] || return 1
  docker exec secondaryhub-control-plane test -f /root/postgresql-secondary-cluster.yaml || return 1

  return 0
}

_do_phase_12_secondaryhub_deploy() {
  log_step "PHASE 12a: SecondaryHub – PostgreSQL (CNPG Standby) First"

  ensure_sandbox_repo
  local cmap_tmpl="${CODE_INSPECTOR_DIR}/charts/apiServer/templates/configmap.yaml"
  if [ -f "$cmap_tmpl" ]; then
    sed -i 's/tpl \$value \$/tpl (\$value | toString) \$/g' "$cmap_tmpl" 2>/dev/null || true
  fi

  _ensure_hub_crds "kind-secondaryhub"
  _relax_webhook_failure_policy "kind-secondaryhub"

  local p2_ready
  p2_ready=$(kubectl --context kind-secondaryhub -n opensandbox-system get pod postgresql-secondary-1 -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")

  if [ "$p2_ready" = "True" ] && [ "$FORCE_RECONFIGURE" != "true" ]; then
    log_info "PostgreSQL standby (postgresql-secondary-1) is already Running and Ready on secondaryhub – skipping Step 12a."
  else
    log_info "Using codeInspector directory: ${CODE_INSPECTOR_DIR}"
    log_info "Deploying CNPG standby on secondaryhub (WAL from ${WG_HUB1_IP}:30432)..."
    helm upgrade --install codeinspector "${CODE_INSPECTOR_DIR}" \
      --skip-crds \
      --kube-context kind-secondaryhub \
      --namespace opensandbox-system \
      --create-namespace \
      --values "${CODE_INSPECTOR_DIR}/values.yaml" \
      --values "${CODE_INSPECTOR_DIR}/values-secondary.yaml" \
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
      --set apiServer.valkey.replication.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.valkey.replication.primaryPort=30379 \
      --set apiServer.cnpg.replication.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.cnpg.replication.primaryPort=30432 \
      --set apiServer.failoverController.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.failoverController.primaryPort=30432 \
      --set opensandbox.enabled=false \
      --set metallb.enabled=false \
      --set "sealed-secrets.enabled=false" \
      --set prometheus.enabled=false \
      --set grafana.enabled=false \
      --set opensandboxResourcePool.enabled=false \
      --timeout 5m

    log_info "Waiting for postgresql-secondary-1 to be Ready before deploying full stack..."
    _wait_for_pod "kind-secondaryhub" "opensandbox-system" "postgresql-secondary-1" 60 "postgresql-secondary-1"
  fi

  log_step "PHASE 12b: SecondaryHub – Full Stack + Failover Controller"

  _ensure_hub_crds "kind-secondaryhub"
  _relax_webhook_failure_policy "kind-secondaryhub"
  if ! kubectl --context kind-secondaryhub get crd agentgatewaypolicies.agentgateway.dev >/dev/null 2>&1; then
    log_warn "Explicitly applying agentgateway-crds.yaml on kind-secondaryhub..."
    _sanitize_agentgateway_crds
    kubectl --context kind-secondaryhub apply --server-side --force-conflicts --field-manager=crd-installer -f "${CODE_INSPECTOR_DIR}/crds/agentgateway-crds.yaml"
    kubectl --context kind-secondaryhub wait --for condition=established --timeout=60s crd/agentgatewaypolicies.agentgateway.dev
  fi

  local p12_ready=true
  for d in valkey sandbox-api opensandbox-server ocm-failover-controller; do
    local avail desired
    desired=$(kubectl --context kind-secondaryhub -n opensandbox-system get deployment "$d" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
    avail=$(kubectl --context kind-secondaryhub -n opensandbox-system get deployment "$d" -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo "0")
    if [ "${avail:-0}" -lt "${desired:-1}" ]; then
      p12_ready=false
      break
    fi
  done

  if [ "$p12_ready" = "true" ] && [ "$FORCE_RECONFIGURE" != "true" ] && helm status codeinspector --kube-context kind-secondaryhub -n opensandbox-system >/dev/null 2>&1; then
    log_info "SecondaryHub full stack is already deployed and healthy – skipping helm upgrade in Step 12b."
  else
    helm upgrade --install codeinspector "${CODE_INSPECTOR_DIR}" \
      --skip-crds \
      --kube-context kind-secondaryhub \
      --namespace opensandbox-system \
      --create-namespace \
      --values "${CODE_INSPECTOR_DIR}/values.yaml" \
      --values "${CODE_INSPECTOR_DIR}/values-secondary.yaml" \
      --set cloudnative-pg.webhook.mutating.failurePolicy=Ignore \
      --set cloudnative-pg.webhook.validating.failurePolicy=Ignore \
      --set apiServer.valkey.replication.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.valkey.replication.primaryPort=30379 \
      --set apiServer.cnpg.replication.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.cnpg.replication.primaryPort=30432 \
      --set apiServer.failoverController.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.failoverController.primaryPort=30432 \
      --set-string apiServer.configMap.ALLOW_MOCK_KEYS="true" \
      --timeout 10m

    kubectl --context kind-secondaryhub rollout status deployment/valkey \
      -n opensandbox-system --timeout=120s || true
    kubectl --context kind-secondaryhub rollout status deployment/sandbox-api \
      -n opensandbox-system --timeout=120s || true
    kubectl --context kind-secondaryhub rollout status deployment/opensandbox-server \
      -n opensandbox-system --timeout=120s || true
    kubectl --context kind-secondaryhub rollout status deployment/ocm-failover-controller \
      -n opensandbox-system --timeout=120s || true
  fi

  # Pre-render the CNPG re-clone manifest for use by the failover controller only if missing
  mkdir -p "$SEC_DIR"
  if [ -s "${SEC_DIR}/postgresql-secondary-cluster.yaml" ] && \
     docker exec secondaryhub-control-plane test -f /root/postgresql-secondary-cluster.yaml 2>/dev/null && \
     [ "$FORCE_RECONFIGURE" != "true" ]; then
    log_info "CNPG re-clone manifest already generated and copied to secondaryhub – skipping."
  else
    helm template codeinspector "${CODE_INSPECTOR_DIR}" \
      -s charts/apiServer/templates/cnpg-cluster.yaml \
      --values "${CODE_INSPECTOR_DIR}/values.yaml" \
      --values "${CODE_INSPECTOR_DIR}/values-secondary.yaml" \
      --set apiServer.cnpg.replication.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.cnpg.replication.primaryPort=30432 \
      --set apiServer.valkey.replication.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.valkey.replication.primaryPort=30379 \
      --set apiServer.failoverController.primaryHost="${WG_HUB1_IP}" \
      --set apiServer.failoverController.primaryPort=30432 \
      > "${SEC_DIR}/postgresql-secondary-cluster.yaml"
    docker cp "${SEC_DIR}/postgresql-secondary-cluster.yaml" \
      secondaryhub-control-plane:/root/postgresql-secondary-cluster.yaml 2>/dev/null || true
  fi

  log_success "SecondaryHub full stack deployed."
}

phase_12_secondaryhub_deploy() {
  run_phase "12" "SecondaryHub – PostgreSQL First, then Full Stack + Failover Controller" _check_phase_12 _do_phase_12_secondaryhub_deploy
}
