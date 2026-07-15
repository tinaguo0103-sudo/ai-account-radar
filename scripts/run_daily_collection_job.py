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
from full_account_collection_contract import rejection_payload, validate_account_limit_argv
from local_env import load_local_env
from feishu_automation_notify import notify
import topic_flow_rework as flow


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "output" / "logs"
CAPTURE_TAIL_CHARS = 4000


def run_step(name: str, command: list[str]) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    print(f"\n== {name} ==", flush=True)
    print(" ".join(command), flush=True)
    output_tail = ""
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            output_tail = (output_tail + line)[-CAPTURE_TAIL_CHARS:]
        returncode = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        output_tail = (output_tail + "\nInterrupted by outer automation.\n")[-CAPTURE_TAIL_CHARS:]
        returncode = 130
    return {
        "name": name,
        "command": command,
        "started_at": started_at,
        "returncode": returncode,
        "stdout": output_tail,
        "stderr": "",
    }


def write_job_log(steps: list[dict[str, Any]]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"scheduled_daily_collection_{datetime.now().strftime('%Y-%m-%d')}.json"
    ok = all(step["returncode"] == 0 for step in steps)
    payload = {
        "ok": ok,
        "status": "completed" if ok else "failed_or_partial",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def failure_summary(steps: list[dict[str, Any]], log_path: Path) -> str:
    return qa_for_steps("08:00 每日全源采集", steps, log_path=str(log_path))


def scheduled_collection_plan(config_path: Path, douyin_account_limit: int) -> dict[str, Any]:
    sources = flow.load_json_config(config_path).get("sources", [])
    governance = flow.source_governance_plan(sources)
    active = governance["active_competitor_accounts"]
    douyin = [row for row in active if row.get("platform") == "抖音"]
    other = [row for row in active if row.get("platform") != "抖音"]
    unlimited = douyin_account_limit == 0
    return {
        "ok": governance["active_competitor_count"] > 0 and unlimited,
        "status": "planned" if unlimited else "limited_plan_rejected",
        "check_only": True,
        "writes_feishu": False,
        "collection_started": False,
        "planned_accounts": governance["active_competitor_count"],
        "planned_douyin_accounts": len(douyin),
        "planned_other_accounts": len(other),
        "planned_account_names": [row["name"] for row in active],
        "douyin_account_limit": douyin_account_limit,
        "polluted_sources_excluded": governance["polluted_match_count"],
        "touches_historical_03": False,
    }


def main() -> int:
    account_gate = validate_account_limit_argv(sys.argv[1:])
    if not account_gate.ok:
        print(json.dumps(rejection_payload("run_daily_collection_job", account_gate), ensure_ascii=False, indent=2))
        return 2

    parser = argparse.ArgumentParser(description="Run daily full-source collection then write Feishu 04.")
    parser.add_argument("--douyin-account-limit", type=int, default=0, help="0 means every eligible Douyin competitor account.")
    parser.add_argument("--douyin-video-limit", type=int, default=3)
    parser.add_argument("--wechat-feed-limit", type=int, default=5)
    parser.add_argument("--wechat-fulltext-provider", default="wewe_rss_local")
    parser.add_argument(
        "--defer-editorial",
        action="store_true",
        help="Generate raw candidates only; the outer Codex automation must apply the editorial Skill and finalize.",
    )
    parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu exception notifications.")
    parser.add_argument("--check-only", action="store_true", help="Print the full scheduled account plan without browser, collection, or Feishu I/O.")
    parser.add_argument("--source-config", default=str(ROOT / "config" / "content_sources.yaml"), help="Source config used by --check-only.")
    parser.add_argument(
        "--allow-non-production-worktree",
        action="store_true",
        help="Allow this scheduled-production entrypoint to run outside the configured production worktree.",
    )
    args = parser.parse_args()
    args.douyin_account_limit = account_gate.value

    if args.check_only:
        plan = scheduled_collection_plan(Path(args.source_config), args.douyin_account_limit)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0 if plan["ok"] else 2

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
        "--force-fetch-douyin",
        "--douyin-verification-action",
        "log-only",
        "--write-feishu",
    ] + (["--defer-editorial"] if args.defer_editorial else [])))
    log_path = write_job_log(steps)
    ok = all(step["returncode"] == 0 for step in steps)
    if not ok and not args.no_notify:
        notify("AI账号雷达采集失败", failure_summary(steps, log_path))
    print(json.dumps({"ok": ok, "log": str(log_path)}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
