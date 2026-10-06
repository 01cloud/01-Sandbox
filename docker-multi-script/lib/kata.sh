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
    # Check RuntimeClass
    kubectl --context "kind-${spoke}" get runtimeclass kata-fc >/dev/null 2>&1 || return 1
    # Check kata shim is executable
    docker exec "${spoke}-control-plane" test -x /usr/local/bin/containerd-shim-kata-v2 2>/dev/null || return 1
    # Check containerd config has kata-fc configured
    docker exec "${spoke}-control-plane" grep -q 'plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-fc' /etc/containerd/config.toml 2>/dev/null || return 1
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

  # If Kata Firecracker is already fully configured on this spoke, skip
  if [ "$FORCE_RECONFIGURE" != "true" ] && \
     kubectl --context "$ctx" get runtimeclass kata-fc >/dev/null 2>&1 && \
     docker exec "$cname" test -x /usr/local/bin/containerd-shim-kata-v2 2>/dev/null && \
     docker exec "$cname" grep -q 'plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-fc' /etc/containerd/config.toml 2>/dev/null; then
    log_info "${spoke}: Kata Firecracker is already fully configured – skipping."
    return 0
  fi

  log_info "── Configuring Kata Firecracker on ${spoke} (${cname}) ──"

  # 1. Install lvm2 + thin-provisioning-tools only if not already present
  if ! docker exec "$cname" dpkg -s lvm2 >/dev/null 2>&1 || \
     ! docker exec "$cname" dpkg -s thin-provisioning-tools >/dev/null 2>&1; then
    log_info "${spoke}: installing lvm2 and thin-provisioning-tools..."
    docker exec "$cname" bash -c "
      dpkg --purge linux-image-rt-amd64 2>/dev/null || true
      apt-get update -qq && apt-get install -y -qq -f >/dev/null 2>&1 || true
      apt-get install -y -qq lvm2 thin-provisioning-tools >/dev/null 2>&1 || true
    "
  else
    log_info "${spoke}: lvm2 and thin-provisioning-tools already installed – skipping apt."
  fi
  docker exec "$cname" bash -c "
    mkdir -p /opt/kata/bin /opt/kata/share/kata-containers /etc/kata-containers /var/lib/containerd/io.containerd.snapshotter.v1.devmapper
  "

  # 2. Inject containerd binary with devmapper enabled (host binary has devmapper built-in)
  if [ -f "/usr/bin/containerd" ] && ! docker exec "$cname" test -f /usr/local/bin/containerd 2>/dev/null; then
    log_info "${spoke}: copying host containerd binary (devmapper-enabled)..."
    docker cp /usr/bin/containerd "${cname}:/usr/local/bin/containerd"
  fi

  # 3. Copy Kata binaries and assets (skip if shim already present)
  if ! docker exec "$cname" test -x /opt/kata/bin/containerd-shim-kata-v2 2>/dev/null; then
    log_info "${spoke}: copying Kata binaries and assets..."
    docker cp "${KATA_SOURCE_DIR}/bin/containerd-shim-kata-v2" "${cname}:/opt/kata/bin/"
    docker cp "${KATA_SOURCE_DIR}/bin/firecracker" "${cname}:/opt/kata/bin/"
    docker cp "${KATA_SOURCE_DIR}/bin/jailer" "${cname}:/opt/kata/bin/" 2>/dev/null || true
    docker cp "${KATA_SOURCE_DIR}/bin/kata-runtime" "${cname}:/opt/kata/bin/" 2>/dev/null || true
    docker cp "${KATA_SOURCE_DIR}/bin/kata-ctl" "${cname}:/opt/kata/bin/" 2>/dev/null || true
    docker cp "${KATA_SOURCE_DIR}/share/kata-containers/." "${cname}:/opt/kata/share/kata-containers/"
    if [ -d "${KATA_SOURCE_DIR}/share/defaults/kata-containers" ]; then
      docker exec "$cname" mkdir -p /opt/kata/share/defaults
      docker cp "${KATA_SOURCE_DIR}/share/defaults/kata-containers" "${cname}:/opt/kata/share/defaults/"
    fi
  else
    log_info "${spoke}: Kata binaries already present – skipping copy."
  fi

  # 4. Copy or generate /etc/kata-containers/configuration.toml (skip if already present)
  if ! docker exec "$cname" test -f /etc/kata-containers/configuration.toml 2>/dev/null; then
    log_info "${spoke}: generating kata configuration.toml..."
    if [ -f "/etc/kata-containers/configuration.toml" ]; then
      docker cp /etc/kata-containers/configuration.toml "${cname}:/etc/kata-containers/configuration.toml"
    else
      docker exec "$cname" bash -c "
        mkdir -p /etc/kata-containers
        cp /opt/kata/share/defaults/kata-containers/configuration-fc.toml /etc/kata-containers/configuration.toml 2>/dev/null || true
        sed -i 's|^path = .*|path = \"/usr/local/bin/firecracker\"|g' /etc/kata-containers/configuration.toml 2>/dev/null || true
        sed -i 's|^jailer_path = .*|jailer_path = \"/usr/local/bin/jailer\"|g' /etc/kata-containers/configuration.toml 2>/dev/null || true
      "
    fi
  else
    log_info "${spoke}: kata configuration.toml already exists – skipping."
  fi

  # 5. Set symlinks inside the container (ln -sf is idempotent, always safe)
  docker exec "$cname" bash -c "
    ln -sf /opt/kata/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
    ln -sf /opt/kata/bin/firecracker /usr/local/bin/firecracker
    ln -sf /opt/kata/bin/jailer /usr/local/bin/jailer 2>/dev/null || true
    ln -sf /opt/kata/bin/kata-runtime /usr/local/bin/kata-runtime 2>/dev/null || true
    ln -sf /opt/kata/bin/kata-ctl /usr/local/bin/kata-ctl 2>/dev/null || true
  "

  # 6. Install robust dmsetup wrapper (skip if already installed)
  if ! docker exec "$cname" test -f /usr/sbin/dmsetup.orig 2>/dev/null; then
    log_info "${spoke}: installing dmsetup wrapper..."
  fi
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

  # 7. Provision dedicated loopback disk & LVM thin-pool (skip if VG already exists)
  docker exec -i "$cname" bash <<EOF
    IMG="/var/lib/containerd-pool-disk-${spoke}.img"
    VG="${vg_name}"
    POOL="containerd-pool"

    if ! vgs "\$VG" >/dev/null 2>&1; then
      truncate -s 15G "\$IMG"
      LOOP_DEV=\$(losetup -fP --show "\$IMG")
      pvcreate -y "\$LOOP_DEV" >/dev/null 2>&1
      vgcreate "\$VG" "\$LOOP_DEV" >/dev/null 2>&1
      lvcreate -y --config 'activation { udev_sync = 0 udev_rules = 0 }' -W n -Z n --size 12G --thinpool "\$POOL" "\$VG" >/dev/null 2>&1
    fi
    vgchange -ay --monitor y "\$VG" >/dev/null 2>&1 || true
    /usr/sbin/dmsetup mknodes >/dev/null 2>&1 || true
