#!/usr/bin/env bash
# ==============================================================================
# lib/03_system.sh – Host-level system helpers
#
# Functions:
#   ensure_kernel_inotify_limits   – raise inotify limits for multi-cluster KinD
#   ensure_docker_access           – start Docker daemon and fix socket perms
#   _apply_repo_paths              – set SANDBOX_REPO_DIR / CODE_INSPECTOR_DIR
#   ensure_sandbox_repo            – locate or clone the 01-Sandbox repository
#   _relax_webhook_failure_policy  – set OCM/CNPG webhooks to Ignore to avoid
#                                    deadlocks during bootstrap
# ==============================================================================

# Ensure host inotify limits are sufficient for running multiple KinD clusters.
# KinD control-plane nodes run systemd, containerd, and multiple daemonsets.
# Default limits (watches: 8192/65536, instances: 128) lead to EMFILE /
# "could not find a log line that matches Reached target Multi-User System" when
# launching the 3rd or 4th cluster.
ensure_kernel_inotify_limits() {
  local cur_watches cur_instances
  cur_watches=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)
  cur_instances=$(cat /proc/sys/fs/inotify/max_user_instances 2>/dev/null || echo 0)

  local need_watches=524288
  local need_instances=8192

  if [ "${cur_watches:-0}" -lt "$need_watches" ] || [ "${cur_instances:-0}" -lt "$need_instances" ]; then
    log_info "Tuning host inotify limits for multi-cluster KinD (watches: $cur_watches -> $need_watches, instances: $cur_instances -> $need_instances)..."
    local SUDO=""
    if [ "$EUID" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
      SUDO="sudo"
    fi
    $SUDO sysctl -w fs.inotify.max_user_watches=$need_watches >/dev/null 2>&1 || sysctl -w fs.inotify.max_user_watches=$need_watches >/dev/null 2>&1 || true
    $SUDO sysctl -w fs.inotify.max_user_instances=$need_instances >/dev/null 2>&1 || sysctl -w fs.inotify.max_user_instances=$need_instances >/dev/null 2>&1 || true

    if [ -d /etc/sysctl.d ]; then
      printf "fs.inotify.max_user_watches = %d\nfs.inotify.max_user_instances = %d\n" "$need_watches" "$need_instances" | \
        $SUDO tee /etc/sysctl.d/99-kind-inotify.conf >/dev/null 2>&1 || true
    fi
  fi
}

# Ensure Docker daemon is active and socket is accessible to the current user.
ensure_docker_access() {
  command -v docker >/dev/null 2>&1 || return 0

  if ! docker info >/dev/null 2>&1; then
    local SUDO=""
    if [ "$EUID" -ne 0 ]; then
      command -v sudo >/dev/null 2>&1 && SUDO="sudo"
    fi

    if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
      $SUDO systemctl enable --now docker 2>/dev/null || true
      $SUDO systemctl start docker 2>/dev/null || true
      $SUDO service docker start 2>/dev/null || true

      # Wait up to 10s for /var/run/docker.sock to appear
      local _w=0
      while [ ! -S /var/run/docker.sock ] && [ $_w -lt 20 ]; do
        sleep 0.5
        _w=$((_w + 1))
      done

      # Add user to docker group permanently
      $SUDO usermod -aG docker "$USER" 2>/dev/null || true

      # Grant immediate read/write access to docker.sock for the current session
      if [ -S /var/run/docker.sock ]; then
        $SUDO chmod 666 /var/run/docker.sock 2>/dev/null || true
        command -v setfacl >/dev/null 2>&1 && $SUDO setfacl -m u:"$USER":rw /var/run/docker.sock 2>/dev/null || true
      fi
    fi
  fi
}

# Apply detected repository root path to all dependent directory variables.
# Args: <repo-root>
_apply_repo_paths() {
  local base="$1"
  SANDBOX_REPO_DIR="$base"
  OPENSANDBOX_BUILD_DIR="${base}/opensandbox-server/docker-build"
  CODE_INSPECTOR_DIR="${base}/codeInspector"

  # Ensure the repository is on branch feat/production
  if [ -d "${base}/.git" ]; then
    local current_branch
    current_branch=$(git -C "$base" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ "$current_branch" != "feat/production" ]; then
      log_info "Switching 01-Sandbox repository from '$current_branch' to branch 'feat/production'..."
      git -C "$base" checkout feat/production 2>/dev/null || \
      git -C "$base" checkout -b feat/production origin/feat/production 2>/dev/null || \
      log_warn "Could not switch to feat/production; remaining on $current_branch."
    fi
  fi

  # Ensure ConfigMap template handles boolean values as strings gracefully
  local cmap_tmpl="${CODE_INSPECTOR_DIR}/charts/apiServer/templates/configmap.yaml"
  if [ -f "$cmap_tmpl" ]; then
    sed -i 's/tpl \$value \$/tpl (\$value | toString) \$/g' "$cmap_tmpl" 2>/dev/null || true
  fi
}

