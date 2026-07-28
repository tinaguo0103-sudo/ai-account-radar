#!/usr/bin/env python3
"""Invoke the current active Skill through Codex and capture bounded provenance."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ACTIVE_ROOT = Path.home() / ".codex" / "skills"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(skills: list[str], payload: dict[str, Any], timeout: int = 600) -> tuple[dict[str, Any], list[dict[str, str]]]:
    identities = []
    for name in skills:
        path = ACTIVE_ROOT / name / "SKILL.md"
        if not path.is_file():
            raise RuntimeError(f"active_skill_missing:{name}")
        identities.append({"name": name, "path": str(path), "sha256": file_hash(path)})
    prompt = (
        "Use exactly these active Skills: " + ", ".join(f"${name}" for name in skills) +
        ". Do not use deterministic, historical, legacy, or fallback output. "
        "Return only one JSON object matching the output_contract in this input.\n" +
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    with tempfile.TemporaryDirectory(prefix="web010_skill_") as tmp:
        output = Path(tmp) / "result.json"
        command = [
            os.getenv("CODEX_BIN", "codex"), "exec", "--ephemeral",
            "--output-last-message", str(output), "-",
        ]
        result = subprocess.run(
            command, input=prompt, text=True, capture_output=True, timeout=timeout,
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError(f"active_skill_execution_failed:{result.returncode}")
        parsed = json.loads(output.read_text(encoding="utf-8"))
    return parsed, identities
