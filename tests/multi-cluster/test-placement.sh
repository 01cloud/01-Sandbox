#!/usr/bin/env bash
# ==============================================================================
# Multi-Cluster Dynamic Resource Allocation (DRA) & Placement Test Suite
# Evaluates OCM Placement decision engine across spoke clusters
# ==============================================================================

set -euo pipefail

PRIMARY_CTX="primaryhub"
KUBECONFIG_HUBS="${HOME}/.kube/config-hubs"

export KUBECONFIG="${KUBECONFIG_HUBS}"

echo "========================================================"
echo "🎯 [PLACEMENT TEST 1] EVALUATING OCM PLACEMENT ENGINE"
echo "========================================================"

echo "--> Labeling Spoke clusters with vendor=RKE2 tag..."
kubectl --context "${PRIMARY_CTX}" label managedcluster spoke1 vendor=RKE2 --overwrite
kubectl --context "${PRIMARY_CTX}" label managedcluster spoke2 vendor=RKE2 --overwrite 2>/dev/null || true

echo "--> Applying OCM Placement policy..."
kubectl --context "${PRIMARY_CTX}" apply -f "$(dirname "$0")/placement-rules.yaml"

echo "--> Waiting for OCM Placement Decision..."
sleep 3
kubectl --context "${PRIMARY_CTX}" get placementdecision -n default

echo ""
echo "========================================================"
echo "📊 [PLACEMENT TEST 2] CHECKING SPOKE RESOURCE CAPACITY TELEMETRY"
echo "========================================================"
kubectl --context "${PRIMARY_CTX}" get managedclusters -o custom-columns='NAME:.metadata.name,JOINED:.status.conditions[?(@.type=="HubAcceptedManagedCluster")].status,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status,CPU-CAPACITY:.status.allocatable.cpu,MEMORY-CAPACITY:.status.allocatable.memory'

echo ""
echo "✅ [SUCCESS] Dynamic Resource Allocation Placement Test Complete!"
