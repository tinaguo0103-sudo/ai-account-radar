#!/usr/bin/env python3
"""Backfill clickable 06 Feishu document/folder URL fields from legacy text fields."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from local_env import load_local_env
import push_to_feishu as feishu
from feishu_table_registry import resolve_table_id


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "output" / "logs"
DOC_TEXT_FIELD = "飞书文档"
FOLDER_TEXT_FIELD = "飞书文件夹"
DOC_URL_FIELD = "飞书文档链接"
FOLDER_URL_FIELD = "飞书文件夹链接"
URL_FIELD_TYPE = 15
LINK_SPECS = {
    DOC_TEXT_FIELD: {"target": DOC_URL_FIELD, "label": "打开飞书文档"},
    FOLDER_TEXT_FIELD: {"target": FOLDER_URL_FIELD, "label": "打开飞书文件夹"},
}
URL_RE = re.compile(r"https?://[^\s，。；、)）\]>\"']+")


def now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("link") or value.get("url") or value.get("text") or value.get("name") or "").strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = normalize(item)
            if text:
                parts.append(text)
        return " ".join(parts).strip()
    return str(value).strip()


def extract_url(value: Any) -> str:
    text = normalize(value)
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return URL_RE.search(text).group(0) if URL_RE.search(text) else ""
    match = URL_RE.search(text)
    return match.group(0) if match else ""


def link_payload(url: str, label: str) -> dict[str, str]:
    return {"text": label, "link": url}


def table_map(token: str, app_token: str) -> dict[str, str]:
    return {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}


def resolve_script_package_table(token: str, app_token: str, explicit_table_id: str = "") -> tuple[str, str]:
    tables = table_map(token, app_token)
    if explicit_table_id:
        return explicit_table_id, next((name for name, table_id in tables.items() if table_id == explicit_table_id), "")
    env_table_id = os.getenv("FEISHU_SCRIPT_PACKAGE_TABLE_ID", "").strip()
    if env_table_id:
        return env_table_id, next((name for name, table_id in tables.items() if table_id == env_table_id), "")
    table_id = resolve_table_id(tables, "script_package") or ""
    if not table_id:
        raise SystemExit("Could not resolve script_package table")
    return table_id, next((name for name, value in tables.items() if value == table_id), "")


def list_fields(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


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
        page_token = str(data.get("page_token") or "")


def record_by_id(token: str, app_token: str, table_id: str, record_id: str) -> dict[str, Any]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}", token=token)
    data = payload.get("data", {})
    return data.get("record", data)


def field_type_errors(fields: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for name in [DOC_URL_FIELD, FOLDER_URL_FIELD]:
        field = fields.get(name)
        if not field:
            errors.append({"field": name, "reason": "missing"})
        elif int(field.get("type") or 0) != URL_FIELD_TYPE:
            errors.append({"field": name, "reason": "wrong_type", "type": field.get("type")})
    return errors


def target_link(record_fields: dict[str, Any], target_field: str) -> str:
    return extract_url(record_fields.get(target_field))


def build_record_plan(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields", {})
    record_id = str(record.get("record_id") or "")
    title = normalize(fields.get("脚本标题") or fields.get("关联选题"))[:120]
    updates: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    invalid: dict[str, str] = {}
    conflicts: dict[str, dict[str, str]] = {}
    already_ok: list[str] = []
    parsed: dict[str, str] = {}
    for source_field, spec in LINK_SPECS.items():
        target_field = spec["target"]
        source_url = extract_url(fields.get(source_field))
        current_url = target_link(fields, target_field)
        if not source_url:
            if normalize(fields.get(source_field)):
                invalid[source_field] = normalize(fields.get(source_field))[:160]
            else:
                skipped[source_field] = "empty_source"
            continue
        parsed[source_field] = source_url
        if current_url == source_url:
            already_ok.append(target_field)
            continue
        if current_url and current_url != source_url:
            conflicts[target_field] = {"current": current_url, "source": source_url}
            continue
        updates[target_field] = link_payload(source_url, spec["label"])
    status = "to_update" if updates else "skip"
    if conflicts:
        status = "conflict"
    elif invalid and not updates:
        status = "invalid_source"
    elif already_ok and not updates:
        status = "already_ok"
    return {
        "record_id": record_id,
        "title": title,
        "status": status,
        "updates": updates,
        "parsed_urls": parsed,
        "already_ok": already_ok,
        "skipped": skipped,
        "invalid": invalid,
        "conflicts": conflicts,
    }


def build_backfill_plan(records: list[dict[str, Any]], *, record_ids: set[str] | None = None, limit: int = 0) -> dict[str, Any]:
    selected = [record for record in records if not record_ids or str(record.get("record_id") or "") in record_ids]
    if limit > 0:
        selected = selected[:limit]
    plans = [build_record_plan(record) for record in selected]
    counts: dict[str, int] = {}
    for plan in plans:
        counts[plan["status"]] = counts.get(plan["status"], 0) + 1
    return {
        "total_records": len(records),
        "selected_records": len(selected),
        "counts": counts,
        "to_update_record_ids": [plan["record_id"] for plan in plans if plan["status"] == "to_update"],
        "plans": plans,
    }


def update_record(token: str, app_token: str, table_id: str, record_id: str, fields: dict[str, Any]) -> None:
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        token=token,
        body={"fields": fields},
    )


def read_back_status(record: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields", {})
    mismatches: list[dict[str, str]] = []
    for target_field, payload in plan["updates"].items():
        expected = payload["link"]
        actual = target_link(fields, target_field)
        if actual != expected:
            mismatches.append({"field": target_field, "expected": expected, "actual": actual})
    return {
        "record_id": plan["record_id"],
        "ok": not mismatches,
        "mismatches": mismatches,
    }


def create_test_fixtures(token: str, app_token: str, table_id: str) -> list[str]:
    stamp = now_slug()
    rows = [
        {
            "脚本标题": f"[AR-011-BACKFILL] both urls {stamp}",
            DOC_TEXT_FIELD: "https://my.feishu.cn/docx/AR011BackfillDoc",
            FOLDER_TEXT_FIELD: "https://my.feishu.cn/drive/folder/AR011BackfillFolder",
            "文档同步状态": "测试 fixture",
        },
        {
            "脚本标题": f"[AR-011-BACKFILL] doc only {stamp}",
            DOC_TEXT_FIELD: "https://my.feishu.cn/docx/AR011BackfillDocOnly",
            FOLDER_TEXT_FIELD: "",
            "文档同步状态": "测试 fixture",
        },
        {
            "脚本标题": f"[AR-011-BACKFILL] invalid url {stamp}",
            DOC_TEXT_FIELD: "not a url",
            FOLDER_TEXT_FIELD: "",
            "文档同步状态": "测试 fixture",
        },
    ]
    record_ids: list[str] = []
    for row in rows:
        payload = feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            token=token,
            body={"fields": row},
        )
        data = payload.get("data", {})
        record = data.get("record", data)
        record_ids.append(str(record.get("record_id") or ""))
        time.sleep(0.1)
    return [record_id for record_id in record_ids if record_id]


def write_report(summary: dict[str, Any]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"ar011_clickable_link_backfill_{now_slug()}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_backfill(
    *,
    token: str,
    app_token: str,
    table_id: str,
    table_name: str,
    write: bool,
    record_ids: set[str] | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    fields = list_fields(token, app_token, table_id)
    errors = field_type_errors(fields)
    if errors:
        return {
            "ok": False,
            "table_id": table_id,
            "table_name": table_name,
            "write": write,
            "field_errors": errors,
            "error": "url_fields_not_ready",
        }
    records = all_records(token, app_token, table_id)
    plan = build_backfill_plan(records, record_ids=record_ids, limit=limit)
    writes: list[dict[str, Any]] = []
    read_back: list[dict[str, Any]] = []
    if write:
        for item in plan["plans"]:
            if item["status"] != "to_update":
                continue
            update_record(token, app_token, table_id, item["record_id"], item["updates"])
            writes.append({"record_id": item["record_id"], "fields": sorted(item["updates"])})
            read_back.append(read_back_status(record_by_id(token, app_token, table_id, item["record_id"]), item))
            time.sleep(0.1)
    return {
        "ok": True,
        "table_id": table_id,
        "table_name": table_name,
        "write": write,
        "plan": plan,
        "writes": writes,
        "read_back": read_back,
        "read_back_ok": all(item["ok"] for item in read_back),
        "write_scope": "only_updates_url_mirror_fields",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill 06 clickable URL fields from legacy text URL fields.")
    parser.add_argument("--env-file", default="", help="Explicit env file, e.g. .env.staging.local")
    parser.add_argument("--table-id", default="", help="Override FEISHU_SCRIPT_PACKAGE_TABLE_ID")
    parser.add_argument("--record-id", action="append", default=[], help="Limit to record_id; can repeat or comma-separate")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--write-feishu", action="store_true", help="Apply URL-field backfill")
    parser.add_argument("--allow-production", action="store_true", help="Allow writes when env appears production")
    parser.add_argument("--create-test-fixtures", action="store_true", help="Create staging/test old-style records before planning")
    parser.add_argument("--output-json", default="", help="Optional report path; defaults to output/logs")
    args = parser.parse_args()

    if args.env_file:
        os.environ["AI_ACCOUNT_RADAR_ENV_FILE"] = args.env_file
    load_local_env(required=True)
    environment = env_label()
    if (args.write_feishu or args.create_test_fixtures) and environment == "production" and not args.allow_production:
        raise SystemExit("Refusing production write without --allow-production")
    app_token = require_app_token()
    token = feishu.tenant_token()
    table_id, table_name = resolve_script_package_table(token, app_token, args.table_id)
    record_ids = {part.strip() for value in args.record_id for part in value.split(",") if part.strip()}
    fixture_ids: list[str] = []
    if args.create_test_fixtures:
        fixture_ids = create_test_fixtures(token, app_token, table_id)
        if not record_ids:
            record_ids = set(fixture_ids)
    summary = run_backfill(
        token=token,
        app_token=app_token,
        table_id=table_id,
        table_name=table_name,
        write=args.write_feishu,
        record_ids=record_ids or None,
        limit=args.limit,
    )
    summary["environment"] = environment
    summary["created_fixture_record_ids"] = fixture_ids
    report_path = Path(args.output_json).expanduser() if args.output_json else write_report(summary)
    if args.output_json:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["report_path"] = str(report_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
