import sys

regions = ["us-east-1", "eu-west-1", "eu-central-1", "ap-south-1"]
base_dir = "/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox"

monitoring_function = """# -----------------------------------------------------------------------------
# Autonomous Spoke Prometheus Remote Write Deployment
# -----------------------------------------------------------------------------
deploy_spoke_monitoring() {
  echo "=== Deploying Autonomous Spoke Prometheus Scraper (Push to PrimaryHub) ==="
  export KUBECONFIG=/etc/kubernetes/admin.conf
  kubectl apply -f - <<'EOF_MON_BASE'
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus-scraper
  namespace: monitoring
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: prometheus-scraper-role
rules:
  - apiGroups: [""]
    resources:
      - nodes
      - nodes/metrics
      - services
      - endpoints
      - pods
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources:
      - daemonsets
      - statefulsets
      - deployments
    verbs: ["get", "list", "watch"]
  - nonResourceURLs: ["/metrics"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: prometheus-scraper-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: prometheus-scraper-role
subjects:
  - kind: ServiceAccount
    name: prometheus-scraper
    namespace: monitoring
EOF_MON_BASE

  cat <<EOF_CM | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-scraper-config
  namespace: monitoring
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
      evaluation_interval: 15s
      scrape_timeout: 10s
      external_labels:
        cluster: "${cluster_name}"
        role: "spoke"

    remote_write:
      - url: "http://10.99.0.1:30900/api/v1/write"
        queue_config:
          capacity: 10000
          max_shards: 10
          min_shards: 1
          max_samples_per_send: 1000
          batch_send_deadline: 5s
          min_backoff: 500ms
          max_backoff: 10s

    scrape_configs:
      - job_name: 'prometheus-scraper'
        static_configs:
          - targets: ['localhost:9090']

      - job_name: 'open-cluster-management-agent'
        kubernetes_sd_configs:
          - role: pod
            namespaces:
              names:
                - open-cluster-management-agent
        relabel_configs:
          - source_labels: [__address__]
            action: replace
            regex: ([^:]+)(?::\\d+)?
            replacement: \\$1:8080
            target_label: __address__
          - action: labelmap
            regex: __meta_kubernetes_pod_label_(.+)
          - source_labels: [__meta_kubernetes_namespace]
            target_label: kubernetes_namespace
          - source_labels: [__meta_kubernetes_pod_name]
            target_label: kubernetes_pod_name

      - job_name: 'kubernetes-services'
        kubernetes_sd_configs:
          - role: service
        relabel_configs:
          - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_scrape]
            action: keep
            regex: true
          - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_path]
            action: replace
            target_label: __metrics_path__
            regex: (.+)
          - source_labels: [__address__, __meta_kubernetes_service_annotation_prometheus_io_port]
            action: replace
            regex: ([^:]+)(?::\\d+)?;(\\d+)
            replacement: \\$1:\\$2
            target_label: __address__
          - action: labelmap
            regex: __meta_kubernetes_service_label_(.+)
          - source_labels: [__meta_kubernetes_namespace]
            target_label: kubernetes_namespace
          - source_labels: [__meta_kubernetes_service_name]
            target_label: kubernetes_name

      - job_name: 'kubernetes-pods'
        kubernetes_sd_configs:
          - role: pod
        relabel_configs:
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
            action: keep
            regex: true
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
            action: replace
            target_label: __metrics_path__
            regex: (.+)
          - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
            action: replace
            regex: ([^:]+)(?::\\d+)?;(\\d+)
            replacement: \\$1:\\$2
            target_label: __address__
          - action: labelmap
            regex: __meta_kubernetes_pod_label_(.+)
          - source_labels: [__meta_kubernetes_namespace]
            target_label: kubernetes_namespace
          - source_labels: [__meta_kubernetes_pod_name]
            target_label: kubernetes_pod_name
EOF_CM

  kubectl apply -f - <<'EOF_MON_DEP'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus-scraper
  namespace: monitoring
  labels:
    app: prometheus-scraper
spec:
  replicas: 1
  selector:
    matchLabels:
      app: prometheus-scraper
  template:
    metadata:
      labels:
        app: prometheus-scraper
    spec:
      serviceAccountName: prometheus-scraper
      containers:
        - name: prometheus
          image: prom/prometheus:v2.54.1
          imagePullPolicy: IfNotPresent
          args:
            - "--config.file=/etc/prometheus/prometheus.yml"
            - "--storage.tsdb.path=/prometheus"
            - "--storage.tsdb.retention.time=6h"
            - "--web.enable-lifecycle"
          ports:
            - containerPort: 9090
              name: metrics
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 256Mi
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
            - name: storage
              mountPath: /prometheus
      volumes:
        - name: config
          configMap:
            name: prometheus-scraper-config
        - name: storage
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus-scraper
  namespace: monitoring
  labels:
    app: prometheus-scraper
spec:
  type: ClusterIP
  selector:
    app: prometheus-scraper
  ports:
    - name: metrics
      port: 9090
      targetPort: 9090
      protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus-scraper-nodeport
  namespace: monitoring
  labels:
    app: prometheus-scraper
spec:
  type: NodePort
  selector:
    app: prometheus-scraper
  ports:
    - name: metrics
      port: 9090
      targetPort: 9090
      nodePort: 30900
      protocol: TCP
EOF_MON_DEP
  echo "=== Autonomous Spoke Prometheus Scraper Deployed Successfully ==="
}
"""

