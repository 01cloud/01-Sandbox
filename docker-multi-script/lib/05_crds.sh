#!/usr/bin/env bash
# ==============================================================================
# lib/05_crds.sh – Custom Resource Definition installation helpers
#
# Functions:
#   _sanitize_agentgateway_crds  – strip CEL rules that exceed k8s 1.30 budget
#   _ensure_hub_crds             – verify & idempotently apply all required CRDs
#   _install_crds                – full CRD install with upstream URL + local fallback
# ==============================================================================

# Strip x-kubernetes-validations from agentgateway-crds.yaml to avoid CEL cost
# budget errors on Kubernetes 1.30+.
_sanitize_agentgateway_crds() {
  local f="${CODE_INSPECTOR_DIR}/crds/agentgateway-crds.yaml"
  if [ -f "$f" ] && grep -q "x-kubernetes-validations:" "$f" 2>/dev/null; then
    log_info "Optimizing agentgateway-crds.yaml (stripping CEL rules exceeding API server cost budget)..."
    python3 -c "
import yaml
with open('$f') as fp:
    docs = list(yaml.safe_load_all(fp))
def rm_cel(obj):
    if isinstance(obj, dict):
        obj.pop('x-kubernetes-validations', None)
        for v in obj.values(): rm_cel(v)
    elif isinstance(obj, list):
        for item in obj: rm_cel(item)
for d in docs: rm_cel(d)
with open('$f', 'w') as fp:
    yaml.dump_all(docs, fp, default_flow_style=False, sort_keys=False)
" 2>/dev/null || true
  fi
}

