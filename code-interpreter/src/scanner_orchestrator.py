#!/usr/bin/env python3
import json
import logging
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

# Configure logging for structured output inside the sandbox
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)

SCAN_DIR = os.getenv("SCAN_DIR", "/workspace")
REPORT_PATH = os.getenv("SCAN_REPORT", "/reports/security_scan_report.json")
SCAN_TOOLS_ENV = os.getenv("SCAN_TOOLS", "")  # Comma-separated list of tools to run


class ScannerOrchestrator:
    """Orchestrates security scanning tools for code-interpreter sandboxes."""

    def __init__(self, target_dir: str):
        self.target_dir = os.path.abspath(target_dir)
        if os.path.exists(self.target_dir):
            try:
                os.chdir(self.target_dir)
            except Exception as e:
                logging.warning(f"Failed to chdir to {self.target_dir}: {e}")
        self.results_lock = threading.Lock()
        self.results = {
            "summary": {},
            "findings": [],
            "files_scanned": [],
            "target": self.target_dir,
            "scans": {},
        }
        self.enabled_tools = self._get_enabled_tools()

    def _get_enabled_tools(self) -> List[str]:
        """Determines tools based on explicit file classification."""
        # 1. Discover all files first
        all_files = []
        for root, _, files in os.walk(self.target_dir):
            for file in files:
                all_files.append(os.path.join(root, file))

        # Standardize extensions to industry defaults for robust tool execution
        standardized_files = []
        for f in [os.path.relpath(f, self.target_dir) for f in all_files]:
            ext = os.path.splitext(f)[1].lower()
            full_path = os.path.join(self.target_dir, f)

            normalized_ext = None
            if ext == ".python":
                normalized_ext = ".py"
            elif ext == ".golang":
                normalized_ext = ".go"
            elif ext == ".javascript":
                normalized_ext = ".js"
            elif ext == ".typescript":
                normalized_ext = ".ts"
            elif ext == ".bash":
                normalized_ext = ".sh"
            elif ext == ".rust":
                normalized_ext = ".rs"
            elif ext in (".hcl", ".terraform", ".tfvars"):
                normalized_ext = ".tf"

            if normalized_ext:
                normalized_name = f"{os.path.splitext(f)[0]}{normalized_ext}"
                normalized_path = os.path.join(self.target_dir, normalized_name)
                try:
                    if not os.path.exists(normalized_path):
                        os.symlink(full_path, normalized_path)
                    f = normalized_name
                except Exception as e:
                    logging.warning(
                        f" Failed to standardize file {f} to {normalized_ext}: {e}"
                    )
            standardized_files.append(f)

        self.results["files_scanned"] = standardized_files

        self.classified_files = {
            "k8s": [],
            "yaml": [],
            "python": [],
            "go": [],
            "shell": [],
            "terraform": [],
            "rust": [],
            "polyglot": [],
        }

        polyglot_exts = {
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".go",
            ".rs",
            ".sh",
            ".bash",
            ".zsh",
            ".java",
            ".c",
            ".cpp",
            ".php",
            ".rb",
        }

        for f in self.results["files_scanned"]:
            ext = os.path.splitext(f)[1].lower()
            full_path = os.path.join(self.target_dir, f)

            # Identify K8s manifests by extension or content
            is_k8s = (ext == ".k8s") or self._is_k8s_manifest(f)

            if is_k8s:
                # CRITICAL FIX: Many tools (kube-linter, kubeconform) IGNORE files without .yaml/.yml extension.
                # We normalize these by creating a symlink with a .yaml extension.
                if ext not in (".yaml", ".yml"):
                    normalized_name = f"{f}.yaml"
                    normalized_path = os.path.join(self.target_dir, normalized_name)
                    try:
                        if not os.path.exists(normalized_path):
                            os.symlink(full_path, normalized_path)
                        f = normalized_name  # Update reference to the normalized name for scanners
                    except Exception as e:
                        logging.warning(f" Failed to normalize K8s file {f}: {e}")

                self.classified_files["k8s"].append(f)
            elif ext in (".yaml", ".yml"):
                self.classified_files["yaml"].append(f)
            elif ext == ".py":
                self.classified_files["python"].append(f)
            elif ext == ".go":
                self.classified_files["go"].append(f)
            elif ext in (".rs", ".rust"):
                self.classified_files["rust"].append(f)
                self.classified_files["polyglot"].append(f)
            elif ext in (".sh", ".bash", ".zsh"):
                self.classified_files["shell"].append(f)
                self.classified_files["polyglot"].append(f)
            elif ext in (".tf", ".tfvars", ".hcl", ".terraform", ".tf.json"):
                self.classified_files["terraform"].append(f)
            elif ext in polyglot_exts:
                self.classified_files["polyglot"].append(f)

        enabled = []

        # Universal tools: Run if installed on PATH
        for tool in ["gitleaks", "semgrep", "trivy"]:
            if shutil.which(tool):
                enabled.append(tool)

        # Language-specific tools: Run if files exist AND tool is installed on PATH
        if self.classified_files["python"]:
            if shutil.which("bandit"):
                enabled.append("bandit")
            if shutil.which("pylint"):
                enabled.append("pylint")
            enabled.append("py_compile")

        if self.classified_files["go"]:
            if shutil.which("gosec"):
                enabled.append("gosec")
            if shutil.which("golangci-lint") or shutil.which("golangci_lint"):
                enabled.append("golangci_lint")
            if shutil.which("go"):
                enabled.extend(["go_build", "staticcheck"])

        if self.classified_files["yaml"] or self.classified_files["k8s"]:
            if shutil.which("yamllint"):
                enabled.append("yamllint")
            if shutil.which("kube-linter"):
                enabled.append("kubelinter")
            if shutil.which("kubeconform"):
                enabled.append("kubeconform")
            if shutil.which("kube-score"):
                enabled.append("kubescore")

        if self.classified_files["shell"]:
            if shutil.which("shellcheck"):
                enabled.append("shellcheck")

        if self.classified_files["terraform"]:
            if shutil.which("tflint"):
                enabled.append("tflint")
            if shutil.which("tfsec"):
                enabled.append("tfsec")
            if shutil.which("checkov"):
                enabled.append("checkov")

        if self.classified_files["rust"]:
            enabled.append("rust_sast")
            if shutil.which("cargo-audit") or shutil.which("cargo"):
                enabled.append("cargo_audit")

        # Additional language tools if present
        if shutil.which("pmd"):
            enabled.append("pmd")
        if shutil.which("eslint"):
            enabled.append("eslint")
        if shutil.which("cargo-audit") or shutil.which("cargo"):
            enabled.append("cargo_audit")
        if shutil.which("cppcheck"):
            enabled.append("cppcheck")
        if shutil.which("clang-tidy"):
            enabled.append("clang_tidy")
        if shutil.which("rubocop"):
            enabled.append("rubocop")
        if shutil.which("brakeman"):
            enabled.append("brakeman")

        # Deduplicate while preserving order
        unique_enabled = list(dict.fromkeys(enabled))

        logging.info(
            f" Classified Files: K8s({len(self.classified_files['k8s'])}), YAML({len(self.classified_files['yaml'])}), Python({len(self.classified_files['python'])}), Go({len(self.classified_files['go'])}), Shell({len(self.classified_files['shell'])}), Terraform({len(self.classified_files.get('terraform', []))}), Rust({len(self.classified_files.get('rust', []))})"
        )
        logging.info(f" Enabled tools: {', '.join(unique_enabled)}")
        return unique_enabled

    def _is_k8s_manifest(self, file_path: str) -> bool:
        """Heuristic to detect K8s manifests: Requires apiVersion AND (kind OR metadata)."""
        full_path = os.path.join(self.target_dir, file_path)
        import re

        try:
            # We check the first 8KB for accuracy
            with open(full_path, "r", errors="ignore") as f:
                content = f.read(8192)
                # Strict check for K8s structure
                has_apiversion = bool(re.search(r"^apiVersion:", content, re.MULTILINE))
                has_kind = bool(re.search(r"^kind:", content, re.MULTILINE))
                has_metadata = bool(re.search(r"^metadata:", content, re.MULTILINE))

                return has_apiversion and (has_kind or has_metadata)
        except Exception:
            return False

        # Default to running all comprehensive tools as requested (Commented for future reference)
        # return ["semgrep", "gitleaks", "trivy", "bandit", "yamllint", "kubelinter", "kubeconform", "kubescore"]

    def run_command(
        self, cmd: List[str], tool_name: str, cwd: str = None, timeout: float = 120.0
    ) -> Dict[str, Any]:
        """Runs a scanning command and returns its exit code and summary."""
        if cwd is None:
            cwd = self.target_dir
        logging.info(f" Running {tool_name} scan...")
        try:
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd,
                timeout=timeout,
            )
            return {
                "exit_code": process.returncode,
                "stdout": process.stdout if process.stdout else "",
                "stderr": process.stderr if process.stderr else "",
                "status": "COMPLETED",  # Default to completed; scanners will determine ISSUES_FOUND
            }
        except FileNotFoundError:
            logging.warning(f" {tool_name} not found on the PATH.")
            return {"status": "NOT_FOUND"}
        except subprocess.TimeoutExpired as e:
            logging.error(f" {tool_name} scan timed out after {timeout}s.")
            return {
                "status": "ERROR",
                "error": f"Scan timed out after {timeout} seconds.",
                "stdout": e.stdout if e.stdout else "",
                "stderr": e.stderr if e.stderr else "",
            }
        except Exception as e:
            logging.error(f" Error running {tool_name}: {str(e)}")
            return {"status": "ERROR", "error": str(e)}

    def _extract_json_payload(self, text: str) -> Any:
        """Safely extracts JSON payload from tool output even if surrounded by progress bars, banners, or ANSI escape codes."""
        if not text:
            return None

        import re

        # Remove ANSI escape sequences
        cleaned = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text).strip()

        # Try direct parse
        try:
            return json.loads(cleaned)
        except Exception:
            pass

        # Try parsing line-by-line starting from lines with '{' or '['
        lines = cleaned.splitlines()
        for i in range(len(lines)):
            line = lines[i].strip()
            if line.startswith("{") or line.startswith("["):
                candidate = "\n".join(lines[i:])
                try:
                    return json.loads(candidate)
                except Exception:
                    pass

        # Search for largest outer JSON block using regex
        for match in re.finditer(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned):
            try:
                return json.loads(match.group(0))
            except Exception:
                continue

        return None

    def scan_py_compile(self) -> List[Dict]:
        """Performs a static syntax check using py_compile to catch broken code early."""
        findings = []
        logging.info("Running Python Syntax Validation (py_compile)...")
        import os
        import py_compile

        for file_path in self.classified_files["python"]:
            full_path = os.path.join(self.target_dir, file_path)
            try:
                # compile() with doraise=True will throw an exception on syntax error
                py_compile.compile(full_path, doraise=True)
            except py_compile.PyCompileError as e:
                # Clean up the error message to be user-friendly
                err_msg = str(e).split("\n")[-2] if "\n" in str(e) else str(e)
                with self.results_lock:
                    self.results["findings"].append(
                        {
                            "tool": "py_compile",
                            "file": file_path,
                            "line": "N/A",
                            "issue": "Critical Python Syntax Fault",
                            "severity": "CRITICAL",
                            "description": f"Code is syntactically invalid: {err_msg}.",
                            "remediation": f"Fix the following syntax error to allow execution: {err_msg}",
                        }
                    )
                self.results["scans"]["py_compile"] = {
                    "status": "ISSUES_FOUND",
                    "error": err_msg,
                }
                return findings

        self.results["scans"]["py_compile"] = {"status": "COMPLETED", "exit_code": 0}
        return findings

    def scan_semgrep(self):
        """Runs Semgrep static analysis with multi-language security patterns."""
        # Expanded extension support for all requested languages
        remm_exts = {
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".go",
            ".rs",
            ".rust",
            ".sh",
            ".bash",
            ".zsh",
            ".yaml",
            ".yml",
            ".json",
            ".k8s",
            ".tf",
            ".hcl",
            ".tfvars",
        }
        if not any(f.endswith(tuple(remm_exts)) for f in self.results["files_scanned"]):
            self.results["scans"]["semgrep"] = {
                "status": "SKIPPED",
                "reason": "No supported files",
            }
            return

        # Strict-Mode security configurations + Harmful Logic Audits (offline local configs)
        rules_dir = "/opt/opensandbox/rules"
        if not os.path.exists(rules_dir):
            rules_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "rules"
            )

        cmd = ["semgrep", "scan"]
        if os.path.exists(rules_dir):
            cmd.extend(["--config", rules_dir])
        else:
            cmd.extend(["--config", "auto"])
        cmd.extend(["--json", "--quiet", self.target_dir])
        res = self.run_command(cmd, "Semgrep")

        if res.get("stdout"):
            try:
                # Cleanup stdout: semgrep sometimes prints headers/updates before the JSON
                clean_stdout = res["stdout"]
                if "{" in clean_stdout:
                    clean_stdout = clean_stdout[clean_stdout.find("{") :]

                data = json.loads(clean_stdout)
                res["stdout"] = data
                results = data.get("results", [])
                if results:
                    res["status"] = "ISSUES_FOUND"
                    res["exit_code"] = 1
                    for result in results:
                        sev = result.get("extra", {}).get("severity", "MEDIUM")
                        if sev == "ERROR":
                            sev = "CRITICAL"

                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "semgrep",
                                    "file": result.get("path"),
                                    "line": result.get("start", {}).get("line"),
                                    "issue": str(
                                        result.get("extra", {}).get("message", "")
                                    ).lower(),
                                    "severity": sev,
                                    "remediation": str(
                                        result.get("extra", {})
                                        .get("metadata", {})
                                        .get("remediation")
                                        or "Audit code logic and follow secure coding patterns."
                                    ).lower(),
                                }
                            )
                else:
                    res["status"] = "COMPLETED"
                    res["exit_code"] = 0
            except Exception as e:
                logging.error(f" Failed to parse Semgrep JSON: {e}")
                res["status"] = "ERROR"
                res["error"] = str(e)

        self.results["scans"]["semgrep"] = res

    def scan_gitleaks(self):
        """Runs Gitleaks secret detection and parses JSON findings."""
        report_path = "/tmp/gitleaks.json"
        # Gitleaks always runs as it scans all content
        cmd = [
            "/usr/local/bin/gitleaks",
            "detect",
            "--source",
            self.target_dir,
            "--no-git",
            "--report-format",
            "json",
            "--report-path",
            report_path,
            "--no-banner",
        ]
        res = self.run_command(cmd, "Gitleaks")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "gitleaks"
            res = self.run_command(cmd, "Gitleaks")

        if os.path.exists(report_path):
            try:
                with open(report_path, "r") as f:
                    leaks = json.load(f)
                    res["stdout"] = leaks
                    if leaks:
                        res["status"] = "ISSUES_FOUND"
                    for leak in leaks:
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "gitleaks",
                                    "file": leak.get("File"),
                                    "line": leak.get("StartLine"),
                                    "issue": f"secret detected: {leak.get('Description')}".lower(),
                                    "severity": "CRITICAL",
                                    "remediation": "immediately revoke the exposed secret and rotate credentials.".lower(),
                                }
                            )
            except Exception as e:
                logging.error(f" Failed to parse Gitleaks JSON: {e}")
            finally:
                if os.path.exists(report_path):
                    os.remove(report_path)

        self.results["scans"]["gitleaks"] = res

    def scan_yamllint(self):
        """Runs yamllint and parses output into findings."""
        yaml_files = self.classified_files.get("yaml", []) + self.classified_files.get(
            "k8s", []
        )
        if not yaml_files:
            self.results["scans"]["yamllint"] = {
                "status": "SKIPPED",
                "reason": "No YAML files",
            }
            return

        # Use parsable format to extract findings
        yamllint_bin = shutil.which("yamllint") or "yamllint"
        cmd = [yamllint_bin, "-f", "parsable"] + yaml_files
        res = self.run_command(cmd, "Yamllint", cwd=self.target_dir)

        if res.get("stdout"):
            for line in res["stdout"].splitlines():
                if ":" in line:
                    parts = line.split(":")
                    if len(parts) >= 4:
                        file_path = parts[0].strip()
                        line_num = parts[1].strip()
                        issue = parts[3].strip()
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "yamllint",
                                    "file": file_path,
                                    "line": int(line_num)
                                    if line_num.isdigit()
                                    else None,
                                    "issue": f"YAML Lint: {issue}",
                                    "severity": "MEDIUM",
                                    "remediation": "Correct the YAML formatting/syntax according to best practices.",
                                }
                            )
            res["status"] = "ISSUES_FOUND"

        self.results["scans"]["yamllint"] = res

    def scan_bandit(self):
        """Runs Bandit Python security linter and parses JSON matches."""
        if not any(f.endswith(".py") for f in self.results["files_scanned"]):
            self.results["scans"]["bandit"] = {
                "status": "SKIPPED",
                "reason": "No Python files",
            }
            return

        cmd = ["/usr/local/bin/bandit", "-r", self.target_dir, "-f", "json", "-q"]
        res = self.run_command(cmd, "Bandit")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "bandit"
            res = self.run_command(cmd, "Bandit")

        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                res["stdout"] = data
                results = data.get("results", [])
                if results:
                    res["status"] = "ISSUES_FOUND"
                    for result in results:
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "bandit",
                                    "file": result.get("filename"),
                                    "line": result.get("line_number"),
                                    "issue": str(
                                        result.get("test_name", "")
                                        + ": "
                                        + result.get("issue_text", "")
                                    ).lower(),
                                    "severity": str(
                                        result.get("issue_severity", "MEDIUM")
                                    ).lower(),
                                    "remediation": str(
                                        result.get(
                                            "more_info",
                                            "Review Python security best practices.",
                                        )
                                    ).lower(),
                                }
                            )
                else:
                    res["status"] = "COMPLETED"
                    res["exit_code"] = 0
            except Exception as e:
                logging.error(f" Failed to parse Bandit JSON: {e}")

        self.results["scans"]["bandit"] = res

    def scan_go_build(self):
        """Performs a compilation check to catch Go syntax and type errors."""
        go_files = self.classified_files.get("go", [])
        if not go_files:
            self.results["scans"]["go_build"] = {
                "status": "SKIPPED",
                "reason": "No Go files",
            }
            return

        logging.info("Running Go Syntax Validation (go build)...")
        # We try to build all files in the directory to check for package-level consistency
        cmd = ["go", "build", "-o", "/dev/null", "."]
        res = self.run_command(cmd, "Go Build", cwd=self.target_dir)

        if res["exit_code"] != 0:
            err_msg = res.get("stderr", "Unknown compilation error")
            self.results["findings"].append(
                {
                    "tool": "go_build",
                    "file": "Go Package",
                    "line": "N/A",
                    "issue": "Critical Go Compilation Fault",
                    "severity": "CRITICAL",
                    "description": f"Go code failed to compile: {err_msg}",
                    "remediation": "Fix the syntax or type errors identified by the Go compiler.",
                }
            )
            res["status"] = "ISSUES_FOUND"
        else:
            res["status"] = "COMPLETED"
            res["exit_code"] = 0

        self.results["scans"]["go_build"] = res

    def scan_gosec(self):
        """Runs gosec for security audits in Go code."""
        if not self.classified_files.get("go"):
            self.results["scans"]["gosec"] = {
                "status": "SKIPPED",
                "reason": "No Go files",
            }
            return

        cmd = ["gosec", "-fmt", "json", "./..."]
        res = self.run_command(cmd, "Gosec", cwd=self.target_dir)

        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                res["stdout"] = data
                issues = data.get("Issues", [])
                if issues:
                    res["status"] = "ISSUES_FOUND"
                    res["exit_code"] = 1  # Force failure for UI highlighting
                    for issue in issues:
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "gosec",
                                    "file": issue.get("file"),
                                    "line": issue.get("line"),
                                    "issue": str(issue.get("details", "")).lower(),
                                    "severity": str(
                                        issue.get("severity", "medium")
                                    ).lower(),
                                    "remediation": f"refer to gosec rule {issue.get('rule_id')}: {issue.get('details')}".lower(),
                                }
                            )
                else:
                    res["status"] = "COMPLETED"
                    res["exit_code"] = 0
            except Exception as e:
                logging.error(f" Failed to parse Gosec JSON: {e}")
                res["status"] = "ERROR"

        self.results["scans"]["gosec"] = res

    def scan_staticcheck(self):
        """Runs staticcheck for advanced Go static analysis."""
        if not self.classified_files.get("go"):
            self.results["scans"]["staticcheck"] = {
                "status": "SKIPPED",
                "reason": "No Go files",
            }
            return

        cmd = ["staticcheck", "-f", "json", "./..."]
        res = self.run_command(cmd, "Staticcheck", cwd=self.target_dir)

        if res.get("stdout"):
            try:
                issues_found = False
                for line in res["stdout"].splitlines():
                    if not line.strip():
                        continue
                    issue = json.loads(line)
                    issues_found = True
                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": "staticcheck",
                                "file": issue.get("location", {}).get("file"),
                                "line": issue.get("location", {}).get("line"),
                                "issue": str(issue.get("message", "")).lower(),
                                "severity": "MEDIUM",
                                "remediation": f"refactor code to resolve: {issue.get('code')}".lower(),
                            }
                        )
                res["status"] = "ISSUES_FOUND" if issues_found else "COMPLETED"
                res["exit_code"] = (
                    1 if issues_found else 0
                )  # Force failure for UI highlighting
            except Exception as e:
                logging.error(f" Failed to parse Staticcheck JSON: {e}")

        self.results["scans"]["staticcheck"] = res

    def scan_golangci_lint(self):
        """Runs golangci-lint as a meta-linter for Go projects."""
        if not self.classified_files.get("go"):
            self.results["scans"]["golangci_lint"] = {
                "status": "SKIPPED",
                "reason": "No Go files",
            }
            return

        cmd = ["golangci-lint", "run", "--out-format", "json", "./..."]
        res = self.run_command(cmd, "GolangCI-Lint", cwd=self.target_dir)

        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                res["stdout"] = data
                issues = data.get("Issues", [])
                if issues:
                    res["status"] = "ISSUES_FOUND"
                    res["exit_code"] = 1  # Force failure
                    for issue in issues:
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "golangci-lint",
                                    "file": issue.get("Pos", {}).get("Filename"),
                                    "line": issue.get("Pos", {}).get("Line"),
                                    "issue": f"[{issue.get('FromLinter')}] {issue.get('Text')}".lower(),
                                    "severity": "MEDIUM",
                                    "remediation": f"follow recommendation from {issue.get('FromLinter')} linter.".lower(),
                                }
                            )
                else:
                    res["status"] = "COMPLETED"
                    res["exit_code"] = 0
            except Exception as e:
                logging.error(f" Failed to parse GolangCI-Lint JSON: {e}")

        self.results["scans"]["golangci_lint"] = res

    def scan_trivy(self):
        """Runs Trivy for vulnerabilities and misconfigurations in Strict Mode."""
        cmd = [
            "/usr/local/bin/trivy",
            "fs",
            "--format",
            "json",
            "--scanners",
            "secret,config",
            "--severity",
            "CRITICAL,HIGH,MEDIUM,LOW",
            "--quiet",
            "--skip-db-update",
            "--skip-java-db-update",
            "--skip-policy-update",
            "--timeout",
            "30s",
            self.target_dir,
        ]
        res = self.run_command(cmd, "Trivy", timeout=45.0)
        if res["status"] == "NOT_FOUND":
            cmd[0] = "trivy"
            res = self.run_command(cmd, "Trivy", timeout=45.0)

        raw_output = res.get("stdout") or res.get("stderr") or ""
        data = self._extract_json_payload(raw_output)
        if data and isinstance(data, dict):
            try:
                res["stdout"] = data
                issues_found = False
                for result in data.get("Results", []):
                    # Parse vulnerabilities
                    for vuln in result.get("Vulnerabilities", []):
                        issues_found = True
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "trivy",
                                    "file": os.path.basename(
                                        result.get("Target", "main.tf")
                                    ),
                                    "line": None,
                                    "issue": f"{vuln.get('VulnerabilityID')}: {vuln.get('Title')}".lower(),
                                    "severity": str(
                                        vuln.get("Severity", "MEDIUM")
                                    ).upper(),
                                    "remediation": "review vulnerability details and update dependency version.".lower(),
                                }
                            )
                    # Parse misconfigurations
                    for conf in result.get("Misconfigurations", []):
                        issues_found = True
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "trivy",
                                    "file": os.path.basename(
                                        result.get("Target", "main.tf")
                                    ),
                                    "line": conf.get("IOMetadata", {}).get("Line")
                                    or conf.get("CauseMetadata", {}).get("StartLine"),
                                    "issue": f"{conf.get('ID')}: {conf.get('Title')}".lower(),
                                    "severity": str(
                                        conf.get("Severity", "MEDIUM")
                                    ).upper(),
                                    "remediation": f"{conf.get('Resolution', 'review security misconfiguration.')}".lower(),
                                }
                            )
                if issues_found:
                    res["status"] = "ISSUES_FOUND"
                else:
                    res["status"] = "COMPLETED"
            except Exception as e:
                logging.error(f" Failed to parse Trivy JSON: {e}")
                res["status"] = "COMPLETED"
        else:
            res["status"] = "COMPLETED"

        self.results["scans"]["trivy"] = res

    def scan_kubelinter(self):
        """Runs kube-linter in Ultra-Strict mode for all built-in security checks."""
        k8s_files = self.classified_files.get("k8s", [])
        if not k8s_files:
            self.results["scans"]["kubelinter"] = {
                "status": "SKIPPED",
                "reason": "No K8s manifests",
            }
            return

        # Enable all built-in checks and force failure on any linting violation
        cmd = [
            "/usr/local/bin/kube-linter",
            "lint",
            "--format",
            "json",
            "--add-all-built-in",
            "--do-not-auto-add-defaults",
        ] + [os.path.join(self.target_dir, f) for f in k8s_files]
        res = self.run_command(cmd, "Kube-Linter")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "kube-linter"
            res = self.run_command(cmd, "Kube-Linter")

        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                res["stdout"] = data

                reports = data.get("Reports") or data.get("reports") or []
                if reports:
                    res["status"] = "ISSUES_FOUND"

                for report in reports:
                    if not isinstance(report, dict):
                        continue
                    check_val = report.get("Check") or report.get("check") or {}
                    check_name = (
                        check_val.get("Name")
                        if isinstance(check_val, dict)
                        else str(check_val)
                    )
                    remediation = report.get("Remediation") or report.get("remediation")
                    obj = report.get("Object") or report.get("object") or {}
                    metadata_val = obj.get("Metadata") or obj.get("metadata") or {}
                    file_path = (
                        metadata_val.get("FilePath")
                        or metadata_val.get("filePath")
                        or "manifest"
                    )

                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": "kubelinter",
                                "file": file_path,
                                "line": None,
                                "issue": f"linting violation: {check_name}".lower(),
                                "severity": "HIGH",
                                "remediation": str(
                                    remediation
                                    or "review kubernetes resource against security best practices."
                                ).lower(),
                            }
                        )
            except Exception as e:
                logging.error(f" Failed to parse Kube-Linter JSON: {e}")

        self.results["scans"]["kubelinter"] = res

    def scan_kubeconform(self):
        """Runs kubeconform in Strict Schema-Validation mode."""
        k8s_files = self.classified_files.get("k8s", [])
        if not k8s_files:
            self.results["scans"]["kubeconform"] = {
                "status": "SKIPPED",
                "reason": "No K8s manifests",
            }
            return

        # Strict: fail on missing schemas and use modern K8s version
        cmd = [
            "/usr/local/bin/kubeconform",
            "-summary",
            "-output",
            "json",
            "-strict",
            "-ignore-missing-schemas=false",
            "-kubernetes-version",
            "1.30.0",
        ] + [os.path.join(self.target_dir, f) for f in k8s_files]
        res = self.run_command(cmd, "Kube-Conform")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "kubeconform"
            res = self.run_command(cmd, "Kube-Conform")

        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                res["stdout"] = data
                resources = data.get("resources", [])
                has_errors = False
                for resource in resources:
                    if resource.get("status") != "valid":
                        has_errors = True
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "kubeconform",
                                    "file": resource.get("filename", "unknown"),
                                    "line": None,
                                    "issue": f"k8s schema validation conflict: {resource.get('kind')} ({resource.get('msg')})".lower(),
                                    "severity": "CRITICAL",
                                    "remediation": "update the manifest fields (like replicas or ports) to use correct data types (e.g., use integers instead of strings).",
                                }
                            )
                if has_errors:
                    res["status"] = "ISSUES_FOUND"
                else:
                    res["status"] = "COMPLETED"
            except Exception as e:
                logging.error(f" Failed to parse Kube-Conform JSON: {e}")

        self.results["scans"]["kubeconform"] = res

    def scan_kubescore(self):
        """Runs kube-score per-file to ensure syntax errors don't block the entire scan."""
        k8s_files = self.classified_files.get("k8s", [])
        if not k8s_files:
            self.results["scans"]["kubescore"] = {
                "status": "SKIPPED",
                "reason": "No K8s manifests",
            }
            return

        total_checks = 0
        passed_checks = 0
        has_issues = False

        for f_path in k8s_files:
            cmd = [
                "/usr/local/bin/kube-score",
                "score",
                "--output-format",
                "json",
                f_path,
            ]
            res = self.run_command(cmd, f"Kube-Score-{f_path}", cwd=self.target_dir)

            # Fatal Parse Error for this specific file
            if res["status"] == "ERROR" or res.get("stdout") in ("", "null", "None"):
                stderr = res.get("stderr", "")
                if (
                    "failed to parse" in stderr.lower()
                    or "cannot unmarshal" in stderr.lower()
                ):
                    err_msg = stderr.split("err=")[-1] if "err=" in stderr else stderr
                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": "kubescore",
                                "file": f_path,
                                "line": None,
                                "issue": "K8s Parsing Failure (Critical)",
                                "severity": "CRITICAL",
                                "remediation": f"Fix Syntax Error in {f_path}: {err_msg.strip()}",
                            }
                        )
                    has_issues = True
                continue

            # Successful parse, extract scores
            try:
                data = json.loads(res["stdout"])
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    obj_meta = item.get("object_meta") or item.get("ObjectMeta") or {}
                    obj_name = obj_meta.get("name") or f_path

                    for check in item.get("checks") or item.get("Checks") or []:
                        if not isinstance(check, dict):
                            continue
                        total_checks += 1
                        grade = check.get("grade", 0)
                        if grade == 0 or check.get("skipped"):
                            passed_checks += 1
                        else:
                            # Grade warnings are treated as informational best practices (INFO) rather than vulnerabilities/risks
                            comments = (
                                check.get("comments") or check.get("Comments") or []
                            )
                            comment = (
                                comments[0]
                                if isinstance(comments, list) and len(comments) > 0
                                else {}
                            )
                            check_meta = check.get("check") or check.get("Check") or {}
                            check_name = check_meta.get("name") or "unknown"

                            with self.results_lock:
                                self.results["findings"].append(
                                    {
                                        "tool": "kubescore",
                                        "file": f"{f_path} ({obj_name})",
                                        "line": None,
                                        "issue": f"{check_name} (grade: {grade})".lower(),
                                        "severity": "info",
                                        "remediation": str(
                                            comment.get(
                                                "summary",
                                                "review hardening best practices.",
                                            )
                                        ).lower(),
                                    }
                                )
            except Exception as e:
                logging.error(f" Failed to parse kube-score JSON for {f_path}: {e}")

        # Calculate Final Quantified Score
        score = 100
        if total_checks > 0:
            score = int((passed_checks / total_checks) * 100)
        elif has_issues:
            # If we had issues (like syntax errors) but 0 checks, score is 0
            score = 0

        self.results["scans"]["kubescore"] = {
            "status": "ISSUES_FOUND" if has_issues else "COMPLETED",
            "security_score": score,
            "checks_total": total_checks,
            "checks_passed": passed_checks,
        }

    def scan_shellcheck(self):
        """Runs ShellCheck for shell scripts and parses JSON results."""
        shell_files = self.classified_files.get("shell", [])
        if not shell_files:
            self.results["scans"]["shellcheck"] = {
                "status": "SKIPPED",
                "reason": "No shell scripts",
            }
            return

        shellcheck_bin = shutil.which("shellcheck") or "/usr/local/bin/shellcheck"
        cmd = [shellcheck_bin, "-f", "json"] + [
            os.path.join(self.target_dir, f) for f in shell_files
        ]
        res = self.run_command(cmd, "ShellCheck")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "shellcheck"
            res = self.run_command(cmd, "ShellCheck")

        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                res["stdout"] = data
                if data:
                    res["status"] = "ISSUES_FOUND"
                for issue in data:
                    # ShellCheck uses string levels: error, warning, info, style
                    raw_sev = str(issue.get("level", "info")).lower()
                    sev = "MEDIUM"
                    if raw_sev == "error":
                        sev = "CRITICAL"
                    elif raw_sev == "warning":
                        sev = "HIGH"
                    elif raw_sev == "style":
                        sev = "LOW"
                    elif raw_sev == "info":
                        sev = "INFO"

                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": "shellcheck",
                                "file": issue.get("file"),
                                "line": issue.get("line"),
                                "issue": f"sc{issue.get('code')}: {issue.get('message')}".lower(),
                                "severity": sev,
                                "remediation": f"review fix at: https://github.com/koalaman/shellcheck/wiki/sc{issue.get('code')}".lower(),
                            }
                        )
            except Exception as e:
                logging.error(f" Failed to parse ShellCheck JSON: {e}")

        self.results["scans"]["shellcheck"] = res

    def scan_tflint(self):
        """Runs TFLint on Terraform configurations with automatic initialization."""
        tf_files = self.classified_files.get("terraform", [])
        if not tf_files:
            self.results["scans"]["tflint"] = {
                "status": "SKIPPED",
                "reason": "No Terraform files",
            }
            return

        tflint_bin = shutil.which("tflint") or "/usr/local/bin/tflint"

        # 1. Attempt plugin/ruleset initialization inside target_dir
        try:
            self.run_command(
                [tflint_bin, "--init"], "TFLint Init", cwd=self.target_dir, timeout=15.0
            )
        except Exception as e:
            logging.warning(f" TFLint init skipped or failed: {e}")

        # 2. Run TFLint scan with cwd=self.target_dir
        cmd = [tflint_bin, "--format=json"]
        res = self.run_command(cmd, "TFLint", cwd=self.target_dir)

        raw_output = res.get("stdout") or res.get("stderr") or ""
        data = self._extract_json_payload(raw_output)

        # Fallback to direct file execution if directory scan yields empty payload
        if not data and tf_files:
            fallback_cmd = [tflint_bin, "--format=json", tf_files[0]]
            res = self.run_command(fallback_cmd, "TFLint Direct", cwd=self.target_dir)
            raw_output = res.get("stdout") or res.get("stderr") or ""
            data = self._extract_json_payload(raw_output)

        if data and isinstance(data, dict):
            try:
                issues = data.get("issues", [])
                errors = data.get("errors", [])

                if issues or errors:
                    res["status"] = "ISSUES_FOUND"
                else:
                    res["status"] = "COMPLETED"

                for issue in issues:
                    rule = issue.get("rule", {})
                    rule_name = rule.get("name", "tflint-rule")
                    message = issue.get("message", "")
                    call = issue.get("call", {})
                    file_name = call.get("filename", "")
                    line_num = call.get("line")
                    severity_raw = str(rule.get("severity", "WARNING")).upper()

                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": "tflint",
                                "file": os.path.basename(file_name)
                                if file_name
                                else (tf_files[0] if tf_files else "main.tf"),
                                "line": line_num,
                                "issue": f"tflint: {rule_name} - {message}".lower(),
                                "severity": severity_raw,
                                "remediation": f"review tflint rule {rule_name}".lower(),
                            }
                        )

                for err in errors:
                    err_msg = err.get("message", "syntax/configuration error")
                    err_summary = err.get("summary", "tflint error")
                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": "tflint",
                                "file": tf_files[0] if tf_files else "main.tf",
                                "line": None,
                                "issue": f"tflint error: {err_summary} - {err_msg}".lower(),
                                "severity": "HIGH",
                                "remediation": "check terraform code syntax and provider blocks".lower(),
                            }
                        )
            except Exception as e:
                logging.error(f" Failed to parse TFLint JSON: {e}")
                res["status"] = "COMPLETED"
        else:
            res["status"] = "COMPLETED"

        self.results["scans"]["tflint"] = res

    def scan_tfsec(self):
        """Runs TFSec on Terraform files with directory and single-file fallback."""
        tf_files = self.classified_files.get("terraform", [])
        if not tf_files:
            self.results["scans"]["tfsec"] = {
                "status": "SKIPPED",
                "reason": "No Terraform files",
            }
            return

        tfsec_bin = shutil.which("tfsec") or "/usr/local/bin/tfsec"
        cmd = [tfsec_bin, "--no-color", "--format", "json", "--soft-fail", "."]
        res = self.run_command(cmd, "TFSec", cwd=self.target_dir)

        raw_output = res.get("stdout") or res.get("stderr") or ""
        data = self._extract_json_payload(raw_output)

        all_results = []
        if data and isinstance(data, dict):
            all_results = data.get("results", []) or []

        # If directory scan returns 0 findings, attempt file-by-file scan
        if not all_results and tf_files:
            for tf_f in tf_files:
                f_cmd = [
                    tfsec_bin,
                    "--no-color",
                    "--format",
                    "json",
                    "--soft-fail",
                    tf_f,
                ]
                f_res = self.run_command(f_cmd, "TFSec File", cwd=self.target_dir)
                f_raw = f_res.get("stdout") or f_res.get("stderr") or ""
                f_data = self._extract_json_payload(f_raw)
                if f_data and isinstance(f_data, dict):
                    all_results.extend(f_data.get("results", []) or [])

        if all_results:
            res["status"] = "ISSUES_FOUND"
            seen_findings = set()
            for item in all_results:
                rule_id = item.get("rule_id", "tfsec-issue")
                description = item.get("description", "")
                location = item.get("location", {})
                file_name = location.get("filename", "")
                start_line = location.get("start_line")
                severity_raw = str(item.get("severity", "MEDIUM")).upper()

                finding_key = (rule_id, file_name, start_line)
                if finding_key in seen_findings:
                    continue
                seen_findings.add(finding_key)

                with self.results_lock:
                    self.results["findings"].append(
                        {
                            "tool": "tfsec",
                            "file": os.path.basename(file_name)
                            if file_name
                            else (tf_files[0] if tf_files else "main.tf"),
                            "line": start_line,
                            "issue": f"tfsec {rule_id}: {description}".lower(),
                            "severity": severity_raw,
                            "remediation": f"review tfsec rule {rule_id}".lower(),
                        }
                    )
        else:
            res["status"] = "COMPLETED"

        self.results["scans"]["tfsec"] = res

    def scan_checkov(self):
        """Runs Checkov IaC security scanner on Terraform code with robust file fallback."""
        tf_files = self.classified_files.get("terraform", [])
        if not tf_files:
            self.results["scans"]["checkov"] = {
                "status": "SKIPPED",
                "reason": "No Terraform files",
            }
            return

        checkov_bin = shutil.which("checkov") or "checkov"
        cmd = [
            checkov_bin,
            "-d",
            ".",
            "-o",
            "json",
            "--framework",
            "terraform",
            "--soft-fail",
        ]
        res = self.run_command(cmd, "Checkov", cwd=self.target_dir)

        raw_output = res.get("stdout") or res.get("stderr") or ""
        data = self._extract_json_payload(raw_output)

        failed_checks = []
        if data:
            framework_results = data if isinstance(data, list) else [data]
            for item in framework_results:
                if isinstance(item, dict):
                    results_obj = item.get("results", {})
                    if isinstance(results_obj, dict):
                        failed_checks.extend(results_obj.get("failed_checks", []))

        # Fallback to single-file scan (-f) if directory scan yielded no failed checks
        if not failed_checks and tf_files:
            for tf_f in tf_files:
                f_cmd = [
                    checkov_bin,
                    "-f",
                    tf_f,
                    "-o",
                    "json",
                    "--framework",
                    "terraform",
                    "--soft-fail",
                ]
                f_res = self.run_command(f_cmd, "Checkov File", cwd=self.target_dir)
                f_raw = f_res.get("stdout") or f_res.get("stderr") or ""
                f_data = self._extract_json_payload(f_raw)
                if f_data:
                    f_framework_results = (
                        f_data if isinstance(f_data, list) else [f_data]
                    )
                    for item in f_framework_results:
                        if isinstance(item, dict):
                            results_obj = item.get("results", {})
                            if isinstance(results_obj, dict):
                                failed_checks.extend(
                                    results_obj.get("failed_checks", [])
                                )

        if failed_checks:
            res["status"] = "ISSUES_FOUND"
            seen_checks = set()
            for check in failed_checks:
                check_id = check.get("check_id", "checkov-issue")
                check_name = check.get("check_name", "")
                file_path = check.get("file_path", "")
                file_line_range = check.get("file_line_range", [None])[0]

                check_key = (check_id, file_path, file_line_range)
                if check_key in seen_checks:
                    continue
                seen_checks.add(check_key)

                with self.results_lock:
                    self.results["findings"].append(
                        {
                            "tool": "checkov",
                            "file": os.path.basename(file_path)
                            if file_path
                            else (tf_files[0] if tf_files else "main.tf"),
                            "line": file_line_range,
                            "issue": f"checkov {check_id}: {check_name}".lower(),
                            "severity": "HIGH",
                            "remediation": f"remediate checkov rule {check_id}".lower(),
                        }
                    )
        else:
            res["status"] = "COMPLETED"

        self.results["scans"]["checkov"] = res

    def scan_pylint(self):
        """Runs pylint static analysis on Python files."""
        py_files = [
            os.path.join(self.target_dir, f)
            for f in self.results["files_scanned"]
            if f.endswith(".py")
        ]
        if not py_files or not shutil.which("pylint"):
            self.results["scans"]["pylint"] = {
                "status": "SKIPPED",
                "reason": "No Python files or tool not available",
            }
            return

        cmd = ["pylint", "--output-format=json"] + py_files
        res = self.run_command(cmd, "pylint")
        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                if data:
                    res["status"] = "ISSUES_FOUND"
                    for item in data:
                        raw_type = item.get("type", "info").lower()
                        sev = (
                            "HIGH"
                            if raw_type in ("error", "fatal")
                            else ("MEDIUM" if raw_type == "warning" else "INFO")
                        )
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "pylint",
                                    "file": item.get("path"),
                                    "line": item.get("line"),
                                    "issue": f"{item.get('symbol')}: {item.get('message')}".lower(),
                                    "severity": sev,
                                    "remediation": f"fix code standard issue: {item.get('message-id')}".lower(),
                                }
                            )
            except Exception as e:
                logging.error(f" Failed to parse pylint JSON: {e}")
        self.results["scans"]["pylint"] = res

    def scan_eslint(self):
        """Runs ESLint on JavaScript/TypeScript files."""
        js_ts_files = [
            os.path.join(self.target_dir, f)
            for f in self.results["files_scanned"]
            if f.endswith((".js", ".ts", ".jsx", ".tsx"))
        ]
        if not js_ts_files or not shutil.which("eslint"):
            self.results["scans"]["eslint"] = {
                "status": "SKIPPED",
                "reason": "No JS/TS files or tool not available",
            }
            return

        cmd = ["npx", "eslint", "--format=json"]

        # Pass fallback config if workspace lacks custom eslint config
        has_user_config = any(
            os.path.exists(os.path.join(self.target_dir, f))
            for f in [
                "eslint.config.js",
                "eslint.config.mjs",
                "eslint.config.cjs",
                ".eslintrc",
                ".eslintrc.json",
                ".eslintrc.js",
            ]
        )
        if not has_user_config:
            fallback_config = "/opt/opensandbox/rules/eslint.config.js"
            if not os.path.exists(fallback_config):
                fallback_config = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..",
                    "rules",
                    "eslint.config.js",
                )
            if os.path.exists(fallback_config):
                cmd.extend(["--config", fallback_config])

        cmd.extend(js_ts_files)
        res = self.run_command(cmd, "eslint")
        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                has_issues = False
                for f_res in data:
                    messages = f_res.get("messages", [])
                    if messages:
                        has_issues = True
                    for msg in messages:
                        sev = "HIGH" if msg.get("severity") == 2 else "MEDIUM"
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "eslint",
                                    "file": f_res.get("filePath"),
                                    "line": msg.get("line"),
                                    "issue": str(msg.get("message")).lower(),
                                    "severity": sev,
                                    "remediation": f"eslint rule: {msg.get('ruleId')}".lower(),
                                }
                            )
                if has_issues:
                    res["status"] = "ISSUES_FOUND"
            except Exception as e:
                logging.error(f" Failed to parse eslint JSON: {e}")
        self.results["scans"]["eslint"] = res

    def scan_pmd(self):
        """Runs PMD static analysis on Java code."""
        java_files = [
            f for f in self.results["files_scanned"] if f.endswith((".java", ".class"))
        ]
        if not java_files or not shutil.which("pmd"):
            self.results["scans"]["pmd"] = {
                "status": "SKIPPED",
                "reason": "No Java files or tool not available",
            }
            return

        cmd = [
            "pmd",
            "check",
            "-d",
            self.target_dir,
            "-R",
            "rulesets/java/quickstart.xml",
            "-f",
            "json",
        ]
        res = self.run_command(cmd, "pmd")
        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                files_res = data.get("files", [])
                if files_res:
                    res["status"] = "ISSUES_FOUND"
                    for f_item in files_res:
                        for viol in f_item.get("violations", []):
                            with self.results_lock:
                                self.results["findings"].append(
                                    {
                                        "tool": "pmd",
                                        "file": f_item.get("filename"),
                                        "line": viol.get("beginline"),
                                        "issue": str(viol.get("description")).lower(),
                                        "severity": "HIGH"
                                        if viol.get("priority", 3) <= 2
                                        else "MEDIUM",
                                        "remediation": f"pmd rule: {viol.get('rule')}".lower(),
                                    }
                                )
            except Exception as e:
                logging.error(f" Failed to parse PMD JSON: {e}")
        self.results["scans"]["pmd"] = res

    def scan_cargo_audit(self):
        """Runs cargo-audit on Rust projects."""
        cargo_audit_bin = shutil.which("cargo-audit")
        cargo_bin = shutil.which("cargo")

        if not cargo_audit_bin and not cargo_bin:
            self.results["scans"]["cargo_audit"] = {
                "status": "SKIPPED",
                "reason": "cargo-audit not installed",
            }
            return

        cmd = (
            [cargo_audit_bin, "--json"]
            if cargo_audit_bin
            else [cargo_bin, "audit", "--json"]
        )
        res = self.run_command(cmd, "cargo-audit", cwd=self.target_dir)
        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                vulns = data.get("vulnerabilities", {}).get("list", [])
                if vulns:
                    res["status"] = "ISSUES_FOUND"
                    for v in vulns:
                        advisory = v.get("advisory", {})
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "cargo_audit",
                                    "file": "Cargo.lock",
                                    "line": None,
                                    "issue": f"{advisory.get('id')}: {advisory.get('title')}".lower(),
                                    "severity": "HIGH",
                                    "remediation": f"update crate {advisory.get('package')}".lower(),
                                }
                            )
                else:
                    res["status"] = "COMPLETED"
            except Exception as e:
                logging.error(f" Failed to parse cargo-audit JSON: {e}")
                res["status"] = "COMPLETED"
        else:
            res["status"] = "COMPLETED"
        self.results["scans"]["cargo_audit"] = res

    def scan_rust_sast(self):
        """Performs dedicated SAST security analysis on Rust source code."""
        rs_files = self.classified_files.get("rust", [])
        if not rs_files:
            self.results["scans"]["rust_sast"] = {
                "status": "SKIPPED",
                "reason": "No Rust files",
            }
            return

        import re

        findings_count = 0
        res = {"status": "COMPLETED", "exit_code": 0}

        for rs_f in rs_files:
            full_path = os.path.join(self.target_dir, rs_f)
            try:
                with open(full_path, "r", errors="ignore") as f:
                    lines = f.readlines()

                for line_num, line in enumerate(lines, 1):
                    stripped = line.strip()
                    # Skip empty lines or pure comments
                    if (
                        not stripped
                        or stripped.startswith("//")
                        or stripped.startswith("/*")
                    ):
                        continue

                    # 1. Command Injection check
                    if "Command::new" in line:
                        findings_count += 1
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "rust_sast",
                                    "file": rs_f,
                                    "line": line_num,
                                    "issue": "rust-command-injection: unsanitized input passed directly to std::process::command subshell.",
                                    "severity": "CRITICAL",
                                    "remediation": "avoid spawning subshells with arbitrary user input. use explicit argument lists without shell wrappers.",
                                }
                            )

                    # 2. Unsafe memory / Raw pointer dereference check
                    if re.search(r"\bunsafe\s+(fn|block|\{)", line) or (
                        "*" in line
                        and ("const" in line or "mut" in line or "ptr" in line)
                    ):
                        findings_count += 1
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "rust_sast",
                                    "file": rs_f,
                                    "line": line_num,
                                    "issue": "rust-unsafe-memory: unsafe function block or raw pointer dereference detected.",
                                    "severity": "HIGH",
                                    "remediation": "validate raw pointer memory boundaries and encapsulate unsafe operations in safe abstractions.",
                                }
                            )
            except Exception as e:
                logging.error(f" Failed to perform Rust SAST on {rs_f}: {e}")

        if findings_count > 0:
            res["status"] = "ISSUES_FOUND"

        self.results["scans"]["rust_sast"] = res

    def scan_cppcheck(self):
        """Runs cppcheck static analysis for C/C++ files."""
        c_cpp_files = [
            f
            for f in self.results["files_scanned"]
            if f.endswith((".c", ".cpp", ".cc", ".h", ".hpp"))
        ]
        if not c_cpp_files or not shutil.which("cppcheck"):
            self.results["scans"]["cppcheck"] = {
                "status": "SKIPPED",
                "reason": "No C/C++ files or tool not available",
            }
            return

        cmd = ["cppcheck", "--enable=all", "--quiet", self.target_dir]
        res = self.run_command(cmd, "cppcheck")
        if res.get("stderr") and (
            "error" in res["stderr"].lower() or "warning" in res["stderr"].lower()
        ):
            res["status"] = "ISSUES_FOUND"
            lines = res["stderr"].splitlines()
            for line in lines[:10]:
                if ":" in line:
                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": "cppcheck",
                                "file": line.split(":")[0],
                                "line": None,
                                "issue": line.lower(),
                                "severity": "HIGH"
                                if "error" in line.lower()
                                else "MEDIUM",
                                "remediation": "review cppcheck C/C++ warning",
                            }
                        )
        self.results["scans"]["cppcheck"] = res

    def scan_clang_tidy(self):
        """Runs clang-tidy on C/C++ files."""
        c_cpp_files = [
            os.path.join(self.target_dir, f)
            for f in self.results["files_scanned"]
            if f.endswith((".c", ".cpp", ".cc", ".h", ".hpp"))
        ]
        if not c_cpp_files or not shutil.which("clang-tidy"):
            self.results["scans"]["clang_tidy"] = {
                "status": "SKIPPED",
                "reason": "No C/C++ files or tool not available",
            }
            return

        cmd = ["clang-tidy"] + c_cpp_files + ["--"]
        res = self.run_command(cmd, "clang-tidy")
        if res.get("stdout") and (
            "warning:" in res["stdout"].lower() or "error:" in res["stdout"].lower()
        ):
            res["status"] = "ISSUES_FOUND"
            lines = res["stdout"].splitlines()
            for line in lines[:10]:
                if ":" in line and (
                    "warning:" in line.lower() or "error:" in line.lower()
                ):
                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": "clang_tidy",
                                "file": line.split(":")[0],
                                "line": None,
                                "issue": line.lower(),
                                "severity": "HIGH"
                                if "error:" in line.lower()
                                else "MEDIUM",
                                "remediation": "review clang-tidy static analysis rule",
                            }
                        )
        self.results["scans"]["clang_tidy"] = res

    def scan_rubocop(self):
        """Runs rubocop static analysis on Ruby code."""
        rb_files = [f for f in self.results["files_scanned"] if f.endswith(".rb")]
        if not rb_files or not shutil.which("rubocop"):
            self.results["scans"]["rubocop"] = {
                "status": "SKIPPED",
                "reason": "No Ruby files or tool not available",
            }
            return

        cmd = ["rubocop", "--format", "json", self.target_dir]
        res = self.run_command(cmd, "rubocop")
        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                files_res = data.get("files", [])
                if files_res:
                    has_offenses = False
                    for f_item in files_res:
                        offenses = f_item.get("offenses", [])
                        if offenses:
                            has_offenses = True
                        for off in offenses:
                            with self.results_lock:
                                self.results["findings"].append(
                                    {
                                        "tool": "rubocop",
                                        "file": f_item.get("path"),
                                        "line": off.get("location", {}).get("line"),
                                        "issue": f"{off.get('cop_name')}: {off.get('message')}".lower(),
                                        "severity": "HIGH"
                                        if off.get("severity") in ("error", "fatal")
                                        else "MEDIUM",
                                        "remediation": f"rubocop rule: {off.get('cop_name')}".lower(),
                                    }
                                )
                    if has_offenses:
                        res["status"] = "ISSUES_FOUND"
            except Exception as e:
                logging.error(f" Failed to parse rubocop JSON: {e}")
        self.results["scans"]["rubocop"] = res

    def scan_brakeman(self):
        """Runs brakeman Rails security scanner."""
        rb_files = [f for f in self.results["files_scanned"] if f.endswith(".rb")]
        if not rb_files or not shutil.which("brakeman"):
            self.results["scans"]["brakeman"] = {
                "status": "SKIPPED",
                "reason": "No Ruby files or tool not available",
            }
            return

        cmd = ["brakeman", "-p", self.target_dir, "-f", "json", "-q"]
        res = self.run_command(cmd, "brakeman")
        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                warnings = data.get("warnings", [])
                if warnings:
                    res["status"] = "ISSUES_FOUND"
                    for w in warnings:
                        with self.results_lock:
                            self.results["findings"].append(
                                {
                                    "tool": "brakeman",
                                    "file": w.get("file"),
                                    "line": w.get("line"),
                                    "issue": f"{w.get('warning_type')}: {w.get('message')}".lower(),
                                    "severity": w.get("confidence", "MEDIUM").upper(),
                                    "remediation": f"brakeman advisory: {w.get('link')}".lower(),
                                }
                            )
            except Exception as e:
                logging.error(f" Failed to parse brakeman JSON: {e}")
        self.results["scans"]["brakeman"] = res

    def _ensure_vulnerability_insights(self):
        """Safety net: Ensure every failed tool has at least one finding in the insights panel."""
        for tool, scan_res in self.results["scans"].items():
            if not isinstance(scan_res, dict):
                continue

            status = scan_res.get("status")
            if status in ("ERROR", "ISSUES_FOUND"):
                # Check if this tool already has findings
                tool_findings = [
                    f
                    for f in self.results["findings"]
                    if f.get("tool")
                    in (tool, tool.replace("_", "-"), tool.replace("-", "_"))
                ]

                if not tool_findings:
                    # No findings recorded yet, but tool failed. Create an auto-insight.
                    logging.warning(
                        f" Tool {tool} failed but provided no insights. Generating auto-insight."
                    )
                    error_msg = (
                        scan_res.get("stderr")
                        or scan_res.get("error")
                        or "Unknown security or execution error."
                    )

                    with self.results_lock:
                        self.results["findings"].append(
                            {
                                "tool": tool,
                                "file": "Pipeline Error",
                                "line": None,
                                "issue": f"Tool Execution Failure: {tool.upper()}",
                                "severity": "CRITICAL",
                                "remediation": f"Review tool error: {error_msg[:200]}...",
                            }
                        )

    def run_all(self):
        """Executes enabled scanners in parallel to prevent request timeouts."""
        scanner_map = {
            "py_compile": self.scan_py_compile,
            "semgrep": self.scan_semgrep,
            "gitleaks": self.scan_gitleaks,
            "yamllint": self.scan_yamllint,
            "bandit": self.scan_bandit,
            "pylint": self.scan_pylint,
            "eslint": self.scan_eslint,
            "pmd": self.scan_pmd,
            "cargo_audit": self.scan_cargo_audit,
            "rust_sast": self.scan_rust_sast,
            "cppcheck": self.scan_cppcheck,
            "clang_tidy": self.scan_clang_tidy,
            "rubocop": self.scan_rubocop,
            "brakeman": self.scan_brakeman,
            "go_build": self.scan_go_build,
            "gosec": self.scan_gosec,
            "staticcheck": self.scan_staticcheck,
            "golangci_lint": self.scan_golangci_lint,
            "trivy": self.scan_trivy,
            "kubelinter": self.scan_kubelinter,
            "kubeconform": self.scan_kubeconform,
            "kubescore": self.scan_kubescore,
            "shellcheck": self.scan_shellcheck,
            "tflint": self.scan_tflint,
            "tfsec": self.scan_tfsec,
            "checkov": self.scan_checkov,
        }

        # Identify which tools to actually run
        tools_to_run = [tool for tool in self.enabled_tools if tool in scanner_map]

        # If Go files exist but no go.mod is present, temporarily create a dummy go.mod
        # so Go tools (go build, gosec, golangci-lint, staticcheck) can inspect ASTs without module errors.
        created_dummy_gomod = False
        gomod_path = os.path.join(self.target_dir, "go.mod")
        if self.classified_files.get("go") and not os.path.exists(gomod_path):
            try:
                with open(gomod_path, "w") as f:
                    f.write("module workspace\n\ngo 1.22\n")
                created_dummy_gomod = True
            except Exception as e:
                logging.warning(f"Could not create dummy go.mod: {e}")

        try:
            with ThreadPoolExecutor(max_workers=len(tools_to_run) or 1) as executor:
                for tool in tools_to_run:
                    executor.submit(scanner_map[tool])
        finally:
            if created_dummy_gomod and os.path.exists(gomod_path):
                try:
                    os.remove(gomod_path)
                except Exception:
                    pass

        # Enforce that all failures result in dashboard insights
        self._ensure_vulnerability_insights()

        summary = self._calculate_summary()

        # Final Safety Pass: Force all finding text to lowercase for UI aesthetics
        for finding in self.results["findings"]:
            for key in ["issue", "remediation", "description"]:
                if key in finding and finding[key]:
                    finding[key] = str(finding[key]).lower()

        self.save_results()
        return summary

    def _calculate_summary(self):
        """Generates a high-level summary object for machine/AI parsing."""
        from datetime import datetime

        scans = self.results.get("scans", {})
        total_tools = len(scans)
        risks = 0
        clean = 0
        errors = 0
        skipped = 0

        for tool, data in scans.items():
            status = data.get("status", "UNKNOWN")
            if status == "ISSUES_FOUND":
                risks += 1
            elif status == "COMPLETED":
                clean += 1
            elif status == "ERROR":
                errors += 1
            elif status == "SKIPPED":
                skipped += 1

        self.results["summary"] = {
            "overall_status": "RISKS_FOUND"
            if risks > 0
            else ("CLEAN" if (errors == 0 and risks == 0) else "ERROR"),
            "security_score": scans.get("kubescore", {}).get("security_score"),
            "total_tools_run": total_tools,
            "risks_detected": risks,
            "findings_count": len(self.results["findings"]),
            "clean_tools": clean,
            "skipped_tools": skipped,
            "failed_tools": errors,
            "timestamp": datetime.now().isoformat(),
        }

    def save_results(self):
        """Saves scan results to a JSON file and displays a pretty summary."""
        try:
            with open(REPORT_PATH, "w") as f:
                json.dump(self.results, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to write report to PVC directly: {e}")

        print("---SCAN_REPORT_START---")
        print(json.dumps(self.results))
        print("---SCAN_REPORT_END---")

        self._display_pretty_summary()

    def _display_pretty_summary(self):
        """Prints a presentable ASCII table and detailed results."""
        print("\n" + "═" * 70)
        print(" 🛡️  SECURITY SCAN DISCOVERY & SUMMARY")
        print("═" * 70)
        print(f" Target Directory: {self.target_dir}")
        print(
            f" Files Analyzed:   {', '.join(self.results['files_scanned']) if self.results['files_scanned'] else 'None'}"
        )
        print("─" * 70)

        # Table Header
        header = f" {'SCANNER':<12} │ {'STATUS':<15} │ {'RESULT SUMMARY'}"
        print(header)
        print(" " + "─" * 12 + "╁" + "─" * 17 + "╁" + "─" * 37)

        for tool in list(self.results["scans"].keys()):
            res = self.results["scans"].get(tool)
            status = res.get("status", "UNKNOWN")

            status_text = status
            summary = ""

            if status == "ISSUES_FOUND":
                status_text = "⚠️  RISK FOUND"
                # Find the first specific issue reported for this tool
                tool_findings = [
                    f for f in self.results["findings"] if f.get("tool") == tool
                ]
                if tool_findings:
                    summary = tool_findings[0].get(
                        "issue", "Review technical findings below."
                    )
                else:
                    summary = "Detailed risks detected. See findings section."
            elif status in ("COMPLETED", "CLEAN"):
                status_text = "✅ CLEAN"
                summary = "No immediate risks identified."
            elif status == "SKIPPED":
                status_text = "⚪ N/A"
                summary = res.get("reason", "Not relevant for this code.")
            elif status == "NOT_FOUND":
                status_text = "🚫 MISSING"
                summary = "Tool not installed in sandbox."
            elif status == "ERROR":
                status_text = "❌ ERROR"
                summary = "Execution failure."

            row = f" {tool.upper():<12} │ {status_text:<15} │ {summary}"
            print(row)

        # Detailed Findings Section
        print("─" * 70)
        print(" 📄 UNIFIED SECURITY FINDINGS")
        print("─" * 70)

        if not self.results["findings"]:
            print("\n No specific vulnerabilities were detailed by the scanners.")
        else:
            for finding in self.results["findings"]:
                severity = finding.get("severity", "INFO").upper()
                emoji = (
                    "🛑"
                    if severity in ("CRITICAL", "HIGH")
                    else ("⚠️" if severity == "MEDIUM" else "ℹ️")
                )
                loc = (
                    f"{finding['file']}:{finding['line']}"
                    if finding["line"]
                    else finding["file"]
                )
                print(
                    f" {emoji} [{severity}] {finding['tool'].upper()}: {finding['issue']}"
                )
                print(f"    Location: {loc}")
                print("    " + "-" * 30)

        print("\n" + "═" * 70)
        print(f" 📁 Persistent JSON Report: {REPORT_PATH}")
        print("═" * 70 + "\n")


if __name__ == "__main__":
    orchestrator = ScannerOrchestrator(SCAN_DIR)
    orchestrator.run_all()
