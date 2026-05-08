#!/usr/bin/env bash
# =============================================================================
# RKE2 Cluster Setup — Cilium CNI + Longhorn Storage
# =============================================================================
# Tested on: Ubuntu 20.04 / 22.04, RHEL/Rocky 8+, AMD ROCm DevCloud nodes,
#            NVIDIA Grace Blackwell GB10 (aarch64)
#
# USAGE
#   First server (init)  : sudo bash setup-rke2.sh server --init
#   Extra server (join)  : sudo bash setup-rke2.sh server --join <IP> <TOKEN>
#   Worker agent (join)  : sudo bash setup-rke2.sh agent  --join <IP> <TOKEN>
#
#   TOKEN may also be supplied via env var to avoid exposure in `ps aux`:
#     sudo RKE2_JOIN_TOKEN=<token> bash setup-rke2.sh agent --join <IP>
#
# FIXES (original codebase)
#   [1]  node-role.kubernetes.io/* labels are kubelet-protected since k8s 1.24
#   [2]  CNI: cni:cilium (RKE2 built-in) + HelmChartConfig; not cni:none
#   [3]  kubeProxyReplacement via HelmChartConfig, not Helm CLI
#   [4]  k8sServiceHost set to "localhost" — correct for single-node and HA
#   [5]  Stale /run/k3s/containerd socket cleaned before every start
#   [6]  cgroup version detected and logged with actionable guidance
#   [7]  wait_for_node_ready polls actual Ready condition (not just API up)
#   [8]  Tools installed after RKE2 so the bundled binary is available
#   [9]  Optional cleanup prompt on re-runs to clear stale state
#  [10]  Agent argument parsing made explicit (--join now required)
#
# FIXES (this revision)
#  [11]  write-kubeconfig-mode: "0600" — was 0644 (world-readable credential)
#  [12]  kubectl fallback now detects CPU arch (amd64/arm64/s390x/ppc64le)
#  [13]  TOKEN accepted via RKE2_JOIN_TOKEN env var (avoids ps aux exposure)
#  [14]  ((i++)) replaced with i=$(( i + 1 )) — safe under set -e
#  [15]  Helm pinned tarball install; k9s pinned GitHub release — no pipe|bash
#  [16]  StorageClass default-class patch is now idempotent
#  [17]  wait_for_node_ready: robust per-node status check via awk
#  [18]  /etc/environment no longer written (root-only path misleads non-root)
#  [19]  Firewall advisory printed when ufw/firewalld is active
#  [20]  Misleading step-fraction labels removed from function headers
# =============================================================================

set -euo pipefail

# ─── Pinned / Configurable Versions ──────────────────────────────────────────

RKE2_VERSION="${RKE2_VERSION:-v1.30.5+rke2r1}"
LONGHORN_VERSION="${LONGHORN_VERSION:-1.6.2}"
HELM_VERSION="${HELM_VERSION:-3.17.1}"         # FIX [15]: pinned
K9S_VERSION="${K9S_VERSION:-0.32.7}"           # FIX [15]: pinned
CLUSTER_CIDR="${CLUSTER_CIDR:-10.42.0.0/16}"
SERVICE_CIDR="${SERVICE_CIDR:-10.43.0.0/16}"
LONGHORN_REPLICAS="${LONGHORN_REPLICAS:-3}"
LONGHORN_DATA_PATH="${LONGHORN_DATA_PATH:-/var/lib/longhorn}"

# ─── Colours ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }     
step()    { echo -e "\n${CYAN}══════════════════════════════════════════════════${NC}"
            echo -e "${CYAN}  $*${NC}"
            echo -e "${CYAN}══════════════════════════════════════════════════${NC}"; }

# ─── Argument Parsing ─────────────────────────────────────────────────────────
#
# All three modes share a single consistent positional layout:
#
#   $1          $2        $3          $4
#   NODE_ROLE   MODE      SERVER_IP   TOKEN
#   ---------   ------    ---------   -----
#   server      --init    —           —
#   server      --join    <IP>        <TOKEN>
#   agent       --join    <IP>        <TOKEN>
#
# FIX [13]: TOKEN is also read from RKE2_JOIN_TOKEN env var so it never
# appears in `ps aux`. Env var takes precedence over the positional arg.
# ─────────────────────────────────────────────────────────────────────────────

