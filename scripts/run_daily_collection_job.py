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
from scheduled_flow_preflight import evaluate_preflight
from source_control import DEFAULT_DB, SourceControl


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
    execution_ok = all(step["returncode"] == 0 for step in steps)
    payload = {
        "ok": execution_ok,
        "status": "completed" if execution_ok else "failed_or_partial",
        "business_continuation_ok": execution_ok,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
    }
    daily_log = read_daily_pipeline_log()
    if daily_log:
        for key in (
            "run_id",
            "full_collection_success",
            "collection_status",
            "downstream_usable",
            "downstream_usable_reason",
            "downstream_usable_checks",
            "downstream_blocked_reasons",
            "source_failure_count",
            "system_failure_count",
            "isolated_failed_account_count",
            "isolated_failed_accounts",
            "today_candidates",
        ):
            if key in daily_log:
                payload[key] = daily_log[key]
        full_success = daily_log.get("full_collection_success") is True
        downstream_usable = daily_log.get("downstream_usable") is True
        payload["ok"] = execution_ok and full_success
        payload["business_continuation_ok"] = execution_ok and (full_success or downstream_usable)
        if execution_ok and not full_success and downstream_usable:
            payload["status"] = "completed_with_failures"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_daily_pipeline_log() -> dict[str, Any]:
    path = LOG_DIR / f"daily_pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def failure_summary(steps: list[dict[str, Any]], log_path: Path) -> str:
    return qa_for_steps("08:00 每日全源采集", steps, log_path=str(log_path))


def scheduled_collection_plan(db_path: Path, douyin_account_limit: int) -> dict[str, Any]:
    plan = SourceControl(db_path).build_collection_plan()
    unlimited = douyin_account_limit == 0
    plan.update({
        "ok": bool(plan["ok"]) and unlimited,
        "status": "planned" if unlimited else "limited_plan_rejected",
        "check_only": True,
        "writes_feishu": False,
        "collection_started": False,
        "douyin_account_limit": douyin_account_limit,
        "source_authority": "sqlite",
        "touches_historical_03": False,
    })
    return plan


def main() -> int:
    account_gate = validate_account_limit_argv(sys.argv[1:])
    if not account_gate.ok:
        print(json.dumps(rejection_payload("run_daily_collection_job", account_gate), ensure_ascii=False, indent=2))
        return 2

    parser = argparse.ArgumentParser(description="Run daily full-source collection then write Feishu 04.")
    parser.add_argument("--douyin-account-limit", type=int, default=0, help="0 means every eligible Douyin competitor account.")
    parser.add_argument("--douyin-video-limit", type=int, default=3)
    parser.add_argument("--wechat-article-limit", type=int, default=1)
    parser.add_argument(
        "--defer-editorial",
        action="store_true",
        help="Generate raw candidates only; the outer Codex automation must apply the editorial Skill and finalize.",
    )
    parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu exception notifications.")
    parser.add_argument("--check-only", action="store_true", help="Print the full scheduled account plan without browser, collection, or Feishu I/O.")
    parser.add_argument("--source-db", default=str(DEFAULT_DB), help="Source-control SQLite authority.")
    parser.add_argument("--run-id", default="", help="Explicit exact run id.")
    parser.add_argument(
        "--allow-non-production-worktree",
        action="store_true",
        help="Allow this scheduled-production entrypoint to run outside the configured production worktree.",
    )
    args = parser.parse_args()
    args.douyin_account_limit = account_gate.value

    if args.check_only:
        plan = scheduled_collection_plan(Path(args.source_db), args.douyin_account_limit)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0 if plan["ok"] else 2

    load_local_env()
    py = sys.executable
    steps: list[dict[str, Any]] = []

    preflight = evaluate_preflight("collection", check_network=True)
    if not preflight["ok"]:
        print(json.dumps({"ok": False, "reason": "scheduled_flow_preflight_failed", "preflight": preflight}, ensure_ascii=False, indent=2))
        return 2

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

    steps.append(run_step("read exact source plan from local SQLite authority", [
        py,
        str(ROOT / "scripts" / "source_control_cli.py"),
        "--db",
        str(args.source_db),
        "plan",
    ]))
    if steps[-1]["returncode"] != 0:
        log_path = write_job_log(steps)
        if not args.no_notify:
            notify("AI账号雷达采集失败", failure_summary(steps, log_path))
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]
    try:
        source_plan_result: dict[str, Any] = json.loads(str(steps[-1].get("stdout") or ""))
    except json.JSONDecodeError:
        source_plan_result = {}
    if source_plan_result.get("plan_ready") is not True:
        steps[-1]["returncode"] = 2
        steps[-1]["stderr"] = "source_plan_not_ready"
        log_path = write_job_log(steps)
        if not args.no_notify:
            notify("AI账号雷达采集失败", failure_summary(steps, log_path))
        print(json.dumps({"ok": False, "reason": "source_plan_not_ready", "log": str(log_path)}, ensure_ascii=False, indent=2))
        return 2
    pipeline_cmd = [
        py,
        str(ROOT / "scripts" / "daily_pipeline.py"),
        "--douyin-account-limit",
        str(args.douyin_account_limit),
        "--douyin-video-limit",
        str(args.douyin_video_limit),
        "--write-feishu",
        "--source-db",
        str(args.source_db),
    ]
    pipeline_cmd.extend([
        "--resolve-url-intake",
        "--fetch-wechat-public-fulltext",
        "--wechat-article-limit",
        str(args.wechat_article_limit),
    ])
    if args.run_id:
        pipeline_cmd.extend(["--run-id", args.run_id])
    if args.defer_editorial:
        pipeline_cmd.append("--defer-editorial")
    steps.append(run_step("run full-source daily pipeline", pipeline_cmd))
    log_path = write_job_log(steps)
    ok = all(step["returncode"] == 0 for step in steps)
    if not ok and not args.no_notify:
        notify("AI账号雷达采集失败", failure_summary(steps, log_path))
    print(json.dumps({"ok": ok, "log": str(log_path)}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
