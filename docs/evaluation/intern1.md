# 1-Day Kubernetes Intern Evaluation Plan — Track 1
## Kubernetes Fundamentals & Cluster Exploration

**Candidate Level:** Intern (entry-level; 0–1 year hands-on Kubernetes experience)
**Evaluation Duration:** 1 Day (8 Hours: 09:30 – 17:30)
**Environment:** Sandbox Multi-Cluster Testbed (kind clusters on your local machine)
**Document Version:** 2.0 (Intern-Calibrated)
**Evaluator / Mentor:** Kamal / DevOps Lead

---

## 1. What This Evaluation Is About

This is a **hands-on, guided evaluation** designed for someone who has studied Kubernetes basics (in a course, bootcamp, or self-study) and is now getting their first real exposure to a running cluster environment.

We are **not** expecting you to know everything. We **are** looking at:
- How you **explore and learn** in an unfamiliar environment.
- How you **read error messages** and figure out what went wrong.
- Whether you can **write basic Kubernetes YAML** from what you have learned.
- How you **communicate** your findings clearly.

> **Mentor note:** Be encouraging. The tasks below are designed to be completable in the allotted time with a bit of thinking and kubectl help pages. Do not pre-answer questions — but do confirm access is working at kickoff.

---

## 2. Environment Overview (Kept Simple)

The intern will work with a multi-cluster setup running locally using `kind` (Kubernetes IN Docker):

| Cluster Name | Role | What's on it |
| :--- | :--- | :--- |
| `kind-primaryhub` | Hub (main manager) | Core application, database, monitoring |
| `kind-secondaryhub` | Hub (standby/backup) | Backup of the main hub |
| `kind-spoke1` | Worker cluster | Runs sandboxed workloads |
| `kind-spoke2` | Worker cluster | Runs sandboxed workloads |
| `kind-spoke3` | Worker cluster | Runs sandboxed workloads |

The intern only needs to focus on **`kind-primaryhub`** and one spoke cluster for most of the day. The bigger multi-cluster picture will be introduced gradually.

---

## 3. Day Schedule at a Glance

| Time | Activity | What the Intern Does |
| :--- | :--- | :--- |
| **09:30 – 10:00** | Kickoff & Setup | Get access, confirm kubectl works, quick tour |
| **10:00 – 11:15** | **Task 1 – Explore the Cluster** | Navigate the cluster using kubectl |
| **11:15 – 12:30** | **Task 2 – Deploy Your First App** | Write and apply a simple deployment |
| **12:30 – 13:30** | Lunch Break | — |
| **13:30 – 14:45** | **Task 3 – Fix Broken Things** | Diagnose and fix 3 pre-staged broken pods |
| **14:45 – 15:45** | **Task 4 – Services & Networking** | Expose the app and verify connectivity |
| **15:45 – 16:30** | **Task 5 – Intro to Multi-Cluster** (guided) | Look at the cluster topology with guidance |
| **16:30 – 17:00** | Write-Up | Intern completes their notes document |
| **17:00 – 17:30** | **Debrief Interview** | Q&A with evaluator |

---

## 4. Detailed Tasks

---

### Task 1: Explore the Cluster (10:00 – 11:15)

**Goal:** Get comfortable navigating a live Kubernetes cluster using `kubectl`.

**Instructions given to intern:**

> "You now have access to the cluster. Your job is to look around and answer the questions below in your notes. Use `kubectl` commands — you can also run `kubectl <command> --help` if you get stuck."

**Questions to answer in their notes:**

1. How many clusters do you have access to? List them.
   - *Hint:* `kubectl config get-contexts`

2. Switch to `kind-primaryhub`. How many nodes does it have, and what is the STATUS of each node?
   - *Hint:* `kubectl get nodes`

3. List all the **namespaces** on `kind-primaryhub`.
   - *Hint:* `kubectl get namespaces`

4. List **all pods** running across all namespaces. How many are there? Are any in a non-Running state?
   - *Hint:* `kubectl get pods -A`

5. Pick any one pod and use `kubectl describe` on it. Write down:
   - What node it is running on
   - Its IP address
   - The last 3 events shown

**What evaluator looks for:**
- Can the intern find and use the right commands without being told the exact syntax?
- Do they notice if something looks wrong (e.g. a pod not in `Running` status)?
- Do they take notes as they go?

**Struggle indicators to watch for:**
- Intern doesn't know how to switch contexts → prompt: *"Try `kubectl config use-context`"*
- Intern doesn't know about namespaces → explain in 2 sentences, let them continue

---

### Task 2: Deploy Your First App (11:15 – 12:30)

**Goal:** Write a basic Kubernetes `Deployment` and `Service` YAML from scratch and apply it to the cluster.

**Instructions given to intern:**

