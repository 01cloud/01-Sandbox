#!/usr/bin/env bash
# ==============================================================================
# lib/preflight.sh – System helpers and host toolchain preflight checks
#
# Functions:
#   ensure_kernel_inotify_limits  – raise inotify limits for multi-cluster KinD
#   ensure_docker_access          – start Docker daemon and fix socket perms
#   ensure_sandbox_repo           – locate or clone the 01-Sandbox repository
#   _relax_webhook_failure_policy – set webhooks to Ignore during bootstrap
#   check_and_install_tools       – install docker/kind/kubectl/helm/clusteradm
#   run_preflight                 – orchestrator: all of the above
# ==============================================================================
#
# Functions:
#   ensure_kernel_inotify_limits   – raise inotify limits for multi-cluster KinD
#   ensure_docker_access           – start Docker daemon and fix socket perms
#   _apply_repo_paths              – set SANDBOX_REPO_DIR / CODE_INSPECTOR_DIR
#   ensure_sandbox_repo            – locate or clone the 01-Sandbox repository
#   _relax_webhook_failure_policy  – set OCM/CNPG webhooks to Ignore to avoid
#                                    deadlocks during bootstrap

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
#
# Checks for and (if missing) installs: git, docker, kind, kubectl, helm,
# clusteradm, jq, curl, wg. Also ensures the Docker daemon is running,
# the 01-Sandbox repository is reachable, and kernel inotify limits are raised.

_check_phase_01() {
  command -v git        >/dev/null 2>&1 || return 1
  command -v docker      >/dev/null 2>&1 || return 1
  docker info            >/dev/null 2>&1 || return 1
  command -v kind        >/dev/null 2>&1 || return 1
  command -v kubectl     >/dev/null 2>&1 || return 1
  command -v helm        >/dev/null 2>&1 || return 1
  command -v clusteradm  >/dev/null 2>&1 || return 1
  command -v jq          >/dev/null 2>&1 || return 1
  command -v curl        >/dev/null 2>&1 || return 1
  command -v wg          >/dev/null 2>&1 || return 1

  local cur_watches cur_instances
  cur_watches=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)
  cur_instances=$(cat /proc/sys/fs/inotify/max_user_instances 2>/dev/null || echo 0)
  [ "${cur_watches:-0}" -ge 524288 ] && [ "${cur_instances:-0}" -ge 8192 ] || return 1

  return 0
}

