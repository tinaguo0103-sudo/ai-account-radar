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


def parse_dom_probe_output(stdout: str) -> tuple[dict, str]:
    raw = (stdout or "").strip()
    if not raw:
        return {"state": "indeterminate", "markers": {}}, "empty_dom_probe_output"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"state": "indeterminate", "markers": {}}, "malformed_dom_probe_output"
    if not isinstance(payload, dict):
        return {"state": "indeterminate", "markers": {}}, "malformed_dom_probe_output"
    return payload, ""


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
    login, parse_error = parse_dom_probe_output(result.stdout or "")
    state = str(login.get("state") or "indeterminate")
    expected_exit = 0 if state == "logged_in" else 4
    exit_error = "" if result.returncode == expected_exit else f"unexpected_dom_probe_exit:{result.returncode}:expected:{expected_exit}"
    probe_error = parse_error or exit_error or str(login.get("error") or "")
    probe_ok = not parse_error and not exit_error and state == "logged_in"
    payload.update({
        "ok": probe_ok,
        "status": "session_verified" if probe_ok else "login_preflight_failed",
        "login_state": state,
        "page": {"url": str(login.get("url") or ""), "title": str(login.get("title") or "")},
        "dom_markers": {str(key): bool(value) for key, value in (login.get("markers") or {}).items()},
        "error": probe_error,
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
