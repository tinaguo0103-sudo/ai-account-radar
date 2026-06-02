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
    "名称",
    "来源角色",
    "是否参与主采样",
    "栏目",
    "栏目权重",
    "默认启用",
    "优先级",
    "平台",
    "主页链接",
    "抓取方式",
    "跟踪频率",
    "关注重点",
    "备注",
]
FALLBACK_NAME_FIELDS = ["名称", "来源名称", "来源"]
SOURCE_VIEW_PLANS = {
    "当前主对标池": {
        "roles": {"current_main_competitor", "current_aux_competitor", "current_main_competitor_placeholder"},
        "visible_fields": ["名称", "来源角色", "栏目", "栏目权重", "平台", "主页链接", "是否参与主采样", "默认启用", "优先级", "抓取方式", "跟踪频率", "关注重点", "备注"],
    },
    "历史参考池": {
        "roles": {"historical_reference"},
        "visible_fields": ["名称", "来源角色", "栏目", "平台", "默认启用", "备注"],
    },
    "系统/官方源": {
        "roles": {"system_hotspot_source", "official_source"},
        "visible_fields": ["名称", "来源角色", "平台", "主页链接", "默认启用", "抓取方式", "跟踪频率", "关注重点"],
    },
    "手动入口": {
        "roles": {"manual_entry", "legacy_manual_entry"},
        "visible_fields": ["名称", "来源角色", "主页链接", "默认启用", "抓取方式", "备注"],
    },
}
COLUMN_ORDER = ["AI业务定调", "真实工作流改造", "汽车与内容营销", "AI导演工作流", "AI项目复盘"]
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, "待定": 3, "": 4}


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


