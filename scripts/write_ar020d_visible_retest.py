#!/usr/bin/env python3
"""Create isolated AR-020D staging records without source/date upsert."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import push_today10_to_feishu as writer
from local_env import load_local_env


PRODUCTION_TOPIC_TABLE_ID = "tblz2CFc9eIa8bMG"


def validate_staging_target(table_id: str, table_source: str, environment: str) -> None:
    if not table_id or not table_source:
        raise RuntimeError("Explicit staging FEISHU_TOPIC_TABLE_ID is required")
    if table_id == PRODUCTION_TOPIC_TABLE_ID or environment.lower() == "production":
        raise RuntimeError("AR-020D visible retest is blocked in production")


def main() -> int:
    load_local_env(required=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    table_id, table_source = writer.explicit_topic_table_id()
    try:
        validate_staging_target(table_id, table_source, os.getenv("AI_ACCOUNT_RADAR_ENV", ""))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    rows, omitted = writer.feishu_visible_rows(writer.read_today10(Path(args.input)))
    mapped = [writer.map_row(row, index, writer.today_slug(), args.run_id) for index, row in enumerate(rows, start=1)]
    if omitted or len(mapped) != 9:
        raise SystemExit(f"Expected exactly 9 visible rows; mapped={len(mapped)} omitted={omitted}")
    if not args.write:
        print(json.dumps({"ok": True, "mode": "dry-run", "rows": len(mapped), "table_id": table_id}, ensure_ascii=False))
        return 0

    app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = writer.feishu.tenant_token()
    writer.ensure_fields(token, app_token, table_id)
    created = writer.batch_create(token, app_token, table_id, mapped, args.run_id)
    records = [
        record for record in writer.all_records(token, app_token, table_id)
        if str(record.get("fields", {}).get("运行批次", "")) == args.run_id
    ]
    if created != 9 or len(records) != 9:
        raise RuntimeError(f"Isolated create/read-back mismatch: created={created} records={len(records)}")
    by_title = {str(record.get("fields", {}).get("选题标题", "")): record for record in records}
    ordered = [by_title.get(row["选题标题"]) for row in mapped]
    if any(record is None for record in ordered):
        raise RuntimeError("Read-back title identity mismatch")
    payload = {
        "ok": True,
        "mode": "write",
        "run_id": args.run_id,
        "table_id": table_id,
        "table_id_source": table_source,
        "created_records": created,
        "expected_rows": mapped,
        "records": ordered,
        "record_ids": [record["record_id"] for record in ordered],
    }
    Path(args.output_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("ok", "run_id", "table_id", "created_records", "record_ids")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
