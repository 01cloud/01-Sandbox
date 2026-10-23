# 1-Day Kubernetes Intern Evaluation Plan — Track 2
## Observability Basics & Working with Running Applications

**Candidate Level:** Intern (entry-level; 0–1 year hands-on Kubernetes experience)
**Evaluation Duration:** 1 Day (8 Hours: 09:30 – 17:30)
**Environment:** Sandbox Multi-Cluster Testbed (kind clusters on your local machine)
**Document Version:** 2.0 (Intern-Calibrated)
**Evaluator / Mentor:** Kamal / DevOps Lead
**Evaluation Focus:** Checking application health, reading metrics, understanding logs, and basic resource management

---

## 1. What This Evaluation Is About

**Track 2** is a different perspective from Track 1. While Track 1 focuses on deploying and debugging basic resources, **Track 2 focuses on understanding what's already running** — reading metrics, checking health, and working with Kubernetes tools to observe a live environment.

This is the practical skill of an engineer who joins a team and needs to quickly understand the state of a system.

We are looking at:
- Can you **find information** about running workloads using standard tools?
- Can you **read and interpret metrics** exposed by an application?
- Do you understand how Kubernetes tracks **resource usage** (CPU/memory)?
- Can you **write and apply** basic Kubernetes objects (ConfigMaps, Secrets, resource limits)?
- How clearly do you **communicate** what you observe?

> **Mentor note:** This track is intentionally less YAML-heavy than Track 1. The intern will spend more time *reading* and *interpreting* the state of the system. Encourage curiosity — asking "why is this like this?" is a sign of a good candidate.

---

## 2. Environment Overview

The intern will work primarily on `kind-primaryhub` which has:

| Component | Namespace | What It Is |
| :--- | :--- | :--- |
| Application API | `opensandbox-system` | The main application (FastAPI-based) |
| Prometheus Scraper | `opensandbox-system` | Collects metrics from the application |
| Database (PostgreSQL) | `cnpg-system` | Stores application data |
| Monitoring namespace | `monitoring` | Grafana dashboards (if available) |

The intern does **not** need to understand how all of these work deeply. They just need to be able to navigate them.

---

## 3. Day Schedule at a Glance

| Time | Activity | What the Intern Does |
| :--- | :--- | :--- |
| **09:30 – 10:00** | Kickoff & Setup | Get access, confirm kubectl works, brief tour |
| **10:00 – 11:15** | **Task 1 – Health Check the Cluster** | Inspect what is running and its health |
| **11:15 – 12:30** | **Task 2 – Read Application Metrics** | Port-forward to Prometheus, explore metrics |
| **12:30 – 13:30** | Lunch Break | — |
| **13:30 – 14:30** | **Task 3 – ConfigMaps & Secrets** | Create and use basic configuration resources |
| **14:30 – 15:30** | **Task 4 – Resource Limits & Pod Behaviour** | Set and observe CPU/memory limits |
| **15:30 – 16:30** | **Task 5 – Investigate a Simulated Incident** | Use logs and metrics to investigate a problem |
| **16:30 – 17:00** | Write-Up | Intern compiles their notes |
| **17:00 – 17:30** | **Debrief Interview** | Q&A with evaluator |

---

## 4. Detailed Tasks

---

### Task 1: Health Check the Cluster (10:00 – 11:15)

**Goal:** Build the habit of checking the overall health of a system before making any changes — a core SRE and DevOps practice.

**Instructions given to intern:**

> "Before touching anything, you want to understand the current state of the system. Go through these steps and answer each question in your notes."

**Step 1 — Node health:**
```bash
kubectl get nodes -o wide
```
- Are all nodes in `Ready` status?
- What Kubernetes version are they running?

**Step 2 — Pod health across all namespaces:**
```bash
kubectl get pods -A
```
- How many pods are in `Running` state?
- Are there any pods in `Pending`, `CrashLoopBackOff`, `Error`, or `Terminating`?
- List any unhealthy pods and their namespaces.

