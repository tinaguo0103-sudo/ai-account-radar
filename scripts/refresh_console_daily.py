#!/usr/bin/env python3
"""Refresh the Feishu pseudo dashboard and generate the daily radar report."""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import push_to_feishu as feishu


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
REPORT_DIR = OUT / "daily_reports"
BASE_URL = "https://my.feishu.cn/base"

APP_TOKEN_ENV = "FEISHU_BASE_APP_TOKEN"

TABLES = [
    "00 主控台",
    "01 来源与采样",
    "06 URL投喂入口",
    "02 内容收件箱",
    "03 分析与选题",
    "04 Brief与制作",
    "05 资产与复盘",
    "99 规则与字典",
]

CONSOLE_FIELDS = [
    ("数量/摘要", 1),
    ("入口说明", 1),
    ("最后更新时间", 1),
]

VIEW_PLAN = {
    "00 主控台": ["今日工作台"],
    "01 来源与采样": ["来源与URL入口"],
    "06 URL投喂入口": ["URL投喂入口"],
    "02 内容收件箱": ["内容收件箱"],
    "03 分析与选题": ["今日Top10"],
    "04 Brief与制作": ["Brief制作后台"],
    "05 资产与复盘": ["资产复盘后台"],
    "99 规则与字典": ["规则与字典"],
}


def require_env() -> str:
    app_token = os.getenv(APP_TOKEN_ENV)
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return app_token


def now_cn() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def table_url(app_token: str, table_id: str) -> str:
    return f"{BASE_URL}/{app_token}?table={table_id}"


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


def fields_by_name(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def ensure_fields(token: str, app_token: str, table_id: str) -> list[str]:
    existing = fields_by_name(token, app_token, table_id)
    created: list[str] = []
    for name, field_type in CONSOLE_FIELDS:
        if name in existing:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body={"field_name": name, "type": field_type},
        )
        created.append(name)
        time.sleep(0.1)
    return created


