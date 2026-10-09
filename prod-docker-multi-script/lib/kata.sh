#!/usr/bin/env bash
# ==============================================================================
# lib/kata.sh – Kata Containers + Firecracker (kata-fc) runtime setup
#
# Configures Kata 3.x with the Firecracker VMM and LVM-backed devmapper
# snapshotter on spoke1 and spoke2 so isolated microVM sandboxes can be
# scheduled using `runtimeClassName: kata-fc`.
#
# Functions:
#   _ensure_kata_host_assets    – download or reuse static Kata release tarball
#   _install_kata_on_spoke      – install binaries, config, and containerd shim
#   setup_kata_firecracker      – orchestrator: install kata-fc on all spokes
# ==============================================================================
#
# Configures Kata Containers 3.x with the Firecracker VMM and LVM-backed devmapper
# snapshotter on spoke1 and spoke2 so that isolated microVM sandboxes can be
# scheduled using `runtimeClassName: kata-fc`.

_check_phase_15b() {
  local spoke
  local is_remote=false
  [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && is_remote=true

  for spoke in spoke1 spoke2; do
    local ctx="kind-${spoke}"
    local target_host="$SPOKE1_HOST"
    [ "$spoke" == "spoke2" ] && target_host="$SPOKE2_HOST"

    # Verify RuntimeClass kata-fc exists in Kubernetes API
    kubectl --context "$ctx" get runtimeclass kata-fc >/dev/null 2>&1 || return 1
    # Verify DaemonSet exists and at least 1 pod is Ready
    kubectl --context "$ctx" -n kube-system get daemonset kata-fc-node-reconciler >/dev/null 2>&1 || return 1
    local ready
    ready=$(kubectl --context "$ctx" -n kube-system get daemonset kata-fc-node-reconciler -o jsonpath='{.status.numberReady}' 2>/dev/null || echo "0")
    [ "$ready" -ge 1 ] || return 1
    # Verify devmapper plugin is active in containerd
    if $is_remote; then
      remote_exec "$target_host" "docker exec ${spoke}-control-plane ctr plugins ls 2>/dev/null | grep -E 'devmapper\s+linux/amd64\s+ok'" >/dev/null 2>&1 || return 1
    else
      docker exec "${spoke}-control-plane" ctr plugins ls 2>/dev/null | grep -E 'devmapper\s+linux/amd64\s+ok' >/dev/null 2>&1 || return 1
    fi
  done
  return 0
}

_ensure_kata_host_assets() {
  mkdir -p "${KATA_CACHE_DIR}"

  # 0. Copy bundled devmapper-enabled containerd toolchain from repo bin/kata
  local repo_kata_bin="${ROOT_DIR}/bin/kata"
  if [ -d "$repo_kata_bin" ]; then
    cp -u "${repo_kata_bin}"/* "${KATA_CACHE_DIR}/" 2>/dev/null || cp "${repo_kata_bin}"/* "${KATA_CACHE_DIR}/" 2>/dev/null || true
  fi

  # 1. Prefer existing host /opt/kata if complete and non-empty
  if [ -s "/opt/kata/bin/containerd-shim-kata-v2" ] && \
     [ -s "/opt/kata/bin/firecracker" ] && \
     [ -s "/opt/kata/share/kata-containers/vmlinux.container" ]; then
    log_info "Reusing existing host Kata assets from /opt/kata."
    KATA_SOURCE_DIR="/opt/kata"
    return 0
  fi

  # 2. Otherwise download static release tarball into cache
  KATA_SOURCE_DIR="${KATA_CACHE_DIR}/opt/kata"
  local kata_tar="${KATA_CACHE_DIR}/kata-static-${KATA_VERSION}-amd64.tar.xz"
  if [ ! -s "$kata_tar" ]; then
    log_info "Downloading Kata Containers static release v${KATA_VERSION}..."
    curl -fSL "https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/kata-static-${KATA_VERSION}-amd64.tar.xz" \
      -o "$kata_tar"
  fi

  if [ ! -s "${KATA_SOURCE_DIR}/bin/containerd-shim-kata-v2" ] || [ ! -s "${KATA_SOURCE_DIR}/share/kata-containers/vmlinux.container" ]; then
    log_info "Extracting Kata static binaries into cache..."
    mkdir -p "${KATA_CACHE_DIR}/extract"
    tar -xJf "$kata_tar" -C "${KATA_CACHE_DIR}/extract"
    mkdir -p "${KATA_SOURCE_DIR}"
    cp -r "${KATA_CACHE_DIR}/extract/opt/kata/"* "${KATA_SOURCE_DIR}/"
    rm -rf "${KATA_CACHE_DIR}/extract"
  fi

  # 3. Ensure firecracker is present and non-empty
  if [ ! -s "${KATA_SOURCE_DIR}/bin/firecracker" ]; then
    log_info "Downloading Firecracker binary ${FIRECRACKER_VERSION}..."
    local fc_tar="${KATA_CACHE_DIR}/firecracker-${FIRECRACKER_VERSION}-x86_64.tgz"
    if [ ! -s "$fc_tar" ]; then
      curl -fSL "https://github.com/firecracker-microvm/firecracker/releases/download/${FIRECRACKER_VERSION}/firecracker-${FIRECRACKER_VERSION}-x86_64.tgz" \
        -o "$fc_tar"
    fi
    tar -xzf "$fc_tar" -C "${KATA_CACHE_DIR}"
    cp "${KATA_CACHE_DIR}/release-${FIRECRACKER_VERSION}-x86_64/firecracker-${FIRECRACKER_VERSION}-x86_64" "${KATA_SOURCE_DIR}/bin/firecracker"
    cp "${KATA_CACHE_DIR}/release-${FIRECRACKER_VERSION}-x86_64/jailer-${FIRECRACKER_VERSION}-x86_64" "${KATA_SOURCE_DIR}/bin/jailer"
    chmod +x "${KATA_SOURCE_DIR}/bin/firecracker" "${KATA_SOURCE_DIR}/bin/jailer"
  fi
}

_configure_spoke_kata_fc() {
  local spoke="$1"
  local ctx="kind-${spoke}"
  local node="${spoke}-control-plane"
  local my_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local daemonset_yaml="${my_dir}/kata-fc-daemonset.yaml"

  log_info "── Configuring Kata Firecracker on ${spoke} (Kubernetes-Native) ──"

  if [ ! -f "$daemonset_yaml" ]; then
    log_fatal "Kata Firecracker DaemonSet manifest not found at: ${daemonset_yaml}"
  fi

  local is_remote=false
  [ "$SPOKE1_HOST" != "127.0.0.1" ] && [ "$SPOKE1_HOST" != "localhost" ] && is_remote=true
  local target_host="$SPOKE1_HOST"
  [ "$spoke" == "spoke2" ] && target_host="$SPOKE2_HOST"

  # 1. Inject devmapper-enabled containerd toolchain into spoke node
  log_info "${spoke}: injecting devmapper containerd binary and tools..."
  if $is_remote; then
    remote_exec "$target_host" "mkdir -p /tmp/kata-assets"
    for f in containerd-devmapper ctr containerd-shim-runc-v2 reset-containerd-devmapper; do
      if [ -s "${KATA_CACHE_DIR}/$f" ]; then
        remote_copy_to "$target_host" "${KATA_CACHE_DIR}/$f" "/tmp/kata-assets/$f"
      fi
    done
    remote_exec "$target_host" "
      if [ -s /tmp/kata-assets/containerd-devmapper ]; then
        docker cp /tmp/kata-assets/containerd-devmapper ${node}:/usr/local/bin/containerd
        docker cp /tmp/kata-assets/containerd-devmapper ${node}:/usr/bin/containerd
      fi
      if [ -s /tmp/kata-assets/ctr ]; then
        docker cp /tmp/kata-assets/ctr ${node}:/usr/local/bin/ctr
        docker cp /tmp/kata-assets/ctr ${node}:/usr/bin/ctr
      fi
      if [ -s /tmp/kata-assets/containerd-shim-runc-v2 ]; then
        docker cp /tmp/kata-assets/containerd-shim-runc-v2 ${node}:/usr/local/bin/containerd-shim-runc-v2
        docker cp /tmp/kata-assets/containerd-shim-runc-v2 ${node}:/usr/bin/containerd-shim-runc-v2
      fi
      if [ -s /tmp/kata-assets/reset-containerd-devmapper ]; then
        docker cp /tmp/kata-assets/reset-containerd-devmapper ${node}:/usr/local/bin/reset-containerd-devmapper
      fi
      docker exec ${node} bash -c '
        [ ! -e /usr/lib/cni ] && ln -sf /opt/cni/bin /usr/lib/cni 2>/dev/null || true
        which vgs >/dev/null 2>&1 && dpkg -l libdevmapper1.02.1 >/dev/null 2>&1 || {
          apt-get update -qq && apt-get install -y -qq lvm2 thin-provisioning-tools libdevmapper1.02.1 >/dev/null 2>&1 || true
        }
      '
    "
  else
    if [ -s "${KATA_CACHE_DIR}/containerd-devmapper" ]; then
      docker cp "${KATA_CACHE_DIR}/containerd-devmapper" "${node}:/usr/local/bin/containerd"
      docker cp "${KATA_CACHE_DIR}/containerd-devmapper" "${node}:/usr/bin/containerd"
    fi
    if [ -s "${KATA_CACHE_DIR}/ctr" ]; then
      docker cp "${KATA_CACHE_DIR}/ctr" "${node}:/usr/local/bin/ctr"
      docker cp "${KATA_CACHE_DIR}/ctr" "${node}:/usr/bin/ctr"
    fi
    if [ -s "${KATA_CACHE_DIR}/containerd-shim-runc-v2" ]; then
      docker cp "${KATA_CACHE_DIR}/containerd-shim-runc-v2" "${node}:/usr/local/bin/containerd-shim-runc-v2"
      docker cp "${KATA_CACHE_DIR}/containerd-shim-runc-v2" "${node}:/usr/bin/containerd-shim-runc-v2"
    fi
    if [ -s "${KATA_CACHE_DIR}/reset-containerd-devmapper" ]; then
      docker cp "${KATA_CACHE_DIR}/reset-containerd-devmapper" "${node}:/usr/local/bin/reset-containerd-devmapper"
    fi

    # 2. Ensure CNI symlink and host dependencies
    docker exec "${node}" bash -c '
      [ ! -e /usr/lib/cni ] && ln -sf /opt/cni/bin /usr/lib/cni 2>/dev/null || true
      which vgs >/dev/null 2>&1 && dpkg -l libdevmapper1.02.1 >/dev/null 2>&1 || {
        apt-get update -qq && apt-get install -y -qq lvm2 thin-provisioning-tools libdevmapper1.02.1 >/dev/null 2>&1 || true
      }
    '
  fi

  # 3. Apply Kubernetes-native resources (RBAC, RuntimeClass, ConfigMap, DaemonSet)
  log_info "${spoke}: applying kata-fc DaemonSet, RuntimeClass, RBAC & reconciler..."
  kubectl --context "$ctx" apply -f "$daemonset_yaml"

  # 4. Wait for Kubernetes DaemonSet rollout
  log_info "${spoke}: waiting for kata-fc-node-reconciler DaemonSet rollout..."
  kubectl --context "$ctx" rollout status daemonset/kata-fc-node-reconciler -n kube-system --timeout=300s

  # 5. Pre-unpack pause image with devmapper snapshotter
  log_info "${spoke}: preparing pause and base images for devmapper snapshotter..."
  if $is_remote; then
    remote_exec "$target_host" "docker exec ${node} bash -c 'ctr -n k8s.io images pull --snapshotter devmapper registry.k8s.io/pause:3.10 >/dev/null 2>&1 || true; ctr -n k8s.io images pull --snapshotter devmapper docker.io/library/alpine:latest >/dev/null 2>&1 || true'"
  else
    docker exec "${node}" bash -c '
      ctr -n k8s.io images pull --snapshotter devmapper registry.k8s.io/pause:3.10 >/dev/null 2>&1 || true
      ctr -n k8s.io images pull --snapshotter devmapper docker.io/library/alpine:latest >/dev/null 2>&1 || true
    '
  fi

  # 6. Verify RuntimeClass is registered in the Kubernetes API
  if ! kubectl --context "$ctx" get runtimeclass kata-fc >/dev/null 2>&1; then
    log_fatal "${spoke}: RuntimeClass kata-fc failed to register in API."
  fi

  # 7. Ensure opensandbox-workloads namespace exists
  kubectl --context "$ctx" create namespace opensandbox-workloads --dry-run=client -o yaml | kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true

  log_success "${spoke}: Kata Firecracker successfully reconciled and active via Kubernetes native DaemonSet."
}

_smoke_test_kata_fc() {
  local spoke="$1"
  local ctx="kind-${spoke}"
  local pod_name="kata-fc-smoke-${spoke}"

  log_info "Running Kata Firecracker smoke test on ${spoke}..."

  cat << EOF | kubectl --context "$ctx" apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: default
spec:
  runtimeClassName: kata-fc
  restartPolicy: Never
  containers:
  - name: test
    image: alpine:latest
    imagePullPolicy: IfNotPresent
    command: ["sh", "-c", "echo 'KATA_SUCCESS' && uname -a && sleep 60"]
EOF

  local pod_ok=false
  for i in $(seq 1 45); do
    local phase
    phase=$(kubectl --context "$ctx" get pod "${pod_name}" -n default -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    if [ "$phase" == "Running" ] || [ "$phase" == "Succeeded" ]; then
      pod_ok=true
      break
    fi
    sleep 2
  done

  if [ "$pod_ok" = true ]; then
    local k_ver
    k_ver=$(kubectl --context "$ctx" logs "${pod_name}" -n default 2>/dev/null | grep -i "Linux" | head -n 1 || echo "")
    log_success "Smoke test pod on ${spoke} is Running under microVM guest kernel: ${k_ver}"
  else
    kubectl --context "$ctx" describe pod "${pod_name}" -n default || true
    kubectl --context "$ctx" delete pod "${pod_name}" -n default --grace-period=0 --force >/dev/null 2>&1 || true
    log_fatal "Smoke test pod on ${spoke} failed to reach Running state under kata-fc!"
  fi

  # Cleanup test pod
  kubectl --context "$ctx" delete pod "${pod_name}" -n default --grace-period=0 --force >/dev/null 2>&1 || true
}

_do_phase_15b_setup_kata_firecracker() {
  # Preflight hardware virtualization check
  if [ ! -e "/dev/kvm" ]; then
    log_warn "Host /dev/kvm was not detected! Firecracker requires hardware virtualization."
    log_warn "If running inside a VM, ensure nested virtualization is enabled."
  else
    log_success "Host /dev/kvm verified."
  fi

  _ensure_kata_host_assets

  for spoke in spoke1 spoke2; do
    # Per-spoke idempotency: check if RuntimeClass and DaemonSet are active
    local ctx="kind-${spoke}"
    local already_ok=true
    kubectl --context "$ctx" get runtimeclass kata-fc >/dev/null 2>&1 || already_ok=false
    local ready
    ready=$(kubectl --context "$ctx" -n kube-system get daemonset kata-fc-node-reconciler -o jsonpath='{.status.numberReady}' 2>/dev/null || echo "0")
    [ "$ready" -ge 1 ] || already_ok=false
    docker exec "${spoke}-control-plane" ctr plugins ls 2>/dev/null | grep -E 'devmapper\s+linux/amd64\s+ok' >/dev/null 2>&1 || already_ok=false

    if [ "$already_ok" = true ] && [ "$FORCE_RECONFIGURE" != "true" ]; then
      log_success "${spoke}: Kata Firecracker Kubernetes DaemonSet already active – skipping."
      continue
    fi
    _configure_spoke_kata_fc "$spoke"
    _smoke_test_kata_fc "$spoke"
  done

  log_success "Phase 15-B: Kata Containers + Firecracker (kata-fc) runtime successfully configured on spoke clusters."
}

phase_15b_setup_kata_firecracker() {
  run_phase "15b" "Setup Kata Firecracker (kata-fc) Runtime on Spokes" _check_phase_15b _do_phase_15b_setup_kata_firecracker
}
