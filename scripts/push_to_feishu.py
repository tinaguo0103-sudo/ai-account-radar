#!/usr/bin/env python3
"""
Push generated AI account radar tables to Feishu Base via Open API.

This is the no-manual-import path. It expects a Feishu self-built app with
bitable permissions and reads credentials from environment variables.

Current sync target: the simplified 6-table execution console. The older
12/13-table import layout is intentionally not used here anymore, because it
can recreate deprecated tables such as 热点分析表、对标分析表、选题候选库、发布复盘表.

Required:
  FEISHU_APP_ID
  FEISHU_APP_SECRET

Optional:
  FEISHU_BASE_APP_TOKEN   Existing Base app_token. If absent, create a new Base.
  FEISHU_API_BASE_URL     Defaults to https://open.feishu.cn. Use https://open.larksuite.com if DNS for open.feishu.cn fails.
  FEISHU_FOLDER_TOKEN     Folder token for creating a new Base in a target folder.
  FEISHU_BASE_NAME        Defaults to "AI账号信息雷达 + 飞书执行台 v0.1"
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from feishu_table_registry import PROTECTED_TABLE_NAMES, table_name


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
DEFAULT_API_HOST = "https://open.feishu.cn"


def api_base_url() -> str:
    host = os.getenv("FEISHU_API_BASE_URL", DEFAULT_API_HOST).rstrip("/")
    return host if host.endswith("/open-apis") else f"{host}/open-apis"

TABLE_FILES = [
    (table_name("source_sampling"), "sources_config.csv"),
    (table_name("content_inbox"), "content_inbox.csv"),
    (table_name("topic_decision"), "topic_candidates.csv"),
    (table_name("brief_production"), "content_briefs.csv"),
    (table_name("review_assets"), "assets.csv"),
]

PROTECTED_TABLES = set(PROTECTED_TABLE_NAMES)
DEPRECATED_TABLE_NAMES = {
    "定位与选题假设",
    "执行台逻辑说明",
    "视图导航表",
    "来源配置表",
    "手动采样入口表",
    "内容收件箱",
    "热点分析表",
    "对标分析表",
    "选题候选库",
    "内容Brief表",
    "资产与资料包表",
    "发布复盘表",
    "周复盘与定位校准表",
}


def die(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)


def request_json(method: str, path: str, token: str | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(api_base_url() + path, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc
    if payload.get("code", 0) != 0:
        raise RuntimeError(f"{method} {path} failed: {payload}")
    return payload


def tenant_token() -> str:
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        die("Missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    payload = request_json("POST", "/auth/v3/tenant_access_token/internal", body={
        "app_id": app_id,
        "app_secret": app_secret,
    })
    token = payload.get("tenant_access_token")
    if not token:
        die("Feishu did not return tenant_access_token")
    return token


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def get_or_create_base(token: str) -> str:
    existing = os.getenv("FEISHU_BASE_APP_TOKEN")
    if existing:
        return existing
    body: dict[str, Any] = {
        "name": os.getenv("FEISHU_BASE_NAME", "AI账号信息雷达 + 飞书执行台 v0.1"),
        "time_zone": "Asia/Shanghai",
    }
    folder_token = os.getenv("FEISHU_FOLDER_TOKEN")
    if folder_token:
        body["folder_token"] = folder_token
    payload = request_json("POST", "/bitable/v1/apps", token=token, body=body)
    data = payload.get("data", {})
    app = data.get("app", data)
    app_token = app.get("app_token") or data.get("app_token")
    if not app_token:
        die(f"Could not find app_token in create-base response: {payload}")
    return app_token


def list_tables(token: str, app_token: str) -> list[dict[str, Any]]:
    payload = request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return payload.get("data", {}).get("items", [])


def delete_table(token: str, app_token: str, table_id: str) -> None:
    request_json("DELETE", f"/bitable/v1/apps/{app_token}/tables/{table_id}", token=token)


def field_type(name: str) -> int:
    # Keep the first API version deliberately conservative: text fields are the
    # most reliable across tenants. Numeric fields can be upgraded in Feishu UI
    # later, or by extending this map once the tenant confirms field-type support.
    return 1


def create_table(token: str, app_token: str, table_name: str, headers: list[str]) -> str:
    fields = [{"field_name": h, "type": field_type(h)} for h in headers[:100]]
    payload = request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        token=token,
        body={"table": {"name": table_name, "default_view_name": "全部", "fields": fields}},
    )
    data = payload.get("data", {})
    table = data.get("table", data)
    table_id = table.get("table_id") or data.get("table_id")
    if not table_id:
        die(f"Could not find table_id in create-table response for {table_name}: {payload}")
    return table_id


def batch_create_records(token: str, app_token: str, table_id: str, rows: list[dict[str, str]]) -> int:
    total = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        records = [{"fields": {k: (v if v is not None else "") for k, v in row.items()}} for row in chunk]
        request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            token=token,
            body={"records": records},
        )
        total += len(chunk)
        time.sleep(0.15)
    return total


def main() -> int:
    token = tenant_token()
    app_token = get_or_create_base(token)
    existing_by_name = {table["name"]: table for table in list_tables(token, app_token)}
    if os.getenv("FEISHU_REPLACE_TABLES") == "1":
        die(
            "FEISHU_REPLACE_TABLES is disabled for the current 6-table console. "
            "This prevents accidental deletion/recreation of business tables and always protects 99 规则与字典."
        )
    summary: dict[str, Any] = {"ok": True, "app_token": app_token, "tables": []}
    deprecated_existing = sorted(name for name in existing_by_name if name in DEPRECATED_TABLE_NAMES)
    if deprecated_existing:
        summary["deprecated_tables_present"] = deprecated_existing
    for table_name, filename in TABLE_FILES:
        rows = read_csv_rows(OUT / filename)
        if not rows:
            summary["tables"].append({"name": table_name, "status": "skipped_empty"})
            continue
        headers = list(rows[0].keys())
        if table_name in existing_by_name:
            summary["tables"].append({"name": table_name, "table_id": existing_by_name[table_name]["table_id"], "status": "exists_skipped"})
            continue
        table_id = create_table(token, app_token, table_name, headers)
        count = batch_create_records(token, app_token, table_id, rows)
        summary["tables"].append({"name": table_name, "table_id": table_id, "records": count})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
