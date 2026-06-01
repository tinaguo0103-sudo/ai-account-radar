#!/usr/bin/env python3
"""Manage the temporary Feishu URL intake table.

Users paste links into 02 URL投喂入口. This script reads those URLs into a
local txt file for intake_urls.py. It does not keep long-term business data.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import resolve_table_id, table_name


ROOT = Path(__file__).resolve().parents[1]
OUT_URLS = ROOT / "data" / "manual" / "feishu_url_intake.txt"
TABLE_KEY = "url_inbox"
TABLE_NAME = table_name(TABLE_KEY)
VIEW_NAME = "URL投喂入口"
FIELDS = ["URL", "备注", "处理状态", "解析结果", "失败原因"]


def require_app_token() -> str:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return app_token


def normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"https?://\S+", text)
    return match.group(0).rstrip("，,。)") if match else ""


def table_map(token: str, app_token: str) -> dict[str, str]:
    return {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}


def create_table(token: str, app_token: str) -> str:
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        token=token,
        body={
            "table": {
                "name": TABLE_NAME,
                "default_view_name": VIEW_NAME,
                "fields": [{"field_name": name, "type": 1} for name in FIELDS],
            }
        },
    )
    data = payload.get("data", {})
    table = data.get("table", data)
    table_id = table.get("table_id") or data.get("table_id")
    if not table_id:
        raise SystemExit(f"Could not create {TABLE_NAME}: {payload}")
    return table_id


def fields_by_name(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def ensure_fields(token: str, app_token: str, table_id: str) -> list[str]:
    existing = fields_by_name(token, app_token, table_id)
    created: list[str] = []
    for name in FIELDS:
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


def ensure_view(token: str, app_token: str, table_id: str) -> str:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    views = payload.get("data", {}).get("items", [])
    for view in views:
        if view.get("view_name") == VIEW_NAME:
            return view.get("view_id", "")
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
        token=token,
        body={"view_name": VIEW_NAME, "view_type": "grid"},
    )
    data = payload.get("data", {})
    view = data.get("view", data)
    return view.get("view_id", "")


def ensure_table(token: str, app_token: str) -> dict[str, Any]:
    tables = table_map(token, app_token)
    created_table = False
    table_id = resolve_table_id(tables, TABLE_KEY)
    if not table_id:
        table_id = create_table(token, app_token)
        created_table = True
        time.sleep(0.2)
    created_fields = ensure_fields(token, app_token, table_id)
    view_id = ensure_view(token, app_token, table_id)
    return {"table_id": table_id, "created_table": created_table, "created_fields": created_fields, "view_id": view_id}


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


def export_urls(token: str, app_token: str, table_id: str, out: Path) -> dict[str, Any]:
    rows = all_records(token, app_token, table_id)
    urls: list[str] = []
    skipped = 0
    for record in rows:
        fields = record.get("fields", {})
        status = str(fields.get("处理状态", ""))
        if status in {"已处理", "跳过"}:
            continue
        url = normalize_url(fields.get("URL", ""))
        if not url:
            skipped += 1
            continue
        urls.append(url)
    urls = list(dict.fromkeys(urls))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
    return {"records": len(rows), "urls": len(urls), "skipped_without_url": skipped, "output": str(out)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read Feishu URL intake table into a local URL text file.")
    parser.add_argument("--setup-only", action="store_true", help="Only create/ensure the table and view.")
    parser.add_argument("--out", default=str(OUT_URLS), help="Output txt path for URLs.")
    args = parser.parse_args()

    app_token = require_app_token()
    token = feishu.tenant_token()
    setup = ensure_table(token, app_token)
    result: dict[str, Any] = {"ok": True, "table": TABLE_NAME, **setup}
    if not args.setup_only:
        result["export"] = export_urls(token, app_token, setup["table_id"], Path(args.out))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
