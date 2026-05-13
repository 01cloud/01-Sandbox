#!/usr/bin/env python3
import subprocess
import os
import json
import logging
from typing import List, Dict, Any

# Configure logging for structured output inside the sandbox
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)

SCAN_DIR = os.getenv("SCAN_DIR", "/workspace")
REPORT_PATH = os.getenv("SCAN_REPORT", "/reports/security_scan_report.json")
SCAN_TOOLS_ENV = os.getenv("SCAN_TOOLS", "") # Comma-separated list of tools to run

class ScannerOrchestrator:
    """Orchestrates security scanning tools for code-interpreter sandboxes."""

    def __init__(self, target_dir: str):
        self.target_dir = target_dir
        self.results = {
            "summary": {},
            "findings": [],
            "files_scanned": [],
            "target": target_dir,
            "scans": {}
        }
        self.enabled_tools = self._get_enabled_tools()

    def _get_enabled_tools(self) -> List[str]:
        """Determines tools based on explicit file classification."""
        # 1. Discover all files first
        all_files = []
        for root, _, files in os.walk(self.target_dir):
            for file in files:
                all_files.append(os.path.join(root, file))
        
        self.results["files_scanned"] = [os.path.relpath(f, self.target_dir) for f in all_files]

        self.classified_files = {
            "k8s": [],
            "yaml": [],
            "python": [],
            "go": [],
            "shell": [],
            "polyglot": []
        }

        polyglot_exts = {".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".c", ".cpp", ".php", ".rb"}

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
                        f = normalized_name # Update reference to the normalized name for scanners
                    except Exception as e:
                        logging.warning(f" Failed to normalize K8s file {f}: {e}")
                
                self.classified_files["k8s"].append(f)
            elif ext in (".yaml", ".yml"):
                self.classified_files["yaml"].append(f)
            elif ext == ".py":
                self.classified_files["python"].append(f)
            elif ext == ".go":
                self.classified_files["go"].append(f)
            elif ext in (".sh", ".bash"):
                self.classified_files["shell"].append(f)
            elif ext in polyglot_exts:
                self.classified_files["polyglot"].append(f)

        # Build enabled tools list (Universal security baseline)
        enabled = ["gitleaks", "semgrep"]
        
        if self.classified_files["python"]:
            # Python: bandit + syntax check + universal tools
            enabled.extend(["bandit", "py_compile"])
        
        if self.classified_files["go"]:
            # Go: gosec + staticcheck + go_build
            enabled.extend(["go_build", "gosec", "staticcheck"])
        
        if self.classified_files["yaml"]:
            # General YAML: yamllint + universal tools
            enabled.append("yamllint")
            
        if self.classified_files["k8s"]:
            # K8s YAML: Kube suite + universal tools
            enabled.extend(["kubelinter", "kubeconform", "kubescore"])
            
        if self.classified_files["shell"]:
            # Shell: shellcheck + universal tools
            enabled.append("shellcheck")

        logging.info(f" Classified Files: K8s({len(self.classified_files['k8s'])}), YAML({len(self.classified_files['yaml'])}), Python({len(self.classified_files['python'])}), Shell({len(self.classified_files['shell'])})")
        logging.info(f" Enabled tools: {', '.join(enabled)}")
        return enabled

    def _is_k8s_manifest(self, file_path: str) -> bool:
        """Heuristic to detect K8s manifests: Requires apiVersion AND (kind OR metadata)."""
        full_path = os.path.join(self.target_dir, file_path)
        import re
        try:
            # We check the first 8KB for accuracy
            with open(full_path, 'r', errors='ignore') as f:
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

    def run_command(self, cmd: List[str], tool_name: str, cwd: str = None) -> Dict[str, Any]:
        """Runs a scanning command and returns its exit code and summary."""
        logging.info(f" Running {tool_name} scan...")
        try:
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd
            )
            return {
                "exit_code": process.returncode,
                "stdout": process.stdout if process.stdout else "",
                "stderr": process.stderr if process.stderr else "",
                "status": "COMPLETED" # Default to completed; scanners will determine ISSUES_FOUND
            }
        except FileNotFoundError:
            logging.warning(f" {tool_name} not found on the PATH.")
            return {"status": "NOT_FOUND"}
        except Exception as e:
            logging.error(f" Error running {tool_name}: {str(e)}")
            return {"status": "ERROR", "error": str(e)}

    def scan_py_compile(self) -> List[Dict]:
        """Performs a static syntax check using py_compile to catch broken code early."""
        findings = []
        logging.info("Running Python Syntax Validation (py_compile)...")
        import py_compile
        import os
        
        for file_path in self.classified_files["python"]:
            full_path = os.path.join(self.target_dir, file_path)
            try:
                # compile() with doraise=True will throw an exception on syntax error
                py_compile.compile(full_path, doraise=True)
            except py_compile.PyCompileError as e:
                # Clean up the error message to be user-friendly
                err_msg = str(e).split('\n')[-2] if '\n' in str(e) else str(e)
                self.results["findings"].append({
                    "tool": "py_compile",
                    "file": file_path,
                    "line": "N/A", 
                    "issue": "Critical Python Syntax Fault",
                    "severity": "CRITICAL",
                    "description": f"Code is syntactically invalid: {err_msg}.",
                    "remediation": f"Fix the following syntax error to allow execution: {err_msg}"
                })
                self.results["scans"]["py_compile"] = {"status": "ISSUES_FOUND", "error": err_msg}
                return findings
        
        self.results["scans"]["py_compile"] = {"status": "COMPLETED", "exit_code": 0}
        return findings

    def scan_semgrep(self):
        """Runs Semgrep static analysis with multi-language security patterns."""
        # Expanded extension support for all requested languages
        remm_exts = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".yaml", ".yml", ".json", ".k8s"}
        if not any(f.endswith(tuple(remm_exts)) for f in self.results["files_scanned"]):
            self.results["scans"]["semgrep"] = {"status": "SKIPPED", "reason": "No supported files"}
            return

        # Strict-Mode security configurations + Harmful Logic Audits
        cmd = [
            "semgrep", "scan", 
            "--config=auto", 
            "--config=p/security-audit", 
            "--config=p/r2c-security-audit",
            "--config=p/secrets", 
            "--config=p/python",
            "--json", "--quiet", self.target_dir
        ]
        res = self.run_command(cmd, "Semgrep")
        
        if res.get("stdout"):
            try:
                # Cleanup stdout: semgrep sometimes prints headers/updates before the JSON
                clean_stdout = res["stdout"]
                if "{" in clean_stdout:
                    clean_stdout = clean_stdout[clean_stdout.find("{"):]
                
                data = json.loads(clean_stdout)
                res["stdout"] = data
                results = data.get("results", [])
                
                if results:
                    res["status"] = "ISSUES_FOUND"
                    for result in results:
                        self.results["findings"].append({
                            "tool": "semgrep",
                            "file": result.get("path"),
                            "line": result.get("start", {}).get("line"),
                            "issue": result.get("extra", {}).get("message"),
                            "severity": result.get("extra", {}).get("severity", "MEDIUM"),
                            "remediation": result.get("extra", {}).get("metadata", {}).get("remediation") or "Audit code logic and follow secure coding patterns."
                        })
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
        cmd = ["/usr/local/bin/gitleaks", "detect", "--source", self.target_dir, "--no-git", "--report-format", "json", "--report-path", report_path, "--no-banner"]
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
                        self.results["findings"].append({
                            "tool": "gitleaks",
                            "file": leak.get("File"),
                            "line": leak.get("StartLine"),
                            "issue": f"Secret detected: {leak.get('Description')}",
                            "severity": "CRITICAL"
                        })
            except Exception as e:
                logging.error(f" Failed to parse Gitleaks JSON: {e}")
            finally:
                if os.path.exists(report_path): os.remove(report_path)
        
        self.results["scans"]["gitleaks"] = res

    def scan_yamllint(self):
        """Runs yamllint and parses output into findings."""
        yaml_files = self.classified_files.get("yaml", []) + self.classified_files.get("k8s", [])
        if not yaml_files:
            self.results["scans"]["yamllint"] = {"status": "SKIPPED", "reason": "No YAML files"}
            return

        # Use parsable format to extract findings
        cmd = ["/usr/local/bin/yamllint", "-f", "parsable"] + yaml_files
        res = self.run_command(cmd, "Yamllint", cwd=self.target_dir)
        
        if res.get("stdout"):
            for line in res["stdout"].splitlines():
                if ":" in line:
                    parts = line.split(":")
                    if len(parts) >= 4:
                        file_path = parts[0].strip()
                        line_num = parts[1].strip()
                        issue = parts[3].strip()
                        self.results["findings"].append({
                            "tool": "yamllint",
                            "file": file_path,
                            "line": int(line_num) if line_num.isdigit() else None,
                            "issue": f"YAML Lint: {issue}",
                            "severity": "MEDIUM",
                            "remediation": "Correct the YAML formatting/syntax according to best practices."
                        })
            res["status"] = "ISSUES_FOUND"
        
        self.results["scans"]["yamllint"] = res

    def scan_bandit(self):
        """Runs Bandit Python security linter and parses JSON matches."""
        if not any(f.endswith(".py") for f in self.results["files_scanned"]):
            self.results["scans"]["bandit"] = {"status": "SKIPPED", "reason": "No Python files"}
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
            self.results["scans"]["go_build"] = {"status": "SKIPPED", "reason": "No Go files"}
            return

        logging.info("Running Go Syntax Validation (go build)...")
        # We try to build all files in the directory to check for package-level consistency
        cmd = ["go", "build", "-o", "/dev/null", "."]
        res = self.run_command(cmd, "Go Build", cwd=self.target_dir)
        
        if res["exit_code"] != 0:
            err_msg = res.get("stderr", "Unknown compilation error")
            self.results["findings"].append({
                "tool": "go_build",
                "file": "Go Package",
                "line": "N/A",
                "issue": "Critical Go Compilation Fault",
                "severity": "CRITICAL",
                "description": f"Go code failed to compile: {err_msg}",
                "remediation": "Fix the syntax or type errors identified by the Go compiler."
            })
            res["status"] = "ISSUES_FOUND"
        else:
            res["status"] = "COMPLETED"
            res["exit_code"] = 0
        
        self.results["scans"]["go_build"] = res

    def scan_gosec(self):
        """Runs gosec for security audits in Go code."""
        if not self.classified_files.get("go"):
            self.results["scans"]["gosec"] = {"status": "SKIPPED", "reason": "No Go files"}
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
                    for issue in issues:
                        self.results["findings"].append({
                            "tool": "gosec",
                            "file": issue.get("file"),
                            "line": issue.get("line"),
                            "issue": issue.get("details"),
                            "severity": issue.get("severity"),
                            "remediation": f"Security concern in {issue.get('file')}. Review Go security best practices."
                        })
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
            self.results["scans"]["staticcheck"] = {"status": "SKIPPED", "reason": "No Go files"}
            return

        cmd = ["staticcheck", "-f", "json", "./..."]
        res = self.run_command(cmd, "Staticcheck", cwd=self.target_dir)
        
        if res.get("stdout"):
            try:
                issues_found = False
                for line in res["stdout"].splitlines():
                    if not line.strip(): continue
                    issue = json.loads(line)
                    issues_found = True
                    self.results["findings"].append({
                        "tool": "staticcheck",
                        "file": issue.get("location", {}).get("file"),
                        "line": issue.get("location", {}).get("line"),
                        "issue": issue.get("message"),
                        "severity": "MEDIUM",
                        "remediation": f"Refactor code to resolve: {issue.get('code')}"
                    })
                res["status"] = "ISSUES_FOUND" if issues_found else "COMPLETED"
                res["exit_code"] = 0 if not issues_found else 1
            except Exception as e:
                logging.error(f" Failed to parse Staticcheck JSON: {e}")
        
        self.results["scans"]["staticcheck"] = res

    def scan_trivy(self):
        """Runs Trivy for vulnerabilities and misconfigurations in Strict Mode."""
        cmd = [
            "/usr/local/bin/trivy", "fs", 
            "--format", "json", 
            "--scanners", "vuln,secret,config", 
            "--severity", "CRITICAL,HIGH,MEDIUM,LOW",
            "--quiet", self.target_dir
        ]
        res = self.run_command(cmd, "Trivy")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "trivy"
            res = self.run_command(cmd, "Trivy")
            
        if res.get("stdout"):
            try:
                data = json.loads(res["stdout"])
                res["stdout"] = data
                issues_found = False
                for result in data.get("Results", []):
                    # Parse vulnerabilities
                    for vuln in result.get("Vulnerabilities", []):
                        issues_found = True
                        self.results["findings"].append({
                            "tool": "trivy",
                            "file": result.get("Target"),
                            "line": None,
                            "issue": f"{vuln.get('VulnerabilityID')}: {vuln.get('Title')}",
                            "severity": vuln.get("Severity")
                        })
                    # Parse misconfigurations
                    for conf in result.get("Misconfigurations", []):
                        issues_found = True
                        self.results["findings"].append({
                            "tool": "trivy",
                            "file": result.get("Target"),
                            "line": conf.get("IOMetadata", {}).get("Line"),
                            "issue": conf.get("Title"),
                            "severity": conf.get("Severity")
                        })
                if issues_found:
                    res["status"] = "ISSUES_FOUND"
            except Exception as e:
                logging.error(f" Failed to parse Trivy JSON: {e}")
                
        self.results["scans"]["trivy"] = res

    def scan_kubelinter(self):
        """Runs kube-linter in Ultra-Strict mode for all built-in security checks."""
        k8s_files = self.classified_files.get("k8s", [])
        if not k8s_files:
            self.results["scans"]["kubelinter"] = {"status": "SKIPPED", "reason": "No K8s manifests"}
            return

        # Enable all built-in checks and force failure on any linting violation
        cmd = ["/usr/local/bin/kube-linter", "lint", "--format", "json", "--add-all-built-in", "--do-not-auto-add-defaults"] + [os.path.join(self.target_dir, f) for f in k8s_files]
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
                    if not isinstance(report, dict): continue
                    check_val = report.get("Check") or report.get("check") or {}
                    check_name = check_val.get("Name") if isinstance(check_val, dict) else str(check_val)
                    remediation = report.get("Remediation") or report.get("remediation")
                    
                    self.results["findings"].append({
                        "tool": "kubelinter",
                        "file": "manifest",
                        "line": None,
                        "issue": f"Linting Violation: {check_name}",
                        "severity": "HIGH",
                        "remediation": remediation or "Review Kubernetes resource against security best practices."
                    })
            except Exception as e:
                logging.error(f" Failed to parse Kube-Linter JSON: {e}")
        
        self.results["scans"]["kubelinter"] = res

    def scan_kubeconform(self):
        """Runs kubeconform in Strict Schema-Validation mode."""
        k8s_files = self.classified_files.get("k8s", [])
        if not k8s_files:
            self.results["scans"]["kubeconform"] = {"status": "SKIPPED", "reason": "No K8s manifests"}
            return

        # Strict: fail on missing schemas and use modern K8s version
        cmd = ["/usr/local/bin/kubeconform", "-summary", "-output", "json", "-strict", "-ignore-missing-schemas=false", "-kubernetes-version", "1.30.0"] + [os.path.join(self.target_dir, f) for f in k8s_files]
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
                        self.results["findings"].append({
                            "tool": "kubeconform",
                            "file": resource.get("filename", "unknown"),
                            "line": None,
                            "issue": f"Schema Error: {resource.get('kind')} ({resource.get('msg')})",
                            "severity": "CRITICAL",
                            "remediation": "Correct the manifest to match the Kubernetes API schema."
                        })
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
            self.results["scans"]["kubescore"] = {"status": "SKIPPED", "reason": "No K8s manifests"}
            return

        total_checks = 0
        passed_checks = 0
        has_issues = False

        for f_path in k8s_files:
            cmd = ["/usr/local/bin/kube-score", "score", "--output-format", "json", f_path]
            res = self.run_command(cmd, f"Kube-Score-{f_path}", cwd=self.target_dir)
            
            # Fatal Parse Error for this specific file
            if res["status"] == "ERROR" or res.get("stdout") in ("", "null", "None"):
                stderr = res.get("stderr", "")
                if "failed to parse" in stderr.lower() or "cannot unmarshal" in stderr.lower():
                    err_msg = stderr.split("err=")[-1] if "err=" in stderr else stderr
                    self.results["findings"].append({
                        "tool": "kubescore",
                        "file": f_path,
                        "line": None,
                        "issue": "K8s Parsing Failure (Critical)",
                        "severity": "CRITICAL",
                        "remediation": f"Fix Syntax Error in {f_path}: {err_msg.strip()}"
                    })
                    has_issues = True
                continue

            # Successful parse, extract scores
            try:
                data = json.loads(res["stdout"])
                for item in data:
                    if not isinstance(item, dict): continue
                    obj_meta = item.get("object_meta") or item.get("ObjectMeta") or {}
                    obj_name = obj_meta.get("name") or f_path
                    
                    for check in item.get("checks") or item.get("Checks") or []:
                        if not isinstance(check, dict): continue
                        total_checks += 1
                        grade = check.get("grade", 0)
                        if grade == 0 or check.get("skipped"):
                            passed_checks += 1
                        else:
                            has_issues = True
                            comments = check.get("comments") or check.get("Comments") or []
                            comment = comments[0] if isinstance(comments, list) and len(comments) > 0 else {}
                            check_meta = check.get("check") or check.get("Check") or {}
                            check_name = check_meta.get("name") or "unknown"
                            
                            self.results["findings"].append({
                                "tool": "kubescore",
                                "file": f"{f_path} ({obj_name})",
                                "line": None,
                                "issue": f"{check_name} (Grade: {grade})",
                                "severity": "HIGH" if grade >= 10 else "MEDIUM",
                                "remediation": comment.get('summary', 'Review hardening best practices.')
                            })
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
            "checks_passed": passed_checks
        }

    def scan_shellcheck(self):
        """Runs ShellCheck for shell scripts and parses JSON results."""
        shell_files = self.classified_files.get("shell", [])
        if not shell_files:
            self.results["scans"]["shellcheck"] = {"status": "SKIPPED", "reason": "No shell scripts"}
            return

        cmd = ["/usr/local/bin/shellcheck", "-f", "json"] + [os.path.join(self.target_dir, f) for f in shell_files]
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
                    sev_map = {1: "INFO", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}
                    self.results["findings"].append({
                        "tool": "shellcheck",
                        "file": issue.get("file"),
                        "line": issue.get("line"),
                        "issue": f"SC{issue.get('code')}: {issue.get('message')}",
                        "severity": sev_map.get(issue.get("level"), "MEDIUM")
                    })
            except Exception as e:
                logging.error(f" Failed to parse ShellCheck JSON: {e}")
        
        self.results["scans"]["shellcheck"] = res

    def _ensure_vulnerability_insights(self):
        """Safety net: Ensure every failed tool has at least one finding in the insights panel."""
        for tool, scan_res in self.results["scans"].items():
            if not isinstance(scan_res, dict): continue
            
            status = scan_res.get("status")
            if status in ("ERROR", "ISSUES_FOUND"):
                # Check if this tool already has findings
                tool_findings = [f for f in self.results["findings"] if f.get("tool") == tool]
                
                if not tool_findings:
                    # No findings recorded yet, but tool failed. Create an auto-insight.
                    logging.warning(f" Tool {tool} failed but provided no insights. Generating auto-insight.")
                    error_msg = scan_res.get("stderr") or scan_res.get("error") or "Unknown security or execution error."
                    
                    self.results["findings"].append({
                        "tool": tool,
                        "file": "Pipeline Error",
                        "line": None,
                        "issue": f"Tool Execution Failure: {tool.upper()}",
                        "severity": "CRITICAL",
                        "remediation": f"Review tool error: {error_msg[:200]}..."
                    })

    def run_all(self):
        """Executes enabled scanners and enforces dashboard insights."""
        if "py_compile" in self.enabled_tools: self.scan_py_compile()
        if "semgrep" in self.enabled_tools: self.scan_semgrep()
        if "gitleaks" in self.enabled_tools: self.scan_gitleaks()
        if "yamllint" in self.enabled_tools: self.scan_yamllint()
        if "bandit" in self.enabled_tools: self.scan_bandit()
        
        if "go_build" in self.enabled_tools: self.scan_go_build()
        if "gosec" in self.enabled_tools: self.scan_gosec()
        if "staticcheck" in self.enabled_tools: self.scan_staticcheck()

        if "trivy" in self.enabled_tools: self.scan_trivy()
        
        if "kubelinter" in self.enabled_tools: self.scan_kubelinter()
        if "kubeconform" in self.enabled_tools: self.scan_kubeconform()
        if "kubescore" in self.enabled_tools: self.scan_kubescore()
        if "shellcheck" in self.enabled_tools: self.scan_shellcheck()

        # Enforce that all failures result in dashboard insights
        self._ensure_vulnerability_insights()
        
        summary = self._calculate_summary()
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
            "overall_status": "RISKS_FOUND" if risks > 0 else ("CLEAN" if (errors == 0 and risks == 0) else "ERROR"),
            "security_score": scans.get("kubescore", {}).get("security_score"),
            "total_tools_run": total_tools,
            "risks_detected": risks,
            "findings_count": len(self.results["findings"]),
            "clean_tools": clean,
            "skipped_tools": skipped,
            "failed_tools": errors,
            "timestamp": datetime.now().isoformat()
        }

    def save_results(self):
        """Saves scan results to a JSON file and displays a pretty summary."""
        with open(REPORT_PATH, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        self._display_pretty_summary()

    def _display_pretty_summary(self):
        """Prints a presentable ASCII table and detailed results."""
        print("\n" + "═"*70)
        print(" 🛡️  SECURITY SCAN DISCOVERY & SUMMARY")
        print("═"*70)
        print(f" Target Directory: {self.target_dir}")
        print(f" Files Analyzed:   {', '.join(self.results['files_scanned']) if self.results['files_scanned'] else 'None'}")
        print("─"*70)
        
        # Table Header
        header = f" {'SCANNER':<12} │ {'STATUS':<15} │ {'RESULT SUMMARY'}"
        print(header)
        print(" " + "─"*12 + "╁" + "─"*17 + "╁" + "─"*37)
        
        for tool in ["py_compile", "semgrep", "gitleaks", "trivy", "yamllint", "bandit", "shellcheck", "kubelinter", "kubeconform", "kubescore"]:
            if tool not in self.results["scans"]:
                continue
                
            res = self.results["scans"].get(tool)
            status = res.get("status", "UNKNOWN")
            
            status_text = status
            summary = ""
            
            if status == "ISSUES_FOUND":
                status_text = "⚠️  RISK FOUND"
                # Find the first specific issue reported for this tool
                tool_findings = [f for f in self.results["findings"] if f.get("tool") == tool]
                if tool_findings:
                    summary = tool_findings[0].get("issue", "Review technical findings below.")
                else:
                    summary = "Detailed risks detected. See findings section."
            elif status == "COMPLETED":
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
        print("─"*70)
        print(" 📄 UNIFIED SECURITY FINDINGS")
        print("─"*70)
        
        if not self.results["findings"]:
            print("\n No specific vulnerabilities were detailed by the scanners.")
        else:
            for finding in self.results["findings"]:
                severity = finding.get('severity', 'INFO').upper()
                emoji = "🛑" if severity in ("CRITICAL", "HIGH") else ("⚠️" if severity == "MEDIUM" else "ℹ️")
                loc = f"{finding['file']}:{finding['line']}" if finding['line'] else finding['file']
                print(f" {emoji} [{severity}] {finding['tool'].upper()}: {finding['issue']}")
                print(f"    Location: {loc}")
                print("    " + "-"*30)

        print("\n" + "═"*70)
        print(f" 📁 Persistent JSON Report: {REPORT_PATH}")
        print("═"*70 + "\n")

if __name__ == "__main__":
    orchestrator = ScannerOrchestrator(SCAN_DIR)
    orchestrator.run_all()
