#!/usr/bin/env python3
"""Create a staging/test 06 record through the real runner row -> record path."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from local_env import load_local_env
import push_to_feishu as feishu
from codex_script_package_runner import FeishuDocSyncResult, create_script_package_record, package_row
from backfill_script_package_clickable_links import extract_url, record_by_id, resolve_script_package_table
from setup_script_package_clickable_links import URL_FIELD_NAMES, list_fields


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOC_URL = "https://my.feishu.cn/docx/AR011FlowQADoc"
DEFAULT_FOLDER_URL = "https://my.feishu.cn/drive/folder/AR011FlowQAFolder"


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


def ensure_url_fields_ready(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    fields = list_fields(token, app_token, table_id)
    errors: list[dict[str, Any]] = []
    for name in URL_FIELD_NAMES:
        field = fields.get(name)
        if not field:
            errors.append({"field": name, "reason": "missing"})
        elif int(field.get("type") or 0) != 15:
            errors.append({"field": name, "reason": "wrong_type", "type": field.get("type")})
    return errors


def flow_fixture_package(title: str) -> dict[str, Any]:
    return {
        "topic_title": title,
        "qa_status": "revise",
        "qa_result": "flow qa fixture; waiting external QA",
        "recommended_template": "flow-qa-fixture",
        "core_viewpoint": "验证 06 写入链路能同时保留旧文本 URL 和新增可点击 URL 字段。",
        "opening_hook": "这条是 AR-011 flow QA fixture，不进入生产。",
        "material_reminders": ["测试记录；不要拍摄"],
        "release_checks": ["测试记录；不要发布"],
        "can_shoot": "否：测试 fixture",
    }


def verify_record(record: dict[str, Any], *, doc_url: str, folder_url: str) -> dict[str, Any]:
    fields = record.get("fields", {})
    checks = {
        "legacy_doc_text": extract_url(fields.get("飞书文档")) == doc_url,
        "legacy_folder_text": extract_url(fields.get("飞书文件夹")) == folder_url,
        "doc_url_field": extract_url(fields.get("飞书文档链接")) == doc_url,
        "folder_url_field": extract_url(fields.get("飞书文件夹链接")) == folder_url,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "read_back": {
            "飞书文档": fields.get("飞书文档"),
            "飞书文件夹": fields.get("飞书文件夹"),
            "飞书文档链接": fields.get("飞书文档链接"),
            "飞书文件夹链接": fields.get("飞书文件夹链接"),
        },
    }


def run_flow_qa(
    *,
    token: str,
    app_token: str,
    table_id: str,
    table_name: str,
    title: str,
    doc_url: str,
    folder_url: str,
    write: bool,
) -> dict[str, Any]:
    errors = ensure_url_fields_ready(token, app_token, table_id)
    if errors:
        return {"ok": False, "table_id": table_id, "table_name": table_name, "write": write, "field_errors": errors}
    local_path = Path("/private/tmp") / f"{title.replace('/', '_')}.md"
    package = flow_fixture_package(title)
    row = package_row(
        {"topic_title": title},
        package,
        local_path,
        FeishuDocSyncResult(url=doc_url, folder_url=folder_url, status="flow qa fixture doc sync"),
        attempts=1,
    )
    if not write:
        return {
            "ok": True,
            "table_id": table_id,
            "table_name": table_name,
            "write": False,
            "planned_row": row,
            "message": "dry-run only; no Feishu record created",
        }
    record_id = create_script_package_record(token, app_token, table_id, row)
    record = record_by_id(token, app_token, table_id, record_id)
    verification = verify_record(record, doc_url=doc_url, folder_url=folder_url)
    return {
        "ok": verification["ok"],
        "table_id": table_id,
        "table_name": table_name,
        "write": True,
        "created_record_id": record_id,
        "verification": verification,
        "test_marker": title,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an AR-011 staging/test 06 clickable-link flow QA fixture.")
    parser.add_argument("--env-file", default="", help="Explicit env file, e.g. .env.staging.local")
    parser.add_argument("--table-id", default="", help="Override FEISHU_SCRIPT_PACKAGE_TABLE_ID")
    parser.add_argument("--title", default="[AR-011-FLOW] 06 clickable link flow QA")
    parser.add_argument("--doc-url", default=DEFAULT_DOC_URL)
    parser.add_argument("--folder-url", default=DEFAULT_FOLDER_URL)
    parser.add_argument("--write-feishu", action="store_true", help="Create a staging/test 06 record")
    parser.add_argument("--allow-production", action="store_true", help="Allow writes when env appears production")
    args = parser.parse_args()

    if args.env_file:
        os.environ["AI_ACCOUNT_RADAR_ENV_FILE"] = args.env_file
    load_local_env(required=True)
    environment = env_label()
    if args.write_feishu and environment == "production" and not args.allow_production:
        raise SystemExit("Refusing production write without --allow-production")
    app_token = require_app_token()
    token = feishu.tenant_token()
    table_id, table_name = resolve_script_package_table(token, app_token, args.table_id)
    result = run_flow_qa(
        token=token,
        app_token=app_token,
        table_id=table_id,
        table_name=table_name,
        title=args.title,
        doc_url=args.doc_url,
        folder_url=args.folder_url,
        write=args.write_feishu,
    )
    result["environment"] = environment
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
