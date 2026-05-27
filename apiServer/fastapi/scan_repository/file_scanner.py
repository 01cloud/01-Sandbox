"""
file_scanner.py — Per-language static analysis scanner dispatch.

Revised architecture: Instead of exec-ing tools in a remote sandbox
(which has no /exec endpoint), we read files from the locally cloned
repo and submit them to the existing POST /scan-jobs pipeline.

The /scan-jobs endpoint provisions a code-interpreter sandbox with all
tools (bandit, pylint, eslint, rubocop, pmd, go vet, etc.) pre-installed
via Dockerfile_base and returns a full security scan report.

File cap: max 100 files per language (spec constraint).
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

import httpx
from config import opensandbox_base_url, opensandbox_headers, opensandbox_route_prefix

from .models import FindingItem, LanguageScanResult

# Maximum files submitted per language
FILE_CAP = 100

# Languages we skip security scanning for (only LoC counted)
LOC_ONLY_LANGS = {
    "yaml",
    "json",
    "markdown",
    "text",
    "toml",
    "xml",
    "ini",
    "dockerfile",
}


def _pmd_priority_to_severity(priority: int) -> str:
    """Convert PMD numeric priority (1=highest) to severity string."""
    return {1: "CRITICAL", 2: "HIGH", 3: "MEDIUM", 4: "LOW"}.get(priority, "INFO")


def _count_loc(files: list[str]) -> int:
    """Count lines of code across a list of local file paths."""
    total = 0
    for path in files:
        try:
            with open(path, "r", errors="replace") as f:
                total += sum(1 for line in f if line.strip())
        except Exception:
            pass
    return total


def _read_files_as_dict(files: list[str], repo_root: str) -> dict[str, str]:
    """
    Read file contents and return {relative_path: content} dict.
    Skips binary files and files > 200 KB.
    """
    result: dict[str, str] = {}
    for path in files[:FILE_CAP]:
        try:
            size = os.path.getsize(path)
            if size > 200 * 1024:  # 200 KB cap per file
                continue
            with open(path, "r", errors="replace") as f:
                content = f.read()
            rel_path = os.path.relpath(path, repo_root)
            result[rel_path] = content
        except Exception:
            continue
    return result


def _parse_scan_report(report: dict, lang_lower: str) -> List[FindingItem]:
    """
    Parse the security_scan_report.json returned by the scan-jobs endpoint.
    The report structure is produced by code-interpreter/src/scanner_orchestrator.py.
    """
    findings: List[FindingItem] = []

    # Findings are nested under tool names in the report
    # Structure: {"findings": [...], "tool_outputs": {...}, ...}
    raw_findings = report.get("findings", [])

    for f in raw_findings:
        severity = f.get("severity", "INFO").upper()
        # Normalize severity levels
        if severity not in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
            severity = "INFO"

        line_val = f.get("line") or f.get("line_number")
        sanitized_line = None
        if line_val is not None:
            try:
                sanitized_line = int(line_val)
            except (ValueError, TypeError):
                sanitized_line = None

        findings.append(
            FindingItem(
                severity=severity,
                file=f.get("file", f.get("filename", "")),
                line=sanitized_line,
                issue=f.get("issue", f.get("message", f.get("description", ""))),
                tool=f.get("tool", "scanner"),
                remediation=f.get("remediation", f.get("more_info", f.get("rule"))),
            )
        )

    return findings


async def _submit_scan_job(
    files_dict: dict[str, str], tools: Optional[list[str]] = None
) -> dict:
    """
    Submit files to POST /scan-jobs and wait for the result.
    Returns the parsed report dict, or {} on failure.
    """
    base_url = opensandbox_base_url()
    prefix = opensandbox_route_prefix()
    url = f"{base_url.rstrip('/')}{prefix}/scan-jobs"

    payload: dict = {"files": files_dict}
    if tools:
        payload["tools"] = tools

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=payload, headers=opensandbox_headers())
            resp.raise_for_status()
            data = resp.json()
            # Scan-jobs returns ScanJobResponse with a "report" field
            return data.get("report") or {}
    except Exception as exc:
        print(f"[RepoScanner] scan-jobs submission error: {exc}")
        return {}


# ─────────────────────────────────────────────
# Per-Language Scanner Dispatch
# ─────────────────────────────────────────────


async def scan_language(
    sandbox_id: str,
    language: str,
    files: list[str],
    percentage: float,
) -> LanguageScanResult:
    """
    Run security analysis for a detected language by submitting
    its source files to the POST /scan-jobs endpoint.

    Args:
        sandbox_id:  Path to the local temp directory (repo root's parent).
        language:    Language name (e.g. "Python").
        files:       List of absolute file paths inside sandbox_id.
        percentage:  Language share of the total repo (0–100).

    Returns:
        LanguageScanResult with findings and LoC.
    """
    lang_lower = language.lower()
    files_capped = files[:FILE_CAP]
    file_count = len(files_capped)
    findings: List[FindingItem] = []

    # Always count LoC from local files (fast, no network)
    lines_of_code = _count_loc(files_capped)

    # Skip security scanning for non-code languages
    if lang_lower in LOC_ONLY_LANGS:
        return LanguageScanResult(
            language=language,
            file_count=file_count,
            lines_of_code=lines_of_code,
            percentage=round(percentage, 2),
            findings=[],
        )

    # Build the file dict to submit to the scan-jobs pipeline
    repo_root = os.path.join(sandbox_id, "repo")
    files_dict = _read_files_as_dict(files_capped, repo_root)

    if not files_dict:
        print(f"[RepoScanner] No readable files for {language}, skipping scan")
        return LanguageScanResult(
            language=language,
            file_count=file_count,
            lines_of_code=lines_of_code,
            percentage=round(percentage, 2),
            findings=[],
        )

    # Select tool hints for the scan-jobs orchestrator
    # (scanner_orchestrator.py uses these to pick the right tools)
    tool_hints: Optional[list[str]] = None
    if lang_lower == "python":
        tool_hints = ["bandit", "semgrep"]
    elif lang_lower in ("javascript", "typescript"):
        tool_hints = ["semgrep"]
    elif lang_lower == "go":
        tool_hints = ["gosec", "semgrep"]
    elif lang_lower == "java":
        tool_hints = ["semgrep"]
    elif lang_lower == "ruby":
        tool_hints = ["semgrep"]
    elif lang_lower in ("shell", "bash"):
        tool_hints = ["shellcheck", "semgrep"]
    elif lang_lower in ("yaml", "yml"):
        tool_hints = ["yamllint", "semgrep"]

    print(
        f"[RepoScanner] Submitting {len(files_dict)} {language} files to scan-jobs pipeline..."
    )
    report = await _submit_scan_job(files_dict, tools=tool_hints)

    if report:
        findings = _parse_scan_report(report, lang_lower)
        print(f"[RepoScanner] {language}: {len(findings)} finding(s) from scan-jobs")
    else:
        print(
            f"[RepoScanner] {language}: scan-jobs returned no report, skipping findings"
        )

    return LanguageScanResult(
        language=language,
        file_count=file_count,
        lines_of_code=lines_of_code,
        percentage=round(percentage, 2),
        findings=findings,
    )
