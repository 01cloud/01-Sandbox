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
  for spoke in spoke1 spoke2; do
    local ctx="kind-${spoke}"
    # Verify RuntimeClass kata-fc exists in Kubernetes API
    kubectl --context "$ctx" get runtimeclass kata-fc >/dev/null 2>&1 || return 1
    # Verify DaemonSet exists and at least 1 pod is Ready
    kubectl --context "$ctx" -n kube-system get daemonset kata-fc-node-reconciler >/dev/null 2>&1 || return 1
    local ready
    ready=$(kubectl --context "$ctx" -n kube-system get daemonset kata-fc-node-reconciler -o jsonpath='{.status.numberReady}' 2>/dev/null || echo "0")
    [ "$ready" -ge 1 ] || return 1
  done
  return 0
}

_ensure_kata_host_assets() {
  mkdir -p "${KATA_CACHE_DIR}"

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
  local my_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local daemonset_yaml="${my_dir}/kata-fc-daemonset.yaml"

  log_info "── Configuring Kata Firecracker on ${spoke} via Kubernetes-Native DaemonSet ──"

  if [ ! -f "$daemonset_yaml" ]; then
    log_error "DaemonSet manifest not found at ${daemonset_yaml}"
    return 1
  fi

  # 1. Declaratively apply the Kubernetes-native Kata Firecracker resources
  log_info "${spoke}: applying kata-fc DaemonSet, RuntimeClass, RBAC & reconciler..."
  kubectl --context "$ctx" apply -f "$daemonset_yaml"

  # 2. Wait for Kubernetes to roll out and reconcile the Kata Firecracker node reconciler
  log_info "${spoke}: waiting for kata-fc-node-reconciler DaemonSet rollout..."
  kubectl --context "$ctx" rollout status daemonset/kata-fc-node-reconciler -n kube-system --timeout=120s

  # 3. Verify RuntimeClass is active
  kubectl --context "$ctx" get runtimeclass kata-fc >/dev/null

  log_success "${spoke}: Kata Firecracker successfully reconciled via Kubernetes native DaemonSet."
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
    image: debian:bookworm-slim
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
    log_warn "Smoke test pod on ${spoke} did not reach Running state within timeout (check logs)."
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
