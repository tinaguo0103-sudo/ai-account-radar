#!/usr/bin/env python3
"""Rename Feishu tables to the current logical order and prepare 06 script packages.

This script preserves existing table IDs and records. Default mode is dry-run.
Use --write-feishu to apply changes.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, VIEW_NAMES, resolve_table_name


ROOT = Path(__file__).resolve().parents[1]
BASE_INFO = ROOT / "feishu_created_base.json"
SCRIPT_PACKAGE_FIELDS = [
    "关联选题",
    "脚本状态",
    "推荐模板",
    "核心观点",
    "开头钩子",
    "飞书文档",
    "飞书文件夹",
    "文档同步状态",
    "文档同步错误",
    "本地文档",
    "素材提醒",
    "发布前核验",
    "QA结果",
    "是否可拍",
    "版本",
]
SCRIPT_PACKAGE_STATUSES = "已生成完整脚本包、完整脚本包-待修订、完整脚本包-阻塞"


def require_app_token() -> str:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return app_token


def list_views(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return payload.get("data", {}).get("items", [])


def fields_by_name(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def records_count(token: str, app_token: str, table_id: str) -> int:
    try:
        payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=1", token=token)
        return int(payload.get("data", {}).get("total", 0))
    except Exception:
        return -1


def rename_table(token: str, app_token: str, table_id: str, new_name: str) -> None:
    attempts = [
        ("PATCH", f"/bitable/v1/apps/{app_token}/tables/{table_id}", {"name": new_name}),
        ("PUT", f"/bitable/v1/apps/{app_token}/tables/{table_id}", {"name": new_name}),
        ("PATCH", f"/bitable/v1/apps/{app_token}/tables/{table_id}", {"table": {"name": new_name}}),
        ("PUT", f"/bitable/v1/apps/{app_token}/tables/{table_id}", {"table": {"name": new_name}}),
    ]
    errors: list[str] = []
    for method, path, body in attempts:
        try:
            feishu.request_json(method, path, token=token, body=body)
            return
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Feishu table rename API failed; no delete/recreate attempted. " + " | ".join(errors))


def create_script_package_table(token: str, app_token: str) -> str:
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        token=token,
        body={
            "table": {
                "name": TABLES["script_package"],
                "default_view_name": VIEW_NAMES["script_package"][0],
                "fields": [{"field_name": name, "type": 1} for name in SCRIPT_PACKAGE_FIELDS],
            }
        },
    )
    data = payload.get("data", {})
    table = data.get("table", data)
    table_id = table.get("table_id") or data.get("table_id")
    if not table_id:
        raise SystemExit(f"Could not create {TABLES['script_package']}: {payload}")
    return table_id


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


def ensure_views(token: str, app_token: str, table_id: str, view_names: list[str]) -> dict[str, list[str]]:
    existing = {view.get("view_name") for view in list_views(token, app_token, table_id)}
    created: list[str] = []
    skipped: list[str] = []
    for view_name in view_names:
        if view_name in existing:
            skipped.append(view_name)
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            token=token,
            body={"view_name": view_name, "view_type": "grid"},
        )
        created.append(view_name)
        time.sleep(0.1)
    return {"created": created, "skipped": skipped}


def update_base_info(app_token: str, rows: list[dict[str, Any]]) -> None:
    data: dict[str, Any] = {}
    if BASE_INFO.exists():
        data = json.loads(BASE_INFO.read_text(encoding="utf-8"))
    data.update({
        "app_token": app_token,
        "last_updated_at": "2026-06-01",
        "restructured_at": datetime.now().strftime("%Y-%m-%d"),
        "tables": rows,
        "logical_tables": TABLES,
        "note": "2026-06-27 取消05正式中间层；06 调整为完整脚本与制作包，保留旧表ID和数据；任务拆分表后续单独设计；99 规则与字典继续受保护。",
    })
    console = next((row for row in rows if row["name"] == TABLES["console"]), None)
    if console:
        data["main_console"] = {
            **data.get("main_console", {}),
            "name": console["name"],
            "table_id": console["table_id"],
            "url": f"https://my.feishu.cn/base/{app_token}?table={console['table_id']}",
        }
    BASE_INFO.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-feishu", action="store_true", help="Apply table renames/create task table. Default dry-run.")
    args = parser.parse_args()

    app_token = require_app_token()
    token = feishu.tenant_token()
    existing = feishu.list_tables(token, app_token)
    by_name = {table["name"]: table["table_id"] for table in existing}
    plan: list[dict[str, Any]] = []

    for key, desired in TABLES.items():
        actual = resolve_table_name(by_name, key)
        if actual:
            table_id = by_name[actual]
            action = "keep" if actual == desired else "rename"
            plan.append({"key": key, "from": actual, "to": desired, "table_id": table_id, "action": action})
        elif key == "script_package":
            plan.append({"key": key, "from": None, "to": desired, "table_id": None, "action": "create"})
        else:
            plan.append({"key": key, "from": None, "to": desired, "table_id": None, "action": "missing"})

    if not args.write_feishu:
        print(json.dumps({"ok": True, "mode": "dry-run", "plan": plan, "script_package_statuses": SCRIPT_PACKAGE_STATUSES}, ensure_ascii=False, indent=2))
        return 0

    if any(row["action"] == "missing" for row in plan):
        raise SystemExit(json.dumps({"ok": False, "missing": [row for row in plan if row["action"] == "missing"]}, ensure_ascii=False))

    applied: list[dict[str, Any]] = []
    table_ids: dict[str, str] = {}
    for row in plan:
        key = row["key"]
        if row["action"] == "rename":
            rename_table(token, app_token, row["table_id"], row["to"])
            time.sleep(0.2)
        elif row["action"] == "create":
            row["table_id"] = create_script_package_table(token, app_token)
            time.sleep(0.2)
        table_ids[key] = row["table_id"]
        if key == "script_package":
            row["created_fields"] = ensure_text_fields(token, app_token, row["table_id"], SCRIPT_PACKAGE_FIELDS)
        row["views"] = ensure_views(token, app_token, row["table_id"], VIEW_NAMES.get(key, []))
        applied.append(row)

    refreshed = feishu.list_tables(token, app_token)
    refreshed_by_name = {table["name"]: table["table_id"] for table in refreshed}
    rows = [
        {
            "name": name,
            "table_id": refreshed_by_name.get(name, table_ids.get(key, "")),
            "records": records_count(token, app_token, refreshed_by_name.get(name, table_ids.get(key, ""))),
        }
        for key, name in TABLES.items()
        if refreshed_by_name.get(name) or table_ids.get(key)
    ]
    update_base_info(app_token, rows)
    print(json.dumps({"ok": True, "mode": "write", "applied": applied, "tables": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
