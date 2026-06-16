#!/usr/bin/env python3
"""Start or check a dedicated Douyin Chrome CDP profile.

This helper keeps the Douyin source-watch browser separate from the user's
daily Chrome profile. It does not read or export cookies; it only starts Chrome
with a local remote debugging port so the CDP probe can open pages.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / ".local_services" / "douyin-chrome-profile"
DEFAULT_URL = "https://www.douyin.com/"
CHROME_APP = "Google Chrome"


def cdp_version(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def launch_chrome(port: int, profile: Path, url: str, foreground: bool) -> subprocess.CompletedProcess:
    profile.mkdir(parents=True, exist_ok=True)
    cmd = ["/usr/bin/open"]
    if not foreground:
        # -g avoids stealing focus; -j asks LaunchServices to hide the app at launch.
        cmd.extend(["-g", "-j"])
    cmd.extend([
        "-na",
        CHROME_APP,
        "--args",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ])
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start/check dedicated Douyin Chrome CDP profile.")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--foreground", action="store_true", help="Bring Chrome to foreground for login/verification.")
    parser.add_argument("--check-only", action="store_true", help="Only check whether CDP is already available.")
    parser.add_argument("--wait-seconds", type=float, default=4.0)
    args = parser.parse_args()

    existing = cdp_version(args.port)
    if existing:
        print(json.dumps({
            "ok": True,
            "status": "already_running",
            "cdp": f"http://127.0.0.1:{args.port}",
            "browser": existing.get("Browser", ""),
            "profile": str(Path(args.profile)),
            "next_probe": f"node scripts/douyin_cdp_source_watch_probe.mjs --cdp http://127.0.0.1:{args.port} --account-limit 3 --video-limit 3",
        }, ensure_ascii=False, indent=2))
        return 0

    if args.check_only:
        print(json.dumps({
            "ok": False,
            "status": "not_running",
            "cdp": f"http://127.0.0.1:{args.port}",
            "next_step": "Run without --check-only to launch the dedicated Chrome profile.",
        }, ensure_ascii=False, indent=2))
        return 2

    proc = launch_chrome(args.port, Path(args.profile), args.url, args.foreground)
    time.sleep(args.wait_seconds)
    version = cdp_version(args.port)
    ok = version is not None
    print(json.dumps({
        "ok": ok,
        "status": "started" if ok else "launch_failed_or_not_ready",
        "cdp": f"http://127.0.0.1:{args.port}",
        "browser": (version or {}).get("Browser", ""),
        "profile": str(Path(args.profile)),
        "foreground": args.foreground,
        "stdout": proc.stdout[-1000:],
        "stderr": proc.stderr[-1000:],
        "login_note": "If Douyin asks for login/verification, rerun with --foreground or open the dedicated Chrome window manually. Do not share QR/cookie/token in chat.",
        "next_probe": f"node scripts/douyin_cdp_source_watch_probe.mjs --cdp http://127.0.0.1:{args.port} --account-limit 3 --video-limit 3",
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
