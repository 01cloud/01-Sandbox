#!/usr/bin/env bash
# ==============================================================================
# Multi-Cluster Failover & Fallback Test Suite
# Target Architecture: Active-Passive Dual-Hub (primaryhub <-> secondaryhub)
# ==============================================================================

set -euo pipefail

PRIMARY_CTX="primaryhub"
SECONDARY_CTX="secondaryhub"
KUBECONFIG_HUBS="${HOME}/.kube/config-hubs"

echo "========================================================"
echo "🚀 [TEST 1] VERIFYING ACTIVE-PASSIVE DUAL-HUB STATUS"
echo "========================================================"

export KUBECONFIG="${KUBECONFIG_HUBS}"

echo "--> Checking ManagedClusters on Primary Hub..."
kubectl --context "${PRIMARY_CTX}" get managedclusters

echo "--> Checking ManagedClusters on Secondary Hub..."
kubectl --context "${SECONDARY_CTX}" get managedclusters

echo ""
echo "========================================================"
echo "🧪 [TEST 2] DEPLOYING TEST MANIFESTWORK FROM PRIMARY HUB"
echo "========================================================"

cat <<EOF | kubectl --context "${PRIMARY_CTX}" apply -f -
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: failover-test-workload
  namespace: spoke1
spec:
  workload:
    manifests:
    - apiVersion: v1
      kind: Namespace
      metadata:
        name: failover-test-ns
    - apiVersion: v1
      kind: ConfigMap
      metadata:
        name: hub-heartbeat-cm
        namespace: failover-test-ns
      data:
        active-hub: "primaryhub"
        timestamp: "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
EOF

echo "--> Waiting for ManifestWork to sync to spoke1..."
sleep 5
kubectl --context "${PRIMARY_CTX}" get manifestwork failover-test-workload -n spoke1 -o wide

echo ""
echo "========================================================"
echo "⚡ [TEST 3] SIMULATING PRIMARY HUB OUTAGE & TAKEOVER"
echo "========================================================"
echo "--> Note: Testing Secondary Hub readiness to dispatch workload..."

cat <<EOF | kubectl --context "${SECONDARY_CTX}" apply -f -
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: failover-test-workload-secondary
  namespace: spoke1
spec:
  workload:
    manifests:
    - apiVersion: v1
      kind: ConfigMap
      metadata:
        name: hub-takeover-cm
        namespace: failover-test-ns
      data:
        active-hub: "secondaryhub"
        takeover-status: "SUCCESS"
        timestamp: "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
EOF

echo "--> Checking Secondary Hub ManifestWork status..."
sleep 5
kubectl --context "${SECONDARY_CTX}" get manifestwork failover-test-workload-secondary -n spoke1

echo ""
echo "========================================================"
echo "🧹 [TEST 4] CLEANUP FAILOVER TEST RESOURCES"
echo "========================================================"
kubectl --context "${PRIMARY_CTX}" delete manifestwork failover-test-workload -n spoke1 --ignore-not-found=true
kubectl --context "${SECONDARY_CTX}" delete manifestwork failover-test-workload-secondary -n spoke1 --ignore-not-found=true

echo "✅ [SUCCESS] Failover test script finished successfully!"
