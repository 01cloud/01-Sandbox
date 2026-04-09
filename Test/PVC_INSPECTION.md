# Guide: How to Inspect Persistent Files in Kubernetes PVC

The CodeInspector system uses a **Persistent Volume Claim (PVC)** called `scan-pvc` to store all code submissions and security reports. These files are isolated by **Unique Job IDs (UUIDs)**.

Follow these steps to view the files manually inside the cluster.

## 1. Locate the OpenSandbox Server Pod
The server pod is the one that has the PVC mounted at `/data`.
```bash
kubectl get pods -n opensandbox-system -l app=opensandbox-server
```

## 2. Access the Pod's Shell
Pick the pod name from the previous step (e.g., `opensandbox-server-xyz`) and run:
```bash
# Enter the pod's terminal
kubectl exec -it <POD_NAME> -n opensandbox-system -- /bin/sh
```

## 3. Explore the Unique Job Folders
Once inside the pod, navigate to the `/data` directory:
```bash
# Go to the PVC mount point
cd /data

# See all unique Job IDs (UUIDs)
ls -l
```

## 4. Inspect a Specific Job
To see the code and reports for a specific `job_id`:
```bash
# Replace <UUID> with a folder name from the 'ls' output
ls -R <UUID>
```

### Expected Structure:
```text
<UUID>/
├── workspace/           # The original code you submitted
│   └── app.py
└── reports/             # The generated security findings
    └── security_scan_report.json
```

---

## 5. View File Contents
You can view the actual JSON report or the code right in your terminal:
```bash
cat <UUID>/reports/security_scan_report.json
```

> [!TIP]
> You don't always need to use `kubectl exec`. Since we've updated the Gateway, you can now use the specialized API to fetch these files:
> - **Report**: `GET http://localhost:8000/v1/scan-jobs/<UUID>/report`
> - **Source**: `GET http://localhost:8000/v1/scan-jobs/<UUID>/workspace/<filename>`
