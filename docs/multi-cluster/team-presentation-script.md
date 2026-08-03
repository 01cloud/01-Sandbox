# 🎤 Presentation Script: Multi-Cluster Architecture for 01-Sandbox

> **Target Audience:** Engineering Team, DevOps, and Technical Managers
> **Duration:** 10–15 minutes
> **Goal:** Explain why we need multi-cluster scaling, how the OCM + Cilium architecture works, and reassure the team that **zero application code changes** are required.

---

## 1. Opening Hook & The Problem (1-2 mins)

> *"Hey everyone, thanks for joining.*
>
> *Right now, our entire `01-Sandbox` infrastructure runs on a **single RKE2 cluster on one server**. It handles everything: receiving API requests, queuing jobs in RabbitMQ, storing data in PostgreSQL, and running heavy code/repo scans inside Kata/Firecracker sandbox pods.*
>
> *This works great for low traffic. But when **100s or 1,000s of users** hit our API at the same time, spinning up hundreds of sandbox scan pods on a single server maxes out CPU, memory, and disk I/O. This risks server crashes, queue backup, and slow scans for users.*
>
> *Today, I’m walking you through our new **Multi-Cluster Scaling Architecture** using **Open Cluster Management (OCM)**, **Cilium Cluster Mesh**, and **ArgoCD** — based on the `BerryBytes/k8s-multicluster-handbook` reference setup.*
>
> *The best part? **We don't need to rewrite a single line of our application code.***

---

## 2. The Analogy: How the Architecture Works (2-3 mins)

> *"To understand how this works, think of our infrastructure like a **global company** with branch offices:*
>
> 1. **OCM (Open Cluster Management)** is **Management Headquarters**. It hires new servers, tells each cluster what workloads to run, and monitors their health.
> 2. **Cilium Cluster Mesh** is our **Private Telecom & Highway Network**. It connects the pod networks of all servers using encrypted eBPF kernel tunnels. Workers in different clusters can talk to each other as if they were sitting on the same local network.
> 3. **Our Local PC** remains **Spoke 1** (our primary cluster), and additional OVH virtual servers become **Spoke 2, Spoke 3, and so on**."

---

## 3. The 3 Architecture Layers (3-4 mins)

> *"The architecture is built on 3 clean layers:*
>
> ### Layer 1: External Traffic Entry (Cloudflare GeoDNS & Ingress)
> *Requests to `api-sandbox.01security.com` are routed to the nearest cluster gateway. If our local server is busy or offline, Cloudflare automatically fails over to an OVH server.*
>
> ### Layer 2: Cluster Management & GitOps (OCM + ArgoCD)
> *We deploy our Helm chart **ONCE** to ArgoCD on the Hub cluster. OCM automatically propagates the deployment to all spoke clusters. When we push code updates to GitHub, ArgoCD + OCM sync the fleet automatically.*
>
> ### Layer 3: Cross-Cluster Networking (Cilium Cluster Mesh)
> *Cilium creates global services for `rabbitmq-service` and `postgresql-service`.*
> - *It uses **Local Affinity**: our FastAPI backend publishes jobs to the **local RabbitMQ instance first** for minimum latency.*
> - *If the local queue is full, remote worker pods on OVH pick up unconsumed jobs over the secure Cilium mesh.*
> - *When an OVH worker finishes a scan, it writes the result back to `postgresql-service:5432` on our local PC transparently through the mesh.*"

---

## 4. Life of a Scan Request (2 mins)

> *"Let’s trace a scan request from start to finish:*
>
> 1. *A user posts a repository scan payload to `/api/v1/scan`.*
> 2. *`sandbox-api` publishes the job to `rabbitmq-service:5672`.*
> 3. *Cilium routes the job to the local RabbitMQ instance first.*
> 4. *If our local PC has capacity, a local worker pod provisions a Kata/Firecracker sandbox pod and scans the code.*
> 5. *If our local PC is overloaded, an OVH worker cluster picks up the job via Cilium Mesh, provisions the sandbox pod in OVH, and executes the scan.*
> 6. *The worker saves findings back to `postgresql-service` on our local PC and notifies the user.*"

---

## 5. What Changes for Us? (The Guarantees) (2 mins)

> *"I know everyone’s biggest question is: **What do we have to change in our code?**
>
> Here are our guarantees:
>
> - ✅ **Zero Python / FastAPI Code Changes:** `consumer.py` uses `amqp://admin:pass@rabbitmq-service:5672/`, DB string uses `postgresql-service:5432`. Connection strings stay 100% identical.
> - ✅ **No CNI Migration Needed:** Our live cluster already runs Cilium (`cilium-mn8zt`). Enabling Cluster Mesh is just one command (`cilium clustermesh enable`).
> - ✅ **Zero Downtime Onboarding:** Adding a new OVH server requires just 2 commands: `clusteradm join` and `cilium clustermesh connect`.
> - ✅ **Local PC Protection:** Heavy sandbox scanning is offloaded to OVH servers during peak load, protecting our local development environment.*"

---

## 6. Anticipated Q&A (3 mins)

> **Q1: "Does testing this locally break our existing RKE2 cluster?"**
> **Answer:** *"No! We test the multi-cluster setup locally using KinD (Kubernetes in Docker). KinD runs inside Docker containers on separate network CIDRs and custom API ports (`7443`). Our RKE2 server on port `6443` remains completely isolated and untouched."*
>
> **Q2: "What happens if the network between Local PC and OVH drops?"**
> **Answer:** *"Cilium Cluster Mesh detects the disconnection and automatically isolates the remote cluster. OVH workers continue running local jobs, and local traffic continues serving local jobs without hanging."*
>
> **Q3: "How hard is it to add a 3rd or 4th server in the future?"**
> **Answer:** *"It takes under 2 minutes: install RKE2 + Cilium on the new VM, run `clusteradm join` from OCM, and run `cilium clustermesh connect`. OCM automatically deploys `sandbox-api` to it."*

---

## 7. Closing & Next Steps

> *"To summarize: this architecture gives us **infinite horizontal scaling** across any number of OVH servers while preserving our exact code, queue logic, and database schemas.
>
> All step-by-step documentation, architecture diagrams, and a manual setup guide are available in `docs/multi-cluster/`.
>
> I’m happy to take any questions or demo the local KinD lab setup!"*
