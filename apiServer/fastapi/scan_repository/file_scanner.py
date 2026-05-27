"""
file_scanner.py — Per-language static analysis scanner dispatch.

Runs the appropriate scanner for each detected language inside the
provisioned sandbox. All tools are already installed in Dockerfile_base.

File cap: max 100 files per language (spec constraint).
"""

from __future__ import annotations

import json
import re
from typing import List

from .models import FindingItem, LanguageScanResult
from .sandbox_provisioner import REPO_DIR, exec_in_sandbox

# Maximum files scanned per language (spec constraint)
FILE_CAP = 100


# ─────────────────────────────────────────────
# Finding Parsers
# ─────────────────────────────────────────────


def _parse_bandit(stdout: str, language: str) -> List[FindingItem]:
    """Parse bandit JSON output."""
    findings: List[FindingItem] = []
    try:
        data = json.loads(stdout)
        for issue in data.get("results", []):
            findings.append(
                FindingItem(
                    severity=issue.get("issue_severity", "MEDIUM").upper(),
                    file=issue.get("filename", "").replace(REPO_DIR + "/", ""),
                    line=issue.get("line_number"),
                    issue=issue.get("issue_text", ""),
                    tool="bandit",
                    remediation=issue.get("more_info", ""),
                )
            )
    except Exception:
        pass
    return findings


def _parse_pylint(stdout: str) -> List[FindingItem]:
    """Parse pylint JSON output."""
    findings: List[FindingItem] = []
    try:
        data = json.loads(stdout)
        for msg in data:
            sev = msg.get("type", "convention").upper()
            # Map pylint types to severity
            sev_map = {
                "ERROR": "HIGH",
                "WARNING": "MEDIUM",
                "REFACTOR": "LOW",
                "CONVENTION": "INFO",
                "INFORMATION": "INFO",
                "FATAL": "CRITICAL",
            }
            findings.append(
                FindingItem(
                    severity=sev_map.get(sev, "INFO"),
                    file=msg.get("path", "").replace(REPO_DIR + "/", ""),
                    line=msg.get("line"),
                    issue=f"[{msg.get('message-id', '')}] {msg.get('message', '')}",
                    tool="pylint",
                    remediation=msg.get("symbol", ""),
                )
            )
    except Exception:
        pass
    return findings


def _parse_eslint(stdout: str) -> List[FindingItem]:
    """Parse ESLint JSON output."""
    findings: List[FindingItem] = []
    try:
        data = json.loads(stdout)
        for file_result in data:
            filepath = file_result.get("filePath", "").replace(REPO_DIR + "/", "")
            for msg in file_result.get("messages", []):
                sev = "HIGH" if msg.get("severity") == 2 else "MEDIUM"
                findings.append(
                    FindingItem(
                        severity=sev,
                        file=filepath,
                        line=msg.get("line"),
                        issue=msg.get("message", ""),
                        tool="eslint",
                        remediation=msg.get("ruleId", ""),
                    )
                )
    except Exception:
        pass
    return findings


def _parse_go_vet(stdout: str, stderr: str) -> List[FindingItem]:
    """Parse go vet output (text format)."""
    findings: List[FindingItem] = []
    text = stderr or stdout
    for line in text.splitlines():
        # Pattern: ./path/file.go:line:col: message
        m = re.match(r"^(.+\.go):(\d+)(?::\d+)?: (.+)$", line.strip())
        if m:
            findings.append(
                FindingItem(
                    severity="MEDIUM",
                    file=m.group(1).replace(REPO_DIR + "/", ""),
                    line=int(m.group(2)),
                    issue=m.group(3),
                    tool="go vet",
                )
            )
    return findings


def _parse_staticcheck(stdout: str) -> List[FindingItem]:
    """Parse staticcheck JSON output (one JSON object per line)."""
    findings: List[FindingItem] = []
    for line in stdout.splitlines():
        try:
            obj = json.loads(line.strip())
            pos = obj.get("position", {})
            findings.append(
                FindingItem(
                    severity="MEDIUM",
                    file=pos.get("file", "").replace(REPO_DIR + "/", ""),
                    line=pos.get("line"),
                    issue=obj.get("message", ""),
                    tool="staticcheck",
                    remediation=obj.get("code", ""),
                )
            )
        except Exception:
            continue
    return findings


def _parse_rubocop(stdout: str) -> List[FindingItem]:
    """Parse rubocop JSON output."""
    findings: List[FindingItem] = []
    try:
        data = json.loads(stdout)
        for file_info in data.get("files", []):
            filepath = file_info.get("path", "").replace(REPO_DIR + "/", "")
            for offense in file_info.get("offenses", []):
                sev = offense.get("severity", "convention").upper()
                sev_map = {
                    "ERROR": "HIGH",
                    "WARNING": "MEDIUM",
                    "CONVENTION": "INFO",
                    "REFACTOR": "LOW",
                    "INFO": "INFO",
                }
                loc = offense.get("location", {})
                findings.append(
                    FindingItem(
                        severity=sev_map.get(sev, "INFO"),
                        file=filepath,
                        line=loc.get("start_line"),
                        issue=offense.get("message", ""),
                        tool="rubocop",
                        remediation=offense.get("cop_name", ""),
                    )
                )
    except Exception:
        pass
    return findings


