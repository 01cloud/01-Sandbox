#!/usr/bin/env bash
# ==============================================================================
# lib/phase_16_ocm_join.sh – Join spokes to OCM with MultipleHubs klusterlet
#
# Flow per spoke:
#   1. Patch hub apiserver certs to include WG SANs (cert rotation if needed)
#   2. Obtain bootstrap tokens from each hub
#   3. Run `clusteradm join` via primaryhub (inside spoke container)
#   4. Create per-hub bootstrap-kubeconfig secrets in open-cluster-management-agent
#   5. Patch klusterlet: enable MultipleHubs feature gate + LocalSecrets config
#   6. Approve CSRs on primaryhub; best-effort pre-approve on secondaryhub
# ==============================================================================

# Write a bootstrap kubeconfig pointing directly at one hub over the WG overlay.
# Both hubs share the same Root CA (phases 3/7), so one CA bundle serves both.
# Args: <out-file> <server-url> <token>
_write_hub_bootstrap_kubeconfig() {
  local out="$1" server="$2" token="$3" ca_b64
  ca_b64=$(base64 -w0 < "${PKI_DIR}/ca.crt")
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

  for i in $(seq 1 20); do
    # Generate long-lived (10-year) token for agent-registration-bootstrap so bootstrap secrets never expire
    tok=$(kubectl --context "$ctx" -n open-cluster-management create token agent-registration-bootstrap --duration=87600h 2>/dev/null || true)
    if [ -n "$tok" ]; then
      rm -f "$err_file"
      echo "$tok"
      return 0
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

_check_phase_16() {
  local spoke avail joined
  for spoke in spoke1 spoke2; do
    avail=$(kubectl --context kind-primaryhub get managedcluster "$spoke" \
      -o jsonpath='{.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status}' 2>/dev/null || echo "")
    [ "$avail" == "True" ] || return 1

    joined=$(kubectl --context kind-primaryhub get managedcluster "$spoke" \
      -o jsonpath='{.status.conditions[?(@.type=="ManagedClusterJoined")].status}' 2>/dev/null || echo "")
    [ "$joined" == "True" ] || return 1

    kubectl --context "kind-${spoke}" get klusterlet klusterlet \
      -o jsonpath='{.spec.registrationConfiguration.featureGates[?(@.feature=="MultipleHubs")].mode}' 2>/dev/null \
      | grep -q "Enable" || return 1
  done
  return 0
}

_do_phase_16_join_spokes_to_ocm() {
  # Ensure hub apiserver certs cover the WG overlay IPs
  _ensure_hub_apiserver_sans "primaryhub"   "10.96.0.0/16"
  _ensure_hub_apiserver_sans "secondaryhub" "10.97.0.0/16"

  local agent_ns="open-cluster-management-agent"
  local hub1_url="https://${WG_HUB1_IP}:6443"
  local hub2_url="https://${WG_HUB2_IP}:6443"

  # Obtain bootstrap tokens
  local hub1_token hub2_token
  hub1_token=$(_get_hub_token kind-primaryhub)
  hub2_token=$(_get_hub_token kind-secondaryhub)
  if [ -z "$hub1_token" ] || [ -z "$hub2_token" ]; then
    log_error "Could not obtain join token(s): primaryhub='${hub1_token:+ok}' secondaryhub='${hub2_token:+ok}'"
    log_error "Is OCM initialised on both hubs? (phase 8)"
    exit 1
  fi

  # Write bootstrap kubeconfigs (index 0 = primary priority)
  _write_hub_bootstrap_kubeconfig "${STATE_DIR}/primaryhub-bootstrap.kubeconfig"   "$hub1_url" "$hub1_token"
  _write_hub_bootstrap_kubeconfig "${STATE_DIR}/secondaryhub-bootstrap.kubeconfig" "$hub2_url" "$hub2_token"

  for spoke in spoke1 spoke2; do
    local ctx="kind-${spoke}"
    log_info "── ${spoke} ──"

    # Reachability sanity check
    for target in "$hub1_url" "$hub2_url"; do
      if docker exec "${spoke}-control-plane" curl -sk -m 5 "${target}/version" >/dev/null 2>&1; then
        log_success "${spoke} can reach ${target}"
      else
        log_warn "${spoke} cannot reach ${target} over WireGuard – check phase 14."
      fi
    done

    # 1. Install klusterlet (operator + CR) if absent
    if ! kubectl --context "$ctx" get klusterlet klusterlet >/dev/null 2>&1; then
      log_info "Running clusteradm join for ${spoke} (initial bootstrap via primaryhub)..."
      if ! docker exec "${spoke}-control-plane" test -x /usr/local/bin/clusteradm; then
        docker cp "$(command -v clusteradm)" "${spoke}-control-plane:/usr/local/bin/clusteradm"
      fi
      docker exec "${spoke}-control-plane" bash -c "
        export KUBECONFIG=/etc/kubernetes/admin.conf
        clusteradm join \
          --hub-token '${hub1_token}' \
          --hub-apiserver '${hub1_url}' \
          --cluster-name '${spoke}'
      " || log_warn "clusteradm join returned non-zero; continuing to verify."

      kubectl --context "$ctx" wait --for=condition=established \
        crd/klusterlets.operator.open-cluster-management.io --timeout=120s || true

      local k_ok=false
      for i in $(seq 1 30); do
        if kubectl --context "$ctx" get klusterlet klusterlet >/dev/null 2>&1; then
          k_ok=true; break
        fi
        sleep 2
      done
      if [ "$k_ok" = false ]; then
        log_error "Klusterlet CR was not created on ${spoke}."
        _dump_klusterlet_debug "$ctx"
        exit 1
      fi
    else
      log_info "Klusterlet already present on ${spoke}."
    fi

    # 2. Per-hub bootstrap kubeconfig secrets
    kubectl --context "$ctx" create namespace "$agent_ns" --dry-run=client -o yaml \
      | kubectl --context "$ctx" apply -f - >/dev/null

    kubectl --context "$ctx" -n "$agent_ns" create secret generic primaryhub-kubeconfig \
      --from-file=kubeconfig="${STATE_DIR}/primaryhub-bootstrap.kubeconfig" \
      --dry-run=client -o yaml | kubectl --context "$ctx" apply -f -
    kubectl --context "$ctx" -n "$agent_ns" create secret generic secondaryhub-kubeconfig \
      --from-file=kubeconfig="${STATE_DIR}/secondaryhub-bootstrap.kubeconfig" \
      --dry-run=client -o yaml | kubectl --context "$ctx" apply -f -

    # 3. Patch klusterlet for MultipleHubs with 60s failover timeout
    log_info "Patching CRD schema and klusterlet on ${spoke}: MultipleHubs + LocalSecrets (60s failover)..."
    kubectl --context "$ctx" patch crd klusterlets.operator.open-cluster-management.io --type json -p '[{"op":"replace","path":"/spec/versions/0/schema/openAPIV3Schema/properties/spec/properties/registrationConfiguration/properties/bootstrapKubeConfigs/properties/localSecretsConfig/properties/hubConnectionTimeoutSeconds/minimum","value":10}]' 2>/dev/null || true

    kubectl --context "$ctx" patch klusterlet klusterlet --type=merge -p '{
      "spec": {
        "registrationConfiguration": {
          "featureGates": [
            { "feature": "MultipleHubs", "mode": "Enable" }
          ],
          "bootstrapKubeConfigs": {
            "type": "LocalSecrets",
            "localSecretsConfig": {
              "hubConnectionTimeoutSeconds": 60,
              "kubeConfigSecrets": [
                { "name": "primaryhub-kubeconfig" },
                { "name": "secondaryhub-kubeconfig" }
              ]
            }
          }
        }
      }
    }'

    # 4. Approve CSRs on primaryhub
    log_info "Accepting ${spoke} on primaryhub..."
    local accepted=false
    for i in $(seq 1 45); do
      if clusteradm accept --context kind-primaryhub --clusters "$spoke" >/dev/null 2>&1; then
        accepted=true; break
      fi
      sleep 2
    done
    [ "$accepted" = false ] && log_warn "clusteradm accept did not succeed for ${spoke} yet (CSR may still be pending)."
    kubectl --context kind-primaryhub patch managedcluster "$spoke" --type merge -p '{"spec":{"leaseDurationSeconds":5}}' 2>/dev/null || true

    # Poll until ManagedClusterConditionAvailable = True
    local avail="False"
    for i in $(seq 1 45); do
      clusteradm accept --context kind-primaryhub --clusters "$spoke" >/dev/null 2>&1 || true
      avail=$(kubectl --context kind-primaryhub get managedcluster "$spoke" \
        -o jsonpath='{.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status}' \
        2>/dev/null || echo "False")
      [ "$avail" == "True" ] && break
      sleep 4
    done

    if [ "$avail" == "True" ]; then
      log_success "Spoke '${spoke}' joined primaryhub and is Available (secondaryhub kept as standby)."
    else
      log_error "Spoke '${spoke}' did not become Available on primaryhub."
      _dump_klusterlet_debug "$ctx"
    fi

    # Best-effort pre-approve on standby hub
    clusteradm accept --context kind-secondaryhub --clusters "$spoke" >/dev/null 2>&1 || true
  done
}

phase_16_join_spokes_to_ocm() {
  run_phase "16" "Joining Spokes to OCM (MultipleHubs: primaryhub → secondaryhub)" _check_phase_16 _do_phase_16_join_spokes_to_ocm
}
