#!/usr/bin/env python3
"""Run the daily full-source collection job for the production schedule."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from automation_failure_qa import qa_for_steps
from automation_worktree_guard import check_automation_worktree, guard_failure_summary
from local_env import load_local_env
from feishu_automation_notify import notify


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "output" / "logs"


def run_step(name: str, command: list[str]) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    print(f"\n== {name} ==", flush=True)
    print(" ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, flush=True)
    return {
        "name": name,
        "command": command,
        "started_at": started_at,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def write_job_log(steps: list[dict[str, Any]]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"scheduled_daily_collection_{datetime.now().strftime('%Y-%m-%d')}.json"
    payload = {
        "ok": all(step["returncode"] == 0 for step in steps),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def failure_summary(steps: list[dict[str, Any]], log_path: Path) -> str:
    return qa_for_steps("08:00 每日全源采集", steps, log_path=str(log_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily full-source collection then write Feishu 04.")
    parser.add_argument("--douyin-account-limit", type=int, default=50)
    parser.add_argument("--douyin-video-limit", type=int, default=3)
    parser.add_argument("--wechat-feed-limit", type=int, default=5)
    parser.add_argument("--wechat-fulltext-provider", default="wewe_rss_local")
    parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu exception notifications.")
    parser.add_argument(
        "--allow-non-production-worktree",
        action="store_true",
        help="Allow this scheduled-production entrypoint to run outside the configured production worktree.",
    )
    args = parser.parse_args()

    load_local_env()
    py = sys.executable
    steps: list[dict[str, Any]] = []

    guard = check_automation_worktree(ROOT, allow_non_production=args.allow_non_production_worktree)
    if not guard.ok:
        summary = guard_failure_summary(guard, "08:00 每日全源采集")
        steps.append({
            "name": "automation worktree guard",
            "command": [py, str(Path(__file__).resolve())],
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "returncode": 2,
            "stdout": "",
            "stderr": summary,
        })
        log_path = write_job_log(steps)
        if not args.no_notify:
            notify("AI账号雷达采集失败", failure_summary(steps, log_path))
        print(json.dumps({"ok": False, "reason": guard.reason, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return 2

    steps.append(run_step("reconcile Feishu 01 source sampling into local config", [
        py,
        str(ROOT / "scripts" / "reconcile_source_sampling_from_feishu.py"),
        "--write-config",
        "--write-feishu",
    ]))
    if steps[-1]["returncode"] != 0:
        log_path = write_job_log(steps)
        if not args.no_notify:
            notify("AI账号雷达采集失败", failure_summary(steps, log_path))
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]

    steps.append(run_step("run full-source daily pipeline", [
        py,
        str(ROOT / "scripts" / "daily_pipeline.py"),
        "--resolve-url-intake",
        "--fetch-wechat-fulltext-provider",
        "--wechat-fulltext-provider",
        args.wechat_fulltext_provider,
        "--wechat-feed-limit",
        str(args.wechat_feed_limit),
        "--douyin-account-limit",
        str(args.douyin_account_limit),
        "--douyin-video-limit",
        str(args.douyin_video_limit),
        "--douyin-verification-action",
        "log-only",
        "--write-feishu",
    ]))
    log_path = write_job_log(steps)
    ok = all(step["returncode"] == 0 for step in steps)
    if not ok and not args.no_notify:
        notify("AI账号雷达采集失败", failure_summary(steps, log_path))
    print(json.dumps({"ok": ok, "log": str(log_path)}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
