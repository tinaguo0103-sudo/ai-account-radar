#!/usr/bin/env python3
"""Create platform content and task plans from approved topics.

Default mode is dry-run. With --write-feishu, reads 04 分析与选题 records whose
status is 进入Brief or 本周做, creates 05 Brief与制作 records and 06 内容任务主表
tasks, then marks the topic as 已拆平台内容.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id


ROOT = Path(__file__).resolve().parents[1]
TODAY10 = ROOT / "output" / "today_10_topics.csv"
TOPIC_READY_STATUSES = {"进入Brief", "本周做"}
BRIEF_FIELDS = ["关联选题", "一句话核心判断", "目标用户", "内容结构", "人工补充", "制作状态", "备注"]
TOPIC_MARK_FIELD = "是否已拆平台内容"
TASK_FIELDS = [
    "任务名称",
    "任务类型",
    "关联母题",
    "关联平台内容",
    "截止时间",
    "预计耗时",
    "优先级",
    "状态",
    "是否今天必须完成",
    "阻塞原因",
    "下一步动作",
    "备注",
]


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def require_app_token() -> str:
    token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return token


def all_records(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        suffix = f"?page_size=500{('&page_token=' + page_token) if page_token else ''}"
        payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records{suffix}", token=token)
        data = payload.get("data", {})
        records.extend(data.get("items", []))
        if not data.get("has_more"):
            return records
        page_token = data.get("page_token", "")


def fields_by_name(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def ensure_text_fields(token: str, app_token: str, table_id: str, field_names: list[str]) -> list[str]:
    existing = fields_by_name(token, app_token, table_id)
    created: list[str] = []
    for name in field_names:
        if name in existing:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body={"field_name": name, "type": 1},
        )
        created.append(name)
        time.sleep(0.1)
    return created


def batch_create(token: str, app_token: str, table_id: str, rows: list[dict[str, str]]) -> int:
    total = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        if not chunk:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            token=token,
            body={"records": [{"fields": row} for row in chunk]},
        )
        total += len(chunk)
        time.sleep(0.15)
    return total


def update_topics_mark(token: str, app_token: str, table_id: str, records: list[dict[str, Any]]) -> int:
    updated = 0
    for record in records:
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record['record_id']}",
            token=token,
            body={"fields": {TOPIC_MARK_FIELD: "是"}},
        )
        updated += 1
        time.sleep(0.1)
    return updated


def brief_from_topic(fields: dict[str, Any]) -> dict[str, str]:
    title = str(fields.get("选题标题", "")).strip()
    scene = str(fields.get("业务场景", "")).strip()
    ai_entry = str(fields.get("AI介入点", "")).strip()
    result = str(fields.get("可展示结果", "")).strip()
    return {
        "关联选题": title,
        "一句话核心判断": str(fields.get("推荐理由", ""))[:200],
        "目标用户": scene or "内容团队/品牌业务/AI项目负责人",
        "内容结构": f"痛点 -> AI介入点 -> 可展示结果 -> 人工判断边界。AI介入点：{ai_entry}；可展示结果：{result}",
        "人工补充": "补真实案例、截图/素材、个人判断、口播风格和CTA。",
        "制作状态": "待补案例",
        "备注": "由 content_ops_pipeline 从 04 分析与选题拆出；不生成完整成稿。",
    }


def tasks_from_topic(fields: dict[str, Any]) -> list[dict[str, str]]:
    title = str(fields.get("选题标题", "")).strip()
    action = str(fields.get("推荐动作", "")).strip()
    priority = "高" if action in {"进入Brief", "本周做"} else "中"
    task_specs = [
        ("写稿", "补真实案例，形成短视频/图文提纲"),
        ("封面", "确认标题、封面文字和视觉素材"),
        ("发布", "检查事实、风险边界和平台发布要点"),
    ]
    return [
        {
            "任务名称": f"{task_type}：{title}",
            "任务类型": task_type,
            "关联母题": title,
            "关联平台内容": title,
            "截止时间": "",
            "预计耗时": "30-60分钟" if task_type != "发布" else "15分钟",
            "优先级": priority,
            "状态": "待办",
            "是否今天必须完成": "否",
            "阻塞原因": "",
            "下一步动作": next_action,
            "备注": f"来源于 {today_slug()} 内容作战台拆解；不自动发布。",
        }
        for task_type, next_action in task_specs
    ]


def local_ready_topics() -> list[dict[str, Any]]:
    if not TODAY10.exists():
        return []
    with TODAY10.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ready = []
    for row in rows:
        status = "进入Brief" if row.get("推荐动作") == "进入Brief" else "本周做" if row.get("推荐动作") == "本周做" else ""
        if status in TOPIC_READY_STATUSES:
            ready.append({"record_id": "", "fields": {
                "选题标题": row.get("我的选题标题", ""),
                "状态": status,
                "推荐动作": row.get("推荐动作", ""),
                "业务场景": row.get("业务场景", ""),
                "AI介入点": row.get("AI介入点", ""),
                "可展示结果": row.get("可展示结果", ""),
                "推荐理由": row.get("推荐理由", ""),
            }})
    return ready


def feishu_ready_topics(token: str, app_token: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    by_name = {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}
    table_ids = {
        "topic_decision": resolve_table_id(by_name, "topic_decision"),
        "brief_production": resolve_table_id(by_name, "brief_production"),
        "task_master": resolve_table_id(by_name, "task_master"),
    }
    missing = [TABLES[key] for key, table_id in table_ids.items() if not table_id]
    if missing:
        raise SystemExit(f"Missing required Feishu tables: {missing}")
    records = all_records(token, app_token, table_ids["topic_decision"])
    ready = []
    for record in records:
        fields = record.get("fields", {})
        if fields.get("状态") in TOPIC_READY_STATUSES and str(fields.get(TOPIC_MARK_FIELD, "")) != "是":
            ready.append(record)
    return table_ids, ready


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-feishu", action="store_true", help="Actually create 05 records and 06 tasks. Default dry-run.")
    args = parser.parse_args()

    token = app_token = ""
    table_ids: dict[str, str] = {}
    if args.write_feishu:
        app_token = require_app_token()
        token = feishu.tenant_token()
        table_ids, topics = feishu_ready_topics(token, app_token)
    else:
        if os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET") and os.getenv("FEISHU_BASE_APP_TOKEN"):
            app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "")
            token = feishu.tenant_token()
            table_ids, topics = feishu_ready_topics(token, app_token)
        else:
            topics = local_ready_topics()

    brief_rows = [brief_from_topic(record["fields"]) for record in topics]
    task_rows: list[dict[str, str]] = []
    for record in topics:
        task_rows.extend(tasks_from_topic(record["fields"]))

    print(json.dumps({
        "ok": True,
        "mode": "write" if args.write_feishu else "dry-run",
        "source": "feishu" if table_ids else "local_today10_fallback",
        "ready_topics": len(topics),
        "brief_records": brief_rows,
        "task_records": task_rows,
    }, ensure_ascii=False, indent=2))

    if not args.write_feishu:
        return 0

    ensure_text_fields(token, app_token, table_ids["topic_decision"], [TOPIC_MARK_FIELD])
    ensure_text_fields(token, app_token, table_ids["brief_production"], BRIEF_FIELDS)
    ensure_text_fields(token, app_token, table_ids["task_master"], TASK_FIELDS)
    created_briefs = batch_create(token, app_token, table_ids["brief_production"], brief_rows)
    created_tasks = batch_create(token, app_token, table_ids["task_master"], task_rows)
    marked = update_topics_mark(token, app_token, table_ids["topic_decision"], topics)
    print(json.dumps({
        "ok": True,
        "created_briefs": created_briefs,
        "created_tasks": created_tasks,
        "marked_topics": marked,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
