#!/usr/bin/env python3
"""Identity contract for the dedicated wewe-rss reauthentication browser."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from douyin_chrome_runtime import listener_pids, process_open_file_names, profile_open_file_proof

CANONICAL_PROFILE = Path.home() / ".codex" / "ai-account-radar-runtime" / "browser_profiles" / "wewe-rss-admin-chrome-profile"
DEFAULT_PORT = 9334


def configured_profile() -> Path:
    return CANONICAL_PROFILE.resolve()


def marker_path(profile: Path, port: int) -> Path:
    return profile.resolve().parent / f"wewe-rss-admin-cdp-{port}.identity.json"


def path_hash(profile: Path) -> str:
    return hashlib.sha256(str(profile.resolve()).encode()).hexdigest()


@dataclass(frozen=True)
class Identity:
    ok: bool
    status: str
    port: int
    pid: int | None
    expected_profile: str
    actual_profile: str
    profile_identity_hash: str
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def verify_identity(port: int, profile: Path, version: dict[str, Any] | None, *,
                    pid_reader: Callable[[int], list[int]] = listener_pids,
                    open_file_reader: Callable[[int], list[str]] = process_open_file_names,
                    marker_reader: Callable[[Path], dict[str, Any]] | None = None) -> Identity:
    expected = profile.resolve()
    base = {"port": port, "expected_profile": str(expected), "profile_identity_hash": path_hash(expected)}
    try:
        pids = pid_reader(port)
    except Exception as exc:
        return Identity(ok=False, status="process_identity_unverifiable", pid=None, actual_profile="", error=str(exc), **base)
    if len(pids) != 1:
        return Identity(ok=False, status="process_identity_unverifiable", pid=None, actual_profile="", error=f"listener_pid_count={len(pids)}", **base)
    pid = pids[0]
    proof, _, actual, error = profile_open_file_proof(pid, expected, open_file_reader=open_file_reader)
    if not proof:
        return Identity(ok=False, status="profile_identity_mismatch", pid=pid, actual_profile=actual, error=error, **base)
    path = marker_path(expected, port)
    try:
        marker = marker_reader(path) if marker_reader else json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return Identity(ok=False, status="identity_marker_missing_or_invalid", pid=pid, actual_profile=actual, error=str(exc), **base)
    websocket = str((version or {}).get("webSocketDebuggerUrl") or "")
    checks = (
        marker.get("marker_version") == 1,
        marker.get("created_by") == "start_wewe_rss_admin_chrome.py",
        marker.get("pid") == pid,
        marker.get("port") == port,
        marker.get("profile") == str(expected),
        marker.get("profile_identity_hash") == path_hash(expected),
        bool(websocket) and marker.get("browser_websocket_identity") == websocket,
    )
    if not all(checks):
        return Identity(ok=False, status="profile_identity_mismatch", pid=pid, actual_profile=str(marker.get("profile") or actual), error="marker_or_websocket_mismatch", **base)
    return Identity(ok=True, status="profile_identity_verified", pid=pid, actual_profile=str(expected), **base)
