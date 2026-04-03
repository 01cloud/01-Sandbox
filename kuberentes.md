# Beginner's Guide: Provisioning OpenSandbox Fully inside Kubernetes

Welcome! If you are new to Kubernetes or OpenSandbox, this guide is written step-by-step just for you. 

We will walk through exactly how to set up everything from scratch on your local machine so you can run the `aio-sandbox` (All-in-One Sandbox) using a complete, 100% Kubernetes-driven setup. This means both the OpenSandbox Controller AND the OpenSandbox Server API will run inside your cluster!

---

## 📖 What are we doing?

OpenSandbox allows you to run secure, isolated programs (like an AI agent or a code interpreter). To do this, it needs a "container engine" to actually run those isolated programs. Kubernetes is highly recommended for production because it is great at managing hundreds or thousands of these sandboxes at the same time.

In this guide, we will:
1. Install a mini-Kubernetes cluster on your own computer.
2. Install the **OpenSandbox Controller** (which teaches your cluster what a Sandbox is).
3. Install the **OpenSandbox Server API** directly into the same Kubernetes cluster.
4. Provision the sandbox without using any local Python scripts at all—using pure Kubernetes setups and raw API requests!

---

## 🛠️ Step 1: Prerequisites & Creating the Cluster

**Prerequisites:**
1. **Docker**: You must have [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running.
2. **kubectl**: Command-line tool to talk to Kubernetes ([install guide](https://kubernetes.io/docs/tasks/tools/)).
3. **kind**: Used to create the mini-cluster ([install guide](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)).
4. **helm**: The package manager for Kubernetes ([install guide](https://helm.sh/docs/intro/install/)).

**Create the cluster:**
Open your terminal and run:
```bash
kind create cluster
```
*What this does: It downloads a Kubernetes image and spins it up inside a Docker container. Now you have a fully functioning Kubernetes cluster on your laptop!*

---

## 🧠 Step 2: Install OpenSandbox Components onto Kubernetes

We will use Helm to install both the Controller and the Server directly into our cluster. 

In your terminal window, navigate to the folder where you downloaded OpenSandbox.
```bash
cd /path/to/OpenSandbox
```

**1. Install the Controller:**
```bash
helm install opensandbox-controller ./kubernetes/charts/opensandbox-controller \
  --namespace opensandbox-system \
  --create-namespace
```

**2. Install the Server API:**
```bash
helm install opensandbox-server ./kubernetes/charts/opensandbox-server \
  --namespace opensandbox-system
```

**3. Wait for everything to become 'Running':**
```bash
kubectl get pods -n opensandbox-system
```

*(You should see both the `opensandbox-controller` and `opensandbox-server` pods listed as `Running`.)*

---

## 🌐 Step 3: Accessing the API & Preparing

Because the Server API is hiding deep inside the cluster, your laptop can't reach it directly just yet. You must open a tunnel (port-forward) so your local `localhost:8080` routes exactly to the Server in the cluster.

```bash
kubectl port-forward svc/opensandbox-server 8080:8080 -n opensandbox-system
```

*(Leave this terminal window running! It keeps the tunnel alive. Open a **brand new Terminal window** for Step 4.)*

---

## 🎉 Step 4: Provisioning Your Sandbox (The Kubernetes Way)

You mentioned you want all the steps driven entirely in Kubernetes! Here are two different ways to do this natively without relying on Python Wrapper scripts.

### Method A: Hitting the Raw OpenSandbox Server REST API

Since we started the Server in Step 3 and port-forwarded it, we can send a raw HTTP request directly to the API endpoint to spin up the `aio-sandbox`. 

In your new terminal window, copy and paste this command:

```bash
curl -X POST "http://localhost:8080/v1/sandboxes" \
  -H "Content-Type: application/json" \
  -d '{
    "image": {
      "uri": "ghcr.io/agent-infra/sandbox:latest"
    },
    "entrypoint": ["/bin/sh"],
    "timeout": 3600,
    "resourceLimits": {
      "cpu": "500m",
      "memory": "512Mi"
    }
  }'

```

**What is happening behind the scenes:**
1. You just sent a direct API POST request asking for a Sandbox using the AIO image (`ghcr.io/agent-infra/sandbox:latest`).
2. The `opensandbox-server` API receives this and tells Kubernetes to spin up a Sandbox pod!
3. The response will instantly output JSON containing your Sandbox's `id` and the port mapping endpoint! 

*(This method is what the Python `aio-sandbox/main.py` is doing behind the scenes under the hood!)*

---

### Method B: Fully Declarative pure-Kubernetes (Without Server API)

If you meant "I want to drive this using pure Kubernetes YAML instead of the OpenSandbox web server", it is entirely possible because of the Controller!

**1. Create a file named `aio-sandbox.yaml`:**
```yaml
apiVersion: sandbox.opensandbox.io/v1alpha1
kind: BatchSandbox
metadata:
  name: my-aio-sandbox
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: sandbox-container
        image: ghcr.io/agent-infra/sandbox:latest
```

**2. Apply it to your cluster:**
```bash
kubectl apply -f aio-sandbox.yaml
```

**3. Check on the Sandbox creation:**
```bash
kubectl get batchsandbox my-aio-sandbox -w
```
*(Once it says `ALLOCATED` and `READY`, your sandbox is successfully running! To find out the IP address, you can run `kubectl get batchsandbox my-aio-sandbox -o jsonpath='{.metadata.annotations.sandbox\.opensandbox\.io/endpoints}'`.)*

### Summary
- If you use **Method A (API route)**, the server handles mapping everything dynamically.
- If you use **Method B (Declarative route)**, you manage everything explicitly telling Kubernetes your desired state.


## 🖥️ Step 5: Viewing the Sandbox GUI in your Web Browser!

Once you have successfully provisioned your AIO sandbox (via either Method A or Method B), the sandbox container is alive, running a mini graphical web portal on port `8080` internally.

However, because that Sandbox container is trapped inside the "Kind" Kubernetes cluster network, your laptop cannot browse its internal IP! We need to punch a hole through the network exactly like we did for the Server.

**1. Find your Sandbox Pod Name:**
Run this command to find the exact name of the pod running your new sandbox.
```bash
kubectl get pods
```
*(Look for a pod with a name that looks generated, like `pod/my-aio-sandbox-xxxx` or a random `id` string generated from the API).*

**2. Create a Port-Forward to the Sandbox:**
Take the exact pod name you just copied, and map its internal port 8080 to your computer's port 8081 (since 8080 is already being used by the server).
```bash
kubectl port-forward pod/<insert-your-pod-name> 8081:8080
```

**3. Open Your Browser!**
Now, open Google Chrome, Safari, or Edge and go to this address:

👉 `http://localhost:8081`

You will immediately see the All-in-One Sandbox GUI Interface loaded right onto your screen, straight from the Kubernetes cluster!