EOF

  docker exec -i "$cname" tee /usr/local/bin/init-containerd-devmapper.sh >/dev/null << 'SCRIPTEOF'
#!/bin/bash
set -e

# 1. Attach loop devices for any containerd disk images in /var/lib
for img in /var/lib/containerd-pool-disk-*.img; do
  if [ -f "$img" ]; then
    if ! losetup -j "$img" 2>/dev/null | grep -q "$img"; then
      losetup -fP "$img" 2>/dev/null || true
    fi
  fi
done

# 2. Determine target pool and volume group from containerd config if available
REQUIRED_POOL=""
if [ -f /etc/containerd/config.toml ]; then
  REQUIRED_POOL=$(grep -oP 'pool_name\s*=\s*"\K[^"]+' /etc/containerd/config.toml 2>/dev/null | head -n1 || true)
fi

# 3. Identify VGs
VGS=$(vgs --noheadings -o vg_name 2>/dev/null | tr -d ' ' | grep '^containerd-vg' || true)
if [ -z "$VGS" ]; then
  vgscan 2>/dev/null || true
  VGS=$(vgs --noheadings -o vg_name 2>/dev/null | tr -d ' ' | grep '^containerd-vg' || true)
fi

# 4. Activate volume groups and clear stale metadata locks if inactive
for vg in $VGS; do
  vgchange -ay "$vg" 2>/dev/null || true
  if ! lvs -o lv_attr --noheadings "$vg/containerd-pool" 2>/dev/null | grep -q '^ *twi-a'; then
    lvchange -an "${vg}/containerd-pool_tmeta" 2>/dev/null || true
    vgchange -an "$vg" 2>/dev/null || true
    vgchange -ay "$vg" 2>/dev/null || true
  fi
done

