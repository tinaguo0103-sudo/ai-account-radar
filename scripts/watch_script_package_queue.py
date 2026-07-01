#!/usr/bin/env python3
"""Lightweight local watcher for 06 script package generation.

This process is intentionally not a Codex automation. It only invokes the
existing runner from a normal local Python process. The runner checks Feishu
first and exits without calling `codex exec` when the queue is empty.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from automation_failure_qa import qa_for_command_failure
from feishu_automation_notify import notify
from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "output" / "logs"
LOCK_FILE = ROOT / ".runtime" / "script_package_watcher.lock"
DEFAULT_INTERVAL_MINUTES = 5


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def date_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def compact(text: str, limit: int = 1600) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[:limit].rstrip() + "..."


def log(event: str, **payload: Any) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    item = {"ts": now_stamp(), "event": event, **payload}
    line = json.dumps(item, ensure_ascii=False)
    print(line, flush=True)
    with (LOG_DIR / f"script_package_watcher_{date_slug()}.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def acquire_lock() -> Any:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_FILE.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another script package watcher is already running.")
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def build_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "codex_script_package_runner.py"),
        "--limit",
        str(args.limit),
        "--max-age-days",
        str(args.max_age_days),
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if args.write_feishu:
        command.append("--write-feishu")
    if args.skip_codex:
        command.append("--skip-codex")
    if args.record_id:
        command.extend(["--record-id", args.record_id])
    if args.include_test_records:
        command.append("--include-test-records")
    return command


def failure_signature(returncode: int, stdout: str, stderr: str) -> str:
    return f"{returncode}|{compact(stderr or stdout, 500)}"


def notify_failure(command: list[str], returncode: int, stdout: str, stderr: str) -> None:
    try:
        notify(
            "AI账号雷达06生成失败",
            qa_for_command_failure("06 完整脚本与制作包 watcher", command, returncode, stdout=stdout, stderr=stderr),
        )
    except Exception as exc:  # noqa: BLE001 - notification must not kill the watcher
        log("failure_notification_failed", error=compact(str(exc), 800))


def run_once(command: list[str], *, notify_failures: bool, last_failure_signature: str = "") -> tuple[int, str]:
    started = time.time()
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    elapsed = round(time.time() - started, 2)
    log(
        "runner_finished",
        returncode=result.returncode,
        elapsed_seconds=elapsed,
        stdout=compact(result.stdout),
        stderr=compact(result.stderr),
    )
    signature = failure_signature(result.returncode, result.stdout, result.stderr) if result.returncode != 0 else ""
    if notify_failures and signature and signature != last_failure_signature:
        notify_failure(command, result.returncode, result.stdout, result.stderr)
    return result.returncode, signature


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll Feishu 04 and run 06 generation only when needed.")
    parser.add_argument("--interval-minutes", type=float, default=float(os.getenv("SCRIPT_PACKAGE_WATCH_INTERVAL_MINUTES", DEFAULT_INTERVAL_MINUTES)))
    parser.add_argument("--limit", type=int, default=int(os.getenv("CODEX_SCRIPT_PACKAGE_LIMIT", "2")))
    parser.add_argument("--max-age-days", type=int, default=int(os.getenv("CODEX_SCRIPT_PACKAGE_MAX_AGE_DAYS", "5")))
    parser.add_argument("--timeout-seconds", type=int, default=int(os.getenv("CODEX_SCRIPT_PACKAGE_TIMEOUT", "900")))
    parser.add_argument("--record-id", default="", help="Only process specific 04 record_id. Mostly for debugging.")
    parser.add_argument("--include-test-records", action="store_true")
    parser.add_argument("--skip-codex", action="store_true", help="Health-check mode: list ready topics, never generate.")
    parser.add_argument("--dry-run", action="store_true", help="Alias for --skip-codex.")
    parser.add_argument("--once", action="store_true", help="Run one polling pass and exit.")
    parser.add_argument("--write-feishu", action="store_true", default=True, help="Write generated 06 package records. Enabled by default.")
    parser.add_argument("--no-write-feishu", dest="write_feishu", action="store_false")
    parser.add_argument("--no-notify-failures", action="store_true", help="Do not send Feishu QA notifications when the runner fails.")
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()
    if args.dry_run:
        args.skip_codex = True

    _lock = acquire_lock()
    stop = {"value": False}

    def handle_signal(_signum: int, _frame: Any) -> None:
        stop["value"] = True
        log("stop_requested")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    interval_seconds = max(30, int(args.interval_minutes * 60))
    command = build_command(args)
    log(
        "watcher_started",
        interval_seconds=interval_seconds,
        command=command,
        write_feishu=args.write_feishu,
        skip_codex=args.skip_codex,
    )
    last_failure_signature = ""

    while not stop["value"]:
        returncode, signature = run_once(
            command,
            notify_failures=not args.no_notify_failures,
            last_failure_signature=last_failure_signature,
        )
        if signature:
            last_failure_signature = signature
        elif returncode == 0:
            last_failure_signature = ""
        if args.once:
            return returncode
        slept = 0
        while slept < interval_seconds and not stop["value"]:
            step = min(5, interval_seconds - slept)
            time.sleep(step)
            slept += step
    log("watcher_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
