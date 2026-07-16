#!/usr/bin/env python3
"""Launch/check the dedicated wewe-rss admin browser; never used by scheduled collection."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from douyin_chrome_runtime import listener_pids, profile_open_file_proof
from wewe_admin_chrome_runtime import DEFAULT_PORT, configured_profile, marker_path, path_hash, verify_identity

CHROME = Path("/Applications/Google Chrome.app")
ADMIN_URL = "http://127.0.0.1:4000/dash"


def cdp_version(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            value = json.loads(response.read())
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def write_marker(port: int, profile: Path, pid: int, version: dict) -> Path:
    proof, count, actual, error = profile_open_file_proof(pid, profile)
    if not proof:
        raise RuntimeError(f"profile_open_file_proof_failed:actual={actual}:count={count}:{error}")
    websocket = str(version.get("webSocketDebuggerUrl") or "")
    if not websocket:
        raise RuntimeError("browser_websocket_identity_missing")
    path = marker_path(profile, port); path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "marker_version": 1, "created_by": "start_wewe_rss_admin_chrome.py", "pid": pid, "port": port,
        "profile": str(profile), "profile_identity_hash": path_hash(profile),
        "browser_websocket_identity": websocket,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile_open_file_count_at_creation": count,
    }
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2); handle.write("\n"); temp = Path(handle.name)
    os.replace(temp, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=5)
    args = parser.parse_args()
    profile = configured_profile()
    if args.port != DEFAULT_PORT:
        print(json.dumps({"ok": False, "status": "noncanonical_port", "expected": DEFAULT_PORT, "actual": args.port})); return 4
    version = cdp_version(args.port)
    if version:
        identity = verify_identity(args.port, profile, version)
        print(json.dumps({"ok": identity.ok, **identity.to_dict(), "admin_url": ADMIN_URL, "secrets_read": False}, ensure_ascii=False, indent=2))
        return 0 if identity.ok else 4
    if args.check_only:
        print(json.dumps({"ok": False, "status": "not_running", "port": args.port, "profile": str(profile)}, ensure_ascii=False)); return 4
    if not args.foreground:
        print(json.dumps({"ok": False, "status": "foreground_required_for_manual_reauth"}, ensure_ascii=False)); return 4
    profile.mkdir(parents=True, exist_ok=True)
    command = ["/usr/bin/open", "-n", "-a", str(CHROME), "--args", f"--remote-debugging-port={args.port}", f"--user-data-dir={profile}", "--no-first-run", ADMIN_URL]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        print(json.dumps({"ok": False, "status": "launch_failed", "returncode": result.returncode})); return 4
    deadline = time.time() + args.wait_seconds
    while time.time() < deadline and not cdp_version(args.port): time.sleep(.25)
    version = cdp_version(args.port); pids = listener_pids(args.port)
    if not version or len(pids) != 1:
        print(json.dumps({"ok": False, "status": "launch_not_ready"})); return 4
    try: write_marker(args.port, profile, pids[0], version)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "status": "identity_proof_failed", "error": str(exc)})); return 4
    identity = verify_identity(args.port, profile, version)
    print(json.dumps({"ok": identity.ok, **identity.to_dict(), "admin_url": ADMIN_URL, "secrets_read": False}, ensure_ascii=False, indent=2))
    return 0 if identity.ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
