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

import push_to_feishu as feishu
from feishu_table_registry import resolve_table_id
from local_env import load_local_env
from feishu_automation_notify import notify


ROOT = Path(__file__).resolve().parents[1]
LATEST_WRITE = ROOT / "output" / "latest_write"
PIPELINE_LOG_DIR = ROOT / "output" / "logs"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
TOPIC_STATUS_FILTER = {"待判断", ""}


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
    feishu_count, feishu_reason = feishu_topic_records_for_run(sampler_run_id)
    if feishu_reason != "ok":
        return False, feishu_reason, sampler_run_id
    if feishu_count <= 0:
        return False, "no_feishu_04_candidates_for_run", sampler_run_id
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
        "missing_feishu_base_app_token": "缺少 FEISHU_BASE_APP_TOKEN，无法确认飞书 04 是否已有本批次候选。",
        "missing_topic_decision_table": "找不到飞书 04 分析与选题表。",
        "no_feishu_04_candidates_for_run": "飞书 04 没有本批次待判断候选，可能是 03 校验失败、04 未写入，或候选已被近 5 天去重过滤。",
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
    args = parser.parse_args()

    load_local_env()
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
