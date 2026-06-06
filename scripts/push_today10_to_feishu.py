#!/usr/bin/env python3
"""Write only today's top-10 topic rows to Feishu 04 分析与选题.

This script is intentionally narrow: it does not rebuild tables, does not
write all candidates, and does not touch publishing/lead workflows.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id, table_name


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
TODAY10 = OUT / "today_10_topics.csv"
TARGET_TABLE_KEY = "topic_decision"
REQUIRED_FIELDS = [
    "选题标题",
    "内部切入角度",
    "可发布标题",
    "内容类型",
    "平台建议",
    "标题风格",
    "标题备选",
    "编辑判断分",
    "标题质量分",
    "AI味风险",
    "今日建议级别",
    "主编判断",
    "不建议做的原因",
    "推荐日期",
    "运行日期",
    "运行批次",
    "是否本次新增",
    "最近参与运行批次",
    "今日排名",
    "状态",
    "推荐动作",
    "原始来源标题",
    "来源类型",
    "来源链接",
    "对应栏目",
    "热点切入方式",
    "业务场景",
    "旧流程痛点",
    "AI介入点",
    "可展示结果",
    "可沉淀资产",
    "推荐理由",
    "相关来源",
]
ACTION_STATUS = {
    "立即蹭热点": "待判断",
    "进入Brief": "进入Brief",
    "本周做": "本周做",
    "暂存观察": "暂存",
    "不做": "不做",
}


def read_today10(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Missing {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 10:
        raise SystemExit(f"Expected 10 rows in {path}, got {len(rows)}")
    return rows


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def default_run_id() -> str:
    return os.getenv("RUN_ID") or os.getenv("AI_ACCOUNT_RADAR_RUN_ID") or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def list_tables(token: str, app_token: str) -> dict[str, str]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return {item["name"]: item["table_id"] for item in payload.get("data", {}).get("items", [])}


def list_fields(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {item["field_name"]: item for item in payload.get("data", {}).get("items", [])}


def list_views(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return payload.get("data", {}).get("items", [])


def ensure_fields(token: str, app_token: str, table_id: str) -> list[str]:
    existing = list_fields(token, app_token, table_id)
    created: list[str] = []
    for field_name in REQUIRED_FIELDS:
        if field_name in existing:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body={"field_name": field_name, "type": 1},
        )
        created.append(field_name)
        time.sleep(0.1)
    return created


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


def map_row(row: dict[str, str], rank: int, date: str, run_id: str) -> dict[str, str]:
    status = ACTION_STATUS.get(row.get("推荐动作", ""), "待判断")
    publishable_title = row.get("可发布标题", "")
    display_title = publishable_title or row.get("来源内容", "") or row.get("我的选题标题", "")
    internal_angle = row.get("内部切入角度") or row.get("我的选题标题", "")
    recommendation_reason = row.get("推荐理由", "")
    action_reason = row.get("推荐动作原因", "")
    if action_reason:
        recommendation_reason = f"{recommendation_reason}\n编辑判断：{action_reason}" if recommendation_reason else f"编辑判断：{action_reason}"
    return {
        "选题标题": display_title,
        "内部切入角度": internal_angle,
        "可发布标题": publishable_title,
        "内容类型": row.get("内容类型", ""),
        "平台建议": row.get("平台建议", ""),
        "标题风格": row.get("标题风格", ""),
        "标题备选": row.get("标题备选", ""),
        "编辑判断分": row.get("编辑判断分", ""),
        "标题质量分": row.get("标题质量分", ""),
        "AI味风险": row.get("AI味风险", ""),
        "今日建议级别": row.get("今日建议级别", ""),
        "主编判断": row.get("主编判断", ""),
        "不建议做的原因": row.get("不建议做的原因", ""),
        "推荐日期": date,
        "运行日期": date,
        "运行批次": run_id,
        "是否本次新增": "是",
        "最近参与运行批次": run_id,
        "今日排名": str(rank),
        "状态": status,
        "推荐动作": row.get("推荐动作", ""),
        "原始来源标题": row.get("来源内容", ""),
        "来源类型": row.get("来源类型", ""),
        "来源链接": row.get("来源链接", ""),
        "对应栏目": row.get("对应栏目", ""),
        "热点切入方式": row.get("热点切入方式", ""),
        "业务场景": row.get("业务场景", ""),
        "旧流程痛点": row.get("旧流程痛点", ""),
        "AI介入点": row.get("AI介入点", ""),
        "可展示结果": row.get("可展示结果", ""),
        "可沉淀资产": row.get("可沉淀资产", ""),
        "推荐理由": recommendation_reason,
        "相关来源": row.get("相关来源", ""),
    }


def dry_run_print(rows: list[dict[str, str]]) -> None:
    print(f"DRY-RUN: will write 10 今日Top10 rows to {table_name(TARGET_TABLE_KEY)}")
    for row in rows:
        print(
            f"{row['今日排名']}. {row['选题标题']} | "
            f"{row['内容类型']} / {row['标题风格']} | "
            f"{row['对应栏目']} / {row['热点切入方式']} | "
            f"{row['业务场景']} | {row['推荐动作']} -> {row['状态']} | "
            f"{row.get('今日建议级别', '')} / 编辑分{row.get('编辑判断分', '')} / AI味{row.get('AI味风险', '')}"
        )
        if row.get("内部切入角度") and row["内部切入角度"] != row["选题标题"]:
            print(f"   内部角度: {row['内部切入角度'][:120]}")
        if row.get("相关来源"):
            print(f"   相关来源: {row['相关来源'][:120]}")


def batch_create(token: str, app_token: str, table_id: str, rows: list[dict[str, str]]) -> int:
    total = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            token=token,
            body={"records": [{"fields": row} for row in chunk]},
        )
        total += len(chunk)
        time.sleep(0.15)
    return total


def update_existing_top10(token: str, app_token: str, table_id: str, record: dict[str, Any], row: dict[str, str]) -> None:
    fields = {
        "今日排名": row["今日排名"],
        "运行日期": row["运行日期"],
        "运行批次": row["运行批次"],
        "是否本次新增": "否",
        "最近参与运行批次": row["最近参与运行批次"],
    }
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record['record_id']}",
        token=token,
        body={"fields": fields},
    )


def ensure_today_top10_view(token: str, app_token: str, table_id: str, run_id: str) -> dict[str, Any]:
    views = {view.get("view_name"): view for view in list_views(token, app_token, table_id)}
    created: list[str] = []
    if "今日Top10" not in views:
        payload = feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            token=token,
            body={"view_name": "今日Top10", "view_type": "grid"},
        )
        views["今日Top10"] = payload.get("data", {}).get("view", payload.get("data", {}))
        created.append("今日Top10")
        time.sleep(0.1)
    fields = list_fields(token, app_token, table_id)
    view = views.get("今日Top10", {})
    run_field = fields.get("最近参与运行批次") or fields.get("运行批次")
    if not view.get("view_id") or not run_field:
        return {"created": created, "configured": "missing_view_or_run_field"}
    visible = {"选题标题", "可发布标题", "内部切入角度", "内容类型", "平台建议", "标题风格", "标题备选", "编辑判断分", "标题质量分", "AI味风险", "今日建议级别", "主编判断", "不建议做的原因", "今日排名", "推荐日期", "运行批次", "是否本次新增", "状态", "推荐动作", "来源类型", "原始来源标题", "来源链接", "对应栏目", "热点切入方式", "业务场景", "推荐理由"}
    hidden = [field["field_id"] for name, field in fields.items() if name not in visible]
    body = {
        "view_name": "今日Top10",
        "property": {
            "filter_info": {
                "conditions": [{
                    "field_id": run_field["field_id"],
                    "operator": "is",
                    "value": json.dumps([run_id], ensure_ascii=False),
                }],
                "conjunction": "and",
            },
            "hidden_fields": hidden,
        },
    }
    try:
        feishu.request_json("PATCH", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}", token=token, body=body)
        return {"created": created, "configured": "ok", "hidden_fields": len(hidden)}
    except Exception as exc:
        return {"created": created, "configured": f"failed:{exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Actually write to Feishu. Default is dry-run only.")
    parser.add_argument("--run-id", default="", help="Stable run id shared by 03 内容收件箱 and 04 分析与选题.")
    args = parser.parse_args()

    date = today_slug()
    run_id = args.run_id or default_run_id()
    mapped = [map_row(row, idx, date, run_id) for idx, row in enumerate(read_today10(TODAY10), start=1)]
    dry_run_print(mapped)

    if not args.write:
        print(json.dumps({"ok": True, "mode": "dry-run", "rows": len(mapped)}, ensure_ascii=False, indent=2))
        return 0

    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = feishu.tenant_token()
    tables = list_tables(token, app_token)
    table_id = resolve_table_id(tables, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {TABLES[TARGET_TABLE_KEY]}")
    created_fields = ensure_fields(token, app_token, table_id)

    existing = all_records(token, app_token, table_id)
    existing_by_key = {
        (str(record.get("fields", {}).get("推荐日期", "")), str(record.get("fields", {}).get("选题标题", ""))): record
        for record in existing
    }
    to_create = []
    updated_existing = 0
    for row in mapped:
        record = existing_by_key.get((row["推荐日期"], row["选题标题"]))
        if record:
            update_existing_top10(token, app_token, table_id, record, row)
            updated_existing += 1
            time.sleep(0.1)
        else:
            to_create.append(row)
    created_records = batch_create(token, app_token, table_id, to_create) if to_create else 0
    print(json.dumps({
        "ok": True,
        "mode": "write",
        "table": TABLES[TARGET_TABLE_KEY],
        "run_id": run_id,
        "created_fields": created_fields,
        "created_records": created_records,
        "updated_existing": updated_existing,
        "skipped_existing": len(mapped) - len(to_create),
        "today_view": ensure_today_top10_view(token, app_token, table_id, run_id),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