> "Create a namespace called `evaluation-<yourname>`. Then, in that namespace, deploy a simple web app that responds with some text. Use the image `hashicorp/http-echo:0.2.3`. This image accepts a flag `-text='some text here'`.
>
> Your deployment should have:
> - 2 replicas
> - The app should run on port `5678`
> - CPU and memory limits set (e.g. 100m CPU, 128Mi memory)
>
> Save your YAML files to your notes. Apply them and verify the pods are `Running`."

**Stretch goal (for interns who finish early):**
> "Add a liveness probe that checks `GET /` on port 5678 every 10 seconds."

**What evaluator looks for:**
- Can they write valid YAML (correct indentation, proper `apiVersion`, `kind`, etc.)?
- Do they know the difference between `kubectl apply` and `kubectl create`?
- Do they verify their work with `kubectl get pods` and `kubectl get deploy`?
- Do they troubleshoot if a pod doesn't come up?

**Common mistakes to watch for:**
- YAML indentation errors → ask them to spot the problem, don't fix for them
- Wrong `image` flag syntax → let them use `kubectl logs` to see the error
- Forgetting `labels` on the pod template so selector doesn't match → guide: *"Check kubectl describe deployment"*

---

### Task 3: Fix Broken Things (13:30 – 14:45)

**Goal:** Evaluate how the intern investigates and resolves problems — the most important real-world skill.

Three broken pods/deployments are pre-staged in namespace `evaluation-debug` on `kind-primaryhub`.

**Instructions given to intern:**

> "There are 3 broken applications in namespace `evaluation-debug`. Figure out what is wrong with each one and fix it. Document the problem you found and the fix you applied for each."

---

#### Broken App A: `CrashLoopBackOff`

**How to stage it:** Deploy a pod with command `["sh", "-c", "echo Starting && exit 1"]`

**What the intern should do:**
1. Run `kubectl get pods -n evaluation-debug` — see the pod is in `CrashLoopBackOff`
2. Run `kubectl logs <pod-name> -n evaluation-debug` — see the output
3. Run `kubectl describe pod <pod-name> -n evaluation-debug` — see the exit code
4. Fix: change the command to something that runs successfully (e.g. `sleep 3600`)

**Hint if stuck (after 10 minutes):** *"What does `kubectl logs` show you? What about `--previous`?"*

---

#### Broken App B: `ImagePullBackOff`

**How to stage it:** Deployment with image `nginx:version-that-does-not-exist`

**What the intern should do:**
1. See the pod in `ImagePullBackOff` or `ErrImagePull`
2. Run `kubectl describe pod` and read the Events section — it will say the image tag is wrong
3. Fix: update the image to `nginx:alpine` and re-apply

**Hint if stuck (after 10 minutes):** *"Look at the Events section at the bottom of `kubectl describe pod`"*

---

#### Broken App C: Service with No Endpoints

**How to stage it:** A Deployment with label `app: web-app` but a Service with selector `app: webapp` (note: no hyphen)

**What the intern should do:**
1. `kubectl get svc -n evaluation-debug` — see the service exists
2. `kubectl get endpoints -n evaluation-debug` — see it shows `<none>`
3. `kubectl get pods --show-labels -n evaluation-debug` — see the labels on the pods
4. Compare the pod labels with the service selector
5. Fix the selector in the Service to match the pod label

**Hint if stuck (after 10 minutes):** *"Run `kubectl get endpoints` — what do you see? Now compare that with `kubectl get pods --show-labels`"*

---

### Task 4: Services & Connectivity (14:45 – 15:45)

**Goal:** Verify the intern understands how to expose and reach a pod through a Service.

**Instructions given to intern:**

