# RabbitMQ Prefetch & Concurrency Flow

When you submit multiple repository scan requests (e.g., 20 repositories) to the API, they are converted into tasks/messages and sent to RabbitMQ. The `prefetch_count` (set to 3) controls how many messages a single worker channel can receive at one time before acknowledging them.

Below is a flowchart visualizing this pipeline, showing how messages travel from the client down to the workers, regulated by the RabbitMQ prefetch limit.

## Architectural Flowchart

```mermaid
graph TD
    %% Styling
    classDef client fill:#eef2ff,stroke:#6366f1,stroke-width:2px,color:#1e1b4b;
    classDef queue fill:#fef2f2,stroke:#f87171,stroke-width:2px,color:#451a03;
    classDef worker fill:#ecfdf5,stroke:#10b981,stroke-width:2px,color:#064e3b;
    classDef process fill:#fffbeb,stroke:#f59e0b,stroke-width:2px,color:#78350f;

    subgraph Client & API Layer
        A[User Submits 20 Repositories] -->|HTTP Post| B[API Server / FastAPI]
        B -->|Publish 20 Messages| C{RabbitMQ Exchange}
    end

    subgraph RabbitMQ Broker
        C -->|Route to Queue| D[Queue: repo_scan_queue<br/>Holds 20 Pending Messages]
    end

    subgraph Prefetch Regulation Layer
        D -->|Prefetch Limit = 3| E[RabbitMQ Dispatcher]
        E -->|Deliver Max 3 Unacked Messages| F[Consumer Channel Buffer<br/>Max Capacity: 3 Tasks]
    end

    subgraph Consumer (Worker) Processing
        F -->|Task 1| G1[Process Repo A]
        F -->|Task 2| G2[Process Repo B]
        F -->|Task 3| G3[Process Repo C]

        G1 -->|Done / Success| H1[Send ACK]
        G2 -->|Done / Failure| H2[Send ACK]
        G3 -->|Done / Timeout| H3[Send ACK / Reject]
    end

    subgraph Feedback Loop
        H1 -->|Decrease Buffer Count| I[Dispatcher Notified]
        H2 -->|Decrease Buffer Count| I
        H3 -->|Decrease Buffer Count| I
        I -->|Pull Next Message from Queue| D
    end

    class A,B client;
    class C,D queue;
    class E,F worker;
    class G1,G2,G3,H1,H2,H3 process;
```

---

## Detailed Step-by-Step Breakdown

### 1. Ingestion & Queueing
1. The **API Server** receives a request to scan 20 GitHub repositories.
2. The API publishes **20 individual messages** (each containing a repository URL, credentials, and job ID) to RabbitMQ.
3. The queue (`repo_scan_queue`) immediately holds these 20 messages. At this stage, all 20 messages are in a `Ready` state.

### 2. Prefetch Limit Enforcement (`basic.qos`)
- When the consumer (worker) connects to RabbitMQ, it sets `channel.basic_qos(prefetch_count=3)`.
- This tells the RabbitMQ broker: **"Do not send me more than 3 unacknowledged messages at a time."**
- RabbitMQ looks at the queue, sees 20 messages, but only delivers **3 messages** to the consumer.
- The status of these 3 messages in RabbitMQ changes from `Ready` to `Unacknowledged`. The remaining 17 messages stay in `Ready` state in the queue.

### 3. Consumer Concurrency
- The consumer now has 3 active tasks in memory.
- Depending on the consumer's architecture:
  - **Asynchronous Consumer (e.g., asyncio/Celery)**: The consumer processes all 3 repository scans concurrently.
  - **Synchronous Consumer**: The consumer processes them one after another, but holds the other 2 in its local memory buffer.
- This protects the consumer from running out of CPU/memory by preventing it from pulling all 20 repositories into memory at the same time.

### 4. Rolling Acknowledgment & Refill Loop (Sliding Window)
1. **No Batch-Waiting**: The consumer does not wait for all 3 tasks to finish before requesting more. Processing operates on a **rolling sliding window** basis.
2. **Individual Completion**: As soon as **any single scan** completes (e.g., `Repo A` finishes processing):
   - The consumer sends a `basic_ack` (acknowledgment) for that specific message.
3. **Queue Cleanup**: RabbitMQ deletes the message for `Repo A` from the queue.
4. **Buffer Decrease**: The consumer's unacknowledged message count drops from **3 to 2**.
5. **Immediate Refill**: RabbitMQ detects that the consumer's active message count is below the prefetch limit (2 < 3) and **immediately dispatches the 4th message** (`Repo D`) to the consumer.
6. **Continuous Stream**: The consumer always maintains up to 3 active tasks in progress, pulling one new task each time any existing task completes, until all 20 repositories have been processed.
