#!/usr/bin/env python3
"""Verify Feishu 04 今日Top10 matches local output/today_10_topics.csv."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id
from push_today10_to_feishu import TODAY10, map_row, today_slug


TARGET_TABLE_KEY = "topic_decision"
FORBIDDEN_VISIBLE_TERMS = [
    "自查表",
    "少做一小时",
    "这类更新",
    "可执行动作",
    "业务动作",
    "业务验收清单",
    "别只看发布信息",
    "先看任务怎么验收",
    "该先判断",
    "最该重排",
    "适合拆成一次真实任务边界测试",
    "适合拆成一次AI视频交付测试",
    "非技术人看 X，不该只看工具名",
    "只有在能说清具体产品层、具体用户任务或具体项目影响时才值得做",
]
COMPARE_FIELDS = [
    "今日建议级别",
    "推荐动作",
    "是否建议进入制作",
    "编辑判断分",
    "标题质量分",
    "AI味风险",
    "可发布标题",
    "标题备选",
    "不建议做的原因",
    "主编判断",
    "推荐理由",
    "运行批次",
]
VISIBLE_FIELDS = [
    "选题标题",
    "我的选题标题",
    "可发布标题",
    "标题备选",
    "推荐理由",
    "不建议做的原因",
    "主编判断",
    "推荐动作原因",
    "降级原因",
]


def read_local(run_id: str) -> list[dict[str, str]]:
    with TODAY10.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    date = today_slug()
    return [map_row(row, idx, date, run_id) for idx, row in enumerate(rows, start=1)]


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


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(normalize(item) for item in value)
    if isinstance(value, dict):
        if "text" in value:
            return str(value.get("text") or "")
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def local_key(row: dict[str, str]) -> tuple[str, str]:
    return (row.get("内容指纹", ""), row.get("原始来源标题", "") or row.get("选题标题", ""))


def feishu_key(fields: dict[str, Any]) -> tuple[str, str]:
    return (normalize(fields.get("内容指纹")), normalize(fields.get("原始来源标题")) or normalize(fields.get("选题标题")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, help="Run id that was just written to Feishu.")
    args = parser.parse_args()

    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = feishu.tenant_token()
    tables_payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    tables_by_name = {item["name"]: item["table_id"] for item in tables_payload.get("data", {}).get("items", [])}
    table_id = resolve_table_id(tables_by_name, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {TABLES[TARGET_TABLE_KEY]}")

    local_rows = read_local(args.run_id)
    records = all_records(token, app_token, table_id)
    run_records = [record for record in records if normalize(record.get("fields", {}).get("运行批次")) == args.run_id]
    feishu_by_key = {feishu_key(record.get("fields", {})): record for record in run_records}

    failures: list[str] = []
    warnings: list[str] = []
    if len(run_records) != 10:
        failures.append(f"Feishu run record count expected 10, got {len(run_records)}")
    if len(local_rows) != 10:
        failures.append(f"Local Top10 count expected 10, got {len(local_rows)}")

    key_counts = Counter(feishu_key(record.get("fields", {})) for record in run_records)
    duplicates = [key for key, count in key_counts.items() if count > 1 and any(key)]
    if duplicates:
        failures.append(f"Duplicate Feishu records in run: {duplicates[:3]}")

    for local in local_rows:
        key = local_key(local)
        record = feishu_by_key.get(key)
        if not record:
            failures.append(f"Missing Feishu row for local key: {key}")
            continue
        fields = record.get("fields", {})
        for field in COMPARE_FIELDS:
            local_value = normalize(local.get(field, ""))
            feishu_value = normalize(fields.get(field))
            if local_value != feishu_value:
                failures.append(
                    f"Field mismatch for {local.get('选题标题', '')[:40]} / {field}: "
                    f"local={local_value!r} feishu={feishu_value!r}"
                )
        if local.get("今日建议级别") == "暂存观察":
            for field in ["可发布标题", "标题备选"]:
                if normalize(fields.get(field)):
                    failures.append(f"Watch row has stale {field}: {local.get('选题标题', '')[:40]}")

    level_counts = Counter(normalize(record.get("fields", {}).get("今日建议级别")) for record in run_records)
    if level_counts.get("今日最值得做", 0) > 3:
        failures.append(f"今日最值得做 count > 3: {level_counts.get('今日最值得做')}")

    forbidden_hits: list[dict[str, str]] = []
    for record in run_records:
        fields = record.get("fields", {})
        visible = "\n".join(normalize(fields.get(field)) for field in VISIBLE_FIELDS)
        for term in FORBIDDEN_VISIBLE_TERMS:
            if term in visible:
                forbidden_hits.append({
                    "title": normalize(fields.get("选题标题"))[:60],
                    "term": term,
                })
    if forbidden_hits:
        failures.append(f"Forbidden visible terms found: {forbidden_hits[:5]}")

    report = {
        "ok": not failures,
        "run_id": args.run_id,
        "local_rows": len(local_rows),
        "feishu_rows": len(run_records),
        "level_counts": dict(level_counts),
        "duplicates": [list(key) for key in duplicates],
        "warnings": warnings,
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