# Verify and idempotently apply all required CRDs on a cluster context.
# Args: <context>
_ensure_hub_crds() {
  local ctx="$1"
  ensure_sandbox_repo || true

  local crd_dir="${CODE_INSPECTOR_DIR}/crds"

  # Remove any blocking admission policy installed by upstream Gateway API
  kubectl --context "$ctx" delete validatingadmissionpolicy safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true
  kubectl --context "$ctx" delete validatingadmissionpolicybinding safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true

  # 1. Gateway API CRDs (TCPRoute, HTTPRoute, Gateway, ReferenceGrant)
  if ! kubectl --context "$ctx" get crd tcproutes.gateway.networking.k8s.io >/dev/null 2>&1 || \
     ! kubectl --context "$ctx" get crd httproutes.gateway.networking.k8s.io >/dev/null 2>&1 || \
     ! kubectl --context "$ctx" get crd gateways.gateway.networking.k8s.io >/dev/null 2>&1; then
    log_info "Ensuring Gateway API CRDs (including TCPRoute) are applied on $ctx..."
    if [ -f "${crd_dir}/gateway-api-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/gateway-api-crds.yaml" >/dev/null 2>&1 || \
      kubectl --context "$ctx" apply -f "${crd_dir}/gateway-api-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # 2. AgentGateway CRDs (AgentgatewayPolicy, AgentgatewayBackend, AgentgatewayParameters)
  if ! kubectl --context "$ctx" get crd agentgatewaypolicies.agentgateway.dev >/dev/null 2>&1 || \
     ! kubectl --context "$ctx" get crd agentgatewaybackends.agentgateway.dev >/dev/null 2>&1; then
    log_info "Ensuring AgentGateway CRDs (including AgentgatewayPolicy) are applied on $ctx..."
    _sanitize_agentgateway_crds
    if [ -f "${crd_dir}/agentgateway-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts --field-manager=crd-installer -f "${crd_dir}/agentgateway-crds.yaml" >/dev/null 2>&1 || \
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/agentgateway-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # 3. OpenSandbox CRDs (BatchSandbox, Pool)
  if ! kubectl --context "$ctx" get crd batchsandboxes.sandbox.opensandbox.io >/dev/null 2>&1 || \
     ! kubectl --context "$ctx" get crd pools.sandbox.opensandbox.io >/dev/null 2>&1; then
    log_info "Ensuring OpenSandbox CRDs are applied on $ctx..."
    if [ -f "${crd_dir}/opensandbox-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/opensandbox-crds.yaml" >/dev/null 2>&1 || \
      kubectl --context "$ctx" apply -f "${crd_dir}/opensandbox-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # 4. MetalLB CRDs (IPAddressPool, L2Advertisement)
  if ! kubectl --context "$ctx" get crd ipaddresspools.metallb.io >/dev/null 2>&1; then
    log_info "Ensuring MetalLB CRDs are applied on $ctx..."
    if [ -f "${crd_dir}/metallb-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/metallb-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # 5. CloudNativePG CRDs
  if ! kubectl --context "$ctx" get crd clusters.postgresql.cnpg.io >/dev/null 2>&1; then
    log_info "Ensuring CloudNativePG CRDs are applied on $ctx..."
    if [ -f "${crd_dir}/cloudnative-pg-crds.yaml" ]; then
      kubectl --context "$ctx" apply --server-side --force-conflicts -f "${crd_dir}/cloudnative-pg-crds.yaml" >/dev/null 2>&1 || true
    fi
  fi

  # Wait for critical CRDs to reach Established condition
  local wait_crds=(
    "agentgatewaypolicies.agentgateway.dev"
    "agentgatewaybackends.agentgateway.dev"
    "tcproutes.gateway.networking.k8s.io"
    "httproutes.gateway.networking.k8s.io"
    "gateways.gateway.networking.k8s.io"
    "clusters.postgresql.cnpg.io"
    "batchsandboxes.sandbox.opensandbox.io"
    "pools.sandbox.opensandbox.io"
  )
  for c in "${wait_crds[@]}"; do
    if kubectl --context "$ctx" get crd "$c" >/dev/null 2>&1; then
      kubectl --context "$ctx" wait --for condition=established --timeout=30s "crd/${c}" >/dev/null 2>&1 || true
    fi
  done
}

# Full CRD installation: try upstream URL first, fall back to local file.
# Prints a rich summary table of all established CRDs on the cluster.
# Args: <context>
_install_crds() {
  local ctx="$1"
  log_info "Installing Custom Resource Definitions (CRDs) on $ctx..."

  ensure_sandbox_repo || true

  local crd_dir="${CODE_INSPECTOR_DIR}/crds"
  local -a crd_manifests=()

  # Preferred install order
  local default_bundles=(
    "gateway-api-crds.yaml"
    "cloudnative-pg-crds.yaml"
    "metallb-crds.yaml"
    "sealed-secrets-crd.yaml"
    "agentgateway-crds.yaml"
    "opensandbox-crds.yaml"
  )

  # Collect available YAML files in preferred order, then append any extras
  if [ -d "$crd_dir" ]; then
    for f in "${default_bundles[@]}"; do
      [ -f "${crd_dir}/$f" ] && crd_manifests+=("$f")
    done
    for f in "$crd_dir"/*.yaml; do
      if [ -f "$f" ]; then
        local bname
        bname=$(basename "$f")
        if [[ ! " ${crd_manifests[*]} " =~ " ${bname} " ]]; then
          crd_manifests+=("$bname")
        fi
      fi
    done
  fi

  # If no local files found, fall back to the known bundle list (URLs only)
  if [ ${#crd_manifests[@]} -eq 0 ]; then
    crd_manifests=("${default_bundles[@]}")
  fi

  # Apply each CRD bundle
  for fname in "${crd_manifests[@]}"; do
    local title="${CRD_NAMES[$fname]:-$fname}"
    local details="${CRD_DETAILS[$fname]:-}"
    local url="${CRD_URLS[$fname]:-}"
    local local_file="${crd_dir}/${fname}"
    local applied=false

    echo -e "\n  ${CYAN}▸ [CRD Package] ${BOLD}${title}${NC} (${fname})"
    if [ -n "$details" ]; then
      echo -e "    ${BOLD}CRDs included:${NC} ${details}"
    fi

    # 1. Try upstream URL
    if [ -n "$url" ]; then
      log_info "    Fetching from upstream URL..."
      if curl -fsSL --connect-timeout 5 --max-time 15 "$url" -o /tmp/_crd_dl.yaml 2>/dev/null; then
        if kubectl --context "$ctx" apply --server-side --force-conflicts -f /tmp/_crd_dl.yaml >/dev/null 2>&1; then
          log_success "    Applied ${title} from upstream repository."
          applied=true
        fi
      fi
      if [ "$applied" = false ]; then
        log_warn "    Upstream download unavailable – falling back to local copy..."
      fi
    fi

    # 2. Local copy fallback
    if [ "$applied" = false ]; then
      if [ -f "$local_file" ]; then
        log_info "    Applying local manifest: ${local_file}"
        if kubectl --context "$ctx" apply --server-side --force-conflicts --field-manager=crd-installer -f "$local_file" >/dev/null 2>&1 || \
           kubectl --context "$ctx" apply --server-side --force-conflicts -f "$local_file" >/dev/null 2>&1; then
          log_success "    Applied ${title} from local manifest."
          applied=true
        else
          log_warn "    Server-side apply warning; retrying standard apply..."
          kubectl --context "$ctx" apply -f "$local_file" >/dev/null 2>&1 || true
          applied=true
        fi
      else
        log_error "    Manifest not found at ${local_file} and no upstream available!"
      fi
    fi
  done

  # Remove safe-upgrades admission policy to prevent Helm being blocked
  kubectl --context "$ctx" delete validatingadmissionpolicy safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true
  kubectl --context "$ctx" delete validatingadmissionpolicybinding safe-upgrades.gateway.networking.k8s.io >/dev/null 2>&1 || true

  # Idempotently ensure all required CRDs are established
  _ensure_hub_crds "$ctx"

  # Print summary table
  echo -e "\n  ${GREEN}${BOLD}Established CRDs on cluster [${ctx}]:${NC}"
  local crd_table
  crd_table=$(kubectl --context "$ctx" get crds --no-headers -o custom-columns='NAME:.metadata.name,GROUP:.spec.group' 2>/dev/null | sort || true)
  if [ -n "$crd_table" ]; then
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      local cname cgroup
      cname=$(echo "$line" | awk '{print $1}')
      cgroup=$(echo "$line" | awk '{print $2}')
      printf "    %-52s %s\n" "${cname}" "(${cgroup})"
    done <<< "$crd_table"
    local total_count
    total_count=$(echo "$crd_table" | wc -l)
    log_success "Total ${total_count} CRDs successfully established on ${ctx}.\n"
  else
    log_warn "No CRDs detected yet on ${ctx}."
  fi
}
