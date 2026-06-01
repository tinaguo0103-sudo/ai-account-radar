#!/usr/bin/env python3
"""Rename Feishu tables to the v0.2 logical order and add 06 内容任务主表.

This script preserves existing table IDs and records. Default mode is dry-run.
Use --write-feishu to apply changes.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, VIEW_NAMES, resolve_table_name


ROOT = Path(__file__).resolve().parents[1]
BASE_INFO = ROOT / "feishu_created_base.json"
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
TASK_TYPES = "写稿、拍摄、剪辑、封面、发布、直播准备、直播预告、直播问题池、直播执行、直播切片、24小时复盘、72小时复盘、7天复盘、私信跟进、资产化"
TASK_STATUSES = "待办、进行中、阻塞、完成、取消"


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


def create_task_table(token: str, app_token: str) -> str:
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        token=token,
        body={
            "table": {
                "name": TABLES["task_master"],
                "default_view_name": VIEW_NAMES["task_master"][0],
                "fields": [{"field_name": name, "type": 1} for name in TASK_FIELDS],
            }
        },
    )
    data = payload.get("data", {})
    table = data.get("table", data)
    table_id = table.get("table_id") or data.get("table_id")
    if not table_id:
        raise SystemExit(f"Could not create {TABLES['task_master']}: {payload}")
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
        "restructured_at": "2026-06-01",
        "tables": rows,
        "logical_tables": TABLES,
        "note": "2026-06-01 按输入层/内容池/选题决策/制作/任务/复盘顺序重命名；02 URL投喂入口由旧06保留table_id和数据改名；06 内容任务主表新增；99 规则与字典继续受保护。",
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
        elif key == "task_master":
            plan.append({"key": key, "from": None, "to": desired, "table_id": None, "action": "create"})
        else:
            plan.append({"key": key, "from": None, "to": desired, "table_id": None, "action": "missing"})

    if not args.write_feishu:
        print(json.dumps({"ok": True, "mode": "dry-run", "plan": plan, "task_types": TASK_TYPES, "task_statuses": TASK_STATUSES}, ensure_ascii=False, indent=2))
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
            row["table_id"] = create_task_table(token, app_token)
            time.sleep(0.2)
        table_ids[key] = row["table_id"]
        if key == "task_master":
            row["created_fields"] = ensure_text_fields(token, app_token, row["table_id"], TASK_FIELDS)
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
