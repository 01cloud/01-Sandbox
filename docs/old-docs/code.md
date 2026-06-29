# Architecture & Data Flow: Intelligent Security Scanning Pipeline

This document outlines how the **OpenSandbox Security Pipeline** processes code submissions, from the initial API request to the generation of "Ultra-Strict" security reports.

## 🏗️ High-Level Architecture

The system consists of two primary layers that communicate through a shared **Persistent Volume Claim (PVC)**:

1.  **Management Layer (`opensandbox-server`)**: Handles API requests, coordinates sandbox lifecycles, and manages file persistence.
2.  **Execution Layer (`code-interpreter` Sandbox)**: An isolated container that executes the "Ultra-Strict" security toolchain.

---

## 🔄 Connectivity & Step-by-Step Flow

### 1. Request Submission (`apiServer` & `opensandbox-server`)
*   **Target Files**: `apiServer/fastapi/models.py`, `schema.py`, `lifecycle.py`.
*   **Flow**:
    1.  A user submits a `ScanJobRequest` via the Gateway (Swagger UI or `curl`).
    2.  The request can contain a structured `files` map or a simplified raw `code` string.
    3.  The **`lifecycle.py`** (The Brain) receives the request and executes **Intelligent Language Detection**.
    4.  The code is automatically saved with the correct extension (e.g., `main.py`, `main.js`) into a dedicated workspace folder on the **Shared PVC** (`/data/<job_id>/workspace`).

### 2. Sandbox Provisioning
*   **Target File**: `lifecycle.py`.
*   **Flow**:
    1.  The server creates a new sandbox using the `codeinterpreter:3.1.0` image.
    2.  It mounts the **Shared PVC** into the sandbox at two locations:
        *   `/workspace`: Contains the source code.
        *   `/reports`: Where the scan results will be saved.
    3.  Environment variables like `SCAN_TOOLS` and `SCAN_REPORT` are injected to guide the scanner.

### 3. Automated Scanning (`code-interpreter`)
*   **Target Files**: `code-interpreter.sh`, `scanner_orchestrator.py`.
*   **Flow**:
    1.  The sandbox starts and triggers **`code-interpreter.sh`**.
    2.  This script invokes the **`ScannerOrchestrator`** (The Automator).
    3.  The orchestrator identifies relevant files and triggers the **Ultra-Strict Security Toolchain**:
        *   **Semgrep**: Deep logic audit (SQLi, Command Injection).
        *   **Bandit**: Python-specific security anti-patterns.
        *   **Gitleaks**: Scanning for hardcoded secrets.
        *   **Trivy**: Configuration and dependency vulnerability checks.
    4.  All raw findings are aggregated, parsed from JSON, and unified into a single report.

### 4. Structured Reporting
*   **Target File**: `scanner_orchestrator.py`.
*   **Flow**:
    1.  The orchestrator writes the final **`security_scan_report.json`** to the `/reports` directory on the PVC.
    2.  This report includes an **Executive Summary** and a **Unified Findings** list with severities and line numbers.
    3.  Even after the sandbox terminates, this report remains accessible on the PVC for retrieval via the `GET /v1/scan-jobs/{job_id}/report` endpoint.

---

## 📂 File Connectivity Matrix

| File Path | Responsibility | Connectivity |
| :--- | :--- | :--- |
| `models.py` | API Gateway Schema | Defines what the user can send (e.g., the `code` field). |
| `lifecycle.py` | Backend Logic | Detects language, writes to PVC, and spawns the Sandbox. |
| `schema.py` | Shared Logic | Syncs the data structure between the Gateway and Backend. |
| `code-interpreter.sh` | Sandbox Entrypoint | Bridging the container start to the scanning logic. |
| `scanner_orchestrator.py`| Scan Engine | Orchestrates tools, parses JSON, and writes the final report. |

---

## ⚡ Key Logic implemented:
- **Intelligent Extension Detection**: No more manual naming. The system "reads" your code and knows if it's Python or Javascript.
- **Ultra-Strict Scanning**: Bandit and Semgrep are forced to their highest sensitivity levels to catch subtle SQL/Command injections.
- **Result-Driven Summaries**: The system determines the "Overall Status" based on actual vulnerability counts, not just tool exit codes.
