#!/usr/bin/env python3
"""Verify Feishu 04 今日候选池 matches a local today candidate CSV."""
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
from push_today10_to_feishu import default_today10_path, map_row, today_slug


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
    "这条视频",
    "这条内容",
    "对标视频真正",
    "博主",
    "老师",
    "带着它的",
    "玛卡巴卡",
    "用户当前",
    "用户自己的",
    "用户作为",
    "适合用户",
    "帮助用户",
    "用户可以",
    "用户会",
    "用户要",
    "非技术人看 X，不该只看工具名",
    "只有在能说清具体产品层、具体用户任务或具体项目影响时才值得做",
]
COMPARE_FIELDS = [
    "选题命题",
    "选题标题",
    "我的选题标题",
    "我要做的实验",
    "热点触发点",
    "我的工作流痛点",
    "主编自由稿",
    "点击钩子",
    "观众为什么会点",
    "title_permission",
    "我的真实矛盾",
    "选题判断",
    "原始钩子",
    "我的切入",
    "我准备怎么讲",
    "可展示证据",
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
    "一句话Brief",
    "我的场景拆解",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "我的思考点",
    "重点体现",
    "可调用案例",
    "证据强度",
]
VISIBLE_FIELDS = [
    "选题标题",
    "选题命题",
    "我要做的实验",
    "热点触发点",
    "我的工作流痛点",
    "我的选题标题",
    "可发布标题",
    "标题备选",
    "title_permission",
    "主编自由稿",
    "点击钩子",
    "观众为什么会点",
    "我的真实矛盾",
    "选题判断",
    "原始钩子",
    "我的切入",
    "我准备怎么讲",
    "可展示证据",
    "推荐理由",
    "不建议做的原因",
    "主编判断",
    "推荐动作原因",
    "降级原因",
    "一句话Brief",
    "我的场景拆解",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "我的思考点",
    "重点体现",
]
EXPERIMENT_ACTION_TERMS = [
    "测试", "验证", "改造", "压缩", "录成", "接进", "变成", "写回", "沉淀",
    "做成", "复用", "拆成", "跑一轮", "对比", "进入", "重写", "少掉",
    "选择", "选", "记录", "导出", "输出", "标出", "检查", "统计", "回填",
]
WEAK_VALIDATION_PHRASES = [
    "检查是否可用",
    "看是否可用",
    "判断是否可用",
    "对比旧流程、新流程、人工修正点",
    "能否沉淀到",
    "验证是否成立",
    "检查能不能",
]
GENERIC_ASSET_PACKS = [
    "Workflow SOP / 字段规则 / Brief 模板 / 飞书任务检查表",
    "Workflow SOP/字段规则/Brief 模板/飞书任务检查表",
    "导演工作流 SOP / 分镜验收表 / 成片 QA 清单",
    "内容资产流 SOP / 发布前后素材清单 / 复盘模板",
    "项目验收清单 / 复盘模板 / 异常处理记录",
]
GENERIC_ASSET_TERMS = ["通用", "资产包", "模板包", "方法论", "闭环", "待补具体资产"]
GENERIC_ASSET_VALUES = {
    "主编Skill",
    "输入字段",
    "输出字段",
    "飞书字段",
    "品牌规则",
    "视觉规则",
    "字体规则",
    "案例规则",
    "失败样例",
    "人工确认点",
    "状态",
    "输入一条候选内容",
    "再跑一条候选检查",
    "按五段任务表",
    "检查结果能不能写回飞书任务单",
    "跑完后写回飞书任务单",
    "就进入封面Skill",
}
ASSET_NOISE_PHRASES = ["输入一条", "再跑一条", "按五段", "能不能", "是否", "如果", "若", "检查结果", "跑完后", "就进入", "不进入", "就判定"]


