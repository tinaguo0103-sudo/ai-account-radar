#!/usr/bin/env python3
"""Send the topic decision card only when today's collection succeeded."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from automation_failure_qa import qa_for_command_failure, qa_for_topic_skip
from automation_worktree_guard import check_automation_worktree, guard_failure_summary
import feishu_idempotency as idempotency
import push_to_feishu as feishu
from feishu_table_registry import resolve_table_id
from feishu_automation_notify import notify
from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
LATEST_WRITE = ROOT / "output" / "latest_write"
PIPELINE_LOG_DIR = ROOT / "output" / "logs"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
TOPIC_STATUS_FILTER = {"待判断", ""}
TOPIC_CARD_GUARD_KINDS = {"topic_candidate_create", "topic_card_send"}


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


def feishu_topic_records_for_run(run_id: str) -> tuple[int, str]:
    app_token = str(os.getenv("FEISHU_BASE_APP_TOKEN") or "")
    if not app_token:
        return 0, "missing_feishu_base_app_token"
    token = feishu.tenant_token()
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    tables = {item["name"]: item["table_id"] for item in payload.get("data", {}).get("items", [])}
    table_id = resolve_table_id(tables, "topic_decision")
    if not table_id:
        return 0, "missing_topic_decision_table"
    count = 0
    page_token = ""
    while True:
        suffix = f"?page_size=500{('&page_token=' + page_token) if page_token else ''}"
        page = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records{suffix}", token=token)
        data = page.get("data", {})
        for record in data.get("items", []):
            fields = record.get("fields", {})
            status = " ".join(str(fields.get("状态") or "").split())
            record_run_id = " ".join(str(fields.get("运行批次") or "").split())
            if record_run_id == run_id and status in TOPIC_STATUS_FILTER:
                count += 1
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "")
    return count, "ok"


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
    return qa_for_topic_skip(reason, run_id)


def idempotency_skip_summary(run_id: str, unknowns: list[dict[str, Any]]) -> str:
    return "\n".join([
        "10:00 每日选题卡发送已跳过：检测到 Feishu 非幂等写入状态未知。",
        idempotency.guard_summary(run_id, unknowns),
        "处理建议：先人工确认 Feishu 04 记录或聊天卡片是否已经发生，再决定恢复或清理；不要绕过守卫重发同一 run_id。",
    ])


def send_failure_summary(command: list[str], run_id: str, returncode: int, stdout: str = "", stderr: str = "") -> str:
    return qa_for_command_failure("10:00 每日选题卡发送", command, returncode, stdout=stdout, stderr=stderr, run_id=run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded sender for the daily topic decision card.")
    parser.add_argument("--limit", type=int, default=7)
    parser.add_argument("--send-dry-run", action="store_true")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only evaluate freshness/idempotency guards; do not invoke the card sender or write card artifacts.",
    )
    parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu skip/failure notifications.")
    parser.add_argument(
        "--allow-non-production-worktree",
        action="store_true",
        help="Allow this scheduled-production entrypoint to run outside the configured production worktree.",
    )
    args = parser.parse_args()

    load_local_env()
    should_notify = (not args.no_notify) and (not args.check_only)
    guard = check_automation_worktree(ROOT, allow_non_production=args.allow_non_production_worktree)
    if not guard.ok:
        summary = guard_failure_summary(guard, "10:00 每日选题卡发送")
        if should_notify:
            notify("AI账号雷达选题卡发送失败", qa_for_command_failure(
                "10:00 每日选题卡发送",
                [sys.executable, str(Path(__file__).resolve())],
                2,
                stderr=summary,
            ))
        print(json.dumps({
            "ok": False,
            "sent": False,
            "would_send": False,
            "check_only": args.check_only,
            "reason": guard.reason,
            "note": "Blocked card sending because the automation entrypoint is not running from the production worktree.",
        }, ensure_ascii=False, indent=2))
        return 2

    ok, reason, run_id = fresh_collection_status()
    if not ok:
        if should_notify:
            notify("AI账号雷达今日未发选题卡", skip_summary(reason, run_id))
        print(json.dumps({
            "ok": True,
            "sent": False,
            "would_send": False,
            "check_only": args.check_only,
            "reason": reason,
            "run_id": run_id,
            "note": "Skipped card sending to avoid reusing stale candidates.",
        }, ensure_ascii=False, indent=2))
        return 0

    unknowns = idempotency.blocking_unknowns(run_id=run_id, kinds=TOPIC_CARD_GUARD_KINDS)
    if unknowns:
        summary = idempotency_skip_summary(run_id, unknowns)
        if should_notify:
            notify("AI账号雷达今日未发选题卡", summary)
        print(json.dumps({
            "ok": True,
            "sent": False,
            "would_send": False,
            "check_only": args.check_only,
            "reason": "feishu_idempotency_unknown_guard",
            "run_id": run_id,
            "unknown_count": len(unknowns),
            "note": "Skipped card sending because a non-idempotent Feishu operation is status-unknown.",
            "summary": summary,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.check_only:
        candidate_count, candidate_reason = feishu_topic_records_for_run(run_id)
        print(json.dumps({
            "ok": True,
            "sent": False,
            "would_send": candidate_reason == "ok" and candidate_count > 0,
            "check_only": True,
            "reason": "fresh",
            "run_id": run_id,
            "candidate_count": candidate_count,
            "candidate_count_reason": candidate_reason,
            "note": "Check-only mode did not invoke the card sender or write card artifacts.",
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
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, flush=True)
    if result.returncode != 0 and should_notify:
        notify("AI账号雷达选题卡发送失败", send_failure_summary(command, run_id, result.returncode, result.stdout, result.stderr))
    print(json.dumps({"ok": result.returncode == 0, "sent": result.returncode == 0, "run_id": run_id}, ensure_ascii=False, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