_do_phase_01_preflight() {
  # ── Detect package manager ─────────────────────────────────────────────────
  local PKG_MGR=""
  if command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
  elif command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
  elif command -v yum >/dev/null 2>&1; then
    PKG_MGR="yum"
  else
    log_warn "No supported package manager found (apt/dnf/yum). Will attempt binary installs only."
  fi

  # ── Sudo availability ──────────────────────────────────────────────────────
  local SUDO=""
  if [ "$EUID" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
      SUDO="sudo"
    else
      log_warn "Not running as root and 'sudo' not found – installations may fail."
    fi
  fi

  # Helper: refresh apt cache once
  local _apt_updated=false
  _apt_update_once() {
    if [ "$_apt_updated" = false ] && [ "$PKG_MGR" = "apt" ]; then
      log_info "Updating apt package index..."
      $SUDO apt-get update -qq
      _apt_updated=true
    fi
  }

  # ── Per-tool installer functions ───────────────────────────────────────────
  _install_docker() {
    log_info "Installing Docker Engine..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq ca-certificates gnupg lsb-release curl >/dev/null 2>&1
      $SUDO install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
      $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
        $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
      $SUDO apt-get update -qq
      $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q yum-utils >/dev/null
      $SUDO yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo >/dev/null
      $SUDO "$PKG_MGR" install -y -q docker-ce docker-ce-cli containerd.io >/dev/null
    else
      log_warn "Cannot install Docker automatically. Please install Docker manually: https://docs.docker.com/engine/install/"
      return 1
    fi
    $SUDO systemctl enable --now docker 2>/dev/null || true
    $SUDO systemctl start docker 2>/dev/null || true
    $SUDO service docker start 2>/dev/null || true
    local _w=0
    while [ ! -S /var/run/docker.sock ] && [ $_w -lt 20 ]; do sleep 0.5; _w=$((_w + 1)); done
    $SUDO usermod -aG docker "$USER" 2>/dev/null || true
    if [ -S /var/run/docker.sock ]; then
      $SUDO chmod 666 /var/run/docker.sock 2>/dev/null || true
      command -v setfacl >/dev/null 2>&1 && $SUDO setfacl -m u:"$USER":rw /var/run/docker.sock 2>/dev/null || true
    fi
    log_success "Docker installed."
  }

  _install_kind() {
    log_info "Installing KinD (Kubernetes in Docker)..."
    local arch; arch=$(uname -m)
    case "$arch" in
      x86_64)  arch="amd64" ;;
      aarch64) arch="arm64" ;;
      *) log_warn "Unsupported arch $arch for KinD"; return 1 ;;
    esac
    local kind_version
    kind_version=$(curl -fsSL https://api.github.com/repos/kubernetes-sigs/kind/releases/latest \
      | grep '"tag_name"' | cut -d'"' -f4)
    kind_version="${kind_version:-v0.23.0}"
    curl -fsSL "https://kind.sigs.k8s.io/dl/${kind_version}/kind-linux-${arch}" -o /tmp/kind-bin
    chmod +x /tmp/kind-bin
    $SUDO mv /tmp/kind-bin /usr/local/bin/kind
    log_success "KinD ${kind_version} installed."
  }

  _install_kubectl() {
    log_info "Installing kubectl..."
    local arch; arch=$(uname -m); [ "$arch" = "x86_64" ] && arch="amd64" || arch="arm64"
    local k8s_version
    k8s_version=$(curl -fsSL https://dl.k8s.io/release/stable.txt)
    k8s_version="${k8s_version:-v1.30.0}"
    curl -fsSL "https://dl.k8s.io/release/${k8s_version}/bin/linux/${arch}/kubectl" -o /tmp/kubectl-bin
    chmod +x /tmp/kubectl-bin
    $SUDO mv /tmp/kubectl-bin /usr/local/bin/kubectl
    log_success "kubectl ${k8s_version} installed."
  }

  _install_helm() {
    log_info "Installing Helm v3..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | $SUDO bash >/dev/null 2>&1
    log_success "Helm installed ($(helm version --short 2>/dev/null || echo 'ok'))."
  }

  _install_clusteradm() {
    log_info "Installing clusteradm (OCM CLI)..."
    curl -fsSL https://raw.githubusercontent.com/open-cluster-management-io/clusteradm/main/install.sh | $SUDO bash >/dev/null 2>&1
    if ! command -v clusteradm >/dev/null 2>&1; then
      local arch; arch=$(uname -m); [ "$arch" = "x86_64" ] && arch="amd64" || arch="arm64"
      local ver
      ver=$(curl -fsSL https://api.github.com/repos/open-cluster-management-io/clusteradm/releases/latest \
        | grep '"tag_name"' | cut -d'"' -f4)
      ver="${ver:-v0.7.0}"
      curl -fsSL "https://github.com/open-cluster-management-io/clusteradm/releases/download/${ver}/clusteradm_linux_${arch}.tar.gz" \
        | $SUDO tar -xz -C /usr/local/bin clusteradm
    fi
    log_success "clusteradm installed."
  }

  _install_jq() {
    log_info "Installing jq..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq jq >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q jq >/dev/null
    else
      local arch; arch=$(uname -m); [ "$arch" = "x86_64" ] && arch="amd64" || arch="arm64"
      local ver
      ver=$(curl -fsSL https://api.github.com/repos/jqlang/jq/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
      ver="${ver:-jq-1.7.1}"
      curl -fsSL "https://github.com/jqlang/jq/releases/download/${ver}/jq-linux-${arch}" -o /tmp/jq-bin
      chmod +x /tmp/jq-bin
      $SUDO mv /tmp/jq-bin /usr/local/bin/jq
    fi
    log_success "jq installed."
  }

  _install_curl() {
    log_info "Installing curl..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq curl >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q curl >/dev/null
    else
      log_warn "Please install curl manually."; return 1
    fi
    log_success "curl installed."
  }

  _install_wireguard() {
    log_info "Installing WireGuard tools..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq wireguard wireguard-tools >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q wireguard-tools >/dev/null
    else
      log_warn "Please install wireguard-tools manually."; return 1
    fi
    log_success "WireGuard tools installed."
  }

  _install_git() {
    log_info "Installing git..."
    if [ "$PKG_MGR" = "apt" ]; then
      _apt_update_once
      $SUDO apt-get install -y -qq git >/dev/null
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      $SUDO "$PKG_MGR" install -y -q git >/dev/null
    else
      log_warn "Please install git manually."; return 1
    fi
    log_success "git installed."
  }

  # ── Check-and-install dispatcher ──────────────────────────────────────────
  local failed=()

  _check_and_install() {
    local tool="$1" installer="$2"
    if ! command -v "$tool" >/dev/null 2>&1; then
      log_warn "'$tool' not found – attempting automatic installation..."
      if $installer; then
        if command -v "$tool" >/dev/null 2>&1; then
          log_success "'$tool' is now available."
        else
          log_error "Installation of '$tool' completed but binary not found in PATH."
          failed+=("$tool")
        fi
      else
        log_error "Failed to install '$tool' automatically."
        failed+=("$tool")
      fi
    else
      log_info "'$tool' already installed: $(command -v "$tool")"
    fi
  }

  _check_and_install git         _install_git
  _check_and_install docker      _install_docker
  _check_and_install kind        _install_kind
  _check_and_install kubectl     _install_kubectl
  _check_and_install helm        _install_helm
  _check_and_install clusteradm  _install_clusteradm
  _check_and_install jq          _install_jq
  _check_and_install curl        _install_curl
  _check_and_install wg          _install_wireguard

  if [ ${#failed[@]} -gt 0 ]; then
    log_error "The following tools could not be installed automatically: ${failed[*]}"
    log_error "Please install them manually and re-run the script."
    exit 1
  fi

  log_success "All required tools are available."

  ensure_docker_access
  if ! docker info >/dev/null 2>&1; then
    log_error "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."
    log_error "Please run: sudo chmod 666 /var/run/docker.sock && sudo systemctl start docker"
    exit 1
  fi
  log_success "Docker daemon is running and accessible."

  ensure_sandbox_repo || log_warn "Repository clone pending; will retry in Phase 10."

  # Load WireGuard kernel module (non-fatal; may be built-in)
  local SUDO_WG=""
  [ "$EUID" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO_WG="sudo"
  $SUDO_WG modprobe wireguard 2>/dev/null || modprobe wireguard 2>/dev/null || true

  ensure_kernel_inotify_limits
}

phase_01_preflight() {
  run_phase "01" "Checking Host Toolchain (Auto-Install if Missing)" _check_phase_01 _do_phase_01_preflight
}