NODE_ROLE="${1:-}"    # server | agent
MODE="${2:-}"         # --init | --join
SERVER_IP="${3:-}"    # join target IP
# FIX [13]: env var wins; positional arg is the fallback
TOKEN="${RKE2_JOIN_TOKEN:-${4:-}}"

print_usage() {
  echo -e "
${CYAN}Usage:${NC}
  Init first server  : sudo $0 server --init
  Join extra server  : sudo $0 server --join <SERVER_IP> <TOKEN>
  Join worker agent  : sudo $0 agent  --join <SERVER_IP> <TOKEN>

${CYAN}TOKEN via env var (recommended — avoids ps aux exposure):${NC}
  sudo RKE2_JOIN_TOKEN=<token> $0 agent --join <SERVER_IP>
"
  exit 1
}

[[ -z "$NODE_ROLE" ]] && print_usage
[[ "$NODE_ROLE" != "server" && "$NODE_ROLE" != "agent" ]] && print_usage
[[ $EUID -ne 0 ]] && error "This script must be run as root (sudo)."

# ─── Global env ───────────────────────────────────────────────────────────────

export KUBECONFIG="/etc/rancher/rke2/rke2.yaml"
export PATH="/var/lib/rancher/rke2/bin:${PATH}"

# ─── Helpers ──────────────────────────────────────────────────────────────────

# FIX [12]: central arch detection used by kubectl, Helm, and k9s installers
detect_arch() {
  case "$(uname -m)" in
    x86_64)  echo "amd64"   ;;
    aarch64) echo "arm64"   ;;
    armv7l)  echo "arm"     ;;
    s390x)   echo "s390x"   ;;
    ppc64le) echo "ppc64le" ;;
    *)       uname -m       ;;
  esac
}

# FIX [14]: use arithmetic expansion instead of ((i++)) which exits-1 under
# set -e when the expression evaluates to zero (i.e. on the first increment).
wait_for() {
  local cmd="$1" desc="${2:-command}" retries="${3:-40}" delay="${4:-10}"
  local i=0
  until eval "$cmd" &>/dev/null; do
    i=$(( i + 1 ))
    [[ $i -ge $retries ]] && error "Timed out waiting for: $desc"
    info "Waiting for $desc … ($i/$retries)"
    sleep "$delay"
  done
  success "$desc is ready."
}

# FIX [17]: robust per-node status check — checks every node's STATUS column
# via awk rather than a regex that could accidentally match "NotReady".
wait_for_node_ready() {
  local retries=60 i=0 statuses
  info "Polling node Ready condition…"
  while true; do
    statuses=$(kubectl get nodes --no-headers 2>/dev/null | awk '{print $2}' || true)
    if [[ -n "$statuses" ]] \
        && ! echo "$statuses" | grep -q 'NotReady' \
        && echo "$statuses" | grep -q 'Ready'; then
      break
    fi
    i=$(( i + 1 ))
    [[ $i -ge $retries ]] && error "Node never became Ready. Run: journalctl -u rke2-server -f"
    info "Node not Ready yet ($i/$retries) — waiting 10 s…"
    sleep 10
  done
  success "Node is Ready."
}

detect_cgroup_version() {
  local fs
  fs=$(stat -fc %T /sys/fs/cgroup/ 2>/dev/null || echo "unknown")
  [[ "$fs" == "cgroup2fs" ]] && echo "v2" || echo "v1"
}

# FIX [19]: warn when a host firewall is active and list the ports RKE2 needs
check_firewall() {
  local active=false
  if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    warn "ufw is active on this host."
    active=true
  elif systemctl is-active --quiet firewalld 2>/dev/null; then
    warn "firewalld is active on this host."
    active=true
  fi
  if $active; then
    warn "Ensure the following ports are open before joining nodes:"
    cat >&2 <<'PORTS'
    TCP 6443        Kubernetes API server
    TCP 9345        RKE2 supervisor API (node join)
    UDP 8472        VXLAN overlay (Cilium)
    TCP 10250       kubelet API
    TCP 2379-2380   etcd peer communication (server nodes only)
    TCP 4240        Cilium health check
    TCP 4244        Hubble relay
PORTS
  fi
}

# ─── System Prerequisites ─────────────────────────────────────────────────────