def ensure_views(token: str, app_token: str, tables: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table_name, view_names in VIEW_PLAN.items():
        table_id = tables.get(table_name)
        if not table_id:
            result[table_name] = {"status": "missing_table"}
            continue
        payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
        existing = {view.get("view_name") for view in payload.get("data", {}).get("items", [])}
        created: list[str] = []
        skipped: list[str] = []
        for view_name in view_names:
            if view_name in existing:
                skipped.append(view_name)
                continue
            feishu.request_json(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
                token=token,
                body={"view_name": view_name, "view_type": "grid"},
            )
            created.append(view_name)
            time.sleep(0.1)
        result[table_name] = {"created": created, "skipped": skipped}
    return result


def score_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def is_ab_or_high(fields: dict[str, Any]) -> bool:
    level = str(fields.get("推荐等级", ""))
    total = score_value(fields.get("总分"))
    return any(mark in level for mark in ["A", "B", "A级", "B级"]) or total >= 82


def top_records(records: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
    return sorted(records, key=lambda r: score_value(r.get("fields", {}).get("总分")), reverse=True)[:n]


def load_run_errors() -> list[str]:
    path = OUT / "run_log.json"
    if not path.exists():
        return ["暂无异常"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["run_log.json 无法解析"]
    errors = [item for item in data.get("aihot_fetch", []) if "ok" not in item.lower()]
    return errors or ["暂无异常"]


def asset_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    done = {"已完成", "完成", "复盘完成", "已归档"}
    priority_marks = {"高", "中高"}
    result = []
    for record in records:
        fields = record.get("fields", {})
        priority = str(fields.get("优先级", ""))
        status = str(fields.get("当前状态", ""))
        if priority in priority_marks and status not in done:
            result.append(record)
    return result


def summarize_sources(records: list[dict[str, Any]]) -> str:
    counter = Counter(str(record.get("fields", {}).get("来源名称") or record.get("fields", {}).get("平台") or "未知来源") for record in records)
    if not counter:
        return "暂无来源数据"
    return "、".join(f"{name} {count}条" for name, count in counter.most_common(4))


def build_console_cards(app_token: str, table_ids: dict[str, str], stats: dict[str, Any], updated_at: str) -> list[dict[str, str]]:
    links = {name: table_url(app_token, table_id) for name, table_id in table_ids.items()}
    return [
        {
            "动作": "今日10选题",
            "优先级": "高",
            "工作区": "分析与选题",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['today_10_count']} 条已生成；最建议进入Brief：{stats['top_today_topic']}",
            "说明": "前台只看这里：今日10选题来自 AIHOT 热点、对标视频和公众号文章的内容拆解，不是数据榜单。",
            "下一步": "打开今日10选题文件，选 1 条进入 03 分析与选题 / 04 Brief与制作。",
            "入口说明": stats["today_10_report"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "URL投喂入口",
            "优先级": "高",
            "工作区": "来源与采样",
            "状态": "临时入口",
            "数量/摘要": "把公众号、抖音、小红书、视频号链接粘到这里；解析后可手动删除。",
            "说明": "这是临时链接入口，不是长期业务表。系统只读取公开可见内容，失败会记录原因。",
            "下一步": "粘贴 URL 后运行 daily_pipeline.py --feishu-urls。",
            "入口说明": links.get("06 URL投喂入口", ""),
            "最后更新时间": updated_at,
        },
        {
            "动作": "今日新增待判断内容",
            "优先级": "高",
            "工作区": "内容收件箱",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['pending_inbox_count']} 条待分析；主要来源：{stats['source_summary']}",
            "说明": "不用每天打开收件箱；这里提示是否有新内容需要进入分析。",
            "下一步": "打开 03 分析与选题 / 今日决策，先看高分候选。",
            "入口说明": links["02 内容收件箱"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "高分选题池",
            "优先级": "高",
            "工作区": "分析与选题",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['high_topic_count']} 条待判断高分/AB 选题",
            "说明": "核心决策区：判断是否进入 Brief、本周做、暂存、归档或不做。",
            "下一步": "进入 03，把值得做的状态改为 进入Brief 或 本周做。",
            "入口说明": links["03 分析与选题"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "待生成 Brief",
            "优先级": "高",
            "工作区": "分析与选题",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['topic_to_brief_count']} 条状态为 进入Brief/本周做 的选题",
            "说明": "这些选题已经通过决策，下一步应进入 Brief。",
            "下一步": "打开 04 Brief与制作，补齐对应 Brief。",
            "入口说明": links["04 Brief与制作"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "Brief 待补案例",
            "优先级": "高",
            "工作区": "Brief与制作",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['brief_need_case_count']} 条待补案例",
            "说明": "系统只给提纲；真实案例、截图、个人判断必须人工补。",
            "下一步": "补真实业务现场、视觉建议、CTA 和边界。",
            "入口说明": links["04 Brief与制作"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "可制作内容",
            "优先级": "中高",
            "工作区": "Brief与制作",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['brief_ready_count']} 条可制作",
            "说明": "这些内容已经具备制作条件，但仍由你人工完成最终表达。",
            "下一步": "选择 1 条开拍、写稿或制图；不自动发布。",
            "入口说明": links["04 Brief与制作"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "已发布待复盘",
            "优先级": "中",
            "工作区": "Brief与制作",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['published_review_count']} 条待复盘",
            "说明": "发布后只回填真实数据，不伪造、不自动发布。",
            "下一步": "回填播放/阅读、收藏、评论、私信和复盘结论。",
            "入口说明": links["04 Brief与制作"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "来源异常/采集失败",
            "优先级": "中",
            "工作区": "来源与采样",
            "状态": "今日工作台",
            "数量/摘要": "；".join(stats["source_errors"]),
            "说明": "某个来源失败不会中断全流程；可手动粘贴 AIHOT 或对标内容。",
            "下一步": "如有异常，打开 01 来源与采样 手动补材料。",
            "入口说明": links["01 来源与采样"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "本周可沉淀资产",
            "优先级": "中高",
            "工作区": "资产与复盘",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['asset_count']} 个高优先级未完成资产",
            "说明": "优先沉淀清单、SOP、流程图、案例库或资料包。",
            "下一步": "每周打开 05，决定本周先做哪个资产。",
            "入口说明": links["05 资产与复盘"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "99 规则与字典入口",
            "优先级": "高",
            "工作区": "规则说明",
            "状态": "固定入口",
            "数量/摘要": "查看表逻辑、字段含义、状态流转、评分规则、AI边界",
            "说明": "这是系统说明书，不是业务数据表。",
            "下一步": "打开 99 规则与字典，按规则类型查看。",
            "入口说明": links["99 规则与字典"],
            "最后更新时间": updated_at,
        },
    ]


def sync_console_cards(token: str, app_token: str, table_id: str, cards: list[dict[str, str]]) -> dict[str, Any]:
    records = all_records(token, app_token, table_id)
    by_action = {record.get("fields", {}).get("动作"): record for record in records}
    expected = {card["动作"] for card in cards}
    updated = created = deleted = 0
    for card in cards:
        existing = by_action.get(card["动作"])
        if existing:
            feishu.request_json(
                "PUT",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{existing['record_id']}",
                token=token,
                body={"fields": card},
            )
            updated += 1
        else:
            feishu.request_json(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                token=token,
                body={"fields": card},
            )
            created += 1
        time.sleep(0.1)

    stale = [record["record_id"] for record in records if record.get("fields", {}).get("动作") not in expected]
    for start in range(0, len(stale), 500):
        chunk = stale[start:start + 500]
        if not chunk:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
            token=token,
            body={"records": chunk},
        )
        deleted += len(chunk)
        time.sleep(0.1)
    return {"updated": updated, "created": created, "deleted_stale": deleted}


def generate_report(stats: dict[str, Any], updated_at: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"ai_account_radar_daily_{today_slug()}.md"
    top_topics = stats["top_topics"]
    brief_items = stats["brief_need_case_items"][:5]
    assets = stats["asset_items"][:5]

    def topic_line(record: dict[str, Any]) -> str:
        fields = record.get("fields", {})
        return (
            f"- {fields.get('选题标题', '未命名选题')} | "
            f"{fields.get('业务场景', '待判断场景')} | "
            f"{fields.get('为什么推荐', '待补推荐理由')}"
        )

    def brief_line(record: dict[str, Any]) -> str:
        fields = record.get("fields", {})
        return f"- {fields.get('关联选题', '未命名Brief')}：{fields.get('人工补充', '补真实案例、个人判断和边界')}"

    def asset_line(record: dict[str, Any]) -> str:
        fields = record.get("fields", {})
        return f"- {fields.get('名称/模块', '未命名资产')}：{fields.get('核心内容', '待补核心内容')}"

    actions = [
        "先在 03 分析与选题 中处理高分待判断选题，至少选 1 条改为 进入Brief 或 本周做。",
        "在 04 Brief与制作 中补 1-3 条真实案例、个人判断、视觉建议和 CTA。",
        "从本周可沉淀资产里选 1 个轻量资产，先做精简版，不追求完整大包。",
    ]
    if stats["source_errors"] != ["暂无异常"]:
        actions[2] = "先处理来源异常；失败来源不要硬抓，必要时用 01 来源与采样 手动粘贴。"

    content = [
        f"# AI账号雷达日报 {today_slug()}",
        "",
        f"生成时间：{updated_at}",
        "",
        "## 1. 今日新增内容",
        f"- 当前 `02 内容收件箱` 有 {stats['pending_inbox_count']} 条待分析内容。",
        f"- 主要来源：{stats['source_summary']}",
        "",
        "## 2. 今日最值得看的选题",
        *(topic_line(record) for record in top_topics[:5]),
        "",
        "## 3. 待补 Brief",
        *(brief_line(record) for record in brief_items),
        "",
        "## 4. 来源异常/采集失败",
        *[f"- {item}" for item in stats["source_errors"]],
        "",
        "## 5. 本周可沉淀资产",
        *(asset_line(record) for record in assets),
        "",
        "## 6. 今天建议做的 3 件事",
        *(f"{index}. {action}" for index, action in enumerate(actions, start=1)),
        "",
        "边界提醒：本日报只做摘要、分析和行动建议；不生成完整成稿、不自动发布、不伪造数据、不绕过平台限制，最终观点由你判断。",
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")
    return path


def load_today_10() -> dict[str, Any]:
    path = OUT / "today_10_topics.csv"
    report = REPORT_DIR / f"today_10_topics_{today_slug()}.md"
    if not path.exists():
        return {"count": 0, "top": "尚未生成", "report": str(report)}
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    top = rows[0].get("我的选题标题", "尚未生成") if rows else "尚未生成"
    return {"count": len(rows), "top": top, "report": str(report)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-feishu", action="store_true", help="Only generate local report; do not update Feishu.")
    args = parser.parse_args()

    app_token = require_env()
    token = feishu.tenant_token()
    tables = {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}
    missing = [name for name in TABLES if name not in tables]
    if missing:
        raise SystemExit(f"Missing required tables: {missing}")

    inbox_records = all_records(token, app_token, tables["02 内容收件箱"])
    topic_records = all_records(token, app_token, tables["03 分析与选题"])
    brief_records = all_records(token, app_token, tables["04 Brief与制作"])
    asset_records = all_records(token, app_token, tables["05 资产与复盘"])

    pending_inbox = [r for r in inbox_records if r.get("fields", {}).get("处理状态") == "待分析"]
    high_topics = [r for r in topic_records if r.get("fields", {}).get("状态") == "待判断" and is_ab_or_high(r.get("fields", {}))]
    topic_to_brief = [r for r in topic_records if r.get("fields", {}).get("状态") in {"进入Brief", "本周做"}]
    brief_need_case = [r for r in brief_records if r.get("fields", {}).get("制作状态") == "待补案例"]
    brief_ready = [r for r in brief_records if r.get("fields", {}).get("制作状态") == "可制作"]
    published_review = [r for r in brief_records if r.get("fields", {}).get("制作状态") == "已发布待复盘"]
    assets = asset_candidates(asset_records)
    today10 = load_today_10()

    stats = {
        "today_10_count": today10["count"],
        "top_today_topic": today10["top"],
        "today_10_report": today10["report"],
        "pending_inbox_count": len(pending_inbox),
        "source_summary": summarize_sources(pending_inbox),
        "high_topic_count": len(high_topics),
        "topic_to_brief_count": len(topic_to_brief),
        "brief_need_case_count": len(brief_need_case),
        "brief_ready_count": len(brief_ready),
        "published_review_count": len(published_review),
        "asset_count": len(assets),
        "source_errors": load_run_errors(),
        "top_topics": top_records(high_topics, 5),
        "brief_need_case_items": brief_need_case,
        "asset_items": assets,
    }
    updated_at = now_cn()
    report_path = generate_report(stats, updated_at)

    views = {}
    console_sync = {}
    created_fields: list[str] = []
    if not args.no_feishu:
        created_fields = ensure_fields(token, app_token, tables["00 主控台"])
        cards = build_console_cards(app_token, tables, stats, updated_at)
        console_sync = sync_console_cards(token, app_token, tables["00 主控台"], cards)
        views = ensure_views(token, app_token, tables)

    print(json.dumps({
        "ok": True,
        "report": str(report_path),
        "stats": {k: v for k, v in stats.items() if not k.endswith("_items") and k != "top_topics"},
        "created_console_fields": created_fields,
        "console_sync": console_sync,
        "views": views,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
