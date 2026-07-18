#!/usr/bin/env python3
"""Resolve a supported installed Codex CLI without pinning stale app paths."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping


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


def codex_runtime_diagnostics(
    configured: str = "",
    *,
    env: Mapping[str, str] | None = None,
    app_candidates: Iterable[str] = APP_CANDIDATES,
) -> dict[str, Any]:
    runtime_env = os.environ if env is None else env
    home = Path(runtime_env.get("HOME") or str(Path.home())).expanduser()
    codex_home = Path(runtime_env.get("CODEX_HOME") or str(home / ".codex")).expanduser()
    state_db = codex_home / "state_5.sqlite"
    reasons: list[str] = []
    try:
        binary = resolve_codex_cli(configured or runtime_env.get("CODEX_BIN", ""), app_candidates)
    except FileNotFoundError:
        binary = ""
        reasons.append("codex_executable_not_found")

    def inspect(path: Path, expected: str) -> dict[str, Any]:
        try:
            info = path.stat()
        except OSError:
            reasons.append(f"{expected}_missing_or_unreadable")
            return {"path": str(path), "exists": False, "owner_uid": None, "writable": False}
        type_ok = stat.S_ISDIR(info.st_mode) if expected == "state_parent" else stat.S_ISREG(info.st_mode)
        owner_ok = info.st_uid == os.getuid()
        mode_writable = bool(info.st_mode & stat.S_IWUSR) if owner_ok else False
        current_process_writable = os.access(path, os.W_OK)
        if not type_ok:
            reasons.append(f"{expected}_type_invalid")
        if not owner_ok:
            reasons.append(f"{expected}_owner_mismatch")
        if not mode_writable:
            reasons.append(f"{expected}_not_writable")
        return {
            "path": str(path),
            "exists": True,
            "owner_uid": info.st_uid,
            "current_uid": os.getuid(),
            "type_ok": type_ok,
            "owner_ok": owner_ok,
            "writable": mode_writable,
            "current_process_writable": current_process_writable,
        }

    parent_report = inspect(codex_home, "state_parent")
    state_report = inspect(state_db, "state_db")
    return {
        "ok": not reasons,
        "error": "" if not reasons else "codex_runtime_unavailable",
        "reasons": reasons,
        "codex_bin": binary,
        "home": str(home),
        "codex_home": str(codex_home),
        "state_parent": parent_report,
        "state_db": state_report,
        "secrets_read": False,
        "state_contents_read": False,
    }
