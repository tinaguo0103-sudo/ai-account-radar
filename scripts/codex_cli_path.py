#!/usr/bin/env python3
"""Resolve a supported installed Codex CLI without pinning stale app paths."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable


APP_CANDIDATES = (
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
)


def executable_path(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    if "/" not in candidate:
        return shutil.which(candidate) or ""
    path = Path(candidate).expanduser()
    return str(path) if path.is_file() and os.access(path, os.X_OK) else ""


def resolve_codex_cli(
    configured: str = "",
    app_candidates: Iterable[str] = APP_CANDIDATES,
) -> str:
    checked: list[str] = []

    def check(value: str) -> str:
        rendered = value.strip()
        if not rendered or rendered in checked:
            return ""
        checked.append(rendered)
        return executable_path(rendered)

    resolved = check(configured)
    if resolved:
        return resolved
    for candidate in app_candidates:
        resolved = check(str(candidate))
        if resolved:
            return resolved
    resolved = check("codex")
    if resolved:
        return resolved
    raise FileNotFoundError(f"Codex CLI executable not found; checked: {checked}")
