#!/usr/bin/env python3
"""Configure Feishu 04 as a topic-selection workspace.

This keeps Feishu as the daily decision surface: gallery for fast scanning,
kanban for status decisions, and a focused evidence-gap view. It only changes
fields/views and does not create script packages or task records.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id
from topic_decision_fields import (
    CARD_SUMMARY_FIELD,
    CORE_VISIBLE_FIELDS,
    DETAIL_VISIBLE_FIELDS,
    FEISHU_KEEP_FIELDS,
    FIELD_OPTIONS,
    MULTI_SELECT_FIELD_OPTIONS,
    card_summary_from_fields,
    field_create_body,
)


TARGET_TABLE_KEY = "topic_decision"

VIEW_SPECS = [
    {
        "name": "今日挑选卡片",
        "type": "gallery",
        "visible": [
            "选题标题",
            CARD_SUMMARY_FIELD,
            "状态",
            "选择原因标签",
            "今日建议级别",
            "AI味风险",
            "对应方向",
            "人工一句话判断",
        ],
        "latest_run": True,
    },
    {
        "name": "今日决策看板",
        "type": "kanban",
        "visible": [
            "选题标题",
            CARD_SUMMARY_FIELD,
            "状态",
            "选择原因标签",
            "今日建议级别",
            "AI味风险",
            "对应方向",
        ],
        "latest_run": True,
    },
    {
        "name": "证据不足",
        "type": "gallery",
        "visible": [
            "选题标题",
            CARD_SUMMARY_FIELD,
            "状态",
            "选择原因标签",
            "对应方向",
            "可展示证据",
            "需要补的证据",
            "人工一句话判断",
        ],
        "latest_run": True,
        "evidence_gap": True,
    },
    {
        "name": "待学习样本",
        "type": "grid",
        "visible": [
            "选题标题",
            "状态",
            "今日建议级别",
            "对应方向",
            "选择原因标签",
            "人工一句话判断",
            "学习状态",
            "推荐理由",
            "不建议做的原因",
        ],
        "learning_pending": True,
    },
    {
        "name": "今日候选池",
        "type": "grid",
        "visible": CORE_VISIBLE_FIELDS,
        "latest_run": True,
    },
    {
        "name": "今日最值得做",
        "type": "grid",
        "visible": CORE_VISIBLE_FIELDS,
        "latest_run": True,
        "level": "今日最值得做",
    },
    {
        "name": "暂存观察",
        "type": "grid",
        "visible": DETAIL_VISIBLE_FIELDS,
        "latest_run": True,
        "level": "暂存观察",
    },
]


def require_app_token() -> str:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return app_token


def latest_run_id() -> str:
    log_path = Path("output/latest_write/content_sampler_log.json")
    if not log_path.exists():
        return ""
    try:
        return json.loads(log_path.read_text(encoding="utf-8")).get("run_id", "")
    except json.JSONDecodeError:
        return ""


def list_fields(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def list_views(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return {view["view_name"]: view for view in payload.get("data", {}).get("items", [])}


def ensure_fields(token: str, app_token: str, table_id: str) -> list[str]:
    existing = list_fields(token, app_token, table_id)
    created: list[str] = []
    for field_name in FEISHU_KEEP_FIELDS:
        if field_name in existing:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body=field_create_body(field_name),
        )
        created.append(field_name)
        time.sleep(0.1)
    return created


def selectable_field_names() -> set[str]:
    return set(FIELD_OPTIONS) | set(MULTI_SELECT_FIELD_OPTIONS)


def needs_select_conversion(field: dict[str, Any]) -> bool:
    field_name = field.get("field_name", "")
    if field_name in FIELD_OPTIONS:
        return field.get("type") != 3
    if field_name in MULTI_SELECT_FIELD_OPTIONS:
        return field.get("type") != 4
    return False


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


def normalize_select_value(field_name: str, value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return ""
    if field_name in MULTI_SELECT_FIELD_OPTIONS:
        return [part.strip() for part in text.replace("；", ",").replace("、", ",").split(",") if part.strip()]
    return text


def convert_existing_select_fields(token: str, app_token: str, table_id: str) -> dict[str, Any]:
    fields = list_fields(token, app_token, table_id)
    targets = [name for name, field in fields.items() if needs_select_conversion(field)]
    if not targets:
        return {"converted": [], "updated_records": 0}

    records = all_records(token, app_token, table_id)
    snapshots: dict[str, dict[str, Any]] = {}
    for record in records:
        values: dict[str, Any] = {}
        for field_name in targets:
            value = normalize_select_value(field_name, record.get("fields", {}).get(field_name))
            if value:
                values[field_name] = value
        if values:
            snapshots[record["record_id"]] = values

    converted: list[str] = []
    for field_name in targets:
        field = fields[field_name]
        feishu.request_json(
            "DELETE",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field['field_id']}",
            token=token,
        )
        time.sleep(0.15)
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body=field_create_body(field_name),
        )
        converted.append(field_name)
        time.sleep(0.15)

    updated = 0
    for record_id, values in snapshots.items():
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            token=token,
            body={"fields": values},
        )
        updated += 1
        time.sleep(0.08)
    return {"converted": converted, "updated_records": updated}


def backfill_card_summary(token: str, app_token: str, table_id: str, run_id: str) -> dict[str, Any]:
    records = all_records(token, app_token, table_id)
    updated = 0
    scanned = 0
    for record in records:
        fields = record.get("fields", {})
        if run_id and str(fields.get("运行批次", "")).strip() != run_id:
            continue
        scanned += 1
        summary = card_summary_from_fields(fields)
        if str(fields.get(CARD_SUMMARY_FIELD, "")).strip() == summary:
            continue
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record['record_id']}",
            token=token,
            body={"fields": {CARD_SUMMARY_FIELD: summary}},
        )
        updated += 1
        time.sleep(0.08)
    return {"scanned_records": scanned, "updated_records": updated}


def ensure_view(token: str, app_token: str, table_id: str, view_name: str, view_type: str) -> dict[str, Any]:
    views = list_views(token, app_token, table_id)
    existing = views.get(view_name)
    if existing:
        return existing
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
        token=token,
        body={"view_name": view_name, "view_type": view_type},
    )
    time.sleep(0.1)
    return payload.get("data", {}).get("view", payload.get("data", {}))


def condition(field: dict[str, Any], operator: str, value: list[str] | None = None) -> dict[str, Any]:
    values = value or []
    if operator == "is" and field.get("type") in {3, 4}:
        options = {
            option.get("name"): option.get("id")
            for option in field.get("property", {}).get("options", [])
        }
        values = [options.get(item, item) for item in values]
    return {
        "field_id": field["field_id"],
        "operator": operator,
        "value": "" if operator in {"isEmpty", "isNotEmpty"} else json.dumps(values, ensure_ascii=False),
    }


def patch_view(token: str, app_token: str, table_id: str, spec: dict[str, Any], run_id: str) -> dict[str, Any]:
    view = ensure_view(token, app_token, table_id, spec["name"], spec["type"])
    fields = list_fields(token, app_token, table_id)
    visible = set(spec.get("visible", []))
    hidden = [field["field_id"] for name, field in fields.items() if name not in visible]
    conditions: list[dict[str, Any]] = []

    if spec.get("latest_run") and run_id and "运行批次" in fields:
        conditions.append(condition(fields["运行批次"], "is", [run_id]))
    if spec.get("level") and "今日建议级别" in fields:
        conditions.append(condition(fields["今日建议级别"], "is", [spec["level"]]))
    if spec.get("learning_pending") and "学习状态" in fields:
        conditions.append(condition(fields["学习状态"], "is", ["待学习"]))
    # The public API currently rejects isNotEmpty for some text-field views even
    # when the value is empty. Keep this view as a focused evidence card surface;
    # users can add the non-empty filter manually in Feishu UI if desired.

    property_body: dict[str, Any] = {}
    supports_hidden_fields = spec["type"] == "grid"
    if supports_hidden_fields:
        property_body["hidden_fields"] = hidden
    if conditions:
        property_body["filter_info"] = {
            "conditions": conditions,
            "conjunction": "and",
        }
    body = {
        "view_name": spec["name"],
        "property": property_body,
    }
    try:
        feishu.request_json(
            "PATCH",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}",
            token=token,
            body=body,
        )
        return {
            "view": spec["name"],
            "type": spec["type"],
            "configured": "ok",
            "hidden_fields": len(hidden) if supports_hidden_fields else None,
            "field_display_note": "gallery/kanban card fields must be adjusted in Feishu UI" if not supports_hidden_fields else "",
        }
    except Exception as exc:
        if spec.get("evidence_gap"):
            # Some tenants reject isNotEmpty through the public API. Keep the view
            # usable and let users still scan the visible evidence gap field.
            body["property"].pop("filter_info", None)
            feishu.request_json(
                "PATCH",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}",
                token=token,
                body=body,
            )
            return {
                "view": spec["name"],
                "type": spec["type"],
                "configured": "without_evidence_filter",
                "warning": str(exc),
                "hidden_fields": len(hidden),
            }
        return {"view": spec["name"], "type": spec["type"], "configured": f"failed:{exc}"}


def main() -> int:
    app_token = require_app_token()
    token = feishu.tenant_token()
    table_id = resolve_table_id({table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing table: {TABLES[TARGET_TABLE_KEY]}")

    created_fields = ensure_fields(token, app_token, table_id)
    converted = convert_existing_select_fields(token, app_token, table_id)
    run_id = latest_run_id()
    card_summary = backfill_card_summary(token, app_token, table_id, run_id)
    views = [patch_view(token, app_token, table_id, spec, run_id) for spec in VIEW_SPECS]
    print(json.dumps({
        "ok": True,
        "table": TABLES[TARGET_TABLE_KEY],
        "table_id": table_id,
        "latest_run_id": run_id,
        "created_fields": created_fields,
        "select_conversion": converted,
        "card_summary": card_summary,
        "views": views,
        "note": "飞书04已配置为挑选台；native automation rules may still need UI binding if the tenant does not expose automation APIs.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
