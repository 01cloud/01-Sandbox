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
REPORT_PATH = os.getenv("SCAN_REPORT", "/tmp/security_scan_report.json")
SCAN_TOOLS_ENV = os.getenv("SCAN_TOOLS", "") # Comma-separated list of tools to run

class ScannerOrchestrator:
    """Orchestrates security scanning tools for code-interpreter sandboxes."""

    def __init__(self, target_dir: str):
        self.target_dir = target_dir
        self.results = {
            "target": target_dir,
            "files_scanned": [],
            "scans": {}
        }
        self.enabled_tools = self._get_enabled_tools()

    def _get_enabled_tools(self) -> List[str]:
        """Determines which tools should be run. Defaults to all requested tools."""
        all_files = []
        for root, _, files in os.walk(self.target_dir):
            for file in files:
                all_files.append(os.path.join(root, file))
        
        self.results["files_scanned"] = [os.path.relpath(f, self.target_dir) for f in all_files]
        
        logging.info("=============================================")
        logging.info(f" Discovering files in {self.target_dir}:")
        for f in self.results["files_scanned"]:
            logging.info(f"  - {f}")
        logging.info("=============================================")

        if SCAN_TOOLS_ENV:
            requested = [t.strip().lower() for t in SCAN_TOOLS_ENV.split(",") if t.strip()]
            return requested

        # Default to running all comprehensive tools as requested
        return ["semgrep", "gitleaks", "trivy", "bandit", "yamllint"]

    def run_command(self, cmd: List[str], tool_name: str) -> Dict[str, Any]:
        """Runs a scanning command and returns its exit code and summary."""
        logging.info(f" Running {tool_name} scan...")
        try:
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )
            return {
                "exit_code": process.returncode,
                "stdout": process.stdout if process.stdout else "",
                "stderr": process.stderr if process.stderr else "",
                "status": "COMPLETED" if process.returncode == 0 else "ISSUES_FOUND"
            }
        except FileNotFoundError:
            logging.warning(f" {tool_name} not found on the PATH.")
            return {"status": "NOT_FOUND"}
        except Exception as e:
            logging.error(f" Error running {tool_name}: {str(e)}")
            return {"status": "ERROR", "error": str(e)}

    def scan_semgrep(self):
        """Runs Semgrep static analysis with auto-config."""
        cmd = ["/usr/local/bin/semgrep", "scan", "--config=auto", "--quiet", "--error", self.target_dir]
        res = self.run_command(cmd, "Semgrep")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "semgrep"
            res = self.run_command(cmd, "Semgrep")
        self.results["scans"]["semgrep"] = res

    def scan_gitleaks(self):
        """Runs Gitleaks secret detection."""
        cmd = ["/usr/local/bin/gitleaks", "detect", "--source", self.target_dir, "--no-git", "--report-format", "json", "--no-banner"]
        res = self.run_command(cmd, "Gitleaks")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "gitleaks"
            res = self.run_command(cmd, "Gitleaks")
        self.results["scans"]["gitleaks"] = res

    def scan_yamllint(self):
        """Runs YAMLlint for configuration files."""
        cmd = ["/usr/local/bin/yamllint", "-d", "{extends: relaxed, rules: {line-length: disable}}", self.target_dir]
        res = self.run_command(cmd, "YAMLlint")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "yamllint"
            res = self.run_command(cmd, "YAMLlint")
        self.results["scans"]["yamllint"] = res

    def scan_bandit(self):
        """Runs Bandit for Python-specific security linting."""
        # Run with all rules recursively
        cmd = ["/usr/local/bin/bandit", "-r", self.target_dir, "-ll", "-q"]
        res = self.run_command(cmd, "Bandit")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "bandit"
            res = self.run_command(cmd, "Bandit")
        self.results["scans"]["bandit"] = res

    def scan_trivy(self):
        """Runs Trivy for filesystem vulnerabilities and misconfigurations."""
        cmd = ["/usr/local/bin/trivy", "fs", "--scanners", "vuln,secret,config", "--quiet", self.target_dir]
        res = self.run_command(cmd, "Trivy")
        if res["status"] == "NOT_FOUND":
            cmd[0] = "trivy"
            res = self.run_command(cmd, "Trivy")
        self.results["scans"]["trivy"] = res

    def run_all(self):
        """Executes enabled scanners."""
        if "semgrep" in self.enabled_tools: self.scan_semgrep()
        if "gitleaks" in self.enabled_tools: self.scan_gitleaks()
        if "yamllint" in self.enabled_tools: self.scan_yamllint()
        if "bandit" in self.enabled_tools: self.scan_bandit()
        if "trivy" in self.enabled_tools: self.scan_trivy()
        self.save_results()

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
        
        for tool in ["semgrep", "gitleaks", "yamllint", "bandit", "trivy"]:
            if tool not in self.results["scans"]:
                continue
                
            res = self.results["scans"].get(tool)
            status = res.get("status", "UNKNOWN")
            
            status_text = status
            summary = ""
            
            if status == "ISSUES_FOUND":
                status_text = "⚠️  RISK FOUND"
                if tool == "gitleaks": summary = "Credential/Secret leak detected!"
                elif tool == "semgrep": summary = "Code logic vulnerabilities found."
                elif tool == "bandit":  summary = "Python security anti-patterns detected."
                else: summary = "Items requiring review discovered."
            elif status == "COMPLETED":
                status_text = "✅ CLEAN"
                summary = "No immediate risks identified."
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
        print(" 📄 DETAILED SCAN FINDINGS")
        print("─"*70)
        
        has_details = False
        for tool, res in self.results["scans"].items():
            if res.get("status") == "ISSUES_FOUND":
                has_details = True
                print(f"\n >>> {tool.upper()} FINDINGS:")
                # Show stdout/stderr for the tool, cleaned up
                output = res.get("stdout", "") + res.get("stderr", "")
                if output.strip():
                    print(output.strip())
                else:
                    print(" (Issues detected but no detailed output captured)")
                print("─" * 40)
        
        if not has_details:
            print("\n No specific vulnerabilities were detailed by the scanners.")

        print("\n" + "═"*70)
        print(f" 📁 Persistent JSON Report: {REPORT_PATH}")
        print("═"*70 + "\n")

if __name__ == "__main__":
    orchestrator = ScannerOrchestrator(SCAN_DIR)
    orchestrator.run_all()