for reg in regions:
    path = f"{base_dir}/{reg}/compute/templates/user_data.sh.tftpl"
    with open(path, "r") as f:
        content = f.read()

    if "deploy_spoke_monitoring()" in content:
        print(f"[{reg}] deploy_spoke_monitoring already defined")
        continue

    # Add function definition right after setup_kubeconfig definition
    marker = "setup_kubeconfig() {\n"
    if marker not in content:
        print(f"[{reg}] ERROR: setup_kubeconfig marker not found")
        sys.exit(1)

    # Find the end of setup_kubeconfig
    end_marker = (
        "  chown -R ssm-user:ssm-user /home/ssm-user/.kube 2>/dev/null || true\n}"
    )
    if end_marker not in content:
        print(f"[{reg}] ERROR: end of setup_kubeconfig not found")
        sys.exit(1)

    replacement_function = end_marker + "\n\n" + monitoring_function
    content = content.replace(end_marker, replacement_function, 1)

    # Now add call to deploy_spoke_monitoring in fresh spoke block:
    # After clusteradm --kubeconfig=/etc/kubernetes/admin.conf join ...
    join_marker = 'clusteradm --kubeconfig=/etc/kubernetes/admin.conf join --hub-token "$HUB_TOKEN" --hub-apiserver "$HUB_URL" --cluster-name "$CLUSTER_NAME" --wait=false 2>&1 || true\n    fi'
    if join_marker in content:
        content = content.replace(
            join_marker, join_marker + "\n    deploy_spoke_monitoring", 1
        )
        print(f"[{reg}] Hooked into fresh spoke bootstrap")
    else:
        print(f"[{reg}] WARNING: join_marker not found")

    # Also hook into spot resume for spoke
    resume_marker = 'docker exec "$CLUSTER_CONTAINER" kubectl --kubeconfig=/etc/kubernetes/admin.conf -n open-cluster-management-agent rollout restart deployment/klusterlet-registration-agent deployment/klusterlet-work-agent 2>/dev/null || true'
    if resume_marker in content:
        content = content.replace(
            resume_marker, resume_marker + "\n    deploy_spoke_monitoring", 1
        )
        print(f"[{reg}] Hooked into spot resumption")
    else:
        print(f"[{reg}] WARNING: resume_marker not found")

    with open(path, "w") as f:
        f.write(content)

print("All regions updated with autonomous spoke monitoring deployment.")
