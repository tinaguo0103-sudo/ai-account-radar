#!/usr/bin/env python3
"""Configure Feishu 06 as a focused script-package workspace.

This script only changes the presentation of `06 完整脚本与制作包`: it keeps
records, creates/patches business views, hides non-business fields, and removes
old task-table residue when Feishu allows it.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id


TARGET_TABLE_KEY = "script_package"

BUSINESS_FIELDS = [
    "脚本标题",
    "关联选题",
    "脚本状态",
    "是否可拍",
    "飞书文档",
    "核心观点",
    "开头钩子",
    "素材提醒",
    "发布前核验",
    "QA结果",
    "文档同步状态",
    "文档同步错误",
    "本地文档",
    "飞书文件夹",
    "推荐模板",
    "版本",
]

DEPRECATED_FIELDS = [
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
    "完整脚本与执行包",
]

VIEW_SPECS = [
    {
        "name": "脚本包主视图",
        "visible": [
            "脚本标题",
            "关联选题",
            "脚本状态",
            "是否可拍",
            "飞书文档",
            "核心观点",
            "开头钩子",
            "素材提醒",
            "发布前核验",
            "QA结果",
        ],
    },
    {
        "name": "待处理与异常",
        "visible": [
            "脚本标题",
            "关联选题",
            "脚本状态",
            "是否可拍",
            "文档同步状态",
            "文档同步错误",
            "素材提醒",
            "发布前核验",
            "QA结果",
            "飞书文档",
            "本地文档",
        ],
    },
    {
        "name": "后台记录",
        "visible": BUSINESS_FIELDS,
    },
]

OLD_VIEW_NAMES = [
    "今日待办",
    "明日预警",
    "本周任务",
    "发布相关任务",
    "直播排期",
    "复盘任务",
    "脚本包后台",
    "可拍脚本包",
    "待修订脚本包",
]


def require_app_token() -> str:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return app_token


def list_fields(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def list_views(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return {view["view_name"]: view for view in payload.get("data", {}).get("items", [])}


def ensure_text_fields(token: str, app_token: str, table_id: str) -> list[str]:
    fields = list_fields(token, app_token, table_id)
    created: list[str] = []
    for name in BUSINESS_FIELDS:
        if name in fields:
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


def rename_primary_title_field(token: str, app_token: str, table_id: str) -> str:
    fields = list_fields(token, app_token, table_id)
    if "脚本标题" in fields:
        return "already_ok"
    field = fields.get("任务名称")
    if not field:
        return "missing_old_title"
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field['field_id']}",
        token=token,
        body={"field_name": "脚本标题", "type": field.get("type", 1)},
    )
    return "renamed"


def delete_deprecated_fields(token: str, app_token: str, table_id: str) -> dict[str, Any]:
    fields = list_fields(token, app_token, table_id)
    deleted: list[str] = []
    kept: list[dict[str, str]] = []
    for name in DEPRECATED_FIELDS:
        field = fields.get(name)
        if not field:
            continue
        try:
            feishu.request_json(
                "DELETE",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field['field_id']}",
                token=token,
            )
            deleted.append(name)
            time.sleep(0.12)
        except Exception as exc:
            kept.append({"field": name, "reason": str(exc)})
    return {"deleted": deleted, "kept": kept}


def ensure_view(token: str, app_token: str, table_id: str, view_name: str) -> dict[str, Any]:
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


def patch_view(token: str, app_token: str, table_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    view = ensure_view(token, app_token, table_id, spec["name"])
    fields = list_fields(token, app_token, table_id)
    visible = set(spec["visible"])
    hidden = [
        field["field_id"]
        for name, field in fields.items()
        if name not in visible and not field.get("is_primary")
    ]
    feishu.request_json(
        "PATCH",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}",
        token=token,
        body={
            "view_name": spec["name"],
            "property": {
                "hidden_fields": hidden,
            },
        },
    )
    return {"view": spec["name"], "hidden_fields": len(hidden), "visible_fields": spec["visible"]}


def delete_old_views(token: str, app_token: str, table_id: str) -> dict[str, Any]:
    views = list_views(token, app_token, table_id)
    protected = {spec["name"] for spec in VIEW_SPECS}
    deleted: list[str] = []
    kept: list[dict[str, str]] = []
    for name in OLD_VIEW_NAMES:
        view = views.get(name)
        if not view or name in protected:
            continue
        try:
            feishu.request_json(
                "DELETE",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}",
                token=token,
            )
            deleted.append(name)
            time.sleep(0.1)
        except Exception as exc:
            kept.append({"view": name, "reason": str(exc)})
    return {"deleted": deleted, "kept": kept}


def main() -> int:
    app_token = require_app_token()
    token = feishu.tenant_token()
    table_id = resolve_table_id({table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing table: {TABLES[TARGET_TABLE_KEY]}")

    title_field = rename_primary_title_field(token, app_token, table_id)
    created_fields = ensure_text_fields(token, app_token, table_id)
    deleted_fields = delete_deprecated_fields(token, app_token, table_id)
    views = [patch_view(token, app_token, table_id, spec) for spec in VIEW_SPECS]
    deleted_views = delete_old_views(token, app_token, table_id)
    final_fields = list(list_fields(token, app_token, table_id))
    final_views = list(list_views(token, app_token, table_id))
    print(json.dumps({
        "ok": True,
        "table": TABLES[TARGET_TABLE_KEY],
        "table_id": table_id,
        "created_fields": created_fields,
        "title_field": title_field,
        "deprecated_fields": deleted_fields,
        "views": views,
        "old_views": deleted_views,
        "final_fields": final_fields,
        "final_views": final_views,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
