#!/usr/bin/env bash
# ==============================================================================
# lib/gvisor.sh – gVisor (runsc) runtime setup for Spoke Clusters
#
# Configures Google gVisor (runsc) on spoke1 and spoke2 so lightweight sandboxed
# workloads can be scheduled using `runtimeClassName: gvisor` without replacing
# or interfering with `kata-fc`.
#
# Functions:
#   _ensure_gvisor_host_assets  – download and cache static gVisor release
#   _install_gvisor_on_spoke    – install binaries, config, and containerd runtime
#   _smoke_test_gvisor          – verify runtime with a container
#   phase_15c_setup_gvisor      – orchestrator: install gvisor on all spokes
# ==============================================================================

_check_phase_15c() {
  local spoke
  local is_remote=false
  [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && is_remote=true

  for spoke in spoke1 spoke2; do
    local target_host="$SPOKE1_HOST"
    [ "$spoke" == "spoke2" ] && target_host="$SPOKE2_HOST"

    # Check RuntimeClass
    kubectl --context "kind-${spoke}" get runtimeclass gvisor >/dev/null 2>&1 || return 1

    if $is_remote; then
      remote_exec "$target_host" "docker exec ${spoke}-control-plane test -x /usr/local/bin/runsc" || return 1
      remote_exec "$target_host" "docker exec ${spoke}-control-plane test -x /usr/local/bin/containerd-shim-runsc-v1" || return 1
      remote_exec "$target_host" "docker exec ${spoke}-control-plane grep -q containerd.runtimes.runsc /etc/containerd/config.toml" || return 1
    else
      # Check runsc binary
      docker exec "${spoke}-control-plane" test -x /usr/local/bin/runsc || return 1
      # Check containerd shim
      docker exec "${spoke}-control-plane" test -x /usr/local/bin/containerd-shim-runsc-v1 || return 1
      # Check containerd plugin configuration
      docker exec "${spoke}-control-plane" grep -q "containerd.runtimes.runsc" /etc/containerd/config.toml || return 1
    fi
  done
  return 0
}

_ensure_gvisor_host_assets() {
  mkdir -p "${GVISOR_CACHE_DIR}"

  if [ -x "${GVISOR_CACHE_DIR}/runsc" ] && \
     [ -x "${GVISOR_CACHE_DIR}/containerd-shim-runsc-v1" ] && \
     [ -d "${GVISOR_CACHE_DIR}/gvisor-bin" ]; then
    log_info "Reusing cached gVisor assets from ${GVISOR_CACHE_DIR}."
    return 0
  fi

  log_info "Downloading gVisor static release (latest x86_64)..."
  local tmp_tar="${GVISOR_CACHE_DIR}/gvisor.tar.zstd"
  curl -fSL "https://storage.googleapis.com/gvisor/releases/release/latest/x86_64/gvisor.tar.zstd" \
    -o "$tmp_tar"

  log_info "Extracting gVisor binaries into cache..."
  tar --zstd -xf "$tmp_tar" -C "${GVISOR_CACHE_DIR}"
  chmod -R a+rx "${GVISOR_CACHE_DIR}"
  rm -f "$tmp_tar"
  log_success "gVisor assets cached in ${GVISOR_CACHE_DIR}."
}

_install_gvisor_on_spoke() {
  local spoke="$1"
  local cname="${spoke}-control-plane"
  local ctx="kind-${spoke}"
  local target_host="$SPOKE1_HOST"
  [ "$spoke" == "spoke2" ] && target_host="$SPOKE2_HOST"

  local is_remote=false
  [ "$target_host" != "127.0.0.1" ] && [ "$target_host" != "localhost" ] && is_remote=true

  log_step "Installing gVisor (runsc) on ${spoke} (${cname})..."

  if $is_remote; then
    # Copy cached binaries to remote host, then copy into container
    remote_copy_to "$target_host" "${GVISOR_CACHE_DIR}/runsc" "/tmp/runsc"
    remote_copy_to "$target_host" "${GVISOR_CACHE_DIR}/containerd-shim-runsc-v1" "/tmp/containerd-shim-runsc-v1"
    remote_exec "$target_host" bash -s << REMOTE_GVISOR_EOF
docker cp /tmp/runsc "${cname}:/usr/local/bin/runsc"
docker cp /tmp/containerd-shim-runsc-v1 "${cname}:/usr/local/bin/containerd-shim-runsc-v1"
docker exec "${cname}" chmod a+rx /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1
docker exec "${cname}" bash -c "
  if ! grep -q 'containerd.runtimes.runsc' /etc/containerd/config.toml; then
    cat << 'EOF' >> /etc/containerd/config.toml

# gVisor Runtime (runsc / gvisor)
[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.runsc]
  runtime_type = \"io.containerd.runsc.v1\"

[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.gvisor]
  runtime_type = \"io.containerd.runsc.v1\"
EOF
    systemctl restart containerd
  fi
"
REMOTE_GVISOR_EOF
  else
    # 1. Copy binaries and sidecars into spoke node only if missing
    if ! docker exec "$cname" test -x /usr/local/bin/runsc 2>/dev/null || \
       ! docker exec "$cname" test -x /usr/local/bin/containerd-shim-runsc-v1 2>/dev/null; then
      docker cp "${GVISOR_CACHE_DIR}/runsc" "${cname}:/usr/local/bin/runsc"
      docker cp "${GVISOR_CACHE_DIR}/containerd-shim-runsc-v1" "${cname}:/usr/local/bin/containerd-shim-runsc-v1"
      docker cp "${GVISOR_CACHE_DIR}/gvisor-bin" "${cname}:/usr/local/bin/gvisor-bin" 2>/dev/null || true
      docker exec "$cname" bash -c "
        chmod a+rx /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1
        [ -d /usr/local/bin/gvisor-bin ] && chmod -R a+rx /usr/local/bin/gvisor-bin || true
      "
    else
      log_info "${spoke}: gVisor binaries already present – skipping copy."
    fi

    # 2. Configure containerd runtime if not already configured
    docker exec "$cname" bash -c "
      if ! grep -q 'containerd.runtimes.runsc' /etc/containerd/config.toml; then
        cat << 'EOF' >> /etc/containerd/config.toml

# gVisor Runtime (runsc / gvisor)
[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.runsc]
  runtime_type = \"io.containerd.runsc.v1\"

[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.gvisor]
  runtime_type = \"io.containerd.runsc.v1\"
EOF
        systemctl restart containerd
      else
        echo '[INFO] containerd config already includes runsc – skipping restart.'
      fi
    "
  fi

  # 3. Apply Kubernetes RuntimeClass gvisor
  cat << EOF | kubectl --context "$ctx" apply -f - >/dev/null
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
EOF
  log_success "RuntimeClass gvisor created on ${spoke}."
}

_smoke_test_gvisor() {
  local spoke="$1"
  local ctx="kind-${spoke}"
  local pod_name="gvisor-smoke-${spoke}"

  log_info "Running gVisor smoke test on ${spoke}..."

  cat << EOF | kubectl --context "$ctx" apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: default
spec:
  runtimeClassName: gvisor
  restartPolicy: Never
  containers:
  - name: test
    image: busybox:musl
    command: ["sh", "-c", "dmesg | head -n 5 && sleep 10"]
EOF

  local passed=false
  for i in $(seq 1 30); do
    local phase
    phase=$(kubectl --context "$ctx" get pod "${pod_name}" -n default -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    if [ "$phase" = "Running" ] || [ "$phase" = "Succeeded" ]; then
      local logs
      logs=$(kubectl --context "$ctx" logs "${pod_name}" -n default 2>/dev/null || echo "")
      if echo "$logs" | grep -qi "Starting gVisor"; then
        log_success "gVisor smoke test PASSED on ${spoke}! (Confirmed gVisor sandbox kernel execution)"
        passed=true
        break
      fi
    elif [ "$phase" = "Failed" ]; then
      log_warn "gVisor smoke test pod reported Failed on ${spoke}."
      break
    fi
    sleep 1
  done

  kubectl --context "$ctx" delete pod "${pod_name}" -n default --ignore-not-found=true >/dev/null 2>&1 || true

  if [ "$passed" != true ]; then
    log_warn "gVisor smoke test did not confirm execution within timeout on ${spoke}."
  fi
}

_do_phase_15c_setup_gvisor() {
  _ensure_gvisor_host_assets

  local is_remote=false
  [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && is_remote=true

  local spoke
  for spoke in spoke1 spoke2; do
    local ctx="kind-${spoke}"
    local cname="${spoke}-control-plane"
    local target_host="$SPOKE1_HOST"
    [ "$spoke" == "spoke2" ] && target_host="$SPOKE2_HOST"
    local already_ok=true

    kubectl --context "$ctx" get runtimeclass gvisor >/dev/null 2>&1 || already_ok=false
    if $is_remote; then
      remote_exec "$target_host" "docker exec $cname test -x /usr/local/bin/runsc" 2>/dev/null || already_ok=false
      remote_exec "$target_host" "docker exec $cname test -x /usr/local/bin/containerd-shim-runsc-v1" 2>/dev/null || already_ok=false
      remote_exec "$target_host" "docker exec $cname grep -q containerd.runtimes.runsc /etc/containerd/config.toml" 2>/dev/null || already_ok=false
    else
      docker exec "$cname" test -x /usr/local/bin/runsc 2>/dev/null                         || already_ok=false
      docker exec "$cname" test -x /usr/local/bin/containerd-shim-runsc-v1 2>/dev/null     || already_ok=false
      docker exec "$cname" grep -q "containerd.runtimes.runsc" /etc/containerd/config.toml 2>/dev/null || already_ok=false
    fi

    if [ "$already_ok" = true ] && [ "$FORCE_RECONFIGURE" != "true" ]; then
      log_success "${spoke}: gVisor (runsc) already configured – skipping."
      continue
    fi

    _install_gvisor_on_spoke "$spoke"
    _smoke_test_gvisor "$spoke"
  done

  log_success "Phase 15-C: gVisor (runsc) runtime successfully configured on spoke clusters."
}

phase_15c_setup_gvisor() {
  run_phase "15c" "Setup gVisor (runsc) Runtime on Spokes" _check_phase_15c _do_phase_15c_setup_gvisor
}
