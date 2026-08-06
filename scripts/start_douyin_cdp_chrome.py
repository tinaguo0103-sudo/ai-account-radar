#!/usr/bin/env python3
"""Start or check a dedicated Douyin Chrome CDP profile.

This helper keeps the Douyin source-watch browser separate from the user's
daily Chrome profile. It does not read or export cookies; it only starts Chrome
with a local remote debugging port so the CDP probe can open pages.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

from douyin_chrome_runtime import configured_profile, listener_pids, verify_listener_identity, write_identity_marker


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = configured_profile()
DEFAULT_URL = "https://www.douyin.com/"
CHROME_BUNDLE_ID = "com.google.Chrome"
DEFAULT_CHROME_APP = Path("/Applications/Google Chrome.app")


def chrome_app_path() -> Path:
    configured = os.getenv("CHROME_APP_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CHROME_APP


def cdp_version(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def open_cdp_target(port: int, url: str) -> dict | None:
    try:
        encoded = urllib.parse.quote(url, safe=":/?&=%#")
        request = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{encoded}", method="PUT")
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def activate_chrome() -> None:
    subprocess.run(
        ["/usr/bin/osascript", "-e", f'tell application id "{CHROME_BUNDLE_ID}" to activate'],
        text=True,
        capture_output=True,
        check=False,
    )


def chrome_arg_list(port: int, profile: Path, url: str, mode: str) -> list[str]:
    cmd = [
        "--remote-allow-origins=*",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if mode == "hidden":
        cmd.append("--start-minimized")
    cmd.append(url)
    return cmd


def chrome_app_launch_command(app_path: Path, port: int, profile: Path, url: str, mode: str) -> list[str]:
    cmd = ["/usr/bin/open", "-n"]
    if mode == "hidden":
        # -g avoids stealing focus; -j asks LaunchServices to hide the app at launch.
        cmd.extend(["-g", "-j"])
    cmd.extend(["-a", str(app_path), "--args", *chrome_arg_list(port, profile, url, mode)])
    return cmd


def launch_status(ok: bool, identity: object | None) -> str:
    return "started" if ok else (str(getattr(identity, "status")) if identity is not None else "launch_failed_or_not_ready")


def launch_chrome(port: int, profile: Path, url: str, mode: str) -> tuple[subprocess.CompletedProcess, Path]:
    profile = profile.expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / ".local_services" / f"douyin-chrome-{mode}-{port}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = chrome_app_launch_command(chrome_app_path(), port, profile, url, mode)
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    log_path.write_text(
        "\n".join([
            f"command: {' '.join(cmd)}",
            f"returncode: {proc.returncode}",
            f"stdout: {proc.stdout[-2000:]}",
            f"stderr: {proc.stderr[-2000:]}",
            "",
        ]),
        encoding="utf-8",
    )
    return proc, log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Start/check dedicated Douyin Chrome CDP profile.")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--mode", choices=["hidden", "foreground"], default="hidden", help="hidden opens a background/minimized app; foreground is for login/verification.")
    parser.add_argument("--foreground", action="store_true", help="Alias for --mode foreground, useful for login/verification.")
    parser.add_argument("--hidden-window", action="store_true", help="Alias for --mode hidden, the normal background sampling mode.")
    parser.add_argument("--check-only", action="store_true", help="Only check whether CDP is already available.")
    parser.add_argument("--wait-seconds", type=float, default=4.0)
    args = parser.parse_args()
    profile_path = configured_profile()
    if args.foreground:
        args.mode = "foreground"
    if args.hidden_window:
        args.mode = "hidden"

    existing = cdp_version(args.port)
    if existing:
        identity = verify_listener_identity(args.port, profile_path, existing)
        if not identity.ok:
            print(json.dumps({"ok": False, **identity.to_dict()}, ensure_ascii=False, indent=2))
            return 3
        opened_target = None
        if args.mode == "foreground":
            opened_target = open_cdp_target(args.port, args.url)
            activate_chrome()
            time.sleep(args.wait_seconds)
        print(json.dumps({
            "ok": True,
            "status": "already_running_foreground" if args.mode == "foreground" else "already_running",
            "cdp": f"http://127.0.0.1:{args.port}",
            "browser": existing.get("Browser", ""),
            "profile": identity.actual_profile,
            "profile_identity": identity.to_dict(),
            "mode": args.mode,
            "opened_url": args.url if opened_target else "",
            "opened_target_id": (opened_target or {}).get("id", ""),
            "next_probe": f"node scripts/douyin_cdp_source_watch_probe.mjs --cdp http://127.0.0.1:{args.port} --account-limit 3 --video-limit 3",
        }, ensure_ascii=False, indent=2))
        return 0

    if args.check_only:
        print(json.dumps({
            "ok": False,
            "status": "not_running",
            "cdp": f"http://127.0.0.1:{args.port}",
            "next_step": "Run without --check-only to launch the dedicated Chrome profile. Default mode is hidden; use --foreground only for login/verification.",
        }, ensure_ascii=False, indent=2))
        return 2

    app_path = chrome_app_path()
    if not app_path.exists():
        print(json.dumps({
            "ok": False,
            "status": "chrome_app_not_found",
            "chrome_app_path": str(app_path),
            "default_chrome_app_path": str(DEFAULT_CHROME_APP),
            "env_var": "CHROME_APP_PATH",
            "next_step": "Set CHROME_APP_PATH to Google Chrome.app or install Google Chrome in /Applications.",
        }, ensure_ascii=False, indent=2))
        return 1
    launch_error = ""
    try:
        proc, log_path = launch_chrome(args.port, profile_path, args.url, args.mode)
    except OSError as exc:
        launch_error = str(exc)
        proc = None
        log_path = ROOT / ".local_services" / f"douyin-chrome-{args.mode}-{args.port}.log"
    stdout = (getattr(proc, "stdout", "") or "")[-1000:]
    stderr = ((getattr(proc, "stderr", "") or "")[-1000:]) if not launch_error else launch_error
    time.sleep(args.wait_seconds)
    version = cdp_version(args.port)
    identity = None
    marker_path = ""
    if version:
        pids = listener_pids(args.port)
        if len(pids) == 1:
            try:
                marker_path = str(write_identity_marker(args.port, profile_path, pids[0], version))
            except Exception as exc:
                launch_error = f"identity_marker_write_failed:{exc}"
        identity = verify_listener_identity(args.port, profile_path, version)
    ok = version is not None and identity is not None and identity.ok
    print(json.dumps({
        "ok": ok,
        "status": launch_status(ok, identity),
        "cdp": f"http://127.0.0.1:{args.port}",
        "browser": (version or {}).get("Browser", ""),
        "profile": (identity.actual_profile if identity else str(profile_path)),
        "profile_identity": identity.to_dict() if identity else {},
        "identity_marker_path": marker_path,
        "mode": args.mode,
        "launch_strategy": "app_path",
        "chrome_app_path": str(app_path),
        "chrome_bundle_id": CHROME_BUNDLE_ID,
        "pid": getattr(proc, "pid", None),
        "stdout": stdout,
        "stderr": stderr,
        "log_path": str(log_path),
        "login_note": "Default hidden mode starts the dedicated Chrome in the background. If Douyin asks for login/verification, rerun with --foreground. Do not share QR/cookie/token in chat.",
        "next_probe": f"node scripts/douyin_cdp_source_watch_probe.mjs --cdp http://127.0.0.1:{args.port} --account-limit 3 --video-limit 3",
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
