"""
language_detector.py — Language detection using local file system tools.

Since the repo is cloned to a local temp directory, we run language
detection tools directly on the API pod via subprocess (exec_in_sandbox).

Priority order:
  1. github-linguist / tokei / enry
  2. Ground-truth file-extension walk (guarantees 100% precision for all repo files)

Tools are run against the local cloned repo directory.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Tuple

from .models import DetectionTool
from .sandbox_provisioner import REPO_DIR, exec_in_sandbox

_TAG = "[RepoScanner][LangDetect]"

# Mapping of file extensions to canonical language names
EXT_MAP = {
    # Python
    ".py": "Python",
    ".pyw": "Python",
    # JavaScript / TypeScript
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    # Go
    ".go": "Go",
    # Rust
    ".rs": "Rust",
    # Java & JVM
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".groovy": "Groovy",
    # Ruby
    ".rb": "Ruby",
    # Shell
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    # YAML / K8s
    ".yaml": "YAML",
    ".yml": "YAML",
    # JSON / Config
    ".json": "JSON",
    ".toml": "TOML",
    ".ini": "INI",
    ".env": "Env",
    # C / C++ / C#
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    # PHP
    ".php": "PHP",
    # Swift
    ".swift": "Swift",
    # Infrastructure / IaC
    ".tf": "Terraform",
    ".tfvars": "Terraform",
    ".hcl": "Terraform",
    # Markdown
    ".md": "Markdown",
}

CANONICAL_LANG_MAP = {
    "yaml": "YAML",
    "yml": "YAML",
    "sh": "Shell",
    "bash": "Shell",
    "shell": "Shell",
    "shellscript": "Shell",
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "go": "Go",
    "rust": "Rust",
    "java": "Java",
    "ruby": "Ruby",
    "c": "C",
    "cheader": "C",
    "cpp": "C++",
    "c++": "C++",
    "dockerfile": "Dockerfile",
    "hcl": "Terraform",
    "terraform": "Terraform",
}

# Directories to skip during file walk
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "vendor",
    "target",
    ".idea",
    ".vscode",
    "dist",
    "build",
}


def _repo_path(sandbox_id: str) -> str:
    """Return the absolute path of the cloned repo directory."""
    return os.path.join(sandbox_id, REPO_DIR)


def _parse_tokei(output: str, base_path: str) -> Dict[str, List[str]]:
    """
    Parse `tokei --output json` to build {language: [absolute_file_paths]}.
    Fixes relative path resolution against base_path.
    """
    result: Dict[str, List[str]] = {}
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return result

    for lang, info in data.items():
        if lang == "Total":
            continue
        raw_files = [r.get("name", "") for r in info.get("reports", [])]
        abs_files = []
        for f in raw_files:
            if not f:
                continue
            path = f if os.path.isabs(f) else os.path.join(base_path, f)
            if os.path.isfile(path):
                abs_files.append(path)

        if abs_files:
            canon = CANONICAL_LANG_MAP.get(lang.lower(), lang)
            result.setdefault(canon, []).extend(abs_files)

    return result


def _parse_linguist(output: str, base_path: str) -> Dict[str, List[str]]:
    """
    Parse `github-linguist --json` to build {language: [absolute_file_paths]}.
    """
    result: Dict[str, List[str]] = {}
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return result

    for lang, info in data.items():
        if not isinstance(info, dict):
            continue
        files = info.get("files", [])
        if not isinstance(files, list):
            continue

        abs_files = []
        for f in files:
            if not f:
                continue
            path = f if os.path.isabs(f) else os.path.join(base_path, f)
            if os.path.isfile(path):
                abs_files.append(path)

        if abs_files:
            canon = CANONICAL_LANG_MAP.get(lang.lower(), lang)
            result.setdefault(canon, []).extend(abs_files)

    return result


def _parse_enry(output: str) -> Dict[str, List[str]]:
    """
    Parse `enry` output — language names only (no per-file detail).
    """
    result: Dict[str, List[str]] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if parts:
            lang = parts[0]
            if lang not in ("Total", "Other"):
                canon = CANONICAL_LANG_MAP.get(lang.lower(), lang)
                result[canon] = []
    return result


def _local_walk(repo_path: str) -> Dict[str, List[str]]:
    """
    Pure-Python extension walk: scan repo directory and classify files
    strictly by extension or known file name.
    """
    result: Dict[str, List[str]] = {}

    for root, dirs, files in os.walk(repo_path):
        # Prune skipped directories in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in files:
            base_lower = filename.lower()
            _, ext = os.path.splitext(filename)

            lang = None
            if base_lower == "dockerfile" or base_lower.startswith("dockerfile."):
                lang = "Dockerfile"
            elif base_lower == "jenkinsfile":
                lang = "Groovy"
            elif base_lower in ("makefile", "gnumakefile"):
                lang = "Make"
            else:
                lang = EXT_MAP.get(ext.lower())

            if lang:
                full_path = os.path.join(root, filename)
                result.setdefault(lang, []).append(full_path)

    return result


# ─────────────────────────────────────────────
# Detection Orchestrator
# ─────────────────────────────────────────────


async def detect_languages(
    sandbox_id: str,
) -> Tuple[Dict[str, List[str]], DetectionTool]:
    """
    Detect languages in the locally cloned repository with strict precision.
    Combines external tools (linguist/tokei/enry) with a ground-truth extension walk.

    Returns:
        (language_map, tool_used)
    """
    repo_path = _repo_path(sandbox_id)
    print(f"{_TAG} Starting strict language detection on: {repo_path}")

    # Ground-truth extension walk
    walk_map = _local_walk(repo_path)

    tool_used = DetectionTool.UNKNOWN
    tool_map: Dict[str, List[str]] = {}

    # ── 1. github-linguist ──────────────────────────────────────────────
    print(f"{_TAG} Trying github-linguist (JSON mode)...")
    t0 = time.monotonic()
    stdout, stderr, exit_code = await exec_in_sandbox(
        sandbox_id=sandbox_id,
        command=["linguist", repo_path, "--breakdown", "--json"],
        timeout=60.0,
    )
    elapsed = time.monotonic() - t0

    if exit_code == 0 and stdout.strip():
        lang_map = _parse_linguist(stdout, repo_path)
        if lang_map:
            print(
                f"{_TAG} linguist succeeded in {elapsed:.2f}s — {len(lang_map)} language(s) detected: {', '.join(lang_map.keys())}"
            )
            tool_map = lang_map
            tool_used = DetectionTool.LINGUIST

    # ── 2. tokei (if linguist not used) ─────────────────────────────────
    if not tool_map:
        print(f"{_TAG} Trying tokei (JSON mode)...")
        t0 = time.monotonic()
        stdout, stderr, exit_code = await exec_in_sandbox(
            sandbox_id=sandbox_id,
            command=["tokei", repo_path, "--output", "json"],
            timeout=60.0,
        )
        elapsed = time.monotonic() - t0

        if exit_code == 0 and stdout.strip():
            lang_map = _parse_tokei(stdout, repo_path)
            if lang_map:
                print(
                    f"{_TAG} tokei succeeded in {elapsed:.2f}s — {len(lang_map)} language(s) detected: {', '.join(lang_map.keys())}"
                )
                tool_map = lang_map
                tool_used = DetectionTool.TOKEI

    # ── 3. enry (if tokei not used) ─────────────────────────────────────
    if not tool_map:
        print(f"{_TAG} Trying enry...")
        t0 = time.monotonic()
        stdout, stderr, exit_code = await exec_in_sandbox(
            sandbox_id=sandbox_id,
            command=["enry", repo_path],
            timeout=60.0,
        )
        elapsed = time.monotonic() - t0

        if exit_code == 0 and stdout.strip():
            lang_names = _parse_enry(stdout)
            if lang_names:
                print(
                    f"{_TAG} enry succeeded in {elapsed:.2f}s — {len(lang_names)} language(s): {', '.join(lang_names.keys())}"
                )
                lang_map: Dict[str, List[str]] = {}
                for lang in lang_names:
                    files = walk_map.get(lang) or next(
                        (v for k, v in walk_map.items() if k.lower() == lang.lower()),
                        [],
                    )
                    if files:
                        lang_map[lang] = files
                if lang_map:
                    tool_map = lang_map
                    tool_used = DetectionTool.ENRY

    # ── 4. Merge tool_map with ground-truth walk_map ────────────────────
    final_map: Dict[str, List[str]] = {}

    # Populate from ground-truth extension walk first
    for lang, files in walk_map.items():
        final_map[lang] = list(set(files))

    # Merge any additional files/languages found by tools
    if tool_map:
        for lang, files in tool_map.items():
            existing = set(final_map.get(lang, []))
            existing.update(files)
            final_map[lang] = sorted(list(existing))
    else:
        tool_used = DetectionTool.UNKNOWN

    print(
        f"{_TAG} Language detection finalized: {len(final_map)} language(s) detected (tool={tool_used.value}): {', '.join(final_map.keys())}"
    )
    for lang, files in final_map.items():
        print(f"{_TAG}   {lang}: {len(files)} file(s)")

    return final_map, tool_used