def read_local(run_id: str, path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    date = today_slug()
    return [map_row(row, idx, date, run_id) for idx, row in enumerate(rows, start=1)]


def is_visible_candidate(row: dict[str, str]) -> bool:
    return row.get("今日建议级别") not in {"暂存观察", "不建议制作"}


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


def has_any(text: str, terms: list[str]) -> bool:
    return any(term in (text or "") for term in terms)


def validation_is_executable(text: str) -> bool:
    value = text or ""
    if not value:
        return False
    if has_any(value, WEAK_VALIDATION_PHRASES):
        return False
    has_marker = any(marker in value for marker in ["1", "2", "3", "一次", "一条", "一个", "分钟", "截图", "字段", "表", "记录", "输出", "导出", "通过", "失败", "少于", "大于", "小于"])
    return has_any(value, EXPERIMENT_ACTION_TERMS) and has_marker


def asset_is_specific(text: str) -> bool:
    value = " ".join((text or "").split())
    if not value:
        return False
    if value in GENERIC_ASSET_PACKS:
        return False
    if has_any(value, GENERIC_ASSET_TERMS):
        return False
    assets = [part.strip() for part in value.replace("、", "/").split("/") if part.strip()]
    concrete_assets = [
        asset for asset in assets
        if asset not in GENERIC_ASSET_VALUES
        and not has_any(asset, ASSET_NOISE_PHRASES)
    ]
    return any(any(key in asset for key in ["表", "清单", "规则", "Skill", "记录", "模板", "检查", "截图", "案例库", "流程图", "QA", "字段", "对比"]) for asset in concrete_assets)


def local_key(row: dict[str, str]) -> tuple[str, str]:
    return (row.get("内容指纹", ""), row.get("原始来源标题", "") or row.get("选题标题", ""))


def feishu_key(fields: dict[str, Any]) -> tuple[str, str]:
    return (normalize(fields.get("内容指纹")), normalize(fields.get("原始来源标题")) or normalize(fields.get("选题标题")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, help="Run id that was just written to Feishu.")
    parser.add_argument("--input", default="", help="Path to the local today candidate CSV that was written.")
    args = parser.parse_args()
    input_path = Path(args.input) if args.input else default_today10_path()

    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = feishu.tenant_token()
    tables_payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    tables_by_name = {item["name"]: item["table_id"] for item in tables_payload.get("data", {}).get("items", [])}
    table_id = resolve_table_id(tables_by_name, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {TABLES[TARGET_TABLE_KEY]}")

    all_local_rows = read_local(args.run_id, input_path)
    local_rows = [row for row in all_local_rows if is_visible_candidate(row)]
    records = all_records(token, app_token, table_id)
    run_records = [record for record in records if normalize(record.get("fields", {}).get("运行批次")) == args.run_id]
    feishu_by_key = {feishu_key(record.get("fields", {})): record for record in run_records}

    failures: list[str] = []
    warnings: list[str] = []
    if len(run_records) != len(local_rows):
        failures.append(f"Feishu run record count expected {len(local_rows)}, got {len(run_records)}")

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
        if normalize(fields.get("title_permission")) != "可发布标题":
            for field in ["可发布标题", "标题备选"]:
                if normalize(fields.get(field)):
                    failures.append(
                        f"title_permission={normalize(fields.get('title_permission'))} but stale {field}: "
                        f"{local.get('选题标题', '')[:40]}"
                    )
        if local.get("今日建议级别") == "暂存观察":
            for field in ["可发布标题", "标题备选"]:
                if normalize(fields.get(field)):
                    failures.append(f"Watch row has stale {field}: {local.get('选题标题', '')[:40]}")
        if normalize(fields.get("选题标题")) != normalize(fields.get("选题命题")):
            failures.append(f"Feishu primary title should mirror 选题命题: {normalize(fields.get('选题标题'))[:40]}")
        if not normalize(fields.get("选题命题")):
            failures.append(f"Feishu row missing 选题命题: {normalize(fields.get('原始来源标题'))[:40]}")
        if len(normalize(fields.get("选题命题"))) > 90:
            failures.append(f"Feishu row 选题命题 too long: {normalize(fields.get('选题命题'))[:60]}")
        for field in ["我要做的实验", "热点触发点", "我的工作流痛点", "旧流程痛点", "AI介入点", "验证方式", "可沉淀资产"]:
            if not normalize(fields.get(field)):
                failures.append(f"Feishu row missing workflow-experiment field {field}: {normalize(fields.get('选题标题'))[:40]}")
        if normalize(fields.get("验证方式")) and not validation_is_executable(normalize(fields.get("验证方式"))):
            failures.append(f"Feishu row 验证方式 is not executable: {normalize(fields.get('选题标题'))[:40]}")
        if normalize(fields.get("可沉淀资产")) and not asset_is_specific(normalize(fields.get("可沉淀资产"))):
            failures.append(f"Feishu row 可沉淀资产 too generic: {normalize(fields.get('选题标题'))[:40]}")

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
        "input": str(input_path),
        "local_rows_all": len(all_local_rows),
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