install_prerequisites() {
  step "System prerequisites"

  if command -v apt-get &>/dev/null; then
    info "apt detected (Debian/Ubuntu)"
    apt-get update -qq
    apt-get install -y -qq \
      curl wget tar jq bash-completion \
      open-iscsi nfs-common \
      cryptsetup dmsetup lvm2 2>/dev/null || true

  elif command -v dnf &>/dev/null; then
    info "dnf detected (RHEL/Rocky/Fedora)"
    dnf install -y -q \
      curl wget tar jq bash-completion \
      iscsi-initiator-utils nfs-utils \
      cryptsetup device-mapper lvm2

  elif command -v yum &>/dev/null; then
    info "yum detected (CentOS)"
    yum install -y -q \
      curl wget tar jq bash-completion \
      iscsi-initiator-utils nfs-utils \
      cryptsetup device-mapper lvm2
  else
    warn "No recognised package manager — ensure curl, open-iscsi, nfs-utils are installed."
  fi

  # iSCSI — Longhorn requires the initiator daemon
  systemctl enable --now iscsid 2>/dev/null || true

  # Kernel modules required by Cilium and Longhorn
  for mod in overlay br_netfilter iscsi_tcp; do
    modprobe "$mod" 2>/dev/null || warn "modprobe $mod skipped (may be built-in)"
  done
  cat > /etc/modules-load.d/rke2.conf <<'EOF'
overlay
br_netfilter
iscsi_tcp
EOF

  # Sysctl
  cat > /etc/sysctl.d/99-rke2.conf <<'EOF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
net.ipv6.conf.all.forwarding        = 1
fs.inotify.max_user_watches         = 524288
fs.inotify.max_user_instances       = 512
EOF
  sysctl --system -q

  # Disable swap — kubelet refuses to start otherwise
  swapoff -a
  sed -i '/\bswap\b/d' /etc/fstab 2>/dev/null || true

  # FIX [5]: clean stale containerd socket from any prior failed attempt
  rm -f /run/k3s/containerd/containerd.sock 2>/dev/null || true

  # FIX [6]: detect cgroup version and give actionable guidance
  local cgv
  cgv=$(detect_cgroup_version)
  info "cgroup version: ${cgv}"
  if [[ "$cgv" == "v1" ]]; then
    info "  cgroup v1 detected — RKE2 and Cilium support this; no extra config needed."
    info "  To migrate to v2 (recommended on RHEL 9 / Ubuntu 22+): set systemd.unified_cgroup_hierarchy=1 in grub."
  else
    info "  cgroup v2 detected — optimal for RKE2 1.25+ and Cilium eBPF."
  fi

  # FIX [19]: warn if host firewall may block cluster traffic
  check_firewall

  success "Prerequisites done."
}

# ─── kubeconfig setup ─────────────────────────────────────────────────────────

setup_kubeconfig() {
  local src="/etc/rancher/rke2/rke2.yaml"
  local dest="$HOME/.kube/config"

  if [[ ! -f "$src" ]]; then
    warn "RKE2 kubeconfig not found at ${src} yet — skipping copy."
    return
  fi

  mkdir -p "$HOME/.kube"
  cp "$src" "$dest"
  chmod 600 "$dest"

  # Export for the current shell session
  export KUBECONFIG="$dest"

  # Persist for all future root logins
  # FIX [18]: /etc/environment intentionally NOT written — it would point to
  # /root/.kube/config which is inaccessible to non-root users and is confusing.
  # Non-root operators should copy the kubeconfig manually per-user.
  for rc in /root/.bashrc /root/.bash_profile /root/.profile; do
    if [[ -f "$rc" ]] || [[ "$rc" == "/root/.bashrc" ]]; then
      grep -q 'KUBECONFIG=.*\.kube/config' "$rc" 2>/dev/null && continue
      cat >> "$rc" <<RCBLOCK

# ── kubectl / RKE2 kubeconfig ───────────────────────────────────────────────
export KUBECONFIG=\$HOME/.kube/config
export PATH=/var/lib/rancher/rke2/bin:\$PATH
RCBLOCK
    fi
  done

  success "Kubeconfig: ${src} → ${dest}"
  success "KUBECONFIG exported for this session and all future root shells."
  info    "Non-root users: copy /etc/rancher/rke2/rke2.yaml to ~/.kube/config and chmod 600."
  info    "Run: kubectl get nodes"
}

# ─── kubectl + k9s ────────────────────────────────────────────────────────────

