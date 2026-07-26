#!/usr/bin/env python3
"""Read Feishu 01 once and import it into a fresh source-control SQLite DB."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import configured_table_id, resolve_table_id
from local_env import load_local_env
from source_control import SourceControl


TABLE_KEY = "source_sampling"
VERIFIED = {
    "铁锤人": {
        "display_name": "铁锤人教AI",
        "configured_identity": "lxfater",
        "verified_identity": "MS4wLjABAAAAcS_9cjYOJTedwgIXLrcXnsqwhre_I_f5NGyQyQ2anrwazRpPco2fGSLic041fGOe",
        "homepage_url": "https://www.douyin.com/user/MS4wLjABAAAAcS_9cjYOJTedwgIXLrcXnsqwhre_I_f5NGyQyQ2anrwazRpPco2fGSLic041fGOe",
    },
    "歸藏 guizang.ai": {
        "display_name": "歸藏",
        "configured_identity": "23188777",
        "verified_identity": "MS4wLjABAAAA0ZMxKodgckhSdf_7KczekIydqVMg2pWKTFKpML598Zw",
        "homepage_url": "https://www.douyin.com/user/MS4wLjABAAAA0ZMxKodgckhSdf_7KczekIydqVMg2pWKTFKpML598Zw",
    },
}


def text(value: Any) -> str:
    return str(value or "").strip()


def label_bool(value: Any, default: bool) -> bool:
    return text(value) in {"是", "启用", "true", "True", "1"} if text(value) else default


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


def normalize(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields") or {}
    name = text(fields.get("名称") or fields.get("来源名称") or fields.get("来源"))
    row = {
        "record_id": text(record.get("record_id")),
        "display_name": name,
        "platform": text(fields.get("平台")),
        "channel_id": text(fields.get("平台")).lower(),
        "homepage_url": text(fields.get("主页链接") or fields.get("链接")),
        "enabled": label_bool(fields.get("默认启用"), True),
        "participates_sampling": label_bool(fields.get("是否参与主采样"), True),
        "priority": text(fields.get("优先级")) or "medium",
        "fetch_method": text(fields.get("抓取方式")),
        "sample_frequency": text(fields.get("跟踪频率")) or "daily_or_when_updated",
        "source_role": text(fields.get("来源角色")),
        "learn_focus": text(fields.get("关注重点")),
        "remarks": text(fields.get("备注")),
    }
    if name in VERIFIED:
        row.update(VERIFIED[name])
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--env-file", default="")
    args = parser.parse_args()
    if args.env_file:
        os.environ["AI_ACCOUNT_RADAR_ENV_FILE"] = args.env_file
    load_local_env(required=True)
    app_token = os.environ["FEISHU_BASE_APP_TOKEN"]
    token = feishu.tenant_token()
    tables_payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    tables = {item["name"]: item["table_id"] for item in tables_payload.get("data", {}).get("items", [])}
    table_id, source = configured_table_id(tables, TABLE_KEY)
    if source == "table_name":
        table_id = resolve_table_id(tables, TABLE_KEY)
    if not table_id:
        raise SystemExit("source_sampling_table_missing")
    records = all_records(token, app_token, table_id)
    snapshot = SourceControl(Path(args.db)).import_accounts([normalize(record) for record in records])
    print(json.dumps({
        "ok": True,
        "feishu_reads_only": True,
        "feishu_writes": 0,
        "record_count": len(records),
        "snapshot_count": snapshot["count"],
        "revision": snapshot["revision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
