#!/usr/bin/env python3
"""Send the topic decision card only when today's collection succeeded."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from automation_worktree_guard import check_automation_worktree, guard_failure_summary
from local_env import load_local_env
from feishu_automation_notify import notify


ROOT = Path(__file__).resolve().parents[1]
LATEST_WRITE = ROOT / "output" / "latest_write"
PIPELINE_LOG_DIR = ROOT / "output" / "logs"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def today_key() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def csv_row_count(path: Path) -> int:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return len(list(csv.DictReader(handle)))


def fresh_collection_status() -> tuple[bool, str, str]:
    today = today_key()
    pipeline_log = read_json(PIPELINE_LOG_DIR / f"daily_pipeline_{today}.json")
    if not pipeline_log.get("ok"):
        return False, "today_daily_pipeline_log_not_ok", ""
    pipeline_run_id = str(pipeline_log.get("run_id") or "")

    sampler_log = read_json(LATEST_WRITE / "content_sampler_log.json")
    generated_at = parse_datetime(str(sampler_log.get("generated_at") or ""))
    if not generated_at or generated_at.strftime("%Y-%m-%d") != today:
        return False, "latest_write_not_generated_today", pipeline_run_id
    sampler_run_id = str(sampler_log.get("run_id") or "")
    if pipeline_run_id and sampler_run_id and pipeline_run_id != sampler_run_id:
        return False, "pipeline_and_latest_write_run_id_mismatch", pipeline_run_id
    if str(sampler_log.get("mode") or "") != "write-feishu":
        return False, "latest_write_is_not_write_feishu_mode", sampler_run_id
    if int(sampler_log.get("today_candidates") or 0) <= 0:
        return False, "no_today_candidates_in_sampler_log", sampler_run_id

    topic_csv = LATEST_WRITE / "today_10_topics.csv"
    if csv_row_count(topic_csv) <= 0:
        return False, "today_10_topics_csv_empty", sampler_run_id
    return True, "fresh", sampler_run_id


def skip_summary(reason: str, run_id: str) -> str:
    today = today_key()
    reason_text = {
        "today_daily_pipeline_log_not_ok": "今天没有成功的 daily_pipeline 日志，可能是 08:00 采集失败或未运行。",
        "latest_write_not_generated_today": "latest_write 不是今天生成的正式候选，已阻止发送旧卡片。",
        "pipeline_and_latest_write_run_id_mismatch": "daily_pipeline 和 latest_write 的运行批次不一致。",
        "latest_write_is_not_write_feishu_mode": "latest_write 不是正式写飞书模式。",
        "no_today_candidates_in_sampler_log": "今天候选数量为 0。",
        "today_10_topics_csv_empty": "today_10_topics.csv 为空。",
    }.get(reason, reason)
    return (
        f"任务：10:00 每日选题卡发送\n"
        f"日期：{today}\n"
        f"运行批次：{run_id or '无'}\n"
        f"结果：未发卡\n"
        f"原因：{reason_text}"
    )


def send_failure_summary(run_id: str, returncode: int) -> str:
    return (
        f"任务：10:00 每日选题卡发送\n"
        f"运行批次：{run_id or '无'}\n"
        f"结果：发卡命令失败\n"
        f"退出码：{returncode}\n"
        "建议：检查 FEISHU_CARD_RECEIVE_TARGETS、机器人会话权限和飞书消息 API 权限。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded sender for the daily topic decision card.")
    parser.add_argument("--limit", type=int, default=7)
    parser.add_argument("--send-dry-run", action="store_true")
    parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu skip/failure notifications.")
    parser.add_argument(
        "--allow-non-production-worktree",
        action="store_true",
        help="Allow this scheduled-production entrypoint to run outside the configured production worktree.",
    )
    args = parser.parse_args()

    load_local_env()
    guard = check_automation_worktree(ROOT, allow_non_production=args.allow_non_production_worktree)
    if not guard.ok:
        summary = guard_failure_summary(guard, "10:00 每日选题卡发送")
        if not args.no_notify:
            notify("AI账号雷达选题卡发送失败", summary)
        print(json.dumps({
            "ok": False,
            "sent": False,
            "reason": guard.reason,
            "note": "Blocked card sending because the automation entrypoint is not running from the production worktree.",
        }, ensure_ascii=False, indent=2))
        return 2

    ok, reason, run_id = fresh_collection_status()
    if not ok:
        if not args.no_notify:
            notify("AI账号雷达今日未发选题卡", skip_summary(reason, run_id))
        print(json.dumps({
            "ok": True,
            "sent": False,
            "reason": reason,
            "run_id": run_id,
            "note": "Skipped card sending to avoid reusing stale candidates.",
        }, ensure_ascii=False, indent=2))
        return 0

    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_topic_decision_card_session.py"),
        "--run-id",
        run_id,
        "--limit",
        str(args.limit),
    ]
    if args.send_dry_run:
        command.append("--send-dry-run")
    result = subprocess.run(command, cwd=ROOT, text=True)
    if result.returncode != 0 and not args.no_notify:
        notify("AI账号雷达选题卡发送失败", send_failure_summary(run_id, result.returncode))
    print(json.dumps({"ok": result.returncode == 0, "sent": result.returncode == 0, "run_id": run_id}, ensure_ascii=False, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