def list_views(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return payload.get("data", {}).get("items", [])


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


def clean_missing_note(text: str) -> str:
    parts = [part.strip() for part in str(text or "").split("；") if part.strip()]
    parts = [
        part
        for part in parts
        if "缺主页链接" not in part and "主页链接待补充" not in part and "needs_url=true" not in part
    ]
    return "；".join(parts)


def usable_home_url(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


def row_from_source(source: dict[str, Any]) -> dict[str, str]:
    role = source.get("source_role") or source.get("source_group", "")
    remarks = source.get("remarks", "")
    if source.get("needs_url"):
        remarks = (remarks + "；" if remarks else "") + "主页链接待补充，needs_url=true。"
    return {
        "名称": source.get("account_name", ""),
        "来源角色": role,
        "是否参与主采样": yes_no(bool(source.get("participates_main_sampling"))),
        "栏目": source.get("column", ""),
        "栏目权重": source.get("column_weight") or "不适用",
        "默认启用": enabled_label(bool(source.get("default_enabled"))),
        "优先级": source.get("priority", ""),
        "平台": source.get("platform", ""),
        "主页链接": source.get("url", ""),
        "抓取方式": source.get("fetch_method", ""),
        "跟踪频率": source.get("sample_frequency", ""),
        "关注重点": source.get("learn_focus", ""),
        "备注": remarks or source.get("do_not_copy", ""),
    }


def load_rows() -> list[dict[str, str]]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    rows = [row_from_source(source) for source in config.get("sources", [])]
    return sorted(rows, key=lambda row: (
        COLUMN_ORDER.index(row["栏目"]) if row["栏目"] in COLUMN_ORDER else 99,
        PRIORITY_ORDER.get(row["优先级"], 9),
        row["名称"],
    ))


def merge_existing_values(row: dict[str, str], existing_fields: dict[str, Any]) -> dict[str, str]:
    merged = dict(row)
    existing_home_url = usable_home_url(existing_fields.get("主页链接", ""))
    legacy_link = usable_home_url(existing_fields.get("链接", ""))
    if not merged.get("主页链接"):
        merged["主页链接"] = existing_home_url or legacy_link
    if not merged.get("抓取方式"):
        merged["抓取方式"] = str(existing_fields.get("抓取方式", "") or existing_fields.get("获取方式", "") or "")
    if not merged.get("关注重点"):
        merged["关注重点"] = str(existing_fields.get("关注重点", "") or existing_fields.get("关注重点/原始内容", "") or "")
    if merged.get("主页链接"):
        merged["备注"] = clean_missing_note(merged.get("备注", ""))
    return merged


def ensure_views(token: str, app_token: str, table_id: str) -> dict[str, Any]:
    existing = {view.get("view_name"): view for view in list_views(token, app_token, table_id)}
    created: list[str] = []
    for view_name in SOURCE_VIEW_PLANS:
        if view_name in existing:
            continue
        payload = feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            token=token,
            body={"view_name": view_name, "view_type": "grid"},
        )
        view = payload.get("data", {}).get("view", payload.get("data", {}))
        existing[view_name] = view
        created.append(view_name)
        time.sleep(0.1)
    return {"created": created, "configured": configure_views(token, app_token, table_id, existing)}


def configure_views(token: str, app_token: str, table_id: str, views_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields = list_fields(token, app_token, table_id)
    role_field = fields.get("来源角色")
    if not role_field:
        return {"status": "missing_role_field"}
    result: dict[str, Any] = {}
    for view_name, plan in SOURCE_VIEW_PLANS.items():
        view = views_by_name.get(view_name)
        if not view or not view.get("view_id"):
            result[view_name] = {"status": "missing_view"}
            continue
        hidden_fields = [
            field["field_id"]
            for name, field in fields.items()
            if name not in plan["visible_fields"]
        ]
        conditions = [
            {"field_id": role_field["field_id"], "operator": "is", "value": json.dumps([role], ensure_ascii=False)}
            for role in sorted(plan["roles"])
        ]
        body = {
            "view_name": view_name,
            "property": {
                "filter_info": {"conditions": conditions, "conjunction": "or"},
                "hidden_fields": hidden_fields,
            },
        }
        try:
            feishu.request_json(
                "PATCH",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}",
                token=token,
                body=body,
            )
            result[view_name] = {"status": "configured", "hidden_fields": len(hidden_fields)}
        except Exception as exc:
            result[view_name] = {"status": "view_created_filter_or_fields_failed", "error": str(exc)}
    return result


def sync_rows(token: str, app_token: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    table_id = resolve_table_id(list_tables(token, app_token), TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {table_name(TABLE_KEY)}")
    created_fields = ensure_fields(token, app_token, table_id)
    records = all_records(token, app_token, table_id)
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        fields = record.get("fields", {})
        primary_name = str(fields.get("名称", ""))
        if primary_name:
            by_name[primary_name] = record
            continue
        for field_name in FALLBACK_NAME_FIELDS[1:]:
            name = str(fields.get(field_name, ""))
            if name and name not in by_name:
                by_name[name] = record
    created = 0
    updated = 0
    for row in rows:
        name = row["名称"]
        record = by_name.get(name)
        fields = merge_existing_values(row, record.get("fields", {}) if record else {})
        fields = {field: fields.get(field, "") for field in SYNC_FIELDS}
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
        "views": ensure_views(token, app_token, table_id),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync source pool and competitor weights to Feishu 01 来源与采样.")
    parser.add_argument("--write-feishu", action="store_true", help="Actually upsert rows to Feishu. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run alias for clarity; dry-run is the default.")
    args = parser.parse_args()
    rows = load_rows()
    summary: dict[str, Any] = {
        "ok": True,
        "mode": "write-feishu" if args.write_feishu else "dry-run",
        "rows": len(rows),
        "current_main_competitors": [row["名称"] for row in rows if row["来源角色"] == "current_main_competitor"],
        "placeholders": [row["名称"] for row in rows if row["来源角色"] == "current_main_competitor_placeholder"],
        "legacy_manual_entries": [row["名称"] for row in rows if row["来源角色"] == "legacy_manual_entry"],
        "historical_references": [row["名称"] for row in rows if row["来源角色"] == "historical_reference"],
    }
    if args.write_feishu:
        app_token = require_feishu_env()
        token = feishu.tenant_token()
        summary["feishu"] = sync_rows(token, app_token, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
