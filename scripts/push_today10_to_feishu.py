#!/usr/bin/env python3
"""Write only today's top-10 topic rows to Feishu 04 分析与选题.

This script is intentionally narrow: it does not rebuild tables, does not
write all candidates, and does not touch publishing/lead workflows.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id, table_name


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
TODAY10 = OUT / "today_10_topics.csv"
TARGET_TABLE_KEY = "topic_decision"
REQUIRED_FIELDS = [
    "选题标题",
    "推荐日期",
    "今日排名",
    "状态",
    "推荐动作",
    "原始来源标题",
    "来源类型",
    "来源链接",
    "对应栏目",
    "热点切入方式",
    "业务场景",
    "旧流程痛点",
    "AI介入点",
    "可展示结果",
    "可沉淀资产",
    "推荐理由",
    "相关来源",
]
ACTION_STATUS = {
    "立即蹭热点": "待判断",
    "进入Brief": "进入Brief",
    "本周做": "本周做",
    "暂存观察": "暂存",
    "不做": "不做",
}


def read_today10(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Missing {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 10:
        raise SystemExit(f"Expected 10 rows in {path}, got {len(rows)}")
    return rows


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def list_tables(token: str, app_token: str) -> dict[str, str]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return {item["name"]: item["table_id"] for item in payload.get("data", {}).get("items", [])}


def list_fields(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {item["field_name"]: item for item in payload.get("data", {}).get("items", [])}


def ensure_fields(token: str, app_token: str, table_id: str) -> list[str]:
    existing = list_fields(token, app_token, table_id)
    created: list[str] = []
    for field_name in REQUIRED_FIELDS:
        if field_name in existing:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body={"field_name": field_name, "type": 1},
        )
        created.append(field_name)
        time.sleep(0.1)
    return created


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


def map_row(row: dict[str, str], rank: int, date: str) -> dict[str, str]:
    status = ACTION_STATUS.get(row.get("推荐动作", ""), "待判断")
    return {
        "选题标题": row.get("我的选题标题", ""),
        "推荐日期": date,
        "今日排名": str(rank),
        "状态": status,
        "推荐动作": row.get("推荐动作", ""),
        "原始来源标题": row.get("来源内容", ""),
        "来源类型": row.get("来源类型", ""),
        "来源链接": row.get("来源链接", ""),
        "对应栏目": row.get("对应栏目", ""),
        "热点切入方式": row.get("热点切入方式", ""),
        "业务场景": row.get("业务场景", ""),
        "旧流程痛点": row.get("旧流程痛点", ""),
        "AI介入点": row.get("AI介入点", ""),
        "可展示结果": row.get("可展示结果", ""),
        "可沉淀资产": row.get("可沉淀资产", ""),
        "推荐理由": row.get("推荐理由", ""),
        "相关来源": row.get("相关来源", ""),
    }


def dry_run_print(rows: list[dict[str, str]]) -> None:
    print(f"DRY-RUN: will write 10 今日Top10 rows to {table_name(TARGET_TABLE_KEY)}")
    for row in rows:
        print(
            f"{row['今日排名']}. {row['选题标题']} | "
            f"{row['对应栏目']} / {row['热点切入方式']} | "
            f"{row['业务场景']} | {row['推荐动作']} -> {row['状态']}"
        )
        if row.get("相关来源"):
            print(f"   相关来源: {row['相关来源'][:120]}")


def batch_create(token: str, app_token: str, table_id: str, rows: list[dict[str, str]]) -> int:
    total = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            token=token,
            body={"records": [{"fields": row} for row in chunk]},
        )
        total += len(chunk)
        time.sleep(0.15)
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Actually write to Feishu. Default is dry-run only.")
    args = parser.parse_args()

    date = today_slug()
    mapped = [map_row(row, idx, date) for idx, row in enumerate(read_today10(TODAY10), start=1)]
    dry_run_print(mapped)

    if not args.write:
        print(json.dumps({"ok": True, "mode": "dry-run", "rows": len(mapped)}, ensure_ascii=False, indent=2))
        return 0

    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = feishu.tenant_token()
    tables = list_tables(token, app_token)
    table_id = resolve_table_id(tables, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {TABLES[TARGET_TABLE_KEY]}")
    created_fields = ensure_fields(token, app_token, table_id)

    existing = all_records(token, app_token, table_id)
    existing_keys = {
        (str(record.get("fields", {}).get("推荐日期", "")), str(record.get("fields", {}).get("选题标题", "")))
        for record in existing
    }
    to_create = [row for row in mapped if (row["推荐日期"], row["选题标题"]) not in existing_keys]
    created_records = batch_create(token, app_token, table_id, to_create) if to_create else 0
    print(json.dumps({
        "ok": True,
        "mode": "write",
        "table": TABLES[TARGET_TABLE_KEY],
        "created_fields": created_fields,
        "created_records": created_records,
        "skipped_existing": len(mapped) - len(to_create),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