# 5. Ensure device nodes exist in /dev and /dev/mapper
mkdir -p /dev/mapper
DMSETUP="/usr/sbin/dmsetup.orig"
[ ! -x "$DMSETUP" ] && DMSETUP="/usr/sbin/dmsetup"
if [ -x "$DMSETUP" ]; then
  $DMSETUP mknodes 2>/dev/null || true
  $DMSETUP ls 2>/dev/null | while read -r name majmin; do
    maj=$(echo "$majmin" | tr -d '()' | cut -d: -f1)
    min=$(echo "$majmin" | tr -d '()' | cut -d: -f2)
    if [ -n "$maj" ] && [ -n "$min" ]; then
      [ ! -e "/dev/dm-${min}" ] && mknod "/dev/dm-${min}" b "$maj" "$min" 2>/dev/null || true
      [ ! -e "/dev/mapper/${name}" ] && ln -sf "/dev/dm-${min}" "/dev/mapper/${name}" 2>/dev/null || true
    fi
  done
fi

# 6. Verify required pool exists if specified
if [ -n "$REQUIRED_POOL" ]; then
  if [ ! -b "/dev/mapper/${REQUIRED_POOL}" ]; then
    TARGET_VG=$(echo "$REQUIRED_POOL" | sed 's/-containerd--pool$//' | sed 's/--/-/g')
    lvchange -an "${TARGET_VG}/containerd-pool_tmeta" 2>/dev/null || true
    vgchange -an "$TARGET_VG" 2>/dev/null || true
    vgchange -ay "$TARGET_VG" 2>/dev/null || true
    [ -x "$DMSETUP" ] && $DMSETUP mknodes 2>/dev/null || true
  fi
fi

exit 0
SCRIPTEOF

  docker exec -i "$cname" tee /etc/systemd/system/containerd-devmapper.service >/dev/null << 'UNITEOF'
[Unit]
Description=Ensure Loop Devices and LVM Thin Pool for Containerd Devmapper
DefaultDependencies=no
Before=containerd.service
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/init-containerd-devmapper.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target containerd.service
UNITEOF

  docker exec "$cname" mkdir -p /etc/systemd/system/containerd.service.d
  docker exec -i "$cname" tee /etc/systemd/system/containerd.service.d/10-devmapper.conf >/dev/null << 'DROPINEOF'
[Service]
ExecStartPre=/usr/local/bin/init-containerd-devmapper.sh
DROPINEOF

  docker exec "$cname" bash -c "
    chmod +x /usr/local/bin/init-containerd-devmapper.sh
    systemctl daemon-reload
    systemctl enable containerd-devmapper.service
  "

  # 8. Configure containerd with devmapper snapshotter and kata-fc runtime handler
  # The grep guards inside are already idempotent; we only restart containerd if a change was made.
  docker exec "$cname" bash -c "
    changed=0
    sed -i 's/discard_unpacked_layers = true/discard_unpacked_layers = false/' /etc/containerd/config.toml 2>/dev/null || true

    if ! grep -q 'plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.kata-fc' /etc/containerd/config.toml; then
      cat <<'EOF' >> /etc/containerd/config.toml

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
      changed=1
    else
      sed -i 's/pool_name = .*/pool_name = \"${pool_name}\"/' /etc/containerd/config.toml 2>/dev/null || true
      sed -i 's/discard_blocks = true/discard_blocks = false/' /etc/containerd/config.toml 2>/dev/null || true
    fi

    # Only restart containerd if config was modified (avoids disrupting running pods)
    if [ \"\$changed\" = 1 ]; then
      systemctl restart containerd
    else
      echo '[INFO] containerd config unchanged – skipping restart.'
    fi
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
    # Per-spoke idempotency: skip configure+test if this spoke is already fully set up
    local ctx="kind-${spoke}"
    local already_ok=true
    kubectl --context "$ctx" get runtimeclass kata-fc >/dev/null 2>&1             || already_ok=false
    docker exec "${spoke}-control-plane" test -x /usr/local/bin/containerd-shim-kata-v2 2>/dev/null || already_ok=false
    docker exec "${spoke}-control-plane" ctr plugins ls 2>/dev/null \
      | grep -E "devmapper\s+linux/amd64\s+ok" >/dev/null 2>&1                    || already_ok=false

    if [ "$already_ok" = true ] && [ "$FORCE_RECONFIGURE" != "true" ]; then
      log_success "${spoke}: Kata Firecracker already configured – skipping."
      continue
    fi
    _configure_spoke_kata_fc "$spoke"
  done

  log_success "Phase 15-B: Kata Containers + Firecracker (kata-fc) runtime successfully configured on spoke clusters."
}

phase_15b_setup_kata_firecracker() {
  run_phase "15b" "Setup Kata Firecracker (kata-fc) Runtime on Spokes" _check_phase_15b _do_phase_15b_setup_kata_firecracker
}
