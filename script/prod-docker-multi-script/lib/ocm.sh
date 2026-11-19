#!/usr/bin/env bash
# ==============================================================================
# lib/ocm.sh – OCM spoke join (MultipleHubs) and registration sync
#
# Functions:
#   _write_hub_bootstrap_kubeconfig  – write a bootstrap kubeconfig for one hub
#   _get_hub_token                   – extract join token from clusteradm
#   _dump_klusterlet_debug           – dump klusterlet diagnostics to stderr
#   _ensure_hub_apiserver_sans       – regenerate apiserver cert with WG SANs
#   join_spokes_to_ocm               – full MultipleHubs klusterlet join + CSR approval
#   sync_spokes_to_secondaryhub      – label spokes + sync registration resources
# ==============================================================================
#
# Flow per spoke:
#   1. Patch hub apiserver certs to include WG SANs (cert rotation if needed)
#   2. Obtain bootstrap tokens from each hub
#   3. Run `clusteradm join` via primaryhub (inside spoke container)
#   4. Create per-hub bootstrap-kubeconfig secrets in open-cluster-management-agent
#   5. Patch klusterlet: enable MultipleHubs feature gate + LocalSecrets config
#   6. Approve CSRs on primaryhub; best-effort pre-approve on secondaryhub

# Write a bootstrap kubeconfig pointing directly at one hub over the WG overlay.
# Extracts live CA certificate directly from the specified hub node container to prevent
# crypto/rsa verification mismatch if hubs have distinct or recreated CAs.
# Args: <out-file> <server-url> <token> <hub-node-name>
_write_hub_bootstrap_kubeconfig() {
  local out="$1" server="$2" token="$3" hub_node="$4" ca_b64=""
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    local hub_name="${hub_node%-control-plane}"
    local hub_host="${NODE_HOSTS[$hub_name]:-$PRIMARYHUB_HOST}"
    ca_b64=$(remote_exec "$hub_host" "docker exec '$hub_node' cat /etc/kubernetes/pki/ca.crt 2>/dev/null" | base64 -w0)
  elif [ -n "$hub_node" ] && docker exec "$hub_node" test -f /etc/kubernetes/pki/ca.crt >/dev/null 2>&1; then
    ca_b64=$(docker exec "$hub_node" cat /etc/kubernetes/pki/ca.crt | base64 -w0)
  fi

  if [ -z "$ca_b64" ] && [ -s "${PKI_DIR}/ca.crt" ]; then
    ca_b64=$(base64 -w0 < "${PKI_DIR}/ca.crt")
  fi

  if [ -z "$ca_b64" ]; then
    log_error "Could not extract CA certificate for hub '${hub_node:-$server}'"
    return 1
  fi

  cat > "$out" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: hub
  cluster:
    server: ${server}
    certificate-authority-data: ${ca_b64}
contexts:
- name: bootstrap
  context:
    cluster: hub
    user: bootstrap
current-context: bootstrap
users:
- name: bootstrap
  user:
    token: ${token}
EOF
  chmod 600 "$out"
}

