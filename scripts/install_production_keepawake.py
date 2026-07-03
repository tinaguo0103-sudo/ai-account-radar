#!/usr/bin/env python3
"""Install a local wake/keep-awake window for the daily production pipeline."""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


LABEL = "com.austin.ai-account-radar.production-keepawake"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "ai-account-radar"
DEFAULT_WAKE_TIME = "07:50:00"
DEFAULT_DURATION_SECONDS = 3 * 60 * 60


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def launchctl_target() -> str:
    return f"gui/{os.getuid()}"


def service_name() -> str:
    return f"{launchctl_target()}/{LABEL}"


def parse_hhmmss(value: str) -> tuple[int, int, int]:
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError("time must be HH:MM or HH:MM:SS")
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise argparse.ArgumentTypeError("time is out of range")
    return hour, minute, second


def build_plist(start_time: str, duration_seconds: int) -> dict[str, object]:
    hour, minute, _ = parse_hhmmss(start_time)
    duration = max(60, int(duration_seconds))
    return {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate",
            "-im",
            "-t",
            str(duration),
        ],
        "StartCalendarInterval": {
            "Hour": hour,
            "Minute": minute,
        },
        "StandardOutPath": str(LOG_DIR / "production_keepawake.out.log"),
        "StandardErrorPath": str(LOG_DIR / "production_keepawake.err.log"),
    }


def bootout() -> None:
    if PLIST_PATH.exists():
        run(["launchctl", "bootout", launchctl_target(), str(PLIST_PATH)], check=False)


def bootstrap() -> None:
    run(["launchctl", "bootstrap", launchctl_target(), str(PLIST_PATH)])
    run(["launchctl", "enable", service_name()], check=False)


def install_launch_agent(args: argparse.Namespace) -> None:
    plist = build_plist(args.start_time, args.duration_seconds)
    if args.dry_run:
        print(plist)
        return
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    Path(str(plist["StandardOutPath"])).touch()
    Path(str(plist["StandardErrorPath"])).touch()
    bootout()
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(plist, handle)
    bootstrap()
    print(f"installed {LABEL}")
    print(f"plist: {PLIST_PATH}")
    print(f"window: starts {args.start_time}, duration {args.duration_seconds}s")


def configure_wake(args: argparse.Namespace) -> int:
    command = ["pmset", "repeat", "wakeorpoweron", args.days, args.wake_time]
    if args.dry_run:
        print(" ".join(command))
        return 0
    result = run(command, check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        print("failed to configure pmset repeat wake; this may require administrator privileges")
    return result.returncode


def uninstall(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"would remove {PLIST_PATH}")
        return 0
    bootout()
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print(f"uninstalled {LABEL}")
    return 0


def status() -> int:
    plist_exists = PLIST_PATH.exists()
    print(f"plist_exists={plist_exists} path={PLIST_PATH}")
    result = run(["launchctl", "print", service_name()], check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    sched = run(["pmset", "-g", "sched"], check=False)
    if sched.stdout:
        print(sched.stdout)
    if sched.stderr:
        print(sched.stderr)
    return 0 if plist_exists else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Install wake and keep-awake safeguards for daily production automation.")
    parser.add_argument("--start-time", default=DEFAULT_WAKE_TIME, help="LaunchAgent caffeinate start time, HH:MM:SS.")
    parser.add_argument("--wake-time", default=DEFAULT_WAKE_TIME, help="pmset repeat wake time, HH:MM:SS.")
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--days", default="MTWRFSU", help="pmset repeat days. Default means every day.")
    parser.add_argument("--configure-wake", action="store_true", help="Also configure pmset repeat wakeorpoweron.")
    parser.add_argument("--launch-agent-only", action="store_true", help="Only install the LaunchAgent.")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    parse_hhmmss(args.start_time)
    parse_hhmmss(args.wake_time)
    if args.status:
        return status()
    if args.uninstall:
        return uninstall(args)
    install_launch_agent(args)
    if args.configure_wake and not args.launch_agent_only:
        return configure_wake(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
