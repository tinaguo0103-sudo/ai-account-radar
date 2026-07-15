#!/usr/bin/env python3
"""Read-only Feishu 04 schema readiness check for AR-020E."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from local_env import load_local_env
import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id


REQUIRED_VISIBLE_FIELDS = {
    "研究摘要": "multiline_text",
    "受众钩子": "multiline_text",
    "研究置信度": "text_or_single_select",
    "内容结构": "multiline_text",
}


def field_matrix(existing: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "field": name,
            "required_semantics": semantics,
            "exists": name in existing,
            "field_id": str(existing.get(name, {}).get("field_id") or ""),
            "field_type": existing.get(name, {}).get("type"),
            "release_action": "none" if name in existing else "create_field_before_runtime_enablement",
        }
        for name, semantics in REQUIRED_VISIBLE_FIELDS.items()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="GET-only AR-020E Feishu 04 schema check.")
    parser.add_argument("--check-only", action="store_true", required=True)
    args = parser.parse_args()
    load_local_env()
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = feishu.tenant_token()
    table_id = os.getenv("FEISHU_TOPIC_TABLE_ID", "").strip()
    if not table_id:
        table_id = resolve_table_id(
            {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)},
            "topic_decision",
        ) or ""
    if not table_id:
        raise SystemExit(f"Missing table: {TABLES['topic_decision']}")
    payload = feishu.request_json(
        "GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token
    )
    items = payload.get("data", {}).get("items", [])
    existing = {str(item.get("field_name") or ""): item for item in items}
    matrix = field_matrix(existing)
    missing = [item["field"] for item in matrix if not item["exists"]]
    print(json.dumps({
        "ok": not missing,
        "check_only": True,
        "table_key": "topic_decision",
        "table_id": table_id,
        "field_count": len(existing),
        "required_fields": matrix,
        "missing_fields": missing,
        "writes_feishu": False,
    }, ensure_ascii=False, indent=2))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