**Step 3 — Recent events (the cluster's "event log"):**
```bash
kubectl get events -A --sort-by='.metadata.creationTimestamp' | tail -20
```
- What were the last few events in the cluster?
- Do you see any `Warning` type events? What are they about?

**Step 4 — Resource usage (if metrics-server is running):**
```bash
kubectl top nodes
kubectl top pods -A
```
- Which pod is using the most CPU?
- Which pod is using the most memory?
- *Note: If `kubectl top` gives an error, skip this and note it down.*

**What evaluator looks for:**
- Does the intern go through the steps methodically or randomly?
- Do they notice and flag anything that looks unhealthy?
- Do they note down their findings (rather than just running commands)?

---

### Task 2: Read Application Metrics (11:15 – 12:30)

**Goal:** Learn how to access and read metrics exposed by a running application — a fundamental observability skill.

**Instructions given to intern:**

> "Our application exposes metrics in Prometheus format. This is a common way applications share health data. Your job is to access those metrics and understand what they are telling you."

**Step 1 — Find the Prometheus service:**
```bash
kubectl get svc -n opensandbox-system
```
- Which service is the Prometheus scraper?
- What port does it listen on?

**Step 2 — Port-forward to access Prometheus from your browser:**
```bash
kubectl port-forward svc/<prometheus-service-name> 9090:9090 -n opensandbox-system
```
- Open `http://localhost:9090` in your browser.
- Go to **Status → Targets** — are there any targets in `DOWN` state?

**Step 3 — Look at basic metrics in the Prometheus UI:**

Try typing these into the query box and pressing "Execute":

1. `up` — This shows which services are being scraped. A value of `1` means healthy, `0` means down.
2. `process_resident_memory_bytes` — Memory used by scraped processes.
3. `kube_pod_container_status_restarts_total` — Container restart counts.

For each query, write in your notes:
- What does this metric tell you?
- What values did you see?

**Step 4 (stretch) — Find pods that have restarted more than once:**
```
kube_pod_container_status_restarts_total > 1
```
- Are there any? Which pods?

**What evaluator looks for:**
- Can the intern port-forward without step-by-step guidance?
- Do they explore the Prometheus UI with curiosity?
- Can they explain in plain English what a metric means?

**If intern has never seen Prometheus before:**
Give a 5-minute explanation: *"Prometheus is a monitoring tool. Applications expose a `/metrics` page with numbers. Prometheus collects those numbers every 15 seconds. You can then query them to understand what's happening in the system."* Then let them continue.

---

### Task 3: ConfigMaps & Secrets (13:30 – 14:30)

**Goal:** Understand how Kubernetes injects configuration into pods — without hardcoding it in the image.

**Instructions given to intern:**

> "In namespace `evaluation-<yourname>`, you will create a ConfigMap and a Secret, then deploy a pod that uses them."

**Part A — Create a ConfigMap:**
```yaml
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: evaluation-<yourname>
data:
  APP_ENV: "evaluation"
  LOG_LEVEL: "info"
```
Apply it and verify with `kubectl get configmap app-config -n evaluation-<yourname> -o yaml`

**Part B — Create a Secret:**
```bash
kubectl create secret generic app-secret \
  --from-literal=API_KEY=super-secret-key-123 \
  -n evaluation-<yourname>
```
Verify it was created. Look at it with `kubectl get secret app-secret -n evaluation-<yourname> -o yaml`.
- Can you read the `API_KEY` value directly? Why or why not?
  - *Expected answer: No — it is base64 encoded. It is not encrypted at rest by default, but it is hidden from casual viewing.*

**Part C — Use them in a Pod:**

Write a YAML for a pod that:
- Uses the image `nginx:alpine`
- Injects `APP_ENV` and `LOG_LEVEL` from the ConfigMap as environment variables
- Injects `API_KEY` from the Secret as an environment variable

Apply and verify the env vars are visible inside the pod:
```bash
kubectl exec -n evaluation-<yourname> <pod-name> -- env | grep -E "APP_ENV|LOG_LEVEL|API_KEY"
```

**What evaluator looks for:**
- Do they understand *why* we use ConfigMaps and Secrets instead of hardcoding values?
- Do they correctly reference the ConfigMap and Secret using `valueFrom.configMapKeyRef` and `valueFrom.secretKeyRef`?
- Do they verify with `kubectl exec`?

---

### Task 4: Resource Limits & Pod Behaviour (14:30 – 15:30)

**Goal:** Understand how Kubernetes manages CPU and memory for pods, and what happens when limits are set (or not set).

**Instructions given to intern:**

> "This task is about understanding resource limits in Kubernetes."

**Part A — Look at existing pods without limits:**
```bash
kubectl get pods -n opensandbox-system -o json | grep -A 5 '"resources"'
```
Or use describe on a specific pod:
```bash
kubectl describe pod <pod-name> -n opensandbox-system
```
- Do any of the pods have no `requests` or `limits` set? What is the risk of that?

**Part B — Deploy two pods side by side:**

Deploy two identical pods running a simple CPU stress test (`polinux/stress`), but with different resource limits:

**Pod 1 — Restricted:**
```yaml
resources:
  requests:
    cpu: "50m"
    memory: "64Mi"
  limits:
    cpu: "100m"
    memory: "128Mi"
```

**Pod 2 — Unrestricted (no limits set):**
```yaml
resources:
  requests:
    cpu: "50m"
    memory: "64Mi"
```

Apply both. Then check:
```bash
kubectl top pod -n evaluation-<yourname>
```

- What is each pod's CPU usage?
- Is the restricted pod being throttled?

**Part C — Discussion question for notes:**
> "In your own words, explain why a pod without resource limits could be a problem on a shared cluster."
- *Expected answer: It could use all available CPU/memory on the node, starving other pods. In a multi-tenant system this could cause outages for other teams' workloads.*

**What evaluator looks for:**
- Does the intern understand what `requests` vs `limits` means?
- Can they interpret `kubectl top` output?
- Do they articulate the "noisy neighbour" problem?

---

### Task 5: Investigate a Simulated Incident (15:30 – 16:30)

**Goal:** Put everything together — given an alert, investigate what is happening using logs, metrics, and kubectl.

**How to stage it (evaluator pre-stages this before 09:30):**
1. Deploy a pod in `evaluation-incident` namespace that crashes after 60 seconds (`sleep 60 && exit 1` command)
2. Set its image pull policy to `Always` to ensure it keeps restarting
3. Let it run so by 15:30 it has restarted 3–5 times

**Instructions given to intern:**

> "You have received an alert: 'Pod in namespace `evaluation-incident` is unhealthy.' Investigate what is happening and write an incident report."

**Expected investigation steps:**
1. `kubectl get pods -n evaluation-incident` — see the restarting pod
2. `kubectl describe pod <name> -n evaluation-incident` — check Events and restart count
3. `kubectl logs <name> -n evaluation-incident` — read current logs
4. `kubectl logs <name> -n evaluation-incident --previous` — read logs from the crashed container
5. Check Prometheus (if still port-forwarded): `kube_pod_container_status_restarts_total{namespace="evaluation-incident"}`

**Incident report format (intern writes in their notes):**

```
## Incident Report

**Time discovered:** <time>
**Pod:** <pod-name>
**Namespace:** evaluation-incident

**What I observed:**
- ...

**Root cause:**
- ...

**Evidence I used:**
- kubectl commands I ran: ...
- What the logs showed: ...

**Recommended fix:**
- ...
```

**What evaluator looks for:**
- Does the intern follow a logical investigation path?
- Do they use both `logs` and `describe` to build a picture?
- Is their incident report clear and readable?
- Do they give a concrete recommended fix, not just describe the problem?

---

### Write-Up Time (16:30 – 17:00)

The intern should compile all their notes into `candidate-notes.md` with sections for each task:
- Commands run
- What they found
- What questions came up
- The incident report from Task 5

---

## 5. Debrief Interview (17:00 – 17:30)

Pick 4–5 questions based on how the day went:

#### Q1: "What is the difference between `kubectl logs` and `kubectl describe` — when do you use each?"
- **Good answer:** `logs` shows the application's standard output (what the app printed). `describe` shows Kubernetes-level metadata — events, scheduling info, container state. You use both — `describe` to understand *what Kubernetes thinks*, `logs` to understand *what the app thinks*.

#### Q2: "What is a ConfigMap? Why would you use one instead of hardcoding a value in the Docker image?"
- **Good answer:** A ConfigMap stores configuration outside of the container image, so you can change the configuration without rebuilding the image. It also lets different environments (dev, staging, prod) use different configs with the same image.

#### Q3: "What does `kubectl top pods` show you? What is the difference between `requests` and `limits`?"
- **Good answer:** `top` shows live resource consumption. `requests` is what the pod says it needs (used for scheduling — Kubernetes places the pod on a node that has enough). `limits` is the maximum it can use — if it exceeds this, CPU is throttled, memory causes an OOMKill.

#### Q4: "In the incident task, how did you figure out the root cause? What was your investigation process?"
- **Good answer:** Intern walks through: checked pod status → describe for events → logs for app output → previous logs for crash output. Shows systematic thinking.

#### Q5 (stretch): "What is Prometheus? How does it collect metrics from pods?"
- **Good answer:** Prometheus is a monitoring tool. It scrapes a `/metrics` endpoint exposed by applications every few seconds and stores the data as time-series. You can then query and alert on it.

---

## 6. Evaluation Scoring Rubric (100 Points)

| Area | Points | What Earns Full Marks |
| :--- | :---: | :--- |
| **Cluster Health Check (Task 1)** | 15 | Goes through all 4 steps; flags anything unhealthy; clear notes |
| **Metrics Reading (Task 2)** | 20 | Port-forwards successfully; explores Prometheus UI; explains what metrics mean in plain English |
| **ConfigMaps & Secrets (Task 3)** | 20 | Creates both correctly; injects into pod; uses `kubectl exec` to verify; explains why Secrets are encoded |
| **Resource Limits (Task 4)** | 20 | Understands requests vs limits; can read `kubectl top`; articulates noisy-neighbour risk |
| **Incident Investigation (Task 5)** | 15 | Uses logs + describe + Prometheus; writes a clear incident report with root cause |
| **Notes & Debrief Communication** | 10 | Notes are readable and complete; explains their work clearly during debrief |

### Hiring Recommendation

| Score | Recommendation |
| :---: | :--- |
| **80 – 100** | **Strong Hire.** Methodical thinker, clear communicator, comfortable with observability tools. Ready to contribute on real incidents. |
| **60 – 79** | **Hire with Mentorship.** Solid foundation in concepts; needs practice with tools and investigation workflow. |
| **40 – 59** | **Marginal.** Understands some concepts but struggles to investigate independently. Consider a structured learning plan. |
| **< 40** | **Do Not Proceed.** Lacks the observability awareness needed for this internship track. |

---

## 7. Evaluator Pre-Day Checklist

- [ ] Create namespace `evaluation-<candidate-name>` on `kind-primaryhub`
- [ ] Create namespace `evaluation-incident` and pre-stage the crashing pod
- [ ] Verify Prometheus is running and accessible via port-forward
- [ ] Verify `kubectl top` works (metrics-server is installed); note if not available
- [ ] Share day schedule and task instructions with intern at 09:30
- [ ] Collect `candidate-notes.md` at 17:00
- [ ] Score the rubric and file it after the debrief

---

## 8. Notes for the Evaluator

- **This track rewards curiosity.** An intern who asks "why does this metric look like this?" is showing exactly the right instinct.
- **Prometheus is new for most interns.** Give the quick intro in Task 2 without hesitation — the goal is to see how they use the tool once they understand it, not whether they already know it.
- **The incident report** is intentionally open-ended. Look for structured thinking, not perfect prose.
- **Don't compare Track 1 vs Track 2 candidates directly** — the tracks test different temperaments. Track 1 is for hands-on builders; Track 2 is for investigators/observers. Both are valuable.
- **Stretch goals** (Prometheus PromQL deep dive, NodePort, base64 decoding) are only for candidates who blaze through the main tasks.
