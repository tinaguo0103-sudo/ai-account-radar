#!/usr/bin/env python3
"""Sync config/content_sources.yaml into Feishu 01 来源与采样.

This is intentionally narrow: it only upserts source rows, never deletes
records, never rebuilds tables, and does not touch content or Top10 logic.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import resolve_table_id, table_name


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "content_sources.yaml"
TABLE_KEY = "source_sampling"
SYNC_FIELDS = [
    "来源名称",
    "来源角色",
    "是否主对标",
    "是否参与主采样",
    "栏目",
    "栏目权重",
    "默认启用",
    "优先级",
    "平台",
    "来源类型",
    "主页链接",
    "抓取方式",
    "是否重点跟踪",
    "跟踪频率",
    "关注重点",
    "备注",
]


def require_feishu_env() -> str:
    missing = [name for name in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN"] if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Feishu sync requires environment variables: {', '.join(missing)}")
    return str(os.getenv("FEISHU_BASE_APP_TOKEN"))


def list_tables(token: str, app_token: str) -> dict[str, str]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return {item["name"]: item["table_id"] for item in payload.get("data", {}).get("items", [])}


def list_fields(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {item["field_name"]: item for item in payload.get("data", {}).get("items", [])}


def ensure_fields(token: str, app_token: str, table_id: str) -> list[str]:
    existing = list_fields(token, app_token, table_id)
    created: list[str] = []
    for field_name in SYNC_FIELDS:
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


def yes_no(value: bool) -> str:
    return "是" if value else "否"


def enabled_label(value: bool) -> str:
    return "启用" if value else "停用"


def row_from_source(source: dict[str, Any]) -> dict[str, str]:
    role = source.get("source_role") or source.get("source_group", "")
    remarks = source.get("remarks", "")
    if source.get("needs_url"):
        remarks = (remarks + "；" if remarks else "") + "缺主页链接，needs_url=true。"
    return {
        "来源名称": source.get("account_name", ""),
        "来源角色": role,
        "是否主对标": yes_no(bool(source.get("is_main_competitor"))),
        "是否参与主采样": yes_no(bool(source.get("participates_main_sampling"))),
        "栏目": source.get("column", ""),
        "栏目权重": source.get("column_weight") or "不适用",
        "默认启用": enabled_label(bool(source.get("default_enabled"))),
        "优先级": source.get("priority", ""),
        "平台": source.get("platform", ""),
        "来源类型": source.get("source_type", ""),
        "主页链接": source.get("url", ""),
        "抓取方式": source.get("fetch_method", ""),
        "是否重点跟踪": "是" if source.get("priority") == "high" and source.get("default_enabled") else "否",
        "跟踪频率": source.get("sample_frequency", ""),
        "关注重点": source.get("learn_focus", ""),
        "备注": remarks or source.get("do_not_copy", ""),
    }


def load_rows() -> list[dict[str, str]]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return [row_from_source(source) for source in config.get("sources", [])]


def sync_rows(token: str, app_token: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    table_id = resolve_table_id(list_tables(token, app_token), TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {table_name(TABLE_KEY)}")
    created_fields = ensure_fields(token, app_token, table_id)
    records = all_records(token, app_token, table_id)
    by_name = {
        str(record.get("fields", {}).get("来源名称", "") or record.get("fields", {}).get("来源", "")): record
        for record in records
    }
    created = 0
    updated = 0
    for row in rows:
        name = row["来源名称"]
        record = by_name.get(name)
        fields = {field: row.get(field, "") for field in SYNC_FIELDS}
        if record:
            feishu.request_json(
                "PUT",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record['record_id']}",
                token=token,
                body={"fields": fields},
            )
            updated += 1
        else:
            feishu.request_json(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                token=token,
                body={"fields": fields},
            )
            created += 1
        time.sleep(0.1)
    return {
        "table": table_name(TABLE_KEY),
        "rows": len(rows),
        "created_records": created,
        "updated_records": updated,
        "created_fields": created_fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync source pool and competitor weights to Feishu 01 来源与采样.")
    parser.add_argument("--write-feishu", action="store_true", help="Actually upsert rows to Feishu. Default is dry-run.")
    args = parser.parse_args()
    rows = load_rows()
    summary: dict[str, Any] = {
        "ok": True,
        "mode": "write-feishu" if args.write_feishu else "dry-run",
        "rows": len(rows),
        "current_main_competitors": [row["来源名称"] for row in rows if row["来源角色"] == "current_main_competitor"],
        "historical_references": [row["来源名称"] for row in rows if row["来源角色"] == "historical_reference"],
    }
    if args.write_feishu:
        app_token = require_feishu_env()
        token = feishu.tenant_token()
        summary["feishu"] = sync_rows(token, app_token, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
