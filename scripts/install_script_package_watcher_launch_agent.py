#!/usr/bin/env python3
"""Install/remove a macOS LaunchAgent for the 06 script package watcher."""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_DIR = Path.home() / ".codex" / "ai-account-radar-runtime"
LABEL = "com.austin.ai-account-radar.script-package-watcher"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "ai-account-radar"
CODEX_BIN = "/Applications/Codex.app/Contents/Resources/codex"
RUNTIME_DIRS = ("scripts", "config", "skills", "docs")
RUNTIME_FILES = ("README.md", ".env.local", ".env")


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def launchctl_target() -> str:
    return f"gui/{os.getuid()}"


def service_name() -> str:
    return f"{launchctl_target()}/{LABEL}"


def bootout() -> None:
    if not PLIST_PATH.exists():
        return
    run(["launchctl", "bootout", launchctl_target(), str(PLIST_PATH)], check=False)


def bootstrap() -> None:
    run(["launchctl", "bootstrap", launchctl_target(), str(PLIST_PATH)])
    run(["launchctl", "enable", service_name()], check=False)


def kickstart() -> None:
    run(["launchctl", "kickstart", "-k", service_name()], check=False)


def sync_runtime(runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for dirname in RUNTIME_DIRS:
        source = ROOT / dirname
        target = runtime_dir / dirname
        if target.exists():
            shutil.rmtree(target)
        if source.exists():
            shutil.copytree(source, target)
    for filename in RUNTIME_FILES:
        source_file = ROOT / filename
        if source_file.exists():
            shutil.copy2(source_file, runtime_dir / filename)
    (runtime_dir / "RUNTIME_SOURCE.txt").write_text(
        f"Synced from: {ROOT}\n"
        "This runtime copy is used by the macOS LaunchAgent to avoid Desktop TCC restrictions.\n",
        encoding="utf-8",
    )


def build_plist(runtime_dir: Path, interval_minutes: float, limit: int, max_age_days: int, python_bin: str) -> dict[str, object]:
    interval = max(1.0, float(interval_minutes))
    return {
        "Label": LABEL,
        "ProgramArguments": [
            python_bin,
            str(runtime_dir / "scripts" / "watch_script_package_queue.py"),
            "--interval-minutes",
            str(interval),
            "--limit",
            str(max(1, int(limit))),
            "--max-age-days",
            str(max(1, int(max_age_days))),
        ],
        "WorkingDirectory": str(runtime_dir),
        "EnvironmentVariables": {
            "PATH": "/Applications/Codex.app/Contents/Resources:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "CODEX_BIN": CODEX_BIN,
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(LOG_DIR / "script_package_watcher_launch_agent.out.log"),
        "StandardErrorPath": str(LOG_DIR / "script_package_watcher_launch_agent.err.log"),
    }


def install(args: argparse.Namespace) -> None:
    python_bin = args.python_bin or sys.executable
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    plist = build_plist(runtime_dir, args.interval_minutes, args.limit, args.max_age_days, python_bin)
    if args.dry_run:
        print(plist)
        return
    if not args.no_sync_runtime:
        sync_runtime(runtime_dir)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    Path(str(plist["StandardOutPath"])).write_text("", encoding="utf-8")
    Path(str(plist["StandardErrorPath"])).write_text("", encoding="utf-8")
    bootout()
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(plist, handle)
    bootstrap()
    if not args.no_kickstart:
        kickstart()
    print(f"installed {LABEL}")
    print(f"plist: {PLIST_PATH}")
    print(f"runtime: {runtime_dir}")
    print(f"stdout: {plist['StandardOutPath']}")
    print(f"stderr: {plist['StandardErrorPath']}")


def uninstall(dry_run: bool) -> None:
    if dry_run:
        print(f"would uninstall {LABEL} at {PLIST_PATH}")
        return
    bootout()
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print(f"uninstalled {LABEL}")


def status() -> int:
    result = run(["launchctl", "print", service_name()], check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the 06 script package watcher as a user LaunchAgent.")
    parser.add_argument("--interval-minutes", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--max-age-days", type=int, default=5)
    parser.add_argument("--python-bin", default="", help="Python executable. Defaults to the interpreter running this installer.")
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR), help="Non-Desktop runtime directory used by LaunchAgent.")
    parser.add_argument("--no-sync-runtime", action="store_true", help="Install plist without refreshing the runtime copy.")
    parser.add_argument("--sync-runtime-only", action="store_true", help="Refresh runtime copy and exit without touching LaunchAgent.")
    parser.add_argument("--no-kickstart", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.status:
        return status()
    if args.sync_runtime_only:
        if args.dry_run:
            print(f"would sync runtime to {Path(args.runtime_dir).expanduser().resolve()}")
            return 0
        sync_runtime(Path(args.runtime_dir).expanduser().resolve())
        print(f"synced runtime to {Path(args.runtime_dir).expanduser().resolve()}")
        return 0
    if args.uninstall:
        uninstall(args.dry_run)
    else:
        install(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