# Locate or clone the 01-Sandbox repository.
# Sets SANDBOX_REPO_DIR, CODE_INSPECTOR_DIR, and OPENSANDBOX_BUILD_DIR.
ensure_sandbox_repo() {
  # 1. Already valid
  if [ -d "${OPENSANDBOX_BUILD_DIR}" ] && [ -f "${OPENSANDBOX_BUILD_DIR}/Dockerfile" ]; then
    _apply_repo_paths "$SANDBOX_REPO_DIR"
    return 0
  fi

  # 2. Search well-known candidate locations
  local candidates=(
    "${ROOT_DIR}/01-Sandbox"
    "${ROOT_DIR}"
    "$(pwd)/01-Sandbox"
    "$(pwd)"
    "/home/berrybytes/Desktop/Kamal/01-Sandbox"
  )

  for cand in "${candidates[@]}"; do
    if [ -d "${cand}/opensandbox-server/docker-build" ] && [ -f "${cand}/opensandbox-server/docker-build/Dockerfile" ]; then
      log_info "Detected 01-Sandbox repository at: ${cand}"
      _apply_repo_paths "$cand"
      return 0
    fi
  done

  # 3. Recursive search under parent and HOME
  local found
  found=$(find "${ROOT_DIR}" "${HOME}" -maxdepth 4 -type d -path "*/opensandbox-server/docker-build" 2>/dev/null | head -1 || true)
  if [ -n "$found" ] && [ -f "${found}/Dockerfile" ]; then
    local repo_base
    repo_base="$(dirname "$(dirname "$found")")"
    log_info "Discovered 01-Sandbox repository at: ${repo_base}"
    _apply_repo_paths "$repo_base"
    return 0
  fi

  # 4. Clone via SSH
  log_step "Cloning 01-Sandbox Repository"
  local clone_target="${ROOT_DIR}/01-Sandbox"
  [ -d "$clone_target" ] && clone_target="${STATE_DIR}/01-Sandbox"

  local repo_url="${REPO_URL:-git@github.com:01cloud/01-Sandbox.git}"
  local repo_branch="${REPO_BRANCH:-feat/production}"

  log_info "Cloning 01-Sandbox via SSH (${repo_url}, branch: ${repo_branch}) into: ${clone_target}..."
  mkdir -p "$(dirname "$clone_target")"
  [ -d "$clone_target" ] && [ ! -d "${clone_target}/opensandbox-server" ] && rm -rf "$clone_target"

  local clone_ok=false
  if GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --depth 1 -b "$repo_branch" "$repo_url" "$clone_target" 2>/dev/null || \
     GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --depth 1 "$repo_url" "$clone_target"; then
    clone_ok=true
  fi

  if [ "$clone_ok" = true ] && [ -d "${clone_target}/opensandbox-server/docker-build" ]; then
    log_success "01-Sandbox repository cloned successfully to: ${clone_target}"
    _apply_repo_paths "$clone_target"
    return 0
  else
    log_error "Unable to locate or clone 01-Sandbox repository via SSH into ${clone_target}."
    log_error "Please ensure your SSH key is added to GitHub (ssh -T git@github.com) or clone manually:"
    log_error "  git clone git@github.com:01cloud/01-Sandbox.git ${clone_target}"
    return 1
  fi
}

# Set OCM and CNPG admission webhooks to failurePolicy=Ignore so that webhook
# pods spinning up during bootstrap do not deadlock pending API requests.
# Args: <context>
_relax_webhook_failure_policy() {
  local ctx="$1"
  log_info "Ensuring admission webhooks on $ctx do not block deployment..."

  for vwh in managedclustersetbindingvalidators.admission.cluster.open-cluster-management.io \
             managedclustervalidators.admission.cluster.open-cluster-management.io \
             manifestworkvalidators.admission.work.open-cluster-management.io; do
    if kubectl --context "$ctx" get validatingwebhookconfiguration "$vwh" >/dev/null 2>&1; then
      kubectl --context "$ctx" get validatingwebhookconfiguration "$vwh" -o json 2>/dev/null | \
        jq '(.webhooks[].failurePolicy) = "Ignore"' 2>/dev/null | \
        kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
    fi
  done

  if kubectl --context "$ctx" get mutatingwebhookconfiguration cnpg-mutating-webhook-configuration >/dev/null 2>&1; then
    kubectl --context "$ctx" get mutatingwebhookconfiguration cnpg-mutating-webhook-configuration -o json 2>/dev/null | \
      jq '(.webhooks[].failurePolicy) = "Ignore"' 2>/dev/null | \
      kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
  fi

  if kubectl --context "$ctx" get validatingwebhookconfiguration cnpg-validating-webhook-configuration >/dev/null 2>&1; then
    kubectl --context "$ctx" get validatingwebhookconfiguration cnpg-validating-webhook-configuration -o json 2>/dev/null | \
      jq '(.webhooks[].failurePolicy) = "Ignore"' 2>/dev/null | \
      kubectl --context "$ctx" apply -f - >/dev/null 2>&1 || true
  fi
}