# Extract the bootstrap join token from `clusteradm get token` on a hub context.
# Retries up to 20 times with 5-second back-off.
# Args: <hub-context>
_get_hub_token() {
  local ctx="$1" out tok err_file
  err_file=$(mktemp)

  # Allow OCM hub controllers to recover after any recent restart
  kubectl --context "$ctx" -n open-cluster-management-hub wait \
    --for=condition=Available deployment --all --timeout=120s >/dev/null 2>&1 || true

  local hub_name="${ctx#kind-}"
  local hub_host="${NODE_HOSTS[$hub_name]:-$hub_name}"

  for i in $(seq 1 20); do
    # 1. Try cluster-bootstrap and agent-registration-bootstrap via local context
    for sa in "cluster-bootstrap" "agent-registration-bootstrap"; do
      tok=$(kubectl --context "$ctx" -n open-cluster-management create token "$sa" --duration=87600h 2>/dev/null || true)
      if [ -n "$tok" ]; then
        rm -f "$err_file"
        echo "$tok"
        return 0
      fi
    done

    # 2. Try remote execution on hub host container directly
    if [ -n "$hub_host" ] && [ "$hub_host" != "127.0.0.1" ] && [ "$hub_host" != "localhost" ]; then
      for sa in "cluster-bootstrap" "agent-registration-bootstrap"; do
        tok=$(remote_exec "$hub_host" "docker exec '${hub_name}-control-plane' kubectl -n open-cluster-management create token '$sa' --duration=87600h 2>/dev/null" || true)
        tok=$(echo "$tok" | tr -d '\r\n ' || true)
        if [ -n "$tok" ] && [[ "$tok" =~ ^ey[A-Za-z0-9_-]+ ]]; then
          rm -f "$err_file"
          echo "$tok"
          return 0
        fi
      done
    fi

    out=$(clusteradm get token --context "$ctx" 2>"$err_file" || true)
    tok=$(echo "$out" | grep '^token=' | head -1 | cut -d'=' -f2- || true)
    if [ -z "$tok" ]; then
      tok=$(echo "$out" | grep -oP '(?<=--hub-token )\S+' | head -1 || true)
    fi
    if [ -n "$tok" ]; then
      rm -f "$err_file"
      echo "$tok"
      return 0
    fi
    sleep 5
  done

  log_warn "clusteradm get token failed for ${ctx}. Last error:" >&2
  sed 's/^/    /' "$err_file" >&2 || true
  kubectl --context "$ctx" get clustermanager 2>&1 | sed 's/^/    /' >&2 || true
  kubectl --context "$ctx" -n open-cluster-management-hub get pods 2>&1 | sed 's/^/    /' >&2 || true
  rm -f "$err_file"
  echo ""
}

# Dump klusterlet diagnostics to stderr for debugging registration failures.
_dump_klusterlet_debug() {
  local ctx="$1"
  log_warn "── Klusterlet debug for ${ctx} ──"
  kubectl --context "$ctx" -n open-cluster-management-agent get pods 2>&1 || true
  kubectl --context "$ctx" -n open-cluster-management-agent get secrets 2>&1 || true
  kubectl --context "$ctx" -n open-cluster-management-agent logs deploy/klusterlet-registration-agent --tail=30 2>&1 || true
  kubectl --context "$ctx" get klusterlet klusterlet \
    -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}' 2>&1 || true
}

