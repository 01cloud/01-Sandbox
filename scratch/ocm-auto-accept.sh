#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG=/etc/kubernetes/admin.conf
[ -f "$KUBECONFIG" ] && command -v clusteradm >/dev/null 2>&1 || exit 0

ROLE="${role:-hub}"
HUB_PRIORITY="${hub_priority:-1}"

# 1. Auto-accept pending managed clusters
for mc in $(kubectl get managedclusters -o jsonpath='{range .items[?(@.status.conditions[?(@.type=="HubAcceptedManagedCluster")].status!="True")]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true); do
  [ -n "$mc" ] && clusteradm accept --clusters "$mc" 2>/dev/null || true
done

# 2. Auto-approve pending CSRs
for csr in $(kubectl get csr --no-headers 2>/dev/null | awk '$5=="Pending" {print $1}'); do
  [ -n "$csr" ] && kubectl certificate approve "$csr" 2>/dev/null || true
done

# 3. Autonomous Spoke Deregistration / Garbage Collection (Reaper on ALL Hubs)
REGIONS="us-east-1 eu-west-1 eu-central-1 ap-south-1"
for mc in $(kubectl get managedclusters -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true); do
  [ -z "$mc" ] && continue
  IS_RUNNING=false
  for r in $REGIONS; do
    inst_state=$(aws ec2 describe-instances --region "$r" --filters "Name=tag:ClusterName,Values=$mc" "Name=instance-state-name,Values=running" --query "Reservations[*].Instances[*].InstanceId" --output text 2>/dev/null || true)
    if [ -n "$inst_state" ]; then
      IS_RUNNING=true
      break
    fi
  done

  if [ "$IS_RUNNING" = false ]; then
    echo "Spoke cluster $mc is no longer running in EC2. Initiating autonomous deregistration on $(hostname)..."
    kubectl patch managedcluster "$mc" -p '{"metadata":{"finalizers":[]}}' --type=merge 2>/dev/null || true
    kubectl delete managedcluster "$mc" --ignore-not-found=true 2>/dev/null || true
    kubectl delete ns "$mc" --ignore-not-found=true 2>/dev/null || true
    kubectl delete clusterrole "open-cluster-management:managedcluster:$mc" --ignore-not-found=true 2>/dev/null || true
    kubectl delete clusterrolebinding "open-cluster-management:managedcluster:$mc" --ignore-not-found=true 2>/dev/null || true
    kubectl get csr -o name 2>/dev/null | grep "$mc" | xargs -r kubectl delete 2>/dev/null || true

    if [ -f /etc/wireguard/wg0.conf ]; then
      configured_pubkey=$(grep -B 2 -A 5 "# $mc" /etc/wireguard/wg0.conf 2>/dev/null | awk '/PublicKey/ {print $3}' | head -n 1)
      if [ -n "$configured_pubkey" ]; then
        ip link show wg0 >/dev/null 2>&1 && wg set wg0 peer "$configured_pubkey" remove 2>/dev/null || true
        sed -i "/# $mc/,+6d" /etc/wireguard/wg0.conf 2>/dev/null || true
      fi
    fi

    if [ "$HUB_PRIORITY" = "1" ]; then
      aws ssm delete-parameter --name "/sandbox/clusters/$mc" --region "us-east-1" 2>/dev/null || true
      aws ssm delete-parameter --name "/sandbox/wireguard/$mc/public-key" --region "us-east-1" 2>/dev/null || true
    fi
  fi
done
