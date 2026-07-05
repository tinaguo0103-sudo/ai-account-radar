#!/usr/bin/env python3
"""Narrow setup for clickable 06 Feishu document/folder link fields.

This script intentionally avoids the broad 06 workspace setup path. It only
creates/verifies the two URL mirror fields and makes them visible in selected
grid views.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from local_env import load_local_env
import push_to_feishu as feishu
from feishu_table_registry import resolve_table_id


ROOT = Path(__file__).resolve().parents[1]
URL_FIELD_NAMES = ["飞书文档链接", "飞书文件夹链接"]
URL_FIELD_TYPE = 15
DEFAULT_VIEW_NAMES = ["脚本包主视图"]


def env_label() -> str:
    explicit = (os.getenv("AI_ACCOUNT_RADAR_ENV") or "").strip().lower()
    env_file = (os.getenv("AI_ACCOUNT_RADAR_ENV_FILE") or os.getenv("ENV_FILE") or "").strip().lower()
    if explicit:
        return "production" if explicit in {"prod", "production"} else explicit
    if any(marker in env_file for marker in ("staging", "stage", "test", "测试")):
        return "staging"
    return "production"


def require_app_token() -> str:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return app_token


def table_map(token: str, app_token: str) -> dict[str, str]:
    return {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}


def resolve_script_package_table(token: str, app_token: str, explicit_table_id: str = "") -> tuple[str, str]:
    if explicit_table_id:
        tables = table_map(token, app_token)
        return explicit_table_id, next((name for name, table_id in tables.items() if table_id == explicit_table_id), "")
    env_table_id = os.getenv("FEISHU_SCRIPT_PACKAGE_TABLE_ID", "").strip()
    tables = table_map(token, app_token)
    if env_table_id:
        return env_table_id, next((name for name, table_id in tables.items() if table_id == env_table_id), "")
    table_id = resolve_table_id(tables, "script_package") or ""
    if not table_id:
        raise SystemExit("Could not resolve script_package table")
    return table_id, next((name for name, value in tables.items() if value == table_id), "")


def list_fields(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def list_views(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return {view["view_name"]: view for view in payload.get("data", {}).get("items", [])}


def get_view(token: str, app_token: str, table_id: str, view_id: str) -> dict[str, Any]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view_id}", token=token)
    data = payload.get("data", {})
    return data.get("view", data)


def plan_url_fields(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    create: list[str] = []
    already_ok: list[str] = []
    conflicts: list[dict[str, Any]] = []
    for name in URL_FIELD_NAMES:
        field = fields.get(name)
        if not field:
            create.append(name)
            continue
        if int(field.get("type") or 0) == URL_FIELD_TYPE:
            already_ok.append(name)
        else:
            conflicts.append({"field_name": name, "current_type": field.get("type"), "required_type": URL_FIELD_TYPE})
    return {"create": create, "already_ok": already_ok, "conflicts": conflicts}


def create_url_fields(token: str, app_token: str, table_id: str, field_names: list[str]) -> list[str]:
    created: list[str] = []
    for name in field_names:
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body={"field_name": name, "type": URL_FIELD_TYPE},
        )
        created.append(name)
        time.sleep(0.1)
    return created


def ensure_grid_view(token: str, app_token: str, table_id: str, view_name: str) -> dict[str, Any]:
    views = list_views(token, app_token, table_id)
    if view_name in views:
        return views[view_name]
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
        token=token,
        body={"view_name": view_name, "view_type": "grid"},
    )
    time.sleep(0.1)
    return payload.get("data", {}).get("view", payload.get("data", {}))


def current_hidden_fields(view: dict[str, Any] | None) -> list[str]:
    if not view:
        return []
    property_value = view.get("property") if isinstance(view.get("property"), dict) else {}
    hidden = property_value.get("hidden_fields", [])
    if not isinstance(hidden, list):
        return []
    return [str(item) for item in hidden if str(item)]


def minimal_hidden_fields_for_new_view(fields: dict[str, dict[str, Any]]) -> list[str]:
    visible_names = {"脚本标题", "关联选题", "飞书文档", "飞书文件夹", *URL_FIELD_NAMES}
    return [
        field["field_id"]
        for name, field in fields.items()
        if name not in visible_names and not field.get("is_primary")
    ]


def plan_view_patch(view_name: str, fields: dict[str, dict[str, Any]], view: dict[str, Any] | None) -> dict[str, Any]:
    missing_url_fields = [name for name in URL_FIELD_NAMES if name not in fields]
    url_field_ids = {str(fields[name]["field_id"]) for name in URL_FIELD_NAMES if name in fields}
    existing_hidden_fields = current_hidden_fields(view)
    if view:
        hidden_fields = [field_id for field_id in existing_hidden_fields if field_id not in url_field_ids]
    else:
        hidden_fields = minimal_hidden_fields_for_new_view(fields)
    return {
        "view_name": view_name,
        "view_id": str((view or {}).get("view_id") or ""),
        "will_create_view": not bool(view),
        "ensured_visible_fields": [name for name in URL_FIELD_NAMES if name in fields],
        "new_view_default_visible_fields": (
            [name for name in ["脚本标题", "关联选题", "飞书文档链接", "飞书文件夹链接", "飞书文档", "飞书文件夹"] if name in fields]
            if not view
            else []
        ),
        "missing_url_fields": missing_url_fields,
        "existing_hidden_fields_count": len(existing_hidden_fields),
        "hidden_fields_count": len(hidden_fields),
        "property": {"hidden_fields": hidden_fields},
    }


def patch_view(token: str, app_token: str, table_id: str, view_name: str, fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    view = ensure_grid_view(token, app_token, table_id, view_name)
    if view.get("view_id"):
        view = get_view(token, app_token, table_id, str(view["view_id"]))
    plan = plan_view_patch(view_name, fields, view)
    feishu.request_json(
        "PATCH",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}",
        token=token,
        body={"view_name": view_name, "property": plan["property"]},
    )
    plan["view_id"] = str(view.get("view_id") or "")
    plan["patched"] = True
    return plan


def run_setup(
    *,
    token: str,
    app_token: str,
    table_id: str,
    table_name: str,
    view_names: list[str],
    write: bool,
) -> dict[str, Any]:
    fields = list_fields(token, app_token, table_id)
    field_plan = plan_url_fields(fields)
    if field_plan["conflicts"]:
        return {
            "ok": False,
            "table_id": table_id,
            "table_name": table_name,
            "write": write,
            "field_plan": field_plan,
            "view_plans": [],
            "error": "url_field_type_conflict",
        }
    created_fields: list[str] = []
    if write and field_plan["create"]:
        created_fields = create_url_fields(token, app_token, table_id, field_plan["create"])
        fields = list_fields(token, app_token, table_id)
        field_plan = plan_url_fields(fields)
    views = list_views(token, app_token, table_id)
    view_plans: list[dict[str, Any]] = []
    for view_name in view_names:
        if write:
            view_plans.append(patch_view(token, app_token, table_id, view_name, fields))
        else:
            view = views.get(view_name)
            if view and view.get("view_id"):
                view = get_view(token, app_token, table_id, str(view["view_id"]))
            view_plans.append(plan_view_patch(view_name, fields, view))
    return {
        "ok": True,
        "table_id": table_id,
        "table_name": table_name,
        "write": write,
        "created_url_fields": created_fields,
        "field_plan": field_plan,
        "view_plans": view_plans,
        "side_effect_scope": [
            "create_missing_url_fields_only",
            "patch_selected_grid_views_only",
            "no_title_rename",
            "no_deprecated_field_delete",
            "no_old_view_delete",
            "no_record_backfill",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up only clickable 06 link URL fields and selected grid views.")
    parser.add_argument("--env-file", default="", help="Explicit env file, e.g. .env.staging.local")
    parser.add_argument("--table-id", default="", help="Override FEISHU_SCRIPT_PACKAGE_TABLE_ID")
    parser.add_argument("--view-name", action="append", default=[], help="Grid view to patch/create; can repeat")
    parser.add_argument("--write-feishu", action="store_true", help="Apply the planned field/view changes")
    parser.add_argument("--allow-production", action="store_true", help="Allow writes when env appears production")
    args = parser.parse_args()

    if args.env_file:
        os.environ["AI_ACCOUNT_RADAR_ENV_FILE"] = args.env_file
    load_local_env(required=True)
    environment = env_label()
    if args.write_feishu and environment == "production" and not args.allow_production:
        raise SystemExit("Refusing production schema/view write without --allow-production")
    app_token = require_app_token()
    token = feishu.tenant_token()
    table_id, table_name = resolve_script_package_table(token, app_token, args.table_id)
    view_names = args.view_name or DEFAULT_VIEW_NAMES
    result = run_setup(
        token=token,
        app_token=app_token,
        table_id=table_id,
        table_name=table_name,
        view_names=view_names,
        write=args.write_feishu,
    )
    result["environment"] = environment
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