# Ensure the hub's kube-apiserver serving cert includes the WireGuard overlay IPs.
# If missing, regenerates apiserver.crt with the same CA and restarts the apiserver.
# Args: <cluster-name> <service-cidr>
_ensure_hub_apiserver_sans() {
  local cluster="$1" svc_cidr="$2" node="${1}-control-plane"
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true

  if $is_remote; then
    log_info "${cluster}: apiserver cert SANs already pre-configured in kubeadm certSANs at creation."
    return 0
  fi

  local crt_text
  crt_text=$(docker exec "$node" cat /etc/kubernetes/pki/apiserver.crt 2>/dev/null \
             | openssl x509 -noout -text 2>/dev/null || true)

  if echo "$crt_text" | grep -q "IP Address:${WG_HUB1_IP}\b" && \
     echo "$crt_text" | grep -q "IP Address:${WG_HUB2_IP}\b" && \
     echo "$crt_text" | grep -q "IP Address:${WG_VIP}\b"; then
    log_info "${cluster}: apiserver cert already contains WG SANs."
    return 0
  fi

  log_warn "${cluster}: apiserver cert is missing WG SANs – regenerating (CA unchanged)..."
  local node_ip transit_ip sans
  node_ip=$(docker inspect -f '{{ .NetworkSettings.Networks.kind.IPAddress }}' "$node")
  transit_ip=$(docker inspect -f "{{ (index .NetworkSettings.Networks \"${TRANSIT_NET_NAME}\").IPAddress }}" "$node" 2>/dev/null || true)
  sans="localhost,127.0.0.1,0.0.0.0,${WG_HUB1_IP},${WG_HUB2_IP},${WG_VIP},${node_ip}"
  [ -n "$transit_ip" ] && sans="${sans},${transit_ip}"

  docker exec "$node" bash -c "
    set -e
    # Ensure /kind/kubeadm.conf preserves SANs across container restarts/reboots
    if [ -f /kind/kubeadm.conf ]; then
      for ip in ${WG_HUB1_IP} ${WG_HUB2_IP} ${WG_VIP} 0.0.0.0; do
        if ! grep -q \"\- \${ip}\" /kind/kubeadm.conf; then
          sed -i \"/certSANs:/a \ \ - \${ip}\" /kind/kubeadm.conf
        fi
      done
    fi
    cd /etc/kubernetes/pki
    mkdir -p /root/pki-backup && cp -f apiserver.crt apiserver.key /root/pki-backup/
    rm -f apiserver.crt apiserver.key
    kubeadm init phase certs apiserver \
      --cert-dir /etc/kubernetes/pki \
      --kubernetes-version \$(kubeadm version -o short) \
      --service-cidr '${svc_cidr}' \
      --apiserver-advertise-address '${node_ip}' \
      --apiserver-cert-extra-sans '${sans}'
    crictl ps --name kube-apiserver -q | xargs -r crictl stop >/dev/null
  "

  log_info "${cluster}: waiting for kube-apiserver to come back..."
  local ok=false
  for i in $(seq 1 60); do
    if docker exec "$node" curl -sk -m 3 https://127.0.0.1:6443/readyz >/dev/null 2>&1; then
      ok=true; break
    fi
    sleep 2
  done
  [ "$ok" = true ] || { log_error "${cluster}: apiserver did not become ready after cert rotation."; exit 1; }

  if docker exec "$node" cat /etc/kubernetes/pki/apiserver.crt | openssl x509 -noout -text \
       | grep -q "IP Address:${WG_HUB1_IP}\b"; then
    log_success "${cluster}: apiserver cert now valid for ${WG_HUB1_IP}, ${WG_HUB2_IP}, ${WG_VIP}."
  else
    log_error "${cluster}: cert regeneration did not add the WG SANs."
    exit 1
  fi
}

_build_hub_kubeconfig_secrets() {
  local secrets_json="["
  local first=true
  for hub in "${HUB_LIST[@]}"; do
    $first || secrets_json+=","
    secrets_json+="{ \"name\": \"${hub}-kubeconfig\" }"
    first=false
  done
  secrets_json+="]"
  echo "$secrets_json"
}

_check_phase_16() {
  local is_remote=false
  [ "$PRIMARYHUB_HOST" != "127.0.0.1" ] && [ "$PRIMARYHUB_HOST" != "localhost" ] && is_remote=true
  if $is_remote; then
    ensure_all_cluster_tunnels 16
  fi

  local primary_hub="${HUB_LIST[0]:-primaryhub}"
  local spoke avail joined
  for spoke in "${SPOKE_LIST[@]}"; do
    avail=$(kubectl --context "kind-${primary_hub}" get managedcluster "$spoke" \
      -o jsonpath='{.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status}' 2>/dev/null || echo "")
    [ "$avail" == "True" ] || return 1

    joined=$(kubectl --context "kind-${primary_hub}" get managedcluster "$spoke" \
      -o jsonpath='{.status.conditions[?(@.type=="ManagedClusterJoined")].status}' 2>/dev/null || echo "")
    [ "$joined" == "True" ] || return 1

    kubectl --context "kind-${spoke}" get klusterlet klusterlet \
      -o jsonpath='{.spec.registrationConfiguration.featureGates[?(@.feature=="MultipleHubs")].mode}' 2>/dev/null \
      | grep -q "Enable" || return 1
  done
  return 0
}

