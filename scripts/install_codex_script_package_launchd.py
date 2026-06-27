#!/usr/bin/env python3
"""Install or remove a macOS launchd job for local Codex script package generation."""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.austin.ai-account-radar.codex-script-packages"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
PYTHON = "/usr/bin/python3"
CODEX_BIN = "/Applications/Codex.app/Contents/Resources/codex"


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def launchctl_target() -> str:
    return f"gui/{os.getuid()}"


def bootout() -> None:
    if not PLIST_PATH.exists():
        return
    run(["launchctl", "bootout", launchctl_target(), str(PLIST_PATH)], check=False)


def bootstrap() -> None:
    run(["launchctl", "bootstrap", launchctl_target(), str(PLIST_PATH)])
    run(["launchctl", "enable", f"{launchctl_target()}/{LABEL}"], check=False)


def kickstart() -> None:
    run(["launchctl", "kickstart", "-k", f"{launchctl_target()}/{LABEL}"], check=False)


def build_plist(interval_seconds: int, limit: int, run_at_load: bool) -> dict[str, object]:
    log_dir = ROOT / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LABEL,
        "ProgramArguments": [
            PYTHON,
            str(ROOT / "scripts" / "codex_script_package_runner.py"),
            "--write-feishu",
            "--limit",
            str(limit),
            "--only-today",
        ],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "PATH": "/Applications/Codex.app/Contents/Resources:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "CODEX_BIN": CODEX_BIN,
            "CODEX_SCRIPT_PACKAGE_LIMIT": str(limit),
        },
        "StartInterval": int(interval_seconds),
        "RunAtLoad": bool(run_at_load),
        "StandardOutPath": str(log_dir / "launchd_codex_script_packages.out.log"),
        "StandardErrorPath": str(log_dir / "launchd_codex_script_packages.err.log"),
    }


def install(interval_minutes: int, limit: int, run_at_load: bool, dry_run: bool) -> None:
    interval_seconds = max(300, int(interval_minutes) * 60)
    plist = build_plist(interval_seconds, limit, run_at_load)
    if dry_run:
        print(plist)
        return
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    bootout()
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(plist, handle)
    bootstrap()
    if run_at_load:
        kickstart()
    print(f"installed {LABEL} -> {PLIST_PATH}")


def uninstall(dry_run: bool) -> None:
    if dry_run:
        print(f"would uninstall {LABEL} at {PLIST_PATH}")
        return
    bootout()
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print(f"uninstalled {LABEL}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-minutes", type=int, default=30, help="Run interval. Default 30 minutes.")
    parser.add_argument("--limit", type=int, default=2, help="Max topics per run. Default 2.")
    parser.add_argument("--no-run-at-load", action="store_true", help="Do not kick off immediately after install.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the launchd job.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.uninstall:
        uninstall(args.dry_run)
    else:
        install(args.interval_minutes, args.limit, not args.no_run_at_load, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
