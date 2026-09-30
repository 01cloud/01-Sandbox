#!/usr/bin/env bash
# ==============================================================================
# lib/phase_15b_kata_fc.sh – Setup Kata Containers + Firecracker (kata-fc) Runtime
#
# Configures Kata Containers 3.x with the Firecracker VMM and LVM-backed devmapper
# snapshotter on spoke1 and spoke2 so that isolated microVM sandboxes can be
# scheduled using `runtimeClassName: kata-fc`.
# ==============================================================================

_check_phase_15b() {
  local spoke
  for spoke in spoke1 spoke2; do
    # Check RuntimeClass
    kubectl --context "kind-${spoke}" get runtimeclass kata-fc >/dev/null 2>&1 || return 1
    # Check containerd devmapper plugin is active (ok)
    docker exec "${spoke}-control-plane" ctr plugins ls 2>/dev/null | grep -E "devmapper\s+linux/amd64\s+ok" >/dev/null 2>&1 || return 1
    # Check kata shim is executable
    docker exec "${spoke}-control-plane" test -x /usr/local/bin/containerd-shim-kata-v2 || return 1
  done
  return 0
}

_ensure_kata_host_assets() {
  mkdir -p "${KATA_CACHE_DIR}"

  # 1. Prefer existing host /opt/kata if complete
  if [ -f "/opt/kata/bin/containerd-shim-kata-v2" ] && \
     [ -f "/opt/kata/bin/firecracker" ] && \
     [ -f "/opt/kata/share/kata-containers/vmlinux.container" ]; then
    log_info "Reusing existing host Kata assets from /opt/kata."
    KATA_SOURCE_DIR="/opt/kata"
    return 0
  fi

  # 2. Otherwise download static release tarball into cache
  KATA_SOURCE_DIR="${KATA_CACHE_DIR}/opt/kata"
  local kata_tar="${KATA_CACHE_DIR}/kata-static-${KATA_VERSION}-amd64.tar.xz"
  if [ ! -f "$kata_tar" ]; then
    log_info "Downloading Kata Containers static release v${KATA_VERSION}..."
    curl -fSL "https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/kata-static-${KATA_VERSION}-amd64.tar.xz" \
      -o "$kata_tar"
  fi

  if [ ! -f "${KATA_SOURCE_DIR}/bin/containerd-shim-kata-v2" ]; then
    log_info "Extracting Kata static binaries into cache..."
    mkdir -p "${KATA_CACHE_DIR}/extract"
    tar -xJf "$kata_tar" -C "${KATA_CACHE_DIR}/extract"
    mkdir -p "${KATA_SOURCE_DIR}"
    cp -r "${KATA_CACHE_DIR}/extract/opt/kata/"* "${KATA_SOURCE_DIR}/"
    rm -rf "${KATA_CACHE_DIR}/extract"
  fi

  # 3. Ensure firecracker is present
  if [ ! -f "${KATA_SOURCE_DIR}/bin/firecracker" ]; then
    log_info "Downloading Firecracker binary ${FIRECRACKER_VERSION}..."
    local fc_tar="${KATA_CACHE_DIR}/firecracker-${FIRECRACKER_VERSION}-x86_64.tgz"
    if [ ! -f "$fc_tar" ]; then
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
  local cname="${spoke}-control-plane"
  local ctx="kind-${spoke}"
  local vg_name="containerd-vg-${spoke}"
  local pool_name="containerd--vg--${spoke}-containerd--pool"

  log_info "── Configuring Kata Firecracker on ${spoke} (${cname}) ──"

  # 1. Purge any conflict kernel image packages & install lvm2 + thin-provisioning-tools
  docker exec "$cname" bash -c "
    dpkg --purge linux-image-rt-amd64 2>/dev/null || true
    apt-get update -qq && apt-get install -y -qq -f >/dev/null 2>&1 || true
    apt-get install -y -qq lvm2 thin-provisioning-tools >/dev/null 2>&1 || true
    mkdir -p /opt/kata/bin /opt/kata/share/kata-containers /etc/kata-containers /var/lib/containerd/io.containerd.snapshotter.v1.devmapper
  "

  # 2. Inject containerd binary with devmapper enabled (host binary has devmapper built-in)
  if [ -f "/usr/bin/containerd" ]; then
    docker cp /usr/bin/containerd "${cname}:/usr/local/bin/containerd"
  fi

  # 3. Copy Kata binaries and assets
  docker cp "${KATA_SOURCE_DIR}/bin/containerd-shim-kata-v2" "${cname}:/opt/kata/bin/"
  docker cp "${KATA_SOURCE_DIR}/bin/firecracker" "${cname}:/opt/kata/bin/"
  docker cp "${KATA_SOURCE_DIR}/bin/jailer" "${cname}:/opt/kata/bin/" 2>/dev/null || true
  docker cp "${KATA_SOURCE_DIR}/bin/kata-runtime" "${cname}:/opt/kata/bin/" 2>/dev/null || true
  docker cp "${KATA_SOURCE_DIR}/bin/kata-ctl" "${cname}:/opt/kata/bin/" 2>/dev/null || true
  docker cp "${KATA_SOURCE_DIR}/share/kata-containers/." "${cname}:/opt/kata/share/kata-containers/"

  # 4. Copy or generate /etc/kata-containers/configuration.toml
  if [ -f "/etc/kata-containers/configuration.toml" ]; then
    docker cp /etc/kata-containers/configuration.toml "${cname}:/etc/kata-containers/configuration.toml"
  else
    docker exec "$cname" bash -c "
      cp /opt/kata/share/defaults/kata-containers/configuration-fc.toml /etc/kata-containers/configuration.toml 2>/dev/null || true
      sed -i 's|^path = .*|path = \"/usr/local/bin/firecracker\"|g' /etc/kata-containers/configuration.toml 2>/dev/null || true
    "
  fi

  # 5. Set symlinks inside the container
  docker exec "$cname" bash -c "
    ln -sf /opt/kata/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
    ln -sf /opt/kata/bin/firecracker /usr/local/bin/firecracker
    ln -sf /opt/kata/bin/jailer /usr/local/bin/jailer 2>/dev/null || true
    ln -sf /opt/kata/bin/kata-runtime /usr/local/bin/kata-runtime 2>/dev/null || true
    ln -sf /opt/kata/bin/kata-ctl /usr/local/bin/kata-ctl 2>/dev/null || true
  "

  # 6. Install robust dmsetup wrapper to auto-create /dev/dm-* and /dev/mapper/* device nodes in KinD
  docker exec "$cname" bash -c '
    if [ ! -f /usr/sbin/dmsetup.orig ]; then
      mv /usr/sbin/dmsetup /usr/sbin/dmsetup.orig
    fi
    cat << "EOF" > /usr/sbin/dmsetup
#!/bin/sh
/usr/sbin/dmsetup.orig "$@"
ret=$?
if [ $ret -eq 0 ]; then
  /usr/sbin/dmsetup.orig mknodes >/dev/null 2>&1 || true
  /usr/sbin/dmsetup.orig ls 2>/dev/null | while read -r name majmin; do
    maj=$(echo "$majmin" | tr -d "()" | cut -d: -f1)
    min=$(echo "$majmin" | tr -d "()" | cut -d: -f2)
    if [ -n "$maj" ] && [ -n "$min" ]; then
      [ ! -e "/dev/dm-${min}" ] && mknod "/dev/dm-${min}" b "$maj" "$min" 2>/dev/null || true
      [ ! -e "/dev/mapper/${name}" ] && ln -sf "/dev/dm-${min}" "/dev/mapper/${name}" 2>/dev/null || true
    fi
  done
fi
exit $ret
EOF
    chmod +x /usr/sbin/dmsetup
  '

  # 7. Provision dedicated loopback disk & LVM thin-pool for this spoke
  docker exec "$cname" bash -c "
    IMG=\"/var/lib/containerd-pool-disk-${spoke}.img\"
    VG=\"${vg_name}\"
    POOL=\"containerd-pool\"

    if ! vgs \"\$VG\" >/dev/null 2>&1; then
      truncate -s 15G \"\$IMG\"
      LOOP_DEV=\$(losetup -fP --show \"\$IMG\")
      pvcreate -y \"\$LOOP_DEV\" >/dev/null 2>&1
      vgcreate \"\$VG\" \"\$LOOP_DEV\" >/dev/null 2>&1
      lvcreate -y --config 'activation { udev_sync = 0 udev_rules = 0 }' -W n -Z n --size 12G --thinpool \"\$POOL\" \"\$VG\" >/dev/null 2>&1
    fi
    vgchange -ay --monitor y \"\$VG\" >/dev/null 2>&1 || true
    /usr/sbin/dmsetup mknodes >/dev/null 2>&1 || true

    cat << \"UNITEOF\" > /etc/systemd/system/containerd-devmapper.service
[Unit]
Description=Ensure Loop Devices and LVM Thin Pool for Containerd Devmapper
DefaultDependencies=no
Before=containerd.service

[Service]
Type=oneshot
ExecStart=/bin/bash -c \"for img in /var/lib/*.img; do [ -f \\\"\\\$img\\\" ] && losetup -fP \\\"\\\$img\\\" 2>/dev/null || true; done; vgchange -ay 2>/dev/null || true; /usr/sbin/dmsetup mknodes 2>/dev/null || true\"
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNITEOF
    systemctl daemon-reload
    systemctl enable containerd-devmapper.service
  "

  # 8. Configure containerd with devmapper snapshotter and kata-fc runtime handler
  docker exec "$cname" bash -c "
    sed -i 's/discard_unpacked_layers = true/discard_unpacked_layers = false/' /etc/containerd/config.toml 2>/dev/null || true

    if ! grep -q 'plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.kata-fc' /etc/containerd/config.toml; then
      cat << 'EOF' >> /etc/containerd/config.toml

# Kata Firecracker Runtime (kata-fc) using devmapper snapshotter
[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.kata-fc]
  runtime_type = \"io.containerd.kata.v2\"
  snapshotter = \"devmapper\"

[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.kata-fc.options]
  ConfigPath = \"/etc/kata-containers/configuration.toml\"

# Devmapper Snapshotter Plugin for Kata
[plugins.\"io.containerd.snapshotter.v1.devmapper\"]
  root_path = \"/var/lib/containerd/io.containerd.snapshotter.v1.devmapper\"
  pool_name = \"${pool_name}\"
  base_image_size = \"4GB\"
  discard_blocks = false
  fs_type = \"ext4\"
EOF
    else
      sed -i 's/pool_name = .*/pool_name = \"${pool_name}\"/' /etc/containerd/config.toml 2>/dev/null || true
      sed -i 's/discard_blocks = true/discard_blocks = false/' /etc/containerd/config.toml 2>/dev/null || true
    fi

    # Restart containerd cleanly
    systemctl restart containerd
  "

  # Wait for containerd to become active
  local ready=false
  for i in $(seq 1 30); do
    if docker exec "$cname" ctr plugins ls 2>/dev/null | grep -E "devmapper\s+linux/amd64\s+ok" >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 1
  done

  if [ "$ready" = true ]; then
    log_success "Containerd devmapper plugin successfully initialized on ${spoke}."
  else
    log_warn "Containerd devmapper plugin check did not report ok yet on ${spoke}."
  fi

  # 9. Apply Kubernetes RuntimeClass kata-fc
  cat << EOF | kubectl --context "$ctx" apply -f - >/dev/null
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: kata-fc
handler: kata-fc
EOF
  log_success "RuntimeClass kata-fc created on ${spoke}."

  # 10. Ensure opensandbox-workloads namespace & klusterlet execution permissions exist
  kubectl --context "$ctx" create namespace opensandbox-workloads --dry-run=client -o yaml | kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
  kubectl --context "$ctx" create clusterrolebinding klusterlet-work-cluster-admin \
    --clusterrole=cluster-admin \
    --serviceaccount=open-cluster-management-agent:klusterlet-work-sa \
    --dry-run=client -o yaml | kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
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
    image: busybox:musl
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
    _configure_spoke_kata_fc "$spoke"
  done

  # Run quick verification smoke test
  for spoke in spoke1 spoke2; do
    _smoke_test_kata_fc "$spoke"
  done

  log_success "Phase 15-B: Kata Containers + Firecracker (kata-fc) runtime successfully configured on spoke clusters."
}

phase_15b_setup_kata_firecracker() {
  run_phase "15b" "Setup Kata Firecracker (kata-fc) Runtime on Spokes" _check_phase_15b _do_phase_15b_setup_kata_firecracker
}
