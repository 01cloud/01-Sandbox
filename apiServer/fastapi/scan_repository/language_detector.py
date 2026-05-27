"""
language_detector.py — Language detection using a priority chain.

Priority order (same tools as GitHub uses internally):
  1. github-linguist  — most accurate; handles vendored/generated/docs
  2. tokei            — fast LoC counter with JSON output
  3. enry             — lightweight Go port of linguist

All tools are pre-installed in code-interpreter/Dockerfile_base so
zero runtime installation occurs.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple

from .models import DetectionTool
from .sandbox_provisioner import REPO_DIR, exec_in_sandbox

# ─────────────────────────────────────────────
# Output Parsers
# ─────────────────────────────────────────────


def _parse_linguist(output: str) -> Dict[str, List[str]]:
    """
    Parse `linguist --breakdown` output.

    Example output:
        Python (87.3%)
        ---------------
        src/main.py
        src/utils.py

        JavaScript (12.7%)
        -------------------
        static/app.js
    """
    result: Dict[str, List[str]] = {}
    current_lang: str | None = None

    for line in output.splitlines():
        line = line.strip()
        if not line:
            current_lang = None
            continue

        # Language header: "Python (87.3%)"
        lang_match = re.match(r"^([A-Za-z+#\s/.-]+?)\s+\(\d+\.\d+%\)$", line)
        if lang_match:
            current_lang = lang_match.group(1).strip()
            result[current_lang] = []
            continue

        # Separator line
        if re.match(r"^-+$", line):
            continue

        # File path
        if current_lang is not None:
            result[current_lang].append(line)

    return result


def _parse_tokei(output: str) -> Dict[str, List[str]]:
    """
    Parse `tokei --output json` output.

    Tokei JSON structure:
    {
      "Python": {"blanks": 10, "code": 200, "comments": 30, "reports": [
          {"name": "src/main.py", "stats": {...}}, ...
      ]},
      ...
    }
    """
    result: Dict[str, List[str]] = {}
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return result

    for lang, info in data.items():
        if lang == "Total":
            continue
        files = [r.get("name", "") for r in info.get("reports", [])]
        files = [f for f in files if f]
        if files:
            result[lang] = files

    return result


def _parse_enry(output: str) -> Dict[str, List[str]]:
    """
    Parse `enry` output.

    Example output:
        Python          87.30%  2 files
        JavaScript      12.70%  1 file
    (enry does not list individual files in its default output;
     we return language → [] and the caller will glob for files)
    """
    result: Dict[str, List[str]] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 1:
            lang = parts[0]
            result[lang] = []  # file list will be filled by file_scanner
    return result


# ─────────────────────────────────────────────
# Detection Orchestrator
# ─────────────────────────────────────────────


async def detect_languages(
    sandbox_id: str,
) -> Tuple[Dict[str, List[str]], DetectionTool]:
    """
    Detect languages in REPO_DIR using the priority chain.

    Returns:
        (language_map, tool_used)
        language_map: {"Python": ["src/main.py", ...], "Go": [...]}
        tool_used: which DetectionTool was successful
    """

    # ── 1. github-linguist ──────────────────────────────────────────────
    stdout, stderr, exit_code = await exec_in_sandbox(
        sandbox_id=sandbox_id,
        command=["linguist", "--breakdown", REPO_DIR],
        timeout=60.0,
    )
    if exit_code == 0 and stdout.strip():
        lang_map = _parse_linguist(stdout)
        if lang_map:
            print(
                f"[RepoScanner] Language detected by linguist: {list(lang_map.keys())}"
            )
            return lang_map, DetectionTool.LINGUIST

    # ── 2. tokei ────────────────────────────────────────────────────────
    stdout, stderr, exit_code = await exec_in_sandbox(
        sandbox_id=sandbox_id,
        command=["tokei", REPO_DIR, "--output", "json"],
        timeout=60.0,
    )
    if exit_code == 0 and stdout.strip():
        lang_map = _parse_tokei(stdout)
        if lang_map:
            print(f"[RepoScanner] Language detected by tokei: {list(lang_map.keys())}")
            return lang_map, DetectionTool.TOKEI

    # ── 3. enry ─────────────────────────────────────────────────────────
    stdout, stderr, exit_code = await exec_in_sandbox(
        sandbox_id=sandbox_id,
        command=["enry", REPO_DIR],
        timeout=60.0,
    )
    lang_map = _parse_enry(stdout)
    if lang_map:
        print(f"[RepoScanner] Language detected by enry: {list(lang_map.keys())}")
        return lang_map, DetectionTool.ENRY

    # ── Fallback: count files by common extension ────────────────────────
    print(
        "[RepoScanner] All detection tools failed — falling back to find-based detection"
    )
    return await _fallback_detect(sandbox_id), DetectionTool.UNKNOWN


async def _fallback_detect(sandbox_id: str) -> Dict[str, List[str]]:
    """
    Last-resort fallback: use `find` to collect files by extension.
    This mirrors what linguist avoids (extension guessing) but is better
    than returning nothing.
    """
    EXT_MAP = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".rb": "Ruby",
        ".sh": "Shell",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".json": "JSON",
        ".md": "Markdown",
    }
    result: Dict[str, List[str]] = {}

    stdout, _, exit_code = await exec_in_sandbox(
        sandbox_id=sandbox_id,
        command=["find", REPO_DIR, "-type", "f", "-not", "-path", "*/.git/*"],
        timeout=30.0,
    )
    if exit_code != 0:
        return result

    for path in stdout.splitlines():
        path = path.strip()
        if not path:
            continue
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        lang = EXT_MAP.get(ext.lower())
        if lang:
            result.setdefault(lang, []).append(path)

    return result
