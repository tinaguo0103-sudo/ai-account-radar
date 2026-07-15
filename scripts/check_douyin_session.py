#!/usr/bin/env python3
"""Read-only identity and login preflight for the dedicated Douyin browser."""
from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

from douyin_chrome_runtime import DEFAULT_PORT, configured_profile, verify_listener_identity

ROOT = Path(__file__).resolve().parents[1]


def cdp_version(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def preflight(port: int, profile: Path, runner=subprocess.run) -> tuple[int, dict]:
    identity = verify_listener_identity(port, profile, cdp_version(port))
    payload = {
        "ok": False,
        "status": identity.status,
        "login_state": "indeterminate",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "identity": identity.to_dict(),
        "page": {"url": "", "title": ""},
        "dom_markers": {},
        "secrets_read": False,
    }
    if not identity.ok:
        return 3, payload
    result = runner(
        ["node", str(ROOT / "scripts" / "douyin_login_dom_probe.mjs"), "--cdp", f"http://127.0.0.1:{port}"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        login = json.loads((result.stdout or "").strip())
    except json.JSONDecodeError:
        login = {"state": "indeterminate", "markers": {}, "error": "malformed_dom_probe_output"}
    state = str(login.get("state") or "indeterminate")
    payload.update({
        "ok": result.returncode == 0 and state == "logged_in",
        "status": "session_verified" if state == "logged_in" else "login_preflight_failed",
        "login_state": state,
        "page": {"url": str(login.get("url") or ""), "title": str(login.get("title") or "")},
        "dom_markers": {str(key): bool(value) for key, value in (login.get("markers") or {}).items()},
        "error": str(login.get("error") or ""),
    })
    return (0 if payload["ok"] else 4), payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Check canonical Douyin Chrome identity and login state without reading secrets.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    code, payload = preflight(args.port, configured_profile())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
