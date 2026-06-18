#!/usr/bin/env python3
"""Write today's candidate topic rows to Feishu 04 分析与选题.

This script is intentionally narrow: it does not rebuild tables, does not
write rejected debug candidates, and does not touch publishing/lead workflows.
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
LATEST_WRITE_TODAY10 = OUT / "latest_write" / "today_10_topics.csv"
LEGACY_LOG = OUT / "content_sampler_log.json"
TARGET_TABLE_KEY = "topic_decision"
REQUIRED_FIELDS = [
    "选题标题",
    "候选状态",
    "推荐等级",
    "我的选题标题",
    "内部切入角度",
    "可发布标题",
    "对应方向",
    "一句话Brief",
    "我的场景拆解",
    "我的思考点",
    "重点体现",
    "可调用案例",
    "内容核心冲突",
    "视频呈现方式",
    "证据强度",
    "Skill编辑层",
    "Skill参考文件",
    "内容类型",
    "平台建议",
    "标题风格",
    "标题备选",
    "标题是否过度内部化",
    "标题改写原因",
    "真实用户问题",
    "为什么今天值得做",
    "我能讲出的独特角度",
    "我的账号为什么能讲",
    "是否只是资讯搬运",
    "是否有足够内容支撑",
    "编辑判断分",
    "标题质量分",
    "人设匹配分",
    "内容可信度",
    "AI味风险",
    "是否建议进入制作",
    "今日建议级别",
    "主编判断",
    "模板词命中情况",
    "不建议做的原因",
    "推荐动作原因",
    "降级原因",
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
    "内容指纹",
]
ALLOWED_LEVELS = {"今日最值得做", "可选候选", "暂存观察", "不建议制作"}
FEISHU_VISIBLE_LEVELS = {"今日最值得做", "可选候选"}
LEVEL_ALIASES = {
    "备选": "可选候选",
    "备选候选": "可选候选",
    "备选，不占今日前三": "可选候选",
    "候选": "可选候选",
    "观察": "暂存观察",
    "暂存": "暂存观察",
    "不做": "不建议制作",
    "放弃": "不建议制作",
}


def normalize_level(value: str) -> str:
    cleaned = (value or "").strip()
    if cleaned in ALLOWED_LEVELS:
        return cleaned
    if cleaned in LEVEL_ALIASES:
        return LEVEL_ALIASES[cleaned]
    for key, target in LEVEL_ALIASES.items():
        if key and key in cleaned:
            return target
    if "最值得" in cleaned:
        return "今日最值得做"
    if "不建议" in cleaned:
        return "不建议制作"
    if "暂存" in cleaned or "观察" in cleaned:
        return "暂存观察"
    return "可选候选" if cleaned else ""


def short_text(value: str, limit: int = 38) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def display_title_for(row: dict[str, str], publishable_title: str) -> str:
    if publishable_title:
        return publishable_title
    level = normalize_level(row.get("今日建议级别", ""))
    direction = row.get("对应方向") or row.get("对应栏目") or "候选"
    source = row.get("来源内容") or row.get("原始来源标题") or row.get("我的选题标题") or "未命名来源"
    source_label = short_text(source, 42)
    if level == "不建议制作":
        return f"不建议制作｜{source_label}"
    if level == "暂存观察":
        return f"暂存观察｜{source_label}"
    return row.get("我的选题标题") or f"{direction}候选：{short_text(source)}"
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
    return rows


def feishu_visible_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    visible = [
        row for row in rows
        if normalize_level(row.get("今日建议级别", "")) in FEISHU_VISIBLE_LEVELS
    ]
    return visible, len(rows) - len(visible)


def default_today10_path() -> Path:
    if LATEST_WRITE_TODAY10.exists():
        return LATEST_WRITE_TODAY10
    if TODAY10.exists() and legacy_today10_is_official():
        return TODAY10
    raise SystemExit(
        "No official today candidate CSV found. Use --input with a run-specific CSV, "
        "or run daily_pipeline.py --write-feishu to create output/latest_write/."
    )


def legacy_today10_is_official() -> bool:
    if not LEGACY_LOG.exists():
        return False
    try:
        data = json.loads(LEGACY_LOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return (
        data.get("mode") == "write-feishu"
        or "feishu_content_ledger" in data
        or bool(data.get("mirrors", {}).get("latest_write"))
    )


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
    level = normalize_level(row.get("今日建议级别", ""))
    display_title = display_title_for(row, publishable_title)
    internal_angle = row.get("内部切入角度") or row.get("我的选题标题", "")
    recommendation_reason = row.get("推荐理由", "")
    return {
        "选题标题": display_title,
        "候选状态": normalize_level(row.get("候选状态", "")) or level,
        "推荐等级": row.get("推荐等级", ""),
        "我的选题标题": row.get("我的选题标题", ""),
        "内部切入角度": internal_angle,
        "可发布标题": publishable_title,
        "对应方向": row.get("对应方向", row.get("对应栏目", "")),
        "一句话Brief": row.get("一句话Brief", ""),
        "我的场景拆解": row.get("我的场景拆解", ""),
        "我的思考点": row.get("我的思考点", ""),
        "重点体现": row.get("重点体现", ""),
        "可调用案例": row.get("可调用案例", ""),
        "内容核心冲突": row.get("内容核心冲突", ""),
        "视频呈现方式": row.get("视频呈现方式", ""),
        "证据强度": row.get("证据强度", ""),
        "Skill编辑层": row.get("Skill编辑层", ""),
        "Skill参考文件": row.get("Skill参考文件", ""),
        "内容类型": row.get("内容类型", ""),
        "平台建议": row.get("平台建议", ""),
        "标题风格": row.get("标题风格", ""),
        "标题备选": row.get("标题备选", ""),
        "标题是否过度内部化": row.get("标题是否过度内部化", ""),
        "标题改写原因": row.get("标题改写原因", ""),
        "真实用户问题": row.get("真实用户问题", ""),
        "为什么今天值得做": row.get("为什么今天值得做", ""),
        "我能讲出的独特角度": row.get("我能讲出的独特角度", ""),
        "我的账号为什么能讲": row.get("我的账号为什么能讲", ""),
        "是否只是资讯搬运": row.get("是否只是资讯搬运", ""),
        "是否有足够内容支撑": row.get("是否有足够内容支撑", ""),
        "编辑判断分": row.get("编辑判断分", ""),
        "标题质量分": row.get("标题质量分", ""),
        "人设匹配分": row.get("人设匹配分", ""),
        "内容可信度": row.get("内容可信度", ""),
        "AI味风险": row.get("AI味风险", ""),
        "是否建议进入制作": row.get("是否建议进入制作", ""),
        "今日建议级别": level,
        "主编判断": row.get("主编判断", ""),
        "模板词命中情况": row.get("模板词命中情况", ""),
        "不建议做的原因": row.get("不建议做的原因", ""),
        "推荐动作原因": row.get("推荐动作原因", ""),
        "降级原因": row.get("降级原因", ""),
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
        "内容指纹": row.get("内容指纹", ""),
    }


def dry_run_print(rows: list[dict[str, str]]) -> None:
    print(f"DRY-RUN: will write {len(rows)} 今日候选 rows to {table_name(TARGET_TABLE_KEY)}")
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
    # Fully overwrite all fields controlled by this script. This intentionally
    # keeps empty strings so stale Feishu values are cleared instead of retained.
    fields = {key: row.get(key, "") for key in REQUIRED_FIELDS}
    fields["是否本次新增"] = "否"
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record['record_id']}",
        token=token,
        body={"fields": fields},
    )


def patch_candidate_view(token: str, app_token: str, table_id: str, view_name: str, run_id: str, visible: set[str], extra_filter: dict[str, Any] | None = None) -> dict[str, Any]:
    views = {view.get("view_name"): view for view in list_views(token, app_token, table_id)}
    created: list[str] = []
    if view_name not in views:
        payload = feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            token=token,
            body={"view_name": view_name, "view_type": "grid"},
        )
        views[view_name] = payload.get("data", {}).get("view", payload.get("data", {}))
        created.append(view_name)
        time.sleep(0.1)
    fields = list_fields(token, app_token, table_id)
    view = views.get(view_name, {})
    run_field = fields.get("最近参与运行批次") or fields.get("运行批次")
    if not view.get("view_id") or not run_field:
        return {"created": created, "configured": "missing_view_or_run_field"}
    hidden = [field["field_id"] for name, field in fields.items() if name not in visible]
    conditions = [{
        "field_id": run_field["field_id"],
        "operator": "is",
        "value": json.dumps([run_id], ensure_ascii=False),
    }]
    if extra_filter and extra_filter.get("field") in fields:
        field = fields[extra_filter["field"]]
        conditions.append({
            "field_id": field["field_id"],
            "operator": extra_filter.get("operator", "is"),
            "value": json.dumps(extra_filter.get("value", []), ensure_ascii=False),
        })
    body = {
        "view_name": view_name,
        "property": {
            "filter_info": {
                "conditions": conditions,
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


def delete_view_if_exists(token: str, app_token: str, table_id: str, view_name: str) -> dict[str, Any]:
    views = {view.get("view_name"): view for view in list_views(token, app_token, table_id)}
    view = views.get(view_name)
    if not view or not view.get("view_id"):
        return {"deleted": False, "reason": "missing"}
    try:
        feishu.request_json("DELETE", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}", token=token)
        return {"deleted": True}
    except Exception as exc:
        return {"deleted": False, "reason": f"failed:{exc}"}


def ensure_today_top10_view(token: str, app_token: str, table_id: str, run_id: str) -> dict[str, Any]:
    core_visible = {
        "选题标题", "今日建议级别", "编辑判断分", "AI味风险", "内容可信度",
        "推荐动作", "状态", "来源类型", "原始来源标题", "对应栏目", "业务场景",
        "一句话Brief", "我的场景拆解", "我的思考点", "重点体现", "可调用案例",
        "证据强度", "推荐理由", "不建议做的原因", "可沉淀资产",
    }
    detail_visible = core_visible | {
        "内部切入角度", "可发布标题", "标题备选", "标题质量分", "人设匹配分",
        "主编判断", "是否建议进入制作", "热点切入方式", "来源链接", "内容指纹",
        "内容核心冲突", "视频呈现方式", "Skill编辑层",
    }
    return {
        "今日候选池": patch_candidate_view(token, app_token, table_id, "今日候选池", run_id, core_visible),
        "今日最值得做": patch_candidate_view(
            token,
            app_token,
            table_id,
            "今日最值得做",
            run_id,
            core_visible,
            {"field": "今日建议级别", "operator": "is", "value": ["今日最值得做"]},
        ),
        "暂存观察": patch_candidate_view(
            token,
            app_token,
            table_id,
            "暂存观察",
            run_id,
            detail_visible,
            {"field": "今日建议级别", "operator": "is", "value": ["暂存观察"]},
        ),
        "删除旧今日Top10视图": delete_view_if_exists(token, app_token, table_id, "今日Top10"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Actually write to Feishu. Default is dry-run only.")
    parser.add_argument("--run-id", default="", help="Stable run id shared by 03 内容收件箱 and 04 分析与选题.")
    parser.add_argument("--input", default="", help="Path to the today candidate CSV for this run.")
    args = parser.parse_args()

    date = today_slug()
    run_id = args.run_id or default_run_id()
    input_path = Path(args.input) if args.input else default_today10_path()
    source_rows, omitted_rows = feishu_visible_rows(read_today10(input_path))
    mapped = [map_row(row, idx, date, run_id) for idx, row in enumerate(source_rows, start=1)]
    dry_run_print(mapped)
    if omitted_rows:
        print(f"INFO: omitted {omitted_rows} 暂存观察/不建议制作 rows from Feishu 04 今日候选池.")

    if not args.write:
        print(json.dumps({
            "ok": True,
            "mode": "dry-run",
            "rows": len(mapped),
            "omitted_rows": omitted_rows,
            "input": str(input_path),
        }, ensure_ascii=False, indent=2))
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
    existing_by_fp = {
        (str(record.get("fields", {}).get("推荐日期", "")), str(record.get("fields", {}).get("内容指纹", ""))): record
        for record in existing
        if record.get("fields", {}).get("内容指纹")
    }
    existing_by_source = {
        (str(record.get("fields", {}).get("推荐日期", "")), str(record.get("fields", {}).get("原始来源标题", ""))): record
        for record in existing
        if record.get("fields", {}).get("原始来源标题")
    }
    existing_by_title = {
        (str(record.get("fields", {}).get("推荐日期", "")), str(record.get("fields", {}).get("选题标题", ""))): record
        for record in existing
    }
    to_create = []
    updated_existing = 0
    updated_titles: list[str] = []
    created_titles: list[str] = []
    for row in mapped:
        record = (
            existing_by_fp.get((row["推荐日期"], row.get("内容指纹", "")))
            or existing_by_source.get((row["推荐日期"], row.get("原始来源标题", "")))
            or existing_by_title.get((row["推荐日期"], row["选题标题"]))
        )
        if record:
            update_existing_top10(token, app_token, table_id, record, row)
            updated_existing += 1
            updated_titles.append(row["选题标题"])
            time.sleep(0.1)
        else:
            to_create.append(row)
            created_titles.append(row["选题标题"])
    created_records = batch_create(token, app_token, table_id, to_create) if to_create else 0
    print(json.dumps({
        "ok": True,
        "mode": "write",
        "table": TABLES[TARGET_TABLE_KEY],
        "run_id": run_id,
        "input": str(input_path),
        "created_fields": created_fields,
        "created_records": created_records,
        "updated_existing": updated_existing,
        "skipped_existing": len(mapped) - len(to_create),
        "omitted_rows": omitted_rows,
        "created_titles": created_titles,
        "updated_titles": updated_titles,
        "today_view": ensure_today_top10_view(token, app_token, table_id, run_id),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
