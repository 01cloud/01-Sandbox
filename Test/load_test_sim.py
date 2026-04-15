import requests
import threading
import time
import json

# ─────────────────────────────────────────────────────────────────────────────
# LOAD TEST CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
# Port 8000 is the local CodeInspector API Gateway
API_URL = "http://localhost:8000/backend/opensandbox/v1/scan-jobs"
HEADERS = {
    "Content-Type": "application/json",
    "OPEN-SANDBOX-API-KEY": "your-secure-api-key"
}
CONCURRENT_USERS = 10  # Change this to test higher or lower loads

def send_scan_request(user_id, results):
    """Simulates a single user submitting a code payload."""
    payload = {
        "files": {
            "main.py": f"print('Production Load Test - User {user_id}')"
        },
        "tools": ["semgrep", "bandit"],
        "metadata": {"user_id": str(user_id), "test": "concurrency_audit"}
    }
    
    start_time = time.time()
    try:
        # We expect a Near-Instant response (Async behavior)
        response = requests.post(API_URL, json=payload, headers=HEADERS, timeout=15.0)
        end_time = time.time()
        
        if response.status_code in (201, 202):
            results.append({
                "user_id": user_id,
                "status": "SUCCESS",
                "latency_ms": round((end_time - start_time) * 1000, 2),
                "job_id": response.json().get("job_id")
            })
        else:
            results.append({
                "user_id": user_id, 
                "status": f"FAILED ({response.status_code})", 
                "error": response.text
            })
    except Exception as e:
        results.append({"user_id": user_id, "status": "ERROR", "error": str(e)})

def run_test():
    print(f"🚀 Initializing Burst Load Test...")
    print(f"👥 Simulated Users: {CONCURRENT_USERS}")
    print(f"🔗 Target API: {API_URL}")
    print("-" * 40)
    
    threads = []
    results = []
    
    start_all = time.time()
    for i in range(CONCURRENT_USERS):
        t = threading.Thread(target=send_scan_request, args=(i, results))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    end_all = time.time()
    
    # Analysis
    successes = [r for r in results if r["status"] == "SUCCESS"]
    latencies = [r["latency_ms"] for r in successes]
    total_time = round(end_all - start_all, 2)
    
    print("\n" + "="*40)
    print("       LOAD TEST FINAL REPORT")
    print("="*40)
    print(f"Total Requests:      {CONCURRENT_USERS}")
    print(f"Success Rate:        {len(successes)}/{CONCURRENT_USERS}")
    print(f"Total Burst Time:    {total_time}s")
    
    if successes:
        print(f"Avg API Latency:    {sum(latencies)/len(latencies):.2f}ms")
        print(f"Slowest Response:    {max(latencies):.2f}ms")
    
    print("-" * 40)
    if len(successes) == CONCURRENT_USERS:
        print("✅ SUCCESS: API handled the surge without blocking!")
        print("💡 NOTE: Check 'kubectl get pods' to see processing progress.")
    else:
        print("❌ WARNING: Some requests failed. Check connectivity or logs.")

if __name__ == "__main__":
    run_test()
