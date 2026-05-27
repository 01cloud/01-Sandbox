"""
language_detector.py — Language detection using local file system tools.

Since the repo is now cloned to a local temp directory, we run language
detection tools directly on the API pod via subprocess (exec_in_sandbox).

Priority order:
  1. tokei  — fast, JSON output with file-level breakdown
  2. enry   — lightweight Go port of linguist (if installed)
  3. Fallback — extension-based file walking (always available)

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
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".bash": "Shell",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".md": "Markdown",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C",
    ".cs": "C#",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
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
        files = [f for f in files if f and os.path.isfile(f)]
        if files:
            result[lang] = files

    return result


def _parse_enry(output: str) -> Dict[str, List[str]]:
    """
    Parse `enry` output — language names only (no per-file detail).
    Returns {language: []} — file list populated by fallback walk.
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
                result[lang] = []
    return result


def _local_walk(repo_path: str) -> Dict[str, List[str]]:
    """
    Pure-Python fallback: walk the repo directory and classify files
    by extension. Always succeeds — never requires external tools.
    """
    result: Dict[str, List[str]] = {}

    for root, dirs, files in os.walk(repo_path):
        # Prune skipped directories in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in files:
            _, ext = os.path.splitext(filename)
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
    Detect languages in the locally cloned repository.

    Returns:
        (language_map, tool_used)
        language_map: {"Python": ["/tmp/reposcanner_abc/repo/src/main.py", ...]}
        tool_used: which DetectionTool was successful
    """
    repo_path = _repo_path(sandbox_id)
    print(f"{_TAG} Starting language detection on: {repo_path}")

    # ── 1. tokei ────────────────────────────────────────────────────────
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
            for lang, files in lang_map.items():
                print(f"{_TAG}   {lang}: {len(files)} file(s)")
            return lang_map, DetectionTool.TOKEI
        else:
            print(
                f"{_TAG} tokei returned output but parsed 0 languages (elapsed={elapsed:.2f}s)"
            )
    else:
        print(
            f"{_TAG} tokei unavailable or failed (exit={exit_code}, elapsed={elapsed:.2f}s) — trying enry..."
        )
        if stderr.strip():
            print(f"{_TAG} tokei stderr: {stderr.strip()[:200]}")

    # ── 2. enry (if available) ──────────────────────────────────────────
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
            # enry gives no file list — fill it via extension walk
            walk_map = _local_walk(repo_path)
            lang_map: Dict[str, List[str]] = {}
            for lang in lang_names:
                files = walk_map.get(lang) or next(
                    (v for k, v in walk_map.items() if k.lower() == lang.lower()), []
                )
                if files:
                    lang_map[lang] = files
            if lang_map:
                print(
                    f"{_TAG} enry + extension walk resolved {len(lang_map)} language(s) with file lists"
                )
                return lang_map, DetectionTool.ENRY
    else:
        print(
            f"{_TAG} enry unavailable or failed (exit={exit_code}, elapsed={elapsed:.2f}s)"
        )
        if stderr.strip():
            print(f"{_TAG} enry stderr: {stderr.strip()[:200]}")

    # ── 3. Pure-Python extension walk (always works) ────────────────────
    print(
        f"{_TAG} Falling back to pure-Python extension walk (no external tools required)..."
    )
    t0 = time.monotonic()
    lang_map = _local_walk(repo_path)
    elapsed = time.monotonic() - t0

    if lang_map:
        print(
            f"{_TAG} Extension walk completed in {elapsed:.3f}s — {len(lang_map)} language(s): {', '.join(lang_map.keys())}"
        )
        for lang, files in lang_map.items():
            print(f"{_TAG}   {lang}: {len(files)} file(s)")
        return lang_map, DetectionTool.UNKNOWN

    print(
        f"{_TAG} Extension walk found 0 files — repository may be empty or binary-only"
    )
    return {}, DetectionTool.UNKNOWN