> "Your `echo-app` from Task 2 should already be running. Now:
> 1. Create a `ClusterIP` Service to expose it on port `8080` → pod port `5678`.
> 2. Verify the Service has endpoints (i.e. it's connected to the pods).
> 3. Use `kubectl port-forward` to access the app from your local browser or `curl`.
>    - Example: `kubectl port-forward svc/<service-name> 8080:8080 -n evaluation-<yourname>`
>    - Then run: `curl http://localhost:8080`
> 4. Write down the response you got."

**Stretch goal:**
> "Change the Service type to `NodePort` and access it on the node's IP instead of port-forwarding."

**What evaluator looks for:**
- Does the intern understand that Services use label selectors to find pods?
- Can they port-forward without help?
- Do they verify with `kubectl get endpoints`?

---

### Task 5: Multi-Cluster Introduction (15:45 – 16:30) — Guided

**Goal:** Give the intern a basic feel for the multi-cluster setup without overwhelming them. This is **guided** — the evaluator walks through this together with the intern.

**Evaluator-led tour (intern observes and asks questions):**

1. Show the intern the multiple kubeconfig contexts and explain each cluster's role (Hub = manager, Spoke = worker).
2. Switch to `kind-spoke1` and run `kubectl get pods -A` together.
3. Point out the `klusterlet` namespace — explain in 1–2 sentences: *"This is the agent that keeps this spoke cluster connected to the hub."*
4. Ask the intern: *"Why do you think we would want multiple clusters instead of just one big one?"* — just have a conversation, no right/wrong answer expected.

**Intern task (write-up, not graded hard):**
> "In 3–5 bullet points, describe what you saw in the multi-cluster setup. What surprised you? What questions do you have?"

---

### Write-Up Time (16:30 – 17:00)

The intern should compile their notes into a file called `candidate-notes.md` with:
- Commands they ran for each task
- What they found / what was broken
- What they fixed and how
- Any questions they still have

This doesn't need to be perfect — it should show their thinking process.

---

## 5. Debrief Interview (17:00 – 17:30)

A relaxed conversation reviewing what the intern did. Use these questions as a guide — pick 4–5 based on how the day went.

#### Q1: "What command would you use to see why a pod is crashing?"
- **Good answer:** `kubectl logs <pod>` and `kubectl describe pod <pod>` — looking at logs and Events
- **Acceptable answer:** Mentions `kubectl logs`; may need prompting for `describe`

#### Q2: "What is the difference between a `Deployment` and a `Pod` in Kubernetes?"
- **Good answer:** A Deployment manages pods — if a pod crashes, the Deployment restarts it. A standalone pod has no self-healing.
- **Acceptable answer:** Deployment = controller that creates/manages pods

#### Q3: "If your Service shows `<none>` for endpoints, what is the most likely cause?"
- **Good answer:** The Service selector labels don't match the pod labels
- **Acceptable answer:** The Service and pod labels are different

#### Q4: "What did port-forwarding do in Task 4? Why did you need it?"
- **Good answer:** `ClusterIP` services are only reachable inside the cluster. Port-forwarding creates a tunnel from localhost to the service so you can reach it from outside.
- **Acceptable answer:** Lets you access a service from your laptop that is normally only inside the cluster

#### Q5 (stretch — only ask if the candidate did well): "What is the purpose of having both a primary hub and a secondary hub in this multi-cluster setup?"
- **Good answer:** The secondary hub is a backup — if the primary hub goes down, the secondary can take over managing the spoke clusters.

---

## 6. Evaluation Scoring Rubric (100 Points)

| Area | Points | What Earns Full Marks |
| :--- | :---: | :--- |
| **Cluster Navigation (Task 1)** | 15 | Can switch contexts, list nodes/namespaces/pods, and describe a pod independently |
| **Manifest Writing (Task 2)** | 25 | Valid YAML deployed successfully; pods are Running; used resource limits |
| **Debugging (Task 3)** | 30 | Identified root cause (not just symptoms) for each broken app; fixed at least 2/3 independently |
| **Services & Connectivity (Task 4)** | 15 | Service correctly connected to pods (endpoints not empty); used port-forward successfully |
| **Notes & Communication (Write-up + Debrief)** | 15 | Notes capture commands used, errors found, and fixes applied; can explain their work in the debrief |

### Hiring Recommendation

| Score | Recommendation |
| :---: | :--- |
| **80 – 100** | **Strong Hire.** Shows solid fundamentals, learns quickly, debugs methodically. Ready to pair on real tasks immediately. |
| **60 – 79** | **Hire with Mentorship.** Core concepts understood; needs hand-holding on YAML and debugging. Will grow with structured pairing. |
| **40 – 59** | **Marginal.** Shows effort but lacks basic command fluency. May benefit from 2–4 weeks of guided study before re-evaluation. |
| **< 40** | **Do Not Proceed.** Fundamentals are not yet in place for a Kubernetes internship. |

---

## 7. Evaluator Pre-Day Checklist

- [ ] Create namespace `evaluation-<candidate-name>` on `kind-primaryhub`
- [ ] Create namespace `evaluation-debug` with the 3 broken apps pre-staged
- [ ] Verify kubeconfig access for the intern (read + limited write permissions)
- [ ] Share the day timetable and task instructions with the intern at 09:30
- [ ] Have a laptop / terminal ready for the Task 5 guided tour
- [ ] Collect `candidate-notes.md` at 17:00
- [ ] Score the rubric and file it after the debrief

---

## 8. Notes for the Evaluator

- **Be patient.** YAML indentation errors, command typos, and namespace confusion are completely normal for an intern. Watch *how* they respond to errors — do they read the error message, or do they panic and ask immediately?
- **Don't give answers outright.** Point them toward the right command or section ("Have you tried kubectl describe?") rather than solving the problem for them.
- **Encourage questions.** An intern who asks smart questions is often better than one who silently struggles.
- **The stretch goals** are only for interns who finish ahead of schedule. Do not count it against anyone who doesn't reach them.
