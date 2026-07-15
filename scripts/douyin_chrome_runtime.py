#!/usr/bin/env python3
"""Fail-closed identity helpers for the dedicated Douyin Chrome runtime."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


CANONICAL_PROFILE = Path.home() / ".codex" / "ai-account-radar-runtime" / "browser_profiles" / "douyin-chrome-profile"
DEFAULT_PORT = 9333


def configured_profile() -> Path:
    value = os.getenv("DOUYIN_CHROME_PROFILE_DIR", "").strip()
    return Path(value).expanduser().resolve() if value else CANONICAL_PROFILE.resolve()


def profile_identity_hash(profile: Path) -> str:
    return hashlib.sha256(str(profile.expanduser().resolve()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProcessIdentity:
    ok: bool
    status: str
    port: int
    pid: int | None
    expected_profile: str
    actual_profile: str
    actual_port: int | None
    profile_identity_hash: str
    profile_open_file_proof: bool = False
    profile_open_file_count: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def listener_pids(port: int) -> list[int]:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"lsof_failed:{result.stderr.strip()}")
    return sorted({int(line) for line in result.stdout.splitlines() if line.strip().isdigit()})


def process_open_file_names(pid: int) -> list[str]:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-Fn"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"lsof_open_files_failed:{result.stderr.strip()}")
    return [line[1:] for line in result.stdout.splitlines() if line.startswith("n/")]


def profile_open_file_proof(
    pid: int,
    expected_profile: Path,
    *,
    open_file_reader: Callable[[int], list[str]] = process_open_file_names,
) -> tuple[bool, int, str, str]:
    expected = expected_profile.expanduser().resolve()
    try:
        paths = [Path(value).expanduser() for value in open_file_reader(pid)]
    except Exception as exc:
        return False, 0, "", str(exc)
    within = [path for path in paths if path == expected or expected in path.parents]
    has_root_marker = any(
        (expected / "BrowserMetrics") in path.parents
        or (path.parent == expected and path.name == "Local State")
        for path in within
    )
    has_default_file = any((expected / "Default") in path.parents for path in within)
    inferred: list[str] = []
    for path in paths:
        parts = path.parts
        if "Default" in parts:
            index = parts.index("Default")
            inferred.append(str(Path(*parts[:index])))
        elif "BrowserMetrics" in parts:
            index = parts.index("BrowserMetrics")
            inferred.append(str(Path(*parts[:index])))
        elif path.name == "Local State":
            inferred.append(str(path.parent))
    actual = max(set(inferred), key=inferred.count) if inferred else ""
    ok = len(within) >= 2 and has_root_marker and has_default_file
    reason = "" if ok else "insufficient_expected_profile_open_file_evidence"
    return ok, len(within), actual, reason


def identity_marker_path(profile: Path, port: int) -> Path:
    return profile.expanduser().resolve().parent / f"douyin-cdp-{port}.identity.json"


def browser_websocket_identity(version: dict) -> str:
    return str(version.get("webSocketDebuggerUrl") or "").strip()


def write_identity_marker(
    port: int,
    profile: Path,
    pid: int,
    version: dict,
    *,
    open_file_reader: Callable[[int], list[str]] = process_open_file_names,
) -> Path:
    profile = profile.expanduser().resolve()
    proof_ok, proof_count, actual_profile, proof_error = profile_open_file_proof(
        pid, profile, open_file_reader=open_file_reader
    )
    if not proof_ok:
        raise RuntimeError(f"profile_open_file_proof_failed:actual={actual_profile}:count={proof_count}:{proof_error}")
    marker = identity_marker_path(profile, port)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "marker_version": 1,
        "port": port,
        "pid": pid,
        "profile": str(profile),
        "profile_identity_hash": profile_identity_hash(profile),
        "browser": str(version.get("Browser") or ""),
        "browser_websocket_identity": browser_websocket_identity(version),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "created_by": "start_douyin_cdp_chrome.py",
        "profile_open_file_count_at_creation": proof_count,
    }
    if not payload["browser_websocket_identity"]:
        raise RuntimeError("browser_websocket_identity_missing")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=marker.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, marker)
    return marker


def verify_listener_identity(
    port: int,
    expected_profile: Path,
    current_version: dict | None,
    *,
    pid_reader: Callable[[int], list[int]] = listener_pids,
    marker_reader: Callable[[Path], dict] | None = None,
    open_file_reader: Callable[[int], list[str]] = process_open_file_names,
) -> ProcessIdentity:
    expected = expected_profile.expanduser().resolve()
    base = {
        "port": port,
        "expected_profile": str(expected),
        "profile_identity_hash": profile_identity_hash(expected),
    }
    try:
        pids = pid_reader(port)
    except Exception as exc:
        return ProcessIdentity(False, "process_identity_unverifiable", pid=None, actual_profile="", actual_port=None, error=str(exc), **base)
    if len(pids) != 1:
        status = "not_running" if not pids else "process_identity_unverifiable"
        return ProcessIdentity(False, status, pid=pids[0] if len(pids) == 1 else None, actual_profile="", actual_port=None, error=f"listener_pid_count={len(pids)}", **base)
    pid = pids[0]
    proof_ok, proof_count, proof_actual, proof_error = profile_open_file_proof(
        pid, expected, open_file_reader=open_file_reader
    )
    if not proof_ok:
        return ProcessIdentity(False, "profile_identity_mismatch", pid=pid, actual_profile=proof_actual, actual_port=None, profile_open_file_proof=False, profile_open_file_count=proof_count, error=proof_error, **base)
    marker_path = identity_marker_path(expected, port)
    try:
        marker = marker_reader(marker_path) if marker_reader else json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return ProcessIdentity(False, "identity_marker_missing_or_invalid", pid=pid, actual_profile=proof_actual, actual_port=None, profile_open_file_proof=True, profile_open_file_count=proof_count, error=str(exc), **base)
    actual_text = str(marker.get("profile") or "")
    actual_port = marker.get("port") if isinstance(marker.get("port"), int) else None
    current_ws = browser_websocket_identity(current_version or {})
    checks = {
        "marker_version": marker.get("marker_version") == 1,
        "profile": actual_text == str(expected),
        "profile_hash": marker.get("profile_identity_hash") == profile_identity_hash(expected),
        "port": actual_port == port,
        "pid": marker.get("pid") == pid,
        "browser_websocket_identity": bool(current_ws) and marker.get("browser_websocket_identity") == current_ws,
        "created_by": marker.get("created_by") == "start_douyin_cdp_chrome.py",
    }
    if not all(checks.values()):
        return ProcessIdentity(False, "profile_identity_mismatch", pid=pid, actual_profile=actual_text, actual_port=actual_port, profile_open_file_proof=True, profile_open_file_count=proof_count, error="identity_marker_mismatch:" + ",".join(key for key, value in checks.items() if not value), **base)
    return ProcessIdentity(True, "profile_identity_verified", pid=pid, actual_profile=actual_text, actual_port=actual_port, profile_open_file_proof=True, profile_open_file_count=proof_count, **base)
