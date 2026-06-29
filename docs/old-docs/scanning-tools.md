# Security Scanning Tools

This document provides an overview of the security scanning tools integrated into the CodeInspector platform. These tools are used to automatically analyze your code and Kubernetes manifests for security vulnerabilities, misconfigurations, and best practice violations.

## Integrated Tools

The following tools are pre-installed and executed automatically during the scanning process:

### 1. Semgrep
- **Description**: Semgrep is a fast, open-source, static analysis tool for finding bugs and enforcing code standards. It supports multiple languages including Python, Go, JavaScript, and more.
- **Uses**: In CodeInspector, Semgrep is used to identify general security patterns, potential vulnerabilities, and logic errors in your source code.

### 2. Gitleaks
- **Description**: Gitleaks is a SAST tool for detecting and preventing hardcoded secrets like passwords, api keys, and tokens in git repos.
- **Uses**: It scans the provided code or files to ensure no sensitive credentials are leaked within the codebase.

### 3. Bandit
- **Description**: Bandit is a tool designed to find common security issues in Python code.
- **Uses**: For Python projects, Bandit performs a deep analysis of AST nodes to identify potential security risks such as the use of unsafe functions or weak cryptographic implementations.

### 4. Trivy
- **Description**: Trivy is a comprehensive security scanner. It can find vulnerabilities in container images, file systems, and Git repositories.
- **Uses**: Trivy is used to scan the filesystem of the workspace for known CVEs (Common Vulnerabilities and Exposures) and potential security flaws in dependencies.

### 5. Yamllint
- **Description**: Yamllint is a linter for YAML files.
- **Uses**: It ensures that all YAML configuration files (including Kubernetes manifests) follow valid syntax and formatting standards, preventing parsing errors during deployment.

### 6. Kube-linter
- **Description**: Kube-linter is a static analysis tool that checks Kubernetes YAML files and Helm charts to ensure they follow security best practices.
- **Uses**: It enforces production-readiness standards, such as ensuring containers don't run as root and have resource limits defined.

### 7. Kubeconform
- **Description**: Kubeconform is a Kubernetes manifest validator. It is a faster, more streamlined version of `kubeval`.
- **Uses**: It validates your Kubernetes manifests against official Kubernetes JSON schemas to ensure they are compatible with the target Kubernetes version.

### 8. Kube-score
- **Description**: Kube-score is a tool that performs static code analysis of your Kubernetes object definitions.
- **Uses**: It provides "scores" and recommendations for improving the security, reliability, and resilience of your Kubernetes deployments.

## How Scans are Executed

When a scan job is submitted to the CodeInspector API:
1. The source code and manifests are uploaded to an isolated sandbox environment.
2. The **Scanner Orchestrator** iterates through the enabled tools.
3. Each tool generates a machine-readable report (usually JSON).
4. Results are aggregated, deduplicated, and mapped to a unified security report format.
5. The final report is returned to the user via the API and displayed on the dashboard.

## Customizing Scans

Individual tools can be toggled or configured via the `ScanJobRequest` payload sent to the `/v1/scan-jobs` endpoint. Refer to the [User Guide](userguild.md) for more details on API usage.
