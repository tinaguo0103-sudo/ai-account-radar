#!/usr/bin/env python3
"""Delete only named AR-040/AR-043B proof records from explicit staging tables."""
from __future__ import annotations

import argparse
import json
import os
import re

import push_to_feishu as feishu
from feishu_table_registry import configured_table_id
from local_env import load_local_env


RUN_RE = re.compile(
    r"^run_\d{8}_\d{6}_(?:ar040_devproof|ar043b_(?:devproof|rework)|ar044_devproof|ar046_devproof)$"
)


def records_for_run(token: str, app_token: str, table_id: str, run_id: str) -> list[str]:
    record_ids: list[str] = []
    page_token = ""
    while True:
        suffix = f"?page_size=500{('&page_token=' + page_token) if page_token else ''}"
        payload = feishu.request_json(
            "GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records{suffix}", token=token,
        )
        data = payload.get("data", {})
        for record in data.get("items", []):
            if str(record.get("fields", {}).get("运行批次") or "").strip() == run_id:
                record_ids.append(str(record.get("record_id") or ""))
        if not data.get("has_more"):
            return [value for value in record_ids if value]
        page_token = str(data.get("page_token") or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    load_local_env(required=True)
    env_marker = " ".join([
        os.getenv("AI_ACCOUNT_RADAR_ENV", ""), os.getenv("AI_ACCOUNT_RADAR_ENV_FILE", ""),
    ]).lower()
    if "staging" not in env_marker:
        raise SystemExit("cleanup_requires_explicit_staging_environment")
    if any(not RUN_RE.fullmatch(value) for value in args.run_id):
        raise SystemExit("cleanup_rejects_unknown_proof_run_identity")
    app_token = os.environ["FEISHU_BASE_APP_TOKEN"]
    tables_by_name: dict[str, str] = {}
    targets = []
    for key in ("content_inbox", "topic_decision"):
        table_id, source = configured_table_id(tables_by_name, key)
        if not table_id or not source.startswith("FEISHU_"):
            raise SystemExit(f"cleanup_requires_explicit_table_id:{key}")
        targets.append((key, table_id))
    token = feishu.tenant_token()
    report = {"ok": True, "mode": "write" if args.write else "check_only", "runs": args.run_id, "tables": []}
    for key, table_id in targets:
        record_ids = []
        for run_id in args.run_id:
            record_ids.extend(records_for_run(token, app_token, table_id, run_id))
        if args.write and record_ids:
            feishu.request_json(
                "POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
                token=token, body={"records": record_ids},
            )
        report["tables"].append({"key": key, "matched": len(record_ids), "deleted": len(record_ids) if args.write else 0})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
