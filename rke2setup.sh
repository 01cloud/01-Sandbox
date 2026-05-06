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