install_tools() {
  step "kubectl + k9s + kubeconfig"

  # ── kubectl ────────────────────────────────────────────────────────────────
  if [[ -f /var/lib/rancher/rke2/bin/kubectl ]]; then
    ln -sf /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/kubectl
    success "kubectl → RKE2 bundle ($(kubectl version --client --short 2>/dev/null | head -1))"
  else
    # FIX [12]: detect arch — was hardcoded to amd64 (breaks arm64/GB10 nodes)
    local arch ver
    arch=$(detect_arch)
    info "Fetching kubectl from upstream (arch: ${arch})…"
    ver=$(curl -fsSL https://dl.k8s.io/release/stable.txt)
    curl -fsSL "https://dl.k8s.io/release/${ver}/bin/linux/${arch}/kubectl" \
      -o /usr/local/bin/kubectl
    chmod +x /usr/local/bin/kubectl
    success "kubectl ${ver} (${arch}) installed."
  fi

  # ── kubeconfig → ~/.kube/config ────────────────────────────────────────────
  setup_kubeconfig

  # ── Shell aliases + completion ─────────────────────────────────────────────
  kubectl completion bash > /etc/bash_completion.d/kubectl 2>/dev/null || true
  grep -q 'alias k=' /root/.bashrc 2>/dev/null || cat >> /root/.bashrc <<'BASHRC'

# ── RKE2 / kubectl ───────────────────────────────────────────────────────────
export KUBECONFIG=$HOME/.kube/config
export PATH=/var/lib/rancher/rke2/bin:$PATH
alias k='kubectl'
alias kgp='kubectl get pods -A'
alias kgn='kubectl get nodes -o wide'
alias kgs='kubectl get svc -A'
alias kd='kubectl describe'
complete -o default -F __start_kubectl k
BASHRC

  # ── k9s — FIX [15]: pinned GitHub release; no pipe-to-shell ───────────────
  if command -v k9s &>/dev/null; then
    success "k9s already installed: $(k9s version --short 2>/dev/null | head -1)"
  else
    local arch
    arch=$(detect_arch)
    info "Installing k9s v${K9S_VERSION} (${arch}) from GitHub releases…"
    local tmp
    tmp=$(mktemp -d)
    local url="https://github.com/derailed/k9s/releases/download/v${K9S_VERSION}/k9s_Linux_${arch}.tar.gz"
    if curl -fsSL "$url" -o "${tmp}/k9s.tar.gz" 2>/dev/null \
        && tar -xzf "${tmp}/k9s.tar.gz" -C "${tmp}" k9s 2>/dev/null; then
      mv "${tmp}/k9s" /usr/local/bin/k9s
      chmod +x /usr/local/bin/k9s
      success "k9s v${K9S_VERSION} installed."
    else
      warn "k9s download failed — install manually:"
      warn "  https://github.com/derailed/k9s/releases/tag/v${K9S_VERSION}"
    fi
    rm -rf "${tmp}"
  fi
}

# ─── Helm ─────────────────────────────────────────────────────────────────────

install_helm() {
  step "Helm v${HELM_VERSION}"
  if command -v helm &>/dev/null; then
    success "Helm already present: $(helm version --short)"
    return
  fi

  # FIX [15]: download pinned tarball — was: curl | bash from unpinned main branch
  local arch
  arch=$(detect_arch)
  local tmp
  tmp=$(mktemp -d)
  info "Downloading Helm v${HELM_VERSION} (${arch})…"
  curl -fsSL \
    "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${arch}.tar.gz" \
    -o "${tmp}/helm.tar.gz"
  tar -xzf "${tmp}/helm.tar.gz" -C "${tmp}"
  mv "${tmp}/linux-${arch}/helm" /usr/local/bin/helm
  chmod +x /usr/local/bin/helm
  rm -rf "${tmp}"
  success "Helm v${HELM_VERSION} (${arch}) installed."
}

# ─── RKE2 binaries ────────────────────────────────────────────────────────────

install_rke2() {
  local role="$1"
  step "RKE2 ${role} — ${RKE2_VERSION}"
  INSTALL_RKE2_VERSION="$RKE2_VERSION" \
  INSTALL_RKE2_TYPE="$role" \
    sh <(curl -sfL https://get.rke2.io)
  success "RKE2 ${role} binaries installed."
}

# ─── Config — first server ────────────────────────────────────────────────────

write_config_server_init() {
  step "Writing configs (server init)"

  local node_ip
  node_ip=$(hostname -I | awk '{print $1}')

  mkdir -p /etc/rancher/rke2
  mkdir -p /var/lib/rancher/rke2/server/manifests

  # FIX [11]: 0600 — kubeconfig holds cluster-admin credentials; 0644 is world-readable
  cat > /etc/rancher/rke2/config.yaml <<EOF
# ── RKE2 Server — cluster init ───────────────────────────────────────────────
cluster-init: true
write-kubeconfig-mode: "0600"

cni: cilium
disable-kube-proxy: true

cluster-cidr: "${CLUSTER_CIDR}"
service-cidr: "${SERVICE_CIDR}"

tls-san:
  - "${node_ip}"
  - "127.0.0.1"
  - "localhost"

disable:
  - rke2-canal
  - rke2-ingress-nginx

kubelet-arg:
  - "max-pods=250"
  - "serialize-image-pulls=false"
EOF

  cat > /var/lib/rancher/rke2/server/manifests/rke2-cilium-config.yaml <<'EOF'
---
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: rke2-cilium
  namespace: kube-system
spec:
  valuesContent: |-
    kubeProxyReplacement: true
    k8sServiceHost: "localhost"
    k8sServicePort: "6443"
    ipam:
      mode: kubernetes
    tunnelProtocol: "vxlan"
    bpf:
      masquerade: true
    hubble:
      enabled: true
      relay:
        enabled: true
      ui:
        enabled: true
    operator:
      replicas: 1
    nodeinit:
      enabled: true
EOF

  success "RKE2 config and Cilium HelmChartConfig written."
}

# ─── Config — joining server ──────────────────────────────────────────────────

write_config_server_join() {
  local server_ip="$1" token="$2"
  step "Writing configs (server join → ${server_ip})"

  local node_ip
  node_ip=$(hostname -I | awk '{print $1}')

  mkdir -p /etc/rancher/rke2

  # FIX [11]: 0600 — kubeconfig holds cluster-admin credentials
  cat > /etc/rancher/rke2/config.yaml <<EOF
# ── RKE2 Server — join existing cluster ──────────────────────────────────────
server: "https://${server_ip}:9345"
token: "${token}"
write-kubeconfig-mode: "0600"

cni: cilium
disable-kube-proxy: true
cluster-cidr: "${CLUSTER_CIDR}"
service-cidr: "${SERVICE_CIDR}"

tls-san:
  - "${node_ip}"
  - "${server_ip}"
  - "127.0.0.1"
  - "localhost"

disable:
  - rke2-canal
  - rke2-ingress-nginx

kubelet-arg:
  - "max-pods=250"
  - "serialize-image-pulls=false"
EOF

  success "Server join config written."
}

# ─── Config — agent ───────────────────────────────────────────────────────────

write_config_agent() {
  local server_ip="$1" token="$2"
  step "Writing configs (agent → ${server_ip})"

  mkdir -p /etc/rancher/rke2

  cat > /etc/rancher/rke2/config.yaml <<EOF
# ── RKE2 Agent (worker node) ─────────────────────────────────────────────────
server: "https://${server_ip}:9345"
token: "${token}"

# FIX [1]: node-role.kubernetes.io/worker is a protected label — do NOT use it.
node-label:
  - "workload-type=gpu-worker"
  - "longhorn.io/storage-node=true"

kubelet-arg:
  - "max-pods=250"
  - "serialize-image-pulls=false"
EOF

  success "Agent config written."
}

# ─── Start RKE2 service ───────────────────────────────────────────────────────

start_rke2() {
  local role="$1"
  step "Starting rke2-${role}.service"

  # FIX [5]: remove stale containerd socket
  rm -f /run/k3s/containerd/containerd.sock 2>/dev/null || true

  systemctl daemon-reload
  systemctl enable "rke2-${role}.service"
  systemctl restart "rke2-${role}.service"

  success "rke2-${role}.service started."

  if [[ "$role" == "server" ]]; then
    info "Waiting 20 s for API server to initialise…"
    sleep 20

    ln -sf /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/kubectl 2>/dev/null || true
    export KUBECONFIG="/etc/rancher/rke2/rke2.yaml"

    wait_for "kubectl cluster-info" "Kubernetes API server" 40 10
    wait_for_node_ready
    setup_kubeconfig
  fi
}

# ─── Longhorn ─────────────────────────────────────────────────────────────────

install_longhorn() {
  step "Longhorn v${LONGHORN_VERSION}"

  export KUBECONFIG="$HOME/.kube/config"

  if ! command -v helm &>/dev/null; then
    error "helm not found — run install_helm() first."
  fi

  if ! kubectl cluster-info &>/dev/null; then
    error "Cannot reach API server. Check: export KUBECONFIG=/etc/rancher/rke2/rke2.yaml"
  fi

  local node_count replicas
  node_count=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
  replicas="${LONGHORN_REPLICAS}"
  if [[ "$node_count" -lt "$replicas" ]]; then
    warn "Only ${node_count} node(s) found — reducing Longhorn replicas to ${node_count} (from ${replicas})"
    replicas="$node_count"
  fi

  mkdir -p "${LONGHORN_DATA_PATH}"

  helm repo add longhorn https://charts.longhorn.io --force-update
  helm repo update longhorn

  kubectl create namespace longhorn-system 2>/dev/null || true

  info "Installing Longhorn ${LONGHORN_VERSION} with ${replicas} replica(s)…"

  helm upgrade --install longhorn longhorn/longhorn \
    --namespace longhorn-system \
    --version "${LONGHORN_VERSION}" \
    --set defaultSettings.defaultReplicaCount="${replicas}" \
    --set defaultSettings.defaultDataPath="${LONGHORN_DATA_PATH}" \
    --set defaultSettings.storageOverProvisioningPercentage=200 \
    --set defaultSettings.storageMinimalAvailablePercentage=10 \
    --set defaultSettings.replicaSoftAntiAffinity=true \
    --set defaultSettings.replicaAutoBalance=best-effort \
    --set defaultSettings.snapshotDataIntegrity=fast-check \
    --set defaultSettings.autoSalvage=true \
    --set defaultSettings.autoDeletePodWhenVolumeDetachedUnexpectedly=true \
    --set persistence.defaultClassReplicaCount="${replicas}" \
    --set persistence.defaultFsType=ext4 \
    --set ingress.enabled=false \
    --set longhornUI.replicas=1 \
    --wait --timeout 10m

  if ! helm status longhorn -n longhorn-system &>/dev/null; then
    error "Longhorn helm release not found after install — check: helm list -A"
  fi

  # FIX [16]: idempotent patch — skip if already the default StorageClass
  local current_default
  current_default=$(kubectl get storageclass longhorn \
    -o jsonpath='{.metadata.annotations.storageclass\.kubernetes\.io/is-default-class}' \
    2>/dev/null || echo "false")
  if [[ "$current_default" != "true" ]]; then
    kubectl patch storageclass longhorn \
      -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
    success "Longhorn StorageClass set as cluster default."
  else
    info "Longhorn StorageClass is already the cluster default — skipping patch."
  fi

  success "Longhorn ${LONGHORN_VERSION} installed (${replicas} replica(s))."

  info "Longhorn pods:"
  kubectl get pods -n longhorn-system --no-headers | awk '{print "  "$1, $3}'
}

# ─── Cleanup ──────────────────────────────────────────────────────────────────

cleanup_previous() {
  warn "Removing previous RKE2 installation…"
  systemctl stop rke2-server rke2-agent 2>/dev/null || true
  /usr/local/bin/rke2-uninstall.sh 2>/dev/null || true
  rm -rf /var/lib/rancher/rke2 /run/k3s /etc/rancher/rke2 /var/lib/rke2
  systemctl daemon-reload
  success "Cleanup done."
}

# ─── Summary ──────────────────────────────────────────────────────────────────

print_join_info() {
  export KUBECONFIG="$HOME/.kube/config"
  local token ip
  token=$(cat /var/lib/rancher/rke2/server/node-token 2>/dev/null || echo "<not-yet-available>")
  ip=$(hostname -I | awk '{print $1}')

  echo ""
  echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║         Cluster bootstrap complete!  🎉                  ║${NC}"
  echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "${CYAN}► Tools installed:${NC}"
  echo -e "  kubectl : $(kubectl version --client 2>/dev/null | grep 'Client Version' | awk '{print $3}' || echo 'check /usr/local/bin/kubectl')"
  echo -e "  k9s     : $(k9s version --short 2>/dev/null | head -1 || echo 'check /usr/local/bin/k9s')"
  echo -e "  helm    : $(helm version --short 2>/dev/null || echo 'check /usr/local/bin/helm')"
  echo ""
  echo -e "${CYAN}► Join an extra control-plane node:${NC}"
  echo -e "  sudo RKE2_JOIN_TOKEN=${token} $0 server --join ${ip}"
  echo ""
  echo -e "${CYAN}► Join a worker node:${NC}"
  echo -e "  sudo RKE2_JOIN_TOKEN=${token} $0 agent --join ${ip}"
  echo ""
  echo -e "${CYAN}► Kubeconfig:${NC}"
  echo -e "  Root on this node : ~/.kube/config  (KUBECONFIG already exported)"
  echo -e "  Non-root users    : copy /etc/rancher/rke2/rke2.yaml → ~/.kube/config && chmod 600"
  echo -e "${CYAN}► Copy to your laptop:${NC}"
  echo -e "  scp root@${ip}:~/.kube/config ~/.kube/config"
  echo ""
  echo -e "${CYAN}► Nodes:${NC}"
  kubectl get nodes -o wide 2>/dev/null || true
  echo ""
  echo -e "${CYAN}► Cilium pods:${NC}"
  kubectl get pods -n kube-system -l k8s-app=cilium --no-headers 2>/dev/null | \
    awk '{print $1, $3}' | column -t || true
  echo ""
  echo -e "${CYAN}► Longhorn pods:${NC}"
  kubectl get pods -n longhorn-system --no-headers 2>/dev/null | \
    awk '{print $1, $3}' | column -t || true
}

# ─── Banner ───────────────────────────────────────────────────────────────────

print_banner() {
  echo -e "\n${CYAN}"
  echo "  ██████╗ ██╗  ██╗███████╗██████╗ "
  echo "  ██╔══██╗██║ ██╔╝██╔════╝╚════██╗"
  echo "  ██████╔╝█████╔╝ █████╗   █████╔╝"
  echo "  ██╔══██╗██╔═██╗ ██╔══╝  ██╔═══╝ "
  echo "  ██║  ██║██║  ██╗███████╗███████╗"
  echo "  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝"
  echo -e "    + Cilium CNI  +  Longhorn Storage${NC}\n"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

main() {
  print_banner

  case "$NODE_ROLE" in

    server)
      case "$MODE" in

        --init)
          if [[ -d /var/lib/rancher/rke2 ]]; then
            warn "Previous RKE2 data found at /var/lib/rancher/rke2"
            read -rp "  Clean it up and reinstall? [y/N] " ans
            [[ "${ans,,}" == "y" ]] && cleanup_previous
          fi

          info "Step [1/6] Prerequisites"
          install_prerequisites     || error "FAILED: install_prerequisites"

          info "Step [2/6] Install RKE2 binaries"
          install_rke2 server       || error "FAILED: install_rke2"

          info "Step [3/6] Write configs (RKE2 + Cilium HelmChartConfig)"
          write_config_server_init  || error "FAILED: write_config_server_init"

          info "Step [4/6] Start RKE2 + wait for node Ready"
          start_rke2 server         || error "FAILED: start_rke2"

          info "Step [5/6] Install tools (kubectl, k9s, helm)"
          install_tools             || warn  "WARNING: install_tools had errors (non-fatal)"
          install_helm              || error "FAILED: install_helm"

          info "Step [6/6] Install Longhorn"
          install_longhorn          || error "FAILED: install_longhorn"

          print_join_info
          ;;

        --join)
          [[ -z "$SERVER_IP" ]] && print_usage
          [[ -z "$TOKEN" ]]     && error "TOKEN required: pass as \$4 or set RKE2_JOIN_TOKEN env var."

          install_prerequisites
          install_rke2 server
          write_config_server_join "$SERVER_IP" "$TOKEN"
          start_rke2 server
          install_tools
          success "Server node joined the cluster."
          info    "Verify on the init node: kubectl get nodes -o wide"
          ;;

        *)
          print_usage
          ;;
      esac
      ;;

    agent)
      case "$MODE" in
        --join)
          [[ -z "$SERVER_IP" ]] && print_usage
          [[ -z "$TOKEN" ]]     && error "TOKEN required: pass as \$4 or set RKE2_JOIN_TOKEN env var."

          install_prerequisites
          install_rke2 agent
          write_config_agent "$SERVER_IP" "$TOKEN"
          start_rke2 agent
          success "Agent node joined the cluster."
          info    "Verify on the init node: kubectl get nodes -o wide"
          ;;
        *)
          print_usage
          ;;
      esac
      ;;

    *)
      print_usage
      ;;
  esac
}

main "$@"
