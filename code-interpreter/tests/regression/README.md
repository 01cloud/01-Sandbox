# Security Scan Regression Testing Suite

A robust, containerized security regression testing suite designed to validate vulnerability scanner detections across multiple languages and verify API gateway authentication enforcement and rate limits.

---

## Architecture Overview

```
                          ┌───────────────────────────┐
                          │     Developer / CI       │
                          └─────────────┬─────────────┘
                                        │
                         Executes test_runner.py
                                        │
                                        ▼
             ┌─────────────────────────────────────────────────────┐
             │         test_runner.py (Python Test Harness)        │
             └──────────┬───────────────────────────────┬──────────┘
                        │                               │
             (Phase A: Scanner Detections)     (Phase B: API Gateway)
                        │                               │
                        ▼                               ▼
            ┌───────────────────────┐       ┌───────────────────────┐
            │   Docker CLI Engine   │       │   FastAPI Web Server  │
            │   (run --rm -v ...)   │       │ (GET/POST /v1/scan...)│
            └───────────┬───────────┘       └───────────┬───────────┘
                        │                               │
                        ▼                               ▼
            ┌───────────────────────┐       ┌───────────────────────┐
            │  code-interpreter Pod │       │   Rate-Limit / Keys   │
            │  (Unified Scanners)   │       │ (Verify 401 & 429 sfx)│
            └───────────────────────┘       └───────────────────────┘
```

---

## Execution Guidelines

The suite operates in two execution phases which can be run independently or combined:

### Phase A: Container Integrity & Scanning Verification
Runs each YAML fixture inside an ephemeral container to check that Checkov, Trivy, Polaris, Semgrep, and other tools correctly report security violations:
```bash
python3 code-interpreter/tests/regression/test_runner.py --mode container --image 01community/01sandbox-codeinterpreter:3.1.0
```

### Phase B: API Gateway Enforcement (Auth & Rate Limits)
Verifies that the FastAPI middleware correctly blocks requests with invalid keys (`401 Unauthorized`) and locks down requests exceeding the sliding-window limits (`429 Too Many Requests`):
```bash
python3 code-interpreter/tests/regression/test_runner.py --mode api --api-url http://localhost:8000 --api-key <YOUR_DEV_KEY>
```

---

## Folder Structure

```
code-interpreter/tests/regression/
├── expected/                       # Declarative Expected Assertions
│   ├── sample_01_expected.json
│   └── ...
├── fixtures/                       # Malicious Code Files to Scan
│   ├── sample_01_privileged_pod.yaml
│   └── ...
├── README.md                       # This Instruction Manual
└── test_runner.py                  # Orchestration script (python3)
```

---

## Customization & Expansion

To add a new code scanning fixture (for K8s YAML or any other programming language):
1. Create a new malicious file under `fixtures/` (e.g. `sample_11_bad_python.py`).
2. Create a corresponding expected match JSON file under `expected/` with the filename mapped and a list of expected scanner output substrings:
   ```json
   {
     "filename": "sample_11_bad_python.py",
     "expected_findings": [
       "subprocess",
       "shell=True",
       "injection"
     ]
   }
   ```
3. Run the test harness! The runner dynamically discovers, schedules, and scores all fixtures at 2-second cooldowns automatically.