def _parse_tokei_loc(stdout: str, language: str) -> int:
    """Extract lines of code from tokei JSON for a specific language."""
    try:
        data = json.loads(stdout)
        lang_data = data.get(language, {})
        if not lang_data:
            # Try case-insensitive match
            for k, v in data.items():
                if k.lower() == language.lower():
                    lang_data = v
                    break
        return lang_data.get("code", 0)
    except Exception:
        return 0


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
    Run the appropriate static analysis scanner for a detected language.

    Args:
        sandbox_id:  Active sandbox to exec commands in.
        language:    Language name from the detector (e.g. "Python").
        files:       List of file paths inside the sandbox.
        percentage:  Language share of the total repo (0-100).

    Returns:
        LanguageScanResult with findings and LoC.
    """
    # Cap files to avoid runaway costs
    files_capped = files[:FILE_CAP]
    file_count = len(files_capped)
    findings: List[FindingItem] = []
    lines_of_code = 0

    # ── Get LoC via tokei ──────────────────────────────────────────────
    loc_stdout, _, _ = await exec_in_sandbox(
        sandbox_id=sandbox_id,
        command=["tokei", REPO_DIR, "--output", "json"],
        timeout=30.0,
    )
    lines_of_code = _parse_tokei_loc(loc_stdout, language)

    lang_lower = language.lower()

    # ── Python ────────────────────────────────────────────────────────
    if lang_lower == "python":
        # bandit security scan
        stdout, _, _ = await exec_in_sandbox(
            sandbox_id=sandbox_id,
            command=["bandit", "-r", "-f", "json", REPO_DIR],
            timeout=90.0,
        )
        findings.extend(_parse_bandit(stdout, language))

        # pylint code quality
        if files_capped:
            stdout, _, _ = await exec_in_sandbox(
                sandbox_id=sandbox_id,
                command=["pylint", "--output-format=json", "--exit-zero"]
                + files_capped,
                timeout=90.0,
            )
            findings.extend(_parse_pylint(stdout))

    # ── JavaScript / TypeScript ───────────────────────────────────────
    elif lang_lower in ("javascript", "typescript"):
        stdout, _, _ = await exec_in_sandbox(
            sandbox_id=sandbox_id,
            command=[
                "eslint",
                "--format",
                "json",
                "--no-eslintrc",
                "--env",
                "browser,node,es2022",
                REPO_DIR,
            ],
            timeout=90.0,
        )
        findings.extend(_parse_eslint(stdout))

    # ── Go ────────────────────────────────────────────────────────────
    elif lang_lower == "go":
        # go vet for correctness
        stdout, stderr, _ = await exec_in_sandbox(
            sandbox_id=sandbox_id,
            command=["go", "vet", "./..."],
            workdir=REPO_DIR,
            timeout=120.0,
        )
        findings.extend(_parse_go_vet(stdout, stderr))

        # staticcheck for extra lint
        stdout, _, _ = await exec_in_sandbox(
            sandbox_id=sandbox_id,
            command=["staticcheck", "-f", "json", "./..."],
            workdir=REPO_DIR,
            timeout=120.0,
        )
        findings.extend(_parse_staticcheck(stdout))

    # ── Rust ─────────────────────────────────────────────────────────
    elif lang_lower == "rust":
        stdout, stderr, _ = await exec_in_sandbox(
            sandbox_id=sandbox_id,
            command=["cargo", "check", "--message-format", "json"],
            workdir=REPO_DIR,
            timeout=180.0,
        )
        for line in (stdout + "\n" + stderr).splitlines():
            try:
                obj = json.loads(line.strip())
                if obj.get("reason") == "compiler-message":
                    msg = obj.get("message", {})
                    level = msg.get("level", "note")
                    if level in ("error", "warning"):
                        spans = msg.get("spans", [{}])
                        span = spans[0] if spans else {}
                        findings.append(
                            FindingItem(
                                severity="HIGH" if level == "error" else "MEDIUM",
                                file=(span.get("file_name", "")).replace(
                                    REPO_DIR + "/", ""
                                ),
                                line=span.get("line_start"),
                                issue=msg.get("message", ""),
                                tool="cargo check",
                                remediation=msg.get("code", {}).get("code")
                                if msg.get("code")
                                else None,
                            )
                        )
            except Exception:
                continue

    # ── Ruby ─────────────────────────────────────────────────────────
    elif lang_lower == "ruby":
        stdout, _, _ = await exec_in_sandbox(
            sandbox_id=sandbox_id,
            command=["rubocop", "--format", "json", "--no-color", REPO_DIR],
            timeout=90.0,
        )
        findings.extend(_parse_rubocop(stdout))

    # ── Java ─────────────────────────────────────────────────────────
    elif lang_lower == "java":
        if files_capped:
            stdout, _, _ = await exec_in_sandbox(
                sandbox_id=sandbox_id,
                command=[
                    "pmd",
                    "check",
                    "-f",
                    "json",
                    "-R",
                    "rulesets/java/quickstart.xml",
                    "-d",
                    REPO_DIR,
                ],
                timeout=120.0,
            )
            try:
                data = json.loads(stdout)
                for v in data.get("violations", []):
                    findings.append(
                        FindingItem(
                            severity=_pmd_priority_to_severity(v.get("priority", 3)),
                            file=v.get("filename", "").replace(REPO_DIR + "/", ""),
                            line=v.get("beginline"),
                            issue=v.get("description", ""),
                            tool="pmd",
                            remediation=v.get("rule", ""),
                        )
                    )
            except Exception:
                pass

    # ── Generic / Shell / YAML / Markdown — just LoC (already done) ──
    # tokei already captured LoC above; no findings for generic langs.

    return LanguageScanResult(
        language=language,
        file_count=file_count,
        lines_of_code=lines_of_code,
        percentage=round(percentage, 2),
        findings=findings,
    )


def _pmd_priority_to_severity(priority: int) -> str:
    """Convert PMD numeric priority (1=highest) to severity string."""
    if priority == 1:
        return "CRITICAL"
    elif priority == 2:
        return "HIGH"
    elif priority == 3:
        return "MEDIUM"
    elif priority == 4:
        return "LOW"
    else:
        return "INFO"
