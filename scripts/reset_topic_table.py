#!/usr/bin/env python3
"""Reset 04 分析与选题 to the clean 今日候选池 field set.

Use only when test data can be removed. The table itself is preserved.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id


TABLE_KEY = "topic_decision"
TABLE_NAME = TABLES[TABLE_KEY]
KEEP_FIELDS = [
    "选题标题",
    "推荐日期",
    "今日排名",
    "状态",
    "推荐动作",
    "来源类型",
    "原始来源标题",
    "来源链接",
    "对应栏目",
    "热点切入方式",
    "业务场景",
    "推荐理由",
    "相关来源",
    "旧流程痛点",
    "AI介入点",
    "可展示结果",
    "可沉淀资产",
]


def app_token() -> str:
    token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return token


def get_table_id(token: str, app: str) -> str:
    tables = {table["name"]: table["table_id"] for table in feishu.list_tables(token, app)}
    table_id = resolve_table_id(tables, TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing table: {TABLE_NAME}")
    return table_id


def all_records(token: str, app: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        suffix = f"?page_size=500{('&page_token=' + page_token) if page_token else ''}"
        payload = feishu.request_json("GET", f"/bitable/v1/apps/{app}/tables/{table_id}/records{suffix}", token=token)
        data = payload.get("data", {})
        records.extend(data.get("items", []))
        if not data.get("has_more"):
            return records
        page_token = data.get("page_token", "")


def batch_delete_records(token: str, app: str, table_id: str, record_ids: list[str]) -> int:
    total = 0
    for start in range(0, len(record_ids), 500):
        chunk = record_ids[start:start + 500]
        if not chunk:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app}/tables/{table_id}/records/batch_delete",
            token=token,
            body={"records": chunk},
        )
        total += len(chunk)
        time.sleep(0.1)
    return total


def fields(token: str, app: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app}/tables/{table_id}/fields", token=token)
    return payload.get("data", {}).get("items", [])


def ensure_text_field(token: str, app: str, table_id: str, field_name: str) -> bool:
    if field_name in {field["field_name"] for field in fields(token, app, table_id)}:
        return False
    feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app}/tables/{table_id}/fields",
        token=token,
        body={"field_name": field_name, "type": 1},
    )
    time.sleep(0.1)
    return True


def trim_fields(token: str, app: str, table_id: str) -> dict[str, Any]:
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    # Rebuild non-primary fields so Feishu displays them in the intended
    # reading order. The primary field cannot be deleted or recreated.
    for field in fields(token, app, table_id):
        name = field.get("field_name", "")
        if name == "选题标题":
            continue
        try:
            feishu.request_json(
                "DELETE",
                f"/bitable/v1/apps/{app}/tables/{table_id}/fields/{field['field_id']}",
                token=token,
            )
            deleted.append(name)
            time.sleep(0.1)
        except Exception as exc:
            failed.append({"field": name, "error": str(exc)})

    created: list[str] = []
    for name in KEEP_FIELDS:
        if name == "选题标题":
            continue
        if ensure_text_field(token, app, table_id, name):
            created.append(name)
    return {"created": created, "deleted": deleted, "failed": failed}


def main() -> int:
    app = app_token()
    token = feishu.tenant_token()
    table_id = get_table_id(token, app)
    records = all_records(token, app, table_id)
    deleted_records = batch_delete_records(token, app, table_id, [record["record_id"] for record in records])
    field_result = trim_fields(token, app, table_id)
    print(json.dumps({
        "ok": True,
        "table": TABLE_NAME,
        "deleted_records": deleted_records,
        "fields": field_result,
        "kept_fields": KEEP_FIELDS,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
