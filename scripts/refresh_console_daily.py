#!/usr/bin/env python3
"""Refresh the Feishu pseudo dashboard and generate the daily radar report."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES as LOGICAL_TABLES
from feishu_table_registry import VIEW_NAMES, resolve_table_id, table_name


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
REPORT_DIR = OUT / "daily_reports"
LATEST_WRITE_DIR = OUT / "latest_write"
BASE_URL = "https://my.feishu.cn/base"

APP_TOKEN_ENV = "FEISHU_BASE_APP_TOKEN"

TABLE_KEYS = list(LOGICAL_TABLES)

CONSOLE_FIELDS = [
    ("卡片类型", 3),
    ("数量/摘要", 1),
    ("入口表", 1),
    ("入口视图", 1),
    ("入口说明", 1),
    ("最后更新时间", 1),
]

VIEW_PLAN = {table_name(key): views for key, views in VIEW_NAMES.items()}
VIEW_PLAN[table_name("console")] = ["今日工作台", "系统导航"]

CARD_TYPE_OPTIONS = ["今日工作", "预警提醒", "进度统计", "系统导航", "规则说明", "临时入口"]
DAILY_CARD_TYPES = {"今日工作", "预警提醒", "进度统计", "临时入口"}
NAV_CARD_TYPES = {"系统导航", "规则说明"}
DAILY_VIEW_VISIBLE_FIELDS = ["动作", "卡片类型", "优先级", "工作区", "状态", "数量/摘要", "下一步", "入口表", "入口视图", "最后更新时间"]
NAV_VIEW_VISIBLE_FIELDS = ["动作", "卡片类型", "优先级", "工作区", "状态", "数量/摘要", "下一步", "入口表", "入口视图", "最后更新时间"]
DEPRECATED_CONSOLE_ACTIONS = {
    "执行层：06 内容任务主表",
    "今日必须完成",
    "明日预警",
    "本周内容进度",
    "未来7天发布",
    "待复盘内容",
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


SCRIPT_PACKAGE_QUEUE_DAYS = 5
TEST_TOPIC_PREFIXES = ("【测试】", "【流程测试】", "【部署后测试】", "【模拟测试】", "[测试]", "测试：", "测试:")
TEST_TOPIC_TAG_RE = re.compile(r"^(【[^】]*(测试|测速)[^】]*】|\[[^\]]*(测试|测速)[^\]]*\])")


def record_day(value: Any) -> date | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp).date()
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), "%Y-%m-%d").date()
    except ValueError:
        return None


def within_recent_days(value: Any, max_age_days: int = SCRIPT_PACKAGE_QUEUE_DAYS) -> bool:
    day = record_day(value)
    if not day:
        return False
    oldest = date.today() - timedelta(days=max_age_days - 1)
    return oldest <= day <= date.today()


def is_test_topic(record: dict[str, Any]) -> bool:
    fields = record.get("fields", {})
    title = str(
        fields.get("选题标题")
        or fields.get("选题命题")
        or fields.get("一句话Brief")
        or ""
    ).strip()
    return title.startswith(TEST_TOPIC_PREFIXES) or bool(TEST_TOPIC_TAG_RE.match(title))


def ready_for_script_recent(record: dict[str, Any]) -> bool:
    fields = record.get("fields", {})
    status = str(fields.get("状态") or fields.get("推荐动作") or "")
    already_generated = str(fields.get("是否已生成脚本稿", "")) == "是"
    return (
        status == "生成脚本包"
        and not already_generated
        and not is_test_topic(record)
        and within_recent_days(fields.get("推荐日期"))
    )


def legacy_sampler_log_is_official(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return (
        data.get("mode") == "write-feishu"
        or "feishu_content_ledger" in data
        or bool(data.get("mirrors", {}).get("latest_write"))
    )


def table_url(app_token: str, table_id: str) -> str:
    return f"{BASE_URL}/{app_token}?table={table_id}"


def all_records(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        suffix = f"?page_size=500{('&page_token=' + page_token) if page_token else ''}"
        for attempt in range(5):
            try:
                payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records{suffix}", token=token)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1.5)
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
        body: dict[str, Any] = {"field_name": name, "type": field_type}
        if name == "卡片类型":
            body["property"] = {
                "options": [
                    {"name": option, "color": index % 10}
                    for index, option in enumerate(CARD_TYPE_OPTIONS)
                ]
            }
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body=body,
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
        existing = {view.get("view_name"): view for view in payload.get("data", {}).get("items", [])}
        created: list[str] = []
        skipped: list[str] = []
        for view_name in view_names:
            if view_name in existing:
                skipped.append(view_name)
                continue
            response = feishu.request_json(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
                token=token,
                body={"view_name": view_name, "view_type": "grid"},
            )
            view = response.get("data", {}).get("view", response.get("data", {}))
            existing[view_name] = view
            created.append(view_name)
            time.sleep(0.1)
        result[table_name] = {"created": created, "skipped": skipped}
        if table_name == table_name_for_console():
            result[table_name]["configured"] = configure_console_views(token, app_token, table_id, existing)
    return result


def table_name_for_console() -> str:
    return table_name("console")


def configure_console_views(token: str, app_token: str, table_id: str, views_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields = fields_by_name(token, app_token, table_id)
    card_type = fields.get("卡片类型")
    if not card_type:
        return {"status": "missing_card_type_field"}
    option_ids = {
        option.get("name"): option.get("id")
        for option in card_type.get("property", {}).get("options", [])
    }

    def patch_view(view_name: str, allowed_types: set[str], visible_fields: list[str]) -> dict[str, Any]:
        view = views_by_name.get(view_name)
        if not view or not view.get("view_id"):
            return {"status": "missing_view"}
        allowed_option_ids = [option_ids[name] for name in sorted(allowed_types) if option_ids.get(name)]
        missing_options = sorted(name for name in allowed_types if not option_ids.get(name))
        if missing_options:
            return {"status": "missing_options", "missing": missing_options}
        hidden_fields = [
            field["field_id"]
            for name, field in fields.items()
            if name not in visible_fields
        ]
        conditions = [
            {
                "field_id": card_type["field_id"],
                "operator": "is",
                "value": json.dumps([option_id], ensure_ascii=False),
            }
            for option_id in allowed_option_ids
        ]
        body = {
            "view_name": view_name,
            "property": {
                "filter_info": {
                    "conditions": conditions,
                    "conjunction": "or",
                },
                "hidden_fields": hidden_fields,
            },
        }
        try:
            feishu.request_json(
                "PATCH",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}",
                token=token,
                body=body,
            )
            return {"status": "configured", "hidden_fields": len(hidden_fields)}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    return {
        "今日工作台": patch_view("今日工作台", DAILY_CARD_TYPES, DAILY_VIEW_VISIBLE_FIELDS),
        "系统导航": patch_view("系统导航", NAV_CARD_TYPES, NAV_VIEW_VISIBLE_FIELDS),
    }


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
    sampler_log = LATEST_WRITE_DIR / "content_sampler_log.json"
    if not sampler_log.exists():
        legacy_log = OUT / "content_sampler_log.json"
        sampler_log = legacy_log if legacy_sampler_log_is_official(legacy_log) else sampler_log
    if sampler_log.exists():
        try:
            data = json.loads(sampler_log.read_text(encoding="utf-8"))
            errors = [log for log in data.get("logs", []) if "failed" in log.lower() or "error" in log.lower() or "jsondecode" in log.lower()]
            if errors:
                return [f"部分源失败：{error}" for error in errors[:5]]
        except json.JSONDecodeError:
            return ["content_sampler_log.json 无法解析"]
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
            "动作": "系统地图：内容从哪里来，到哪里去",
            "卡片类型": "系统导航",
            "优先级": "高",
            "工作区": "系统地图",
            "状态": "固定导航",
            "数量/摘要": "01/02 输入 → 03 内容池 → 04 今日候选池 → 06 完整脚本与制作包 → 07 复盘资产化",
            "说明": "系统不是一堆孤立表，而是一条从内容输入到选题、脚本制作包和复盘的链路。",
            "下一步": "看完地图后回到 今日工作台。",
            "入口表": "00 主控台",
            "入口视图": "今日工作台",
            "入口说明": "docs/system_map.md",
            "最后更新时间": updated_at,
        },
        {
            "动作": "输入层：01/02/03",
            "卡片类型": "系统导航",
            "优先级": "中",
            "工作区": "输入层",
            "状态": "固定导航",
            "数量/摘要": "01 来源配置；02 链接/OCR/手动内容入口；03 后台内容池。",
            "说明": "输入层主要给系统用，不是每天处理任务的地方。",
            "下一步": "只有补链接、排查采集或看原始内容时才打开。",
            "入口表": "02 URL投喂入口",
            "入口视图": "URL投喂入口",
            "入口说明": links.get("02 URL投喂入口", ""),
            "最后更新时间": updated_at,
        },
        {
            "动作": "选题层：04 分析与选题",
            "卡片类型": "系统导航",
            "优先级": "高",
            "工作区": "选题层",
            "状态": "固定导航",
            "数量/摘要": "今日候选池和选题决策区。",
            "说明": "这里决定做、不做、暂存，或确认生成脚本包。",
            "下一步": "看 今日候选池 视图，决定 1 条。",
            "入口表": "04 分析与选题",
            "入口视图": "今日候选池",
            "入口说明": links.get("04 分析与选题", ""),
            "最后更新时间": updated_at,
        },
        {
            "动作": "制作层：06 完整脚本与制作包",
            "卡片类型": "系统导航",
            "优先级": "高",
            "工作区": "制作层",
            "状态": "固定导航",
            "数量/摘要": "保存已生成完整口播稿、制作执行包、本地文档路径、素材提醒和 QA。",
            "说明": "这里承接被选中的选题；完整内容看本地 Markdown，飞书只保留摘要、路径、素材提醒和 QA。",
            "下一步": "打开脚本包主视图，按飞书文档或本地文档继续拍摄准备。",
            "入口表": "06 完整脚本与制作包",
            "入口视图": "脚本包主视图",
            "入口说明": links.get("06 完整脚本与制作包", ""),
            "最后更新时间": updated_at,
        },
        {
            "动作": "复盘层：07 资产与复盘",
            "卡片类型": "系统导航",
            "优先级": "中高",
            "工作区": "复盘层",
            "状态": "固定导航",
            "数量/摘要": "发布后 24小时、72小时、7天数据，以及复刻、改角度再发、淘汰或资产化判断。",
            "说明": "发布后看数据和资产化机会，不靠感觉做内容。",
            "下一步": "发布后按提醒复盘，不要靠感觉做内容。",
            "入口表": "07 资产与复盘",
            "入口视图": "资产复盘后台",
            "入口说明": links.get("07 资产与复盘", ""),
            "最后更新时间": updated_at,
        },
        {
            "动作": "规则层：99 规则与字典",
            "卡片类型": "规则说明",
            "优先级": "中",
            "工作区": "规则层",
            "状态": "固定导航",
            "数量/摘要": "系统说明书：字段、状态、评分、AI边界、表逻辑。",
            "说明": "这里是系统说明书。看不懂字段、状态、评分、AI边界时再打开。",
            "下一步": "规则不合理时先改这里和 config/system_rules.yaml。",
            "入口表": "99 规则与字典",
            "入口视图": "规则与字典",
            "入口说明": links.get("99 规则与字典", ""),
            "最后更新时间": updated_at,
        },
        {
            "动作": "今日候选池",
            "卡片类型": "今日工作",
            "优先级": "高",
            "工作区": "分析与选题",
            "状态": "今日工作台",
            "数量/摘要": f"今日候选池 {stats['today_10_count']} 条，优先只推进 1 条。",
            "说明": f"最建议优先看：{stats['top_today_topic']}",
            "下一步": "进入 04 今日候选池，选 1 条生成脚本包。",
            "入口表": "04 分析与选题",
            "入口视图": "今日候选池",
            "入口说明": links["04 分析与选题"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "待生成脚本包",
            "卡片类型": "今日工作",
            "优先级": "高",
            "工作区": "分析与选题",
            "状态": "今日工作台",
            "数量/摘要": f"近 {SCRIPT_PACKAGE_QUEUE_DAYS} 天 {stats['topic_to_script_count']} 条选题已确认生成脚本包，等待生成完整口播稿与制作执行包。",
            "说明": "daily_pipeline 只写入 04；本机轻量 watcher 会自动生成 06，扫描窗口和卡片有效期一致，空队列不调用 Codex。",
            "下一步": f"等待本机轻量 watcher；急用时运行 codex_script_package_runner.py --write-feishu --limit 2 --max-age-days {SCRIPT_PACKAGE_QUEUE_DAYS}。",
            "入口表": "04 分析与选题",
            "入口视图": "今日候选池",
            "入口说明": links["04 分析与选题"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "已生成脚本包",
            "卡片类型": "进度统计",
            "优先级": "中高",
            "工作区": "脚本与制作",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['script_package_count']} 个完整脚本与制作包；{stats['script_package_ready_count']} 个标记可拍。",
            "说明": "完整脚本统一看 06 里的本地文档路径；飞书只放轻量摘要和提醒。",
            "下一步": "进入 06 脚本包主视图，打开飞书文档或本地文档继续制作。",
            "入口表": "06 完整脚本与制作包",
            "入口视图": "脚本包主视图",
            "入口说明": links["06 完整脚本与制作包"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "待处理与异常脚本包",
            "卡片类型": "预警提醒",
            "优先级": "中高",
            "工作区": "脚本与制作",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['script_package_revise_count']} 个脚本包需要补关键判断、字段或证据。",
            "说明": "只有 package 本身不成立才算待修订；普通拍摄提醒不降级。",
            "下一步": "进入 06 待处理与异常，处理同步失败、阻塞或待修订记录。",
            "入口表": "06 完整脚本与制作包",
            "入口视图": "待处理与异常",
            "入口说明": links["06 完整脚本与制作包"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "可复刻内容",
            "卡片类型": "进度统计",
            "优先级": "中高",
            "工作区": "资产与复盘",
            "状态": "今日工作台",
            "数量/摘要": f"{stats['asset_count']} 个高优先级资产/复刻候选。",
            "说明": "把表现好的内容变成下周选题或资产。",
            "下一步": "进入 07，选择1个可复刻内容沉淀。",
            "入口表": "07 资产与复盘",
            "入口视图": "资产复盘后台",
            "入口说明": links["07 资产与复盘"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "来源异常/采集失败",
            "卡片类型": "预警提醒",
            "优先级": "中",
            "工作区": "来源与采样",
            "状态": "今日工作台",
            "数量/摘要": "；".join(stats["source_errors"]),
            "说明": "某个来源失败不会中断全流程。",
            "下一步": "如有异常，去 02 URL投喂入口 手动补链接或OCR文本。",
            "入口表": "02 URL投喂入口",
            "入口视图": "URL投喂入口",
            "入口说明": links["02 URL投喂入口"],
            "最后更新时间": updated_at,
        },
        {
            "动作": "URL投喂入口",
            "卡片类型": "临时入口",
            "优先级": "中",
            "工作区": "来源与采样",
            "状态": "临时入口",
            "数量/摘要": "临时粘贴公众号、抖音单条、RSS或网页链接。",
            "说明": "解析后会回写处理状态，记录可手动清理。",
            "下一步": "进入 02 粘贴链接，再运行 daily_pipeline.py --resolve-url-intake。",
            "入口表": "02 URL投喂入口",
            "入口视图": "URL投喂入口",
            "入口说明": links.get("02 URL投喂入口", ""),
            "最后更新时间": updated_at,
        },
        {
            "动作": "99规则与字典入口",
            "卡片类型": "临时入口",
            "优先级": "低",
            "工作区": "规则说明",
            "状态": "固定入口",
            "数量/摘要": "看不懂字段、状态、评分或AI边界时再打开。",
            "说明": "这是系统说明书，不是业务数据表。",
            "下一步": "进入 99，按规则类型查看。",
            "入口表": "99 规则与字典",
            "入口视图": "规则与字典",
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

    stale = [record for record in records if record.get("fields", {}).get("动作") not in expected]
    for record in stale:
        if record.get("fields", {}).get("动作") not in DEPRECATED_CONSOLE_ACTIONS:
            continue
        feishu.request_json(
            "DELETE",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record['record_id']}",
            token=token,
        )
        deleted += 1
        time.sleep(0.1)
    preserved = len(stale) - deleted
    return {"updated": updated, "created": created, "deleted_stale": deleted, "preserved_stale": preserved}


def generate_report(stats: dict[str, Any], updated_at: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"ai_account_radar_daily_{today_slug()}.md"
    top_topics = stats["top_topics"]
    package_items = stats["script_package_revise_items"][:5]
    assets = stats["asset_items"][:5]

    def topic_line(record: dict[str, Any]) -> str:
        fields = record.get("fields", {})
        return (
            f"- {fields.get('选题标题', '未命名选题')} | "
            f"{fields.get('对应方向', '待判断方向')} | "
            f"{fields.get('推荐理由', '待补推荐理由')}"
        )

    def package_line(record: dict[str, Any]) -> str:
        fields = record.get("fields", {})
        return f"- {fields.get('关联选题', '未命名脚本包')}：{fields.get('QA结果', fields.get('脚本状态', '待确认'))}"

    def asset_line(record: dict[str, Any]) -> str:
        fields = record.get("fields", {})
        return f"- {fields.get('名称/模块', '未命名资产')}：{fields.get('核心内容', '待补核心内容')}"

    actions = [
        "先在 04 分析与选题 中处理高分待判断选题，至少确认 1 条生成脚本包。",
        f"等待本机轻量 watcher 生成 06；急用时运行 codex_script_package_runner.py --write-feishu --limit 2 --max-age-days {SCRIPT_PACKAGE_QUEUE_DAYS}。",
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
        f"- 当前 `03 内容收件箱` 有 {stats['pending_inbox_count']} 条待分析内容。",
        f"- 主要来源：{stats['source_summary']}",
        "",
        "## 2. 今日最值得看的选题",
        *(topic_line(record) for record in top_topics[:5]),
        "",
        "## 3. 待处理与异常脚本包",
        *(package_line(record) for record in package_items),
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
        "边界提醒：本日报只做摘要、分析和行动建议；不在日报里生成完整成稿、不自动发布、不伪造数据、不绕过平台限制，最终观点由你判断。",
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")
    return path


def load_today_10() -> dict[str, Any]:
    path = LATEST_WRITE_DIR / "today_10_topics.csv"
    legacy_log = OUT / "content_sampler_log.json"
    if not path.exists() and legacy_sampler_log_is_official(legacy_log):
        path = OUT / "today_10_topics.csv"
    report = LATEST_WRITE_DIR / f"today_10_topics_{today_slug()}.md"
    if not report.exists():
        report = REPORT_DIR / f"today_10_topics_{today_slug()}.md"
    if not path.exists():
        return {"count": 0, "top": "尚未生成", "report": str(report)}
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    top_row = next((row for row in rows if row.get("今日建议级别") == "今日最值得做"), rows[0] if rows else {})
    top = (
        top_row.get("选题命题")
        or top_row.get("选题标题")
        or top_row.get("我的选题标题")
        or top_row.get("可发布标题")
        or top_row.get("来源内容")
        or "尚未生成"
    ) if rows else "尚未生成"
    top = " ".join(str(top).split())
    if len(top) > 70:
        top = top[:70].rstrip() + "..."
    return {"count": len(rows), "top": top, "report": str(report)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-feishu", action="store_true", help="Only generate local report; do not update Feishu.")
    args = parser.parse_args()

    app_token = require_env()
    token = feishu.tenant_token()
    tables_by_name = {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}
    tables = {table_name(key): resolve_table_id(tables_by_name, key) for key in TABLE_KEYS}
    missing = [table_name(key) for key in TABLE_KEYS if not tables.get(table_name(key))]
    if missing:
        raise SystemExit(f"Missing required tables: {missing}")

    inbox_records = all_records(token, app_token, tables["03 内容收件箱"])
    topic_records = all_records(token, app_token, tables["04 分析与选题"])
    script_package_records = all_records(token, app_token, tables["06 完整脚本与制作包"])
    asset_records = all_records(token, app_token, tables["07 资产与复盘"])

    pending_inbox = [r for r in inbox_records if r.get("fields", {}).get("处理状态") == "待分析"]
    high_topics = [r for r in topic_records if r.get("fields", {}).get("状态") == "待判断" and is_ab_or_high(r.get("fields", {}))]
    topic_to_script = [r for r in topic_records if ready_for_script_recent(r)]
    script_package_ready = [r for r in script_package_records if str(r.get("fields", {}).get("是否可拍", "")).startswith("是")]
    script_package_revise = [
        r for r in script_package_records
        if "待修订" in str(r.get("fields", {}).get("脚本状态", ""))
        or "阻塞" in str(r.get("fields", {}).get("脚本状态", ""))
    ]
    assets = asset_candidates(asset_records)
    today10 = load_today_10()

    stats = {
        "today_10_count": today10["count"],
        "top_today_topic": today10["top"],
        "today_10_report": today10["report"],
        "pending_inbox_count": len(pending_inbox),
        "source_summary": summarize_sources(pending_inbox),
        "high_topic_count": len(high_topics),
        "topic_to_script_count": len(topic_to_script),
        "script_package_count": len(script_package_records),
        "script_package_ready_count": len(script_package_ready),
        "script_package_revise_count": len(script_package_revise),
        "asset_count": len(assets),
        "source_errors": load_run_errors(),
        "top_topics": top_records(high_topics, 5),
        "script_package_revise_items": script_package_revise,
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
