#!/usr/bin/env bash
# =====================================================================
# Verification Script: CloudNativePG & Open Cluster Management Metrics
# =====================================================================
# Tests metric accessibility directly without requiring any UI.
# Usage:
#   ./verify-metrics.sh [KUBE_CONTEXT]
# Example:
#   ./verify-metrics.sh kind-primaryhub
#   ./verify-metrics.sh kind-secondaryhub
# =====================================================================

set -eo pipefail

CTX="${1:-$(kubectl config current-context 2>/dev/null || echo 'kind-primaryhub')}"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}${BLUE}=== Verifying Prometheus Metrics on Context: ${CTX} ===${NC}\n"

# Helper for testing pod curl
test_pod_metrics() {
  local namespace="$1"
  local label="$2"
  local port="$3"
  local path="${4:-/metrics}"
  local name="$5"

  echo -e "${BOLD}[Testing ${name}]${NC}"
  local pod
  pod=$(kubectl --context "$CTX" -n "$namespace" get pods -l "$label" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [ -z "$pod" ]; then
    echo -e "  ${YELLOW}⚠ Pod with label '${label}' not found in namespace '${namespace}'. Skipping.${NC}"
    return 0
  fi

  echo -e "  Discovered Pod: ${GREEN}${pod}${NC} in ${namespace}"
  local sample
  sample=$(kubectl --context "$CTX" -n "$namespace" exec "$pod" -- curl -s --max-time 5 "http://127.0.0.1:${port}${path}" 2>/dev/null || true)
  if [ -z "$sample" ]; then
    # Fallback via ephemeral port-forward if pod does not have curl
    echo -e "  ${YELLOW}exec curl failed (container might lack curl). Testing via port-forward...${NC}"
    local local_port=$((19000 + RANDOM % 1000))
    kubectl --context "$CTX" -n "$namespace" port-forward "pod/${pod}" "${local_port}:${port}" >/dev/null 2>&1 &
    local pf_pid=$!
    sleep 2
    sample=$(curl -s --max-time 5 "http://127.0.0.1:${local_port}${path}" 2>/dev/null || true)
    kill -9 "$pf_pid" 2>/dev/null || true
  fi

  if [ -n "$sample" ]; then
    local metric_count
    metric_count=$(echo "$sample" | grep -v '^#' | grep -c '=' || echo "$sample" | grep -v '^#' | wc -l)
    echo -e "  ${GREEN}✓ SUCCESS:${NC} Retrieved ${metric_count} metrics lines from ${pod}:${port}${path}"
    echo -e "  ${BOLD}Sample Metrics:${NC}"
    echo "$sample" | grep -v '^#' | head -n 5 | sed 's/^/    /'
  else
    echo -e "  ${RED}✗ FAILED:${NC} Unable to scrape metrics from ${pod}:${port}${path}"
  fi
  echo ""
}

# 1. CloudNativePG Database Instance Metrics (Port 9187)
test_pod_metrics "opensandbox-system" "cnpg.io/cluster" "9187" "/metrics" "CloudNativePG Database Instance"

# 2. CloudNativePG Operator Manager Metrics (Port 8080)
test_pod_metrics "opensandbox-system" "app.kubernetes.io/name=cloudnative-pg" "8080" "/metrics" "CloudNativePG Operator Manager"

# 3. OCM Hub Registration Controller (Port 8080)
test_pod_metrics "open-cluster-management-hub" "app=cluster-manager-registration-controller" "8080" "/metrics" "OCM Hub Registration Controller"

# 4. OCM Hub Work Controller (Port 8080)
test_pod_metrics "open-cluster-management-hub" "app=cluster-manager-work-controller" "8080" "/metrics" "OCM Hub Work Controller"

# 5. OCM Spoke Klusterlet Agent (Port 8080)
test_pod_metrics "open-cluster-management-agent" "app=klusterlet-registration-agent" "8080" "/metrics" "OCM Klusterlet Registration Agent"

# 6. Prometheus Scraper Target Status (Port 9090)
echo -e "${BOLD}[Testing Prometheus Scraper Target Status]${NC}"
prom_pod=$(kubectl --context "$CTX" -n monitoring get pods -l "app in (prometheus, prometheus-scraper)" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$prom_pod" ]; then
  local local_prom_port=$((29000 + RANDOM % 1000))
  kubectl --context "$CTX" -n monitoring port-forward "pod/${prom_pod}" "${local_prom_port}:9090" >/dev/null 2>&1 &
  pf_prom_pid=$!
  sleep 2
  active_targets=$(curl -s "http://127.0.0.1:${local_prom_port}/api/v1/targets" 2>/dev/null || true)
  kill -9 "$pf_prom_pid" 2>/dev/null || true

  if [ -n "$active_targets" ]; then
    echo -e "  ${GREEN}✓ Prometheus scraper is running!${NC}"
    echo "  Scrape target summary:"
    echo "$active_targets" | grep -o '"health":"[^"]*"' | sort | uniq -c | sed 's/^/    /' || true
  else
    echo -e "  ${YELLOW}⚠ Could not fetch targets from Prometheus pod directly.${NC}"
  fi
else
  echo -e "  ${YELLOW}⚠ prometheus-scraper pod not found in 'monitoring' namespace.${NC}"
fi

echo -e "\n${BOLD}${GREEN}=== Verification Complete ===${NC}"
