# Monitoring RabbitMQ Metrics Without Grafana/Prometheus

This document outlines the UX recommendations regarding the **Queue Monitor** tab on the Developer Dashboard, and details the alternative ways to view message broker metrics when external monitoring tools (such as Grafana or Prometheus) are not in use.

---

## UI Recommendation: Restrict or Remove the "Queue Monitor"

The "Queue Monitor" tab displays system-wide, low-level metrics (queue depths, active consumers, and throughput rates) for RabbitMQ. Because this interface is primarily accessed by end-users (rather than administrators or developers), exposing this raw infrastructure data exposes system design, adds unnecessary cognitive load, and can cause confusion.

### Recommendation
If external monitoring is not available, we recommend **restricting access to the tab**:
* Configure the dashboard to only render the "Queue Monitor" tab for users with an `admin` role or flag.
* For regular users, render only product-centric tabs like "Applications" and "API Management".

---

## Alternative Ways to View Queue Metrics

If the "Queue Monitor" is removed from the UI, you can easily view live message broker metrics using any of the following three methods:

### 1. RabbitMQ Management Web UI
RabbitMQ provides a built-in, lightweight web console that displays real-time statistics (including graphs for queue depths, consumers, connections, and message rates).

* **Access URL**: `http://<your-rabbitmq-host>:15672` (locally: `http://localhost:15672`)
* **Credentials**: Use the same username and password defined in the FastAPI application's RabbitMQ connection environment variables.

---

### 2. Querying the FastAPI stats Endpoint
The FastAPI gateway exposes endpoints that return queue metrics directly from RabbitMQ. You can query these programmatically or via terminal:

#### Private/Authenticated Endpoint
```bash
curl -X GET http://localhost:8000/v1/queue/stats \
  -H "Authorization: Bearer <your-developer-api-key>"
```

#### Public/Unauthenticated Endpoint
```bash
curl -X GET http://localhost:8000/queue-stats
```

#### Example JSON Response
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
    }
  }
}
```

---

### 3. RabbitMQ Command-Line Interface (`rabbitmqctl`)
If you have SSH or terminal access to the host machine where RabbitMQ is running, you can run CLI commands to fetch stats:

* **List active queues with depth and consumers**:
  ```bash
  rabbitmqctl list_queues name messages consumers
  ```
* **Check overall node health/status**:
  ```bash
  rabbitmqctl status
  ```