_do_phase_16_join_spokes_to_ocm() {
  # Ensure all hub ssh tunnels and apiserver certs cover the WG overlay IPs
  for hub in "${HUB_LIST[@]}"; do
    local hub_host="${NODE_HOSTS[$hub]:-$hub}"
    if [ "$hub_host" != "127.0.0.1" ] && [ "$hub_host" != "localhost" ]; then
      ensure_cluster_ssh_tunnel "$hub" "$hub_host"
      _sync_remote_kubeconfig "$hub" "$hub_host"
    fi
    _ensure_hub_apiserver_sans "$hub" "10.96.0.0/16"
  done

  local agent_ns="open-cluster-management-agent"
  local primary_hub="${HUB_LIST[0]:-primaryhub}"
  local primary_wg_ip
  primary_wg_ip=$(_get_wg_ip_for_node "$primary_hub")
  local primary_hub_url="https://${primary_wg_ip}:6443"

  # Obtain bootstrap tokens and write bootstrap kubeconfigs for all registered hubs
  declare -A HUB_TOKENS=()
  for hub in "${HUB_LIST[@]}"; do
    local tok
    tok=$(_get_hub_token "kind-${hub}")
    if [ -z "$tok" ]; then
      log_error "Could not obtain join token for hub: ${hub}"
      log_error "Is OCM initialised on all hubs? (phase 8)"
      exit 1
    fi
    HUB_TOKENS["$hub"]="$tok"

    local h_wg_ip
    h_wg_ip=$(_get_wg_ip_for_node "$hub")
    local h_url="https://${h_wg_ip}:6443"
    _write_hub_bootstrap_kubeconfig "${STATE_DIR}/${hub}-bootstrap.kubeconfig" "$h_url" "$tok" "${hub}-control-plane"
  done

  # Backward compatibility: write secondaryhub config if secondaryhub is present
  if [ "${#HUB_LIST[@]}" -gt 1 ]; then
    cp -f "${STATE_DIR}/${HUB_LIST[1]}-bootstrap.kubeconfig" "${STATE_DIR}/secondaryhub-bootstrap.kubeconfig" 2>/dev/null || true
  fi

  for spoke in "${SPOKE_LIST[@]}"; do
    local is_remote=false
    local target_spoke_host="${NODE_HOSTS[$spoke]:-$spoke}"
    [ "$target_spoke_host" != "127.0.0.1" ] && [ "$target_spoke_host" != "localhost" ] && is_remote=true

    if $is_remote; then
      ensure_cluster_ssh_tunnel "$spoke" "$target_spoke_host"
      _sync_remote_kubeconfig "$spoke" "$target_spoke_host"
    else
      kind export kubeconfig --name "$spoke" 2>/dev/null || true
    fi
    local ctx="kind-${spoke}"
    log_info "── ${spoke} ──"

    # Ensure control-plane NoSchedule taint is removed on single-node spoke clusters.
    kubectl --context "$ctx" taint nodes "${spoke}-control-plane"       node-role.kubernetes.io/control-plane:NoSchedule- 2>/dev/null || true

    # 1. Run clusteradm join pointing at primaryhub
    log_info "Running clusteradm join for ${spoke} -> ${primary_hub} (${primary_hub_url})..."
    local join_ok=false
    for i in $(seq 1 15); do
      local join_err
      join_err=$(clusteradm --context "$ctx" join         --hub-token "${HUB_TOKENS[$primary_hub]}"         --hub-apiserver "$primary_hub_url"         --cluster-name "$spoke"         --force-internal-endpoint-lookup         --wait 2>&1 || true)
      if echo "$join_err" | grep -q "Please log in to the hub"; then
        join_ok=true
        log_info "clusteradm join succeeded for ${spoke}."
        break
      fi
      if kubectl --context "$ctx" get klusterlet klusterlet >/dev/null 2>&1; then
        join_ok=true
        log_info "Klusterlet CR already present on ${spoke} – join accepted."
        break
      fi
      log_warn "clusteradm join attempt $i failed for ${spoke}: $(echo "$join_err" | tail -1) – retrying in 4s..."
      sleep 4
    done
    [ "$join_ok" = false ] && log_warn "clusteradm join did not complete cleanly for ${spoke}; proceeding with patch."

    # 2. Populate bootstrap secrets for ALL hubs in HUB_LIST
    kubectl --context "$ctx" create namespace "$agent_ns" 2>/dev/null || true
    for hub in "${HUB_LIST[@]}"; do
      kubectl --context "$ctx" -n "$agent_ns" create secret generic "${hub}-kubeconfig"         --from-file=kubeconfig="${STATE_DIR}/${hub}-bootstrap.kubeconfig"         --dry-run=client -o yaml | kubectl --context "$ctx" apply -f -
    done
    kubectl --context "$ctx" -n "$agent_ns" create secret generic bootstrap-hub-kubeconfig       --from-file=kubeconfig="${STATE_DIR}/${primary_hub}-bootstrap.kubeconfig"       --dry-run=client -o yaml | kubectl --context "$ctx" apply -f -

    # 3. Patch klusterlet for MultipleHubs with 60s failover timeout
    log_info "Patching CRD schema and klusterlet on ${spoke}: MultipleHubs + LocalSecrets (60s failover)..."
    kubectl --context "$ctx" patch crd klusterlets.operator.open-cluster-management.io --type json -p '[{"op":"replace","path":"/spec/versions/0/schema/openAPIV3Schema/properties/spec/properties/registrationConfiguration/properties/bootstrapKubeConfigs/properties/localSecretsConfig/properties/hubConnectionTimeoutSeconds/minimum","value":10}]' 2>/dev/null || true

    local hub_secrets
    hub_secrets=$(_build_hub_kubeconfig_secrets)

    kubectl --context "$ctx" patch klusterlet klusterlet --type=merge -p "{
      \"spec\": {
        \"registrationConfiguration\": {
          \"featureGates\": [
            { \"feature\": \"MultipleHubs\", \"mode\": \"Enable\" }
          ],
          \"bootstrapKubeConfigs\": {
            \"type\": \"LocalSecrets\",
            \"localSecretsConfig\": {
              \"hubConnectionTimeoutSeconds\": 60,
              \"kubeConfigSecrets\": ${hub_secrets}
            }
          }
        }
      }
    }"

    # 4. Restart registration agent so it picks up the new MultipleHubs config immediately
    log_info "Restarting klusterlet-registration-agent on ${spoke} to apply MultipleHubs config..."
    kubectl --context "$ctx" -n open-cluster-management-agent rollout restart deployment       klusterlet-registration-agent 2>/dev/null || true
    kubectl --context "$ctx" -n open-cluster-management-agent delete pod       -l app=klusterlet-registration-agent --wait=false --request-timeout=5s --ignore-not-found=true 2>/dev/null || true
    sleep 5

    # 5. Approve CSRs on primary hub
    log_info "Accepting ${spoke} on ${primary_hub}..."
    local accepted=false
    for i in $(seq 1 45); do
      if clusteradm accept --context "kind-${primary_hub}" --clusters "$spoke" >/dev/null 2>&1; then
        accepted=true; break
      fi
      sleep 2
    done
    [ "$accepted" = false ] && log_warn "clusteradm accept did not succeed for ${spoke} yet (CSR may still be pending)."
    kubectl --context "kind-${primary_hub}" patch managedcluster "$spoke" --type merge -p '{"spec":{"leaseDurationSeconds":5}}' 2>/dev/null || true

    # Poll until ManagedClusterConditionAvailable = True on primary hub
    local avail="False"
    for i in $(seq 1 60); do
      clusteradm accept --context "kind-${primary_hub}" --clusters "$spoke" >/dev/null 2>&1 || true
      avail=$(kubectl --context "kind-${primary_hub}" get managedcluster "$spoke"         -o jsonpath='{.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status}'         2>/dev/null || echo "False")
      [ "$avail" == "True" ] && break
      sleep 4
    done

    if [ "$avail" == "True" ]; then
      log_success "Spoke '${spoke}' joined ${primary_hub} and is Available (standby hubs kept in failover chain)."
    else
      log_error "Spoke '${spoke}' did not become Available on ${primary_hub}."
      _dump_klusterlet_debug "$ctx"
    fi

    # Best-effort pre-approve on all standby hubs
    for standby_hub in "${HUB_LIST[@]:1}"; do
      clusteradm accept --context "kind-${standby_hub}" --clusters "$spoke" >/dev/null 2>&1 || true
    done
  done
}

