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
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / ".local_services" / "douyin-chrome-profile"
DEFAULT_URL = "https://www.douyin.com/"
CHROME_APP = "Google Chrome"
CHROME_BINARY = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


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
        ["/usr/bin/osascript", "-e", f'tell application "{CHROME_APP}" to activate'],
        text=True,
        capture_output=True,
        check=False,
    )


def launch_headless_chrome(port: int, profile: Path, url: str) -> tuple[subprocess.Popen, Path]:
    profile = profile.expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / ".local_services" / f"douyin-chrome-headless-{port}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(CHROME_BINARY),
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--remote-allow-origins=*",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    handle = log_path.open("ab")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=handle)
    return proc, log_path


def launch_chrome(port: int, profile: Path, url: str, mode: str) -> subprocess.CompletedProcess:
    profile = profile.expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    cmd = ["/usr/bin/open"]
    if mode == "hidden":
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
        "--start-minimized",
        url,
    ])
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start/check dedicated Douyin Chrome CDP profile.")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--mode", choices=["hidden", "foreground", "headless"], default="hidden", help="hidden opens a background/minimized app; foreground is for login/verification; headless is experimental and may not work with Douyin login.")
    parser.add_argument("--foreground", action="store_true", help="Alias for --mode foreground, useful for login/verification.")
    parser.add_argument("--hidden-window", action="store_true", help="Alias for --mode hidden, the normal background sampling mode.")
    parser.add_argument("--headless", action="store_true", help="Alias for --mode headless. Experimental; use only when no login/verification is required.")
    parser.add_argument("--check-only", action="store_true", help="Only check whether CDP is already available.")
    parser.add_argument("--wait-seconds", type=float, default=4.0)
    args = parser.parse_args()
    profile_path = Path(args.profile).expanduser().resolve()
    if args.foreground:
        args.mode = "foreground"
    if args.hidden_window:
        args.mode = "hidden"
    if args.headless:
        args.mode = "headless"

    existing = cdp_version(args.port)
    if existing:
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
            "profile": str(profile_path),
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

    if args.mode == "headless":
        if not CHROME_BINARY.exists():
            print(json.dumps({
                "ok": False,
                "status": "chrome_binary_not_found",
                "path": str(CHROME_BINARY),
                "next_step": "Use --mode hidden or install Google Chrome in /Applications.",
            }, ensure_ascii=False, indent=2))
            return 1
        proc, log_path = launch_headless_chrome(args.port, profile_path, args.url)
        stdout = ""
        stderr = f"headless log: {log_path}"
    else:
        proc = launch_chrome(args.port, profile_path, args.url, args.mode)
        stdout = proc.stdout[-1000:]
        stderr = proc.stderr[-1000:]
    time.sleep(args.wait_seconds)
    version = cdp_version(args.port)
    ok = version is not None
    print(json.dumps({
        "ok": ok,
        "status": "started" if ok else "launch_failed_or_not_ready",
        "cdp": f"http://127.0.0.1:{args.port}",
        "browser": (version or {}).get("Browser", ""),
        "profile": str(profile_path),
        "mode": args.mode,
        "pid": getattr(proc, "pid", None),
        "stdout": stdout,
        "stderr": stderr,
        "log_path": str(log_path) if args.mode == "headless" else "",
        "login_note": "Default hidden mode starts the dedicated Chrome in the background. If Douyin asks for login/verification, rerun with --foreground. Do not share QR/cookie/token in chat.",
        "next_probe": f"node scripts/douyin_cdp_source_watch_probe.mjs --cdp http://127.0.0.1:{args.port} --account-limit 3 --video-limit 3",
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
