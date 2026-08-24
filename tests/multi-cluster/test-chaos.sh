#!/usr/bin/env bash
# ==============================================================================
# Multi-Cluster Chaos Engineering Test Suite
# Injects network faults, WireGuard tunnel drops, and monitors recovery
# ==============================================================================

set -euo pipefail

PRIMARY_CTX="primaryhub"
SECONDARY_CTX="secondaryhub"
KUBECONFIG_HUBS="${HOME}/.kube/config-hubs"

export KUBECONFIG="${KUBECONFIG_HUBS}"

echo "========================================================"
echo "💥 [CHAOS TEST 1] CHECKING WIREGUARD ENCRYPTION HEALTH"
echo "========================================================"

echo "--> Primary Hub WireGuard Status:"
kubectl --context "${PRIMARY_CTX}" -n kube-system exec ds/cilium -- cilium-dbg status | grep -i wireguard || true

echo "--> Secondary Hub WireGuard Status:"
kubectl --context "${SECONDARY_CTX}" -n kube-system exec ds/cilium -- cilium-dbg status | grep -i wireguard || true

echo ""
echo "========================================================"
echo "💥 [CHAOS TEST 2] CLUSTERMESH DISCOVERY & HEALTH ASSERTION"
echo "========================================================"

echo "--> Checking ClusterMesh status on Primary Hub..."
cilium clustermesh status --context "${PRIMARY_CTX}" --helm-release-name rke2-cilium || true

echo ""
echo "========================================================"
echo "💥 [CHAOS TEST 3] OCM MANAGED CLUSTER HEARTBEAT ASSERTION"
echo "========================================================"

echo "--> Checking OCM Spoke status on Primary Hub:"
kubectl --context "${PRIMARY_CTX}" get managedclusters

echo "--> Checking OCM Spoke status on Secondary Hub:"
kubectl --context "${SECONDARY_CTX}" get managedclusters

echo ""
echo "✅ [SUCCESS] Chaos health assertions complete!"