phase_16_join_spokes_to_ocm() {
  run_phase "16" "Joining Spokes to OCM (MultipleHubs: primaryhub → secondaryhub)" _check_phase_16 _do_phase_16_join_spokes_to_ocm
}

_check_phase_17() {
  local primary_hub="${HUB_LIST[0]:-primaryhub}"
  local spoke lbl
  for spoke in "${SPOKE_LIST[@]}"; do
    for standby_hub in "${HUB_LIST[@]:1}"; do
      kubectl --context "kind-${standby_hub}" get managedcluster "$spoke" >/dev/null 2>&1 || return 1
    done
    lbl=$(kubectl --context "kind-${primary_hub}" get managedcluster "$spoke" \
      -o jsonpath='{.metadata.labels.sandbox-workload-capable}' 2>/dev/null || echo "")
    [ "$lbl" == "true" ] || return 1
  done
  for standby_hub in "${HUB_LIST[@]:1}"; do
    kubectl --context "kind-${standby_hub}" get managedclusterset sandbox-spokes >/dev/null 2>&1 || return 1
  done
  return 0
}

_do_phase_17_sync_spokes_to_secondaryhub() {
  if [ "$FORCE_RECONFIGURE" != "true" ] && _check_phase_17; then
    log_info "Spoke registration and labels already synchronized to standby hubs – skipping."
    return 0
  fi

  local primary_hub="${HUB_LIST[0]:-primaryhub}"
  log_info "Syncing spoke registration resources from ${primary_hub} to all standby hubs..."

  for standby_hub in "${HUB_LIST[@]:1}"; do
    local target_ctx="kind-${standby_hub}"
    log_info "Syncing spokes to ${standby_hub}..."

    for spoke in "${SPOKE_LIST[@]}"; do
      kubectl --context "kind-${primary_hub}" get namespace "$spoke" -o yaml 2>/dev/null | \
        kubectl --context "$target_ctx" apply -f - 2>/dev/null || true
      kubectl --context "kind-${primary_hub}" get clusterrole \
        "open-cluster-management:managedcluster:${spoke}" -o yaml 2>/dev/null | \
        kubectl --context "$target_ctx" apply -f - 2>/dev/null || true
      kubectl --context "kind-${primary_hub}" get clusterrolebinding \
        "open-cluster-management:managedcluster:${spoke}" -o yaml 2>/dev/null | \
        kubectl --context "$target_ctx" apply -f - 2>/dev/null || true
      kubectl --context "kind-${primary_hub}" get rolebinding -n "$spoke" -o yaml 2>/dev/null | \
        kubectl --context "$target_ctx" apply -f - 2>/dev/null || true
      kubectl --context "kind-${primary_hub}" get managedcluster "$spoke" -o json 2>/dev/null | \
        jq 'del(
              .metadata.uid,
              .metadata.resourceVersion,
              .metadata.creationTimestamp,
              .metadata.ownerReferences,
              .metadata.managedFields,
              .metadata.annotations,
              .metadata.finalizers,
              .status
            ) | .spec.hubAcceptsClient = false' | \
        kubectl --context "$target_ctx" apply -f - 2>/dev/null || true
    done

    kubectl --context "kind-${primary_hub}" get managedclusterset sandbox-spokes -o yaml 2>/dev/null | \
      kubectl --context "$target_ctx" apply -f - 2>/dev/null || true
    kubectl --context "kind-${primary_hub}" get managedclustersetbinding -A -o yaml 2>/dev/null | \
      kubectl --context "$target_ctx" apply -f - 2>/dev/null || true
  done

  # Label spokes across all hubs (primary and standbys)
  for spoke in "${SPOKE_LIST[@]}"; do
    local spoke_wg_ip
    spoke_wg_ip=$(_get_wg_ip_for_node "$spoke")

    for hub in "${HUB_LIST[@]}"; do
      kubectl --context "kind-${hub}" label managedcluster "$spoke" \
        wireguard-ip="${spoke_wg_ip}" \
        sandbox-workload-capable=true \
        runtime.gvisor=true runtime.kata=true runtime.kata-fc=true \
        --overwrite 2>/dev/null || true
    done
  done

  log_success "Spoke registration synced across all active hub clusters."
}

phase_17_sync_spokes_to_secondaryhub() {
  run_phase "17" "Labeling Spokes & Syncing Registration to Standby Hubs" _check_phase_17 _do_phase_17_sync_spokes_to_secondaryhub
}
