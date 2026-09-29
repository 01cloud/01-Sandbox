#!/usr/bin/env bash
# ==============================================================================
# lib/phase_01_preflight.sh – Host toolchain check and auto-install
#
# Checks for and (if missing) installs: git, docker, kind, kubectl, helm,
# clusteradm, jq, curl, wg. Also ensures the Docker daemon is running,
# the 01-Sandbox repository is reachable, and kernel inotify limits are raised.
# ==============================================================================

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
