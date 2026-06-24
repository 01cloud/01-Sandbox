# Viewing RabbitMQ Metrics

This document outlines the three methods for querying active RabbitMQ queue statistics and status in the `01-Sandbox` deployment.

> [!NOTE]
> The active RabbitMQ message broker and the FastAPI gateway are deployed inside the Kubernetes (RKE2) cluster in the `opensandbox-system` namespace. Host-level utilities running on `10.0.10.9` directly do not target the active application broker.

---

## Method 1: Query the FastAPI Gateway API (Recommended)

The gateway exposes public and authenticated endpoints that query the active RabbitMQ broker on the cluster. The `sandbox-api-service` is exposed on the host via **NodePort 30080**.

### 1. Public / Unauthenticated Endpoint
Query this from any terminal with network access to the server:
```bash
curl -X GET http://10.0.10.9:30080/queue-stats
```

### 2. Private / Authenticated Endpoint
```bash
curl -X GET http://10.0.10.9:30080/v1/queue/stats \
  -H "Authorization: Bearer <your-developer-api-key>"
```

### Example Response
The response returns metrics for all active application queues, including scan tasks, deletes, and email notifications:
```json
{
  "available": true,
  "queues": {
    "scan.quick": {
      "depth": 0,
      "consumers": 2,
      "throughput": 0.0
    },
    "scan.repo": {
      "depth": 0,
      "consumers": 2,
      "throughput": 0.0
    },
    "scan.failed": {
      "depth": 0,
      "consumers": 0,
      "throughput": 0.0
    },
    "scan.delete": {
      "depth": 0,
      "consumers": 2,
      "throughput": 0.0
    },
    "notification.email": {
      "depth": 0,
      "consumers": 2,
      "throughput": 0.0
    }
  }
}
```

---

## Method 2: Query via Kubernetes CLI (`rabbitmqctl`)

If you have SSH access to the `10.0.10.9` host machine, you can execute commands inside the active RabbitMQ pod using `kubectl`.

### 1. View Queues, Depth, and Active Consumers
```bash
ssh ktamang@10.0.10.9 'sudo kubectl exec -n opensandbox-system deployment/rabbitmq -- rabbitmqctl list_queues name messages consumers'
```

### 2. View Overall Broker Health and Memory Usage
```bash
ssh ktamang@10.0.10.9 'sudo kubectl exec -n opensandbox-system deployment/rabbitmq -- rabbitmqctl status'
```

---

## Method 3: Access RabbitMQ Management Web Console

You can tunnel the internal Kubernetes RabbitMQ service to your local browser using an SSH local port-forward:

1. **Establish the port-forward tunnel**:
   Run this command from your local terminal:
   ```bash
   ssh -L 15672:127.0.0.1:15672 ktamang@10.0.10.9 "sudo kubectl port-forward -n opensandbox-system service/rabbitmq-service 15672:15672"
   ```
2. **Access the Web UI**:
   Open your browser and navigate to:
   [http://localhost:15672](http://localhost:15672)
3. **Login**:
   Use the RabbitMQ credentials specified in the cluster configuration values.
