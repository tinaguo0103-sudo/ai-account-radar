#!/usr/bin/env python3
"""Simplify Feishu Base views and front-stage fields for v0.2.

This script keeps one view per table and trims 03/04 to the fields needed by
the current "content inbox -> 今日Top10" workflow. It does not create business
tables and never deletes 99 规则与字典.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, VIEW_NAMES, resolve_table_id, table_name


TABLE_VIEW_PLAN = {table_name(key): views[0] for key, views in VIEW_NAMES.items()}

FIELD_KEEP = {
    table_name("content_inbox"): [
        "标题",
        "来源类型",
        "来源名称",
        "平台",
        "链接",
        "发布时间",
        "采集时间",
        "采集状态",
        "失败原因",
        "摘要/片段",
        "作者/账号",
        "内容指纹",
        "是否重复",
        "处理状态",
    ],
    table_name("topic_decision"): [
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
    ],
}


def require_app_token() -> str:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return app_token


def list_views(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return payload.get("data", {}).get("items", [])


def list_fields(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return payload.get("data", {}).get("items", [])


def ensure_single_view(token: str, app_token: str, table_id: str, keep_name: str) -> dict[str, Any]:
    views = list_views(token, app_token, table_id)
    existing = next((view for view in views if view.get("view_name") == keep_name), None)
    created = False
    if not existing:
        payload = feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            token=token,
            body={"view_name": keep_name, "view_type": "grid"},
        )
        existing = payload.get("data", {}).get("view", payload.get("data", {}))
        created = True
        time.sleep(0.1)

    keep_id = existing.get("view_id")
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for view in list_views(token, app_token, table_id):
        if view.get("view_id") == keep_id:
            continue
        try:
            feishu.request_json(
                "DELETE",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}",
                token=token,
            )
            deleted.append(view.get("view_name", view["view_id"]))
            time.sleep(0.1)
        except Exception as exc:  # Feishu may refuse deleting the last/default view.
            failed.append({"view": view.get("view_name", view["view_id"]), "error": str(exc)})
    return {"kept": keep_name, "created": created, "deleted": deleted, "failed": failed}


def ensure_text_field(token: str, app_token: str, table_id: str, field_name: str) -> bool:
    existing = {field["field_name"] for field in list_fields(token, app_token, table_id)}
    if field_name in existing:
        return False
    feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        token=token,
        body={"field_name": field_name, "type": 1},
    )
    time.sleep(0.1)
    return True


def trim_fields(token: str, app_token: str, table_id: str, table_name: str) -> dict[str, Any]:
    keep = FIELD_KEEP.get(table_name)
    if not keep:
        return {"created": [], "deleted": [], "failed": [], "skipped": "no field trim plan"}

    created = [name for name in keep if ensure_text_field(token, app_token, table_id, name)]
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for field in list_fields(token, app_token, table_id):
        field_name = field.get("field_name", "")
        if field_name in keep:
            continue
        try:
            feishu.request_json(
                "DELETE",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field['field_id']}",
                token=token,
            )
            deleted.append(field_name)
            time.sleep(0.1)
        except Exception as exc:
            failed.append({"field": field_name, "error": str(exc)})
    return {"created": created, "deleted": deleted, "failed": failed}


def main() -> int:
    app_token = require_app_token()
    token = feishu.tenant_token()
    tables_by_name = {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}
    tables = {name: resolve_table_id(tables_by_name, key) for key, name in TABLES.items()}
    missing = [name for name, table_id in tables.items() if not table_id]
    if missing:
        raise SystemExit(f"Missing required tables: {missing}")

    summary: dict[str, Any] = {"ok": True, "tables": []}
    for table_name, keep_view in TABLE_VIEW_PLAN.items():
        table_id = tables[table_name]
        view_result = ensure_single_view(token, app_token, table_id, keep_view)
        field_result = trim_fields(token, app_token, table_id, table_name)
        summary["tables"].append({
            "name": table_name,
            "table_id": table_id,
            "view": view_result,
            "fields": field_result,
        })
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
