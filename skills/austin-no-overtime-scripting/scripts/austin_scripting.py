#!/usr/bin/env python3
"""Deterministic v0.2 renderer for Austin no-overtime scripting packages."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SKILL_VERSION = "austin-script-skill-v0.2"
SKILL_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FIELDS = [
    "topic_title",
    "core_thesis",
    "pain_point",
    "target_audience",
    "old_workflow",
    "ai_intervention",
    "takeaway_asset",
]

OUTPUT_FILES = ["script_outline_brief.md"]

TEMPLATE_TYPES = [
    "Skill公开型",
    "热点业务转译型",
    "认知定调型",
    "真实工作流改造型",
    "Agent实战型",
    "项目复盘型",
]


@dataclass
class ValidationResult:
    status: str
    missing_required: list[str]
    evidence_gaps: list[str]
    fact_check_points: list[str]
    notes: list[str]


EMPTYISH_VALUES = {"", "无", "暂无", "无额外", "none", "null", "nan", "n/a", "NA"}
REQUIRED_PLACEHOLDER_PREFIXES = ("待补", "待确认", "待填写", "待定")


def normalized_text(value: Any) -> str:
    return str(value or "").strip().strip("。.!！?？；;，,、 ")


def is_emptyish_value(value: Any) -> bool:
    text = normalized_text(value)
    return not text or text.lower() in EMPTYISH_VALUES or text in EMPTYISH_VALUES


def is_required_placeholder(value: Any) -> bool:
    text = normalized_text(value)
    return is_emptyish_value(text) or "待补" in text or any(text.startswith(prefix) for prefix in REQUIRED_PLACEHOLDER_PREFIXES)


def first_non_empty(fields: dict[str, Any], names: list[str], default: str = "", skip_placeholders: bool = True) -> str:
    for name in names:
        value = fields.get(name)
        if isinstance(value, list):
            joined = "、".join(normalized_text(item) for item in value if not is_emptyish_value(item))
            if joined and not (skip_placeholders and is_required_placeholder(joined)):
                return joined
        elif value is not None and not is_emptyish_value(value):
            text = normalized_text(value)
            if not (skip_placeholders and is_required_placeholder(text)):
                return text
    return default


def split_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [normalized_text(item) for item in value if not is_emptyish_value(item)]
    text = normalized_text(value)
    if is_emptyish_value(text):
        return []
    parts = re.split(r"[；;\n、]+|(?<=。)", text)
    return [normalized_text(part) for part in parts if not is_emptyish_value(part)]


def slugify(text: str, fallback: str = "topic") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", text.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:48] or fallback


def parse_duration(value: Any) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 4
    return number if number in {3, 4, 5} else 4


def load_private_runtime() -> dict[str, Any]:
    path = SKILL_ROOT / "references" / "private" / "private_runtime.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def topic_search_text(topic: dict[str, Any]) -> str:
    keys = [
        "topic_title",
        "content_pillar",
        "core_thesis",
        "target_audience",
        "pain_point",
        "old_workflow",
        "ai_intervention",
        "unique_judgment",
        "takeaway_asset",
    ]
    parts = [str(topic.get(key, "")) for key in keys]
    source_fields = topic.get("source_fields")
    if isinstance(source_fields, dict):
        parts.extend(str(value) for value in source_fields.values())
    return " ".join(parts).lower()


def matched_private_cases(topic: dict[str, Any], runtime: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    text = topic_search_text(topic)
    matches: list[tuple[int, dict[str, Any]]] = []
    for case in runtime.get("case_anchors", []):
        keywords = [str(keyword).lower() for keyword in case.get("keywords", [])]
        score = sum(2 for keyword in keywords if keyword and keyword in text)
        for pillar in case.get("usable_for", []):
            if str(pillar).lower() in text:
                score += 1
        if score:
            matches.append((score, case))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [case for _, case in matches[:limit]]


def private_case_names(private_cases: list[dict[str, Any]]) -> str:
    return md_list([str(case.get("name", "")) for case in private_cases], "未自动匹配私有案例锚点，需人工判断可借用场景。")


def private_style_summary(runtime: dict[str, Any]) -> str:
    rules = runtime.get("style_rules", [])
    return md_list([str(rule) for rule in rules[:4]], "真实、直接、业务判断强、不像课程老师")


def private_boundaries(private_cases: list[dict[str, Any]]) -> list[str]:
    boundaries: list[str] = []
    for case in private_cases:
        for item in case.get("boundaries", []):
            text = str(item).strip()
            if text and text not in boundaries:
                boundaries.append(text)
    return boundaries


def normalize_topic(fields: dict[str, Any], record_id: str = "") -> dict[str, Any]:
    topic_title = first_non_empty(fields, ["topic_title", "选题命题", "我的选题标题", "选题标题", "可发布标题"])
    demo_materials = split_items(first_non_empty(fields, ["demo_materials", "可展示证据", "可展示结果", "演示素材"]))
    missing_evidence = split_items(first_non_empty(fields, ["missing_evidence", "需要补的证据", "证据缺口"]))
    fact_check_points = split_items(first_non_empty(fields, ["fact_check_points", "不能声称的部分", "不能照搬/风险提示", "风险点"]))
    publish_platforms = split_items(first_non_empty(fields, ["publish_platforms", "适合平台", "平台建议", "发布平台"]))
    topic_id = first_non_empty(fields, ["topic_id", "选题ID", "内容指纹"], record_id)
    if not topic_id:
        topic_id = f"T{datetime.now().strftime('%Y%m%d%H%M%S')}"

    return {
        "topic_id": topic_id,
        "status": first_non_empty(fields, ["status", "状态", "推荐动作", "脚本状态"], skip_placeholders=False),
        "topic_title": topic_title,
        "content_pillar": first_non_empty(fields, ["content_pillar", "对应方向", "对应栏目", "业务场景"], "真实工作流改造"),
        "core_thesis": first_non_empty(fields, ["core_thesis", "一句话Brief", "重点体现", "选题命题", "我的切入", "选题标题", "可发布标题"]),
        "target_audience": first_non_empty(fields, ["target_audience", "目标观众", "影响对象", "业务场景"], "内容团队、品牌方、创作者、创业者"),
        "pain_point": first_non_empty(fields, ["pain_point", "我的工作流痛点", "旧流程痛点", "我的场景拆解", "真实用户问题"]),
        "old_workflow": first_non_empty(fields, ["old_workflow", "旧流程痛点", "我的场景拆解"]),
        "ai_intervention": first_non_empty(fields, ["ai_intervention", "AI介入点", "我要做的实验", "验证方式"]),
        "demo_materials": demo_materials,
        "missing_evidence": missing_evidence,
        "unique_judgment": first_non_empty(fields, ["unique_judgment", "我的思考点", "主编判断", "选题判断", "我的切入"]),
        "takeaway_asset": first_non_empty(fields, ["takeaway_asset", "可沉淀资产", "资料包承接方式", "重点体现"]),
        "preferred_duration_min": parse_duration(first_non_empty(fields, ["preferred_duration_min", "目标时长"], "4")),
        "publish_platforms": publish_platforms,
        "fact_check_points": fact_check_points,
        "source_fields": fields,
    }


def classify_template(topic: dict[str, Any]) -> tuple[str, str]:
    text = " ".join(
        str(topic.get(key, ""))
        for key in ["topic_title", "content_pillar", "core_thesis", "pain_point", "ai_intervention"]
    ).lower()
    if any(term.lower() in text for term in ["复盘", "揭秘", "交付", "三天", "从idea到成片"]):
        return "项目复盘型", "标题或场景指向项目过程、交付难点和方法沉淀。"
    if any(term.lower() in text for term in ["agent", "codex", "claude", "知识库", "监控", "自动执行"]):
        return "Agent实战型", "内容涉及Agent任务边界、执行过程或验收。"
    if any(term in text for term in ["为什么", "2026", "一定要", "文科生", "业务人"]):
        return "认知定调型", "内容更像能力模型或认知立场，需要结论先行。"
    if any(term.lower() in text for term in ["更新", "上新", "发布", "模型", "插件"]):
        return "热点业务转译型", "内容从外部热点进入，需要转译成业务场景和边界。"
    if any(term.lower() in text for term in ["skill", "自动化", "公开", "模板", "一键生成", "工作流"]):
        return "Skill公开型", "内容重点是把高频流程沉淀成可复用资产。"
    return "真实工作流改造型", "默认按真实业务场景、旧流程、新流程和资产沉淀来拍。"


def validate_topic(topic: dict[str, Any]) -> ValidationResult:
    missing_required = [field for field in REQUIRED_FIELDS if not str(topic.get(field, "")).strip()]
    evidence_gaps = list(topic.get("missing_evidence", []))
    notes: list[str] = []
    if not topic.get("demo_materials"):
        evidence_gaps.append("缺少可展示证据：需要截图、录屏、结果对比或实际输出。")
    if not topic.get("unique_judgment"):
        notes.append("缺少独有判断：需要补奥斯汀的主观判断、取舍或人工修正点。")
    fact_check_points = list(topic.get("fact_check_points", []))
    fact_parts = [str(topic.get(key, "")) for key in ["topic_title", "core_thesis", "ai_intervention"]]
    source_fields = topic.get("source_fields")
    if isinstance(source_fields, dict):
        fact_parts.extend(str(value) for value in source_fields.values())
    fact_text = " ".join(fact_parts)

    def add_fact_check(text: str) -> None:
        if text not in fact_check_points:
            fact_check_points.append(text)

    if any(term in fact_text for term in ["OpenAI", "Codex", "Claude", "飞书", "价格", "规则", "更新", "发布"]):
        add_fact_check("涉及产品能力、平台规则或更新信息，发布前需事实核验。")
    if any(term in fact_text for term in ["政策", "法规", "国标", "强制性", "公示", "实施", "监管", "标准"]):
        add_fact_check("涉及政策、法规、国标、公示或实施时间，发布前需核验权威原文和具体日期。")
    if any(term in fact_text for term in ["L3", "L4", "自动驾驶", "智能驾驶", "辅助驾驶", "功能安全"]):
        add_fact_check("涉及智能驾驶等级、功能安全或汽车功能边界，发布前需核验官方定义，不能扩大声称。")

    if missing_required:
        status = "blocked"
    elif evidence_gaps or fact_check_points or notes:
        status = "revise"
    else:
        status = "pass"
    return ValidationResult(status, missing_required, evidence_gaps, fact_check_points, notes)


def director_summary(topic: dict[str, Any], template: str, private_cases: list[dict[str, Any]] | None = None) -> str:
    summary = (
        f"这条视频按「{template}」处理：先把「{topic['topic_title']}」收成几句话的核心观点，"
        f"再按时间线展开真实现场、观点定调、实验主线、证据/反例和资产收束，"
        f"最后把关键证据与边界交给06生成完整脚本包。"
    )
    if private_cases:
        summary += f" 优先借用私有案例锚点：{private_case_names(private_cases)}。"
    return summary


def md_list(items: list[str], fallback: str = "待补") -> str:
    clean = [item for item in items if item]
    return "；".join(clean) if clean else fallback


def inline_items(items: list[str], fallback: str = "待补", limit: int = 3, item_limit: int = 42) -> str:
    clean = [clip_text(item, item_limit, "") for item in items if str(item).strip()]
    clean = [item for item in clean if item]
    return " / ".join(clean[:limit]) if clean else fallback


def md_bullets(items: list[str], fallback: str = "待补") -> str:
    clean = [item for item in items if item]
    if not clean:
        return f"- {fallback}"
    return "\n".join(f"- {item}" for item in clean)


def md_numbered(items: list[str], fallback: str = "待补") -> str:
    clean = [item for item in items if item]
    if not clean:
        return f"1. {fallback}"
    return "\n".join(f"{index}. {item}" for index, item in enumerate(clean, 1))


def script_status_from_validation(validation: ValidationResult) -> str:
    if validation.missing_required:
        return "缺字段"
    if production_todo_items(validation):
        return "待补素材"
    if validation.notes:
        return "待补判断"
    if validation.fact_check_points:
        return "待核验确认"
    return "待确认大纲"


def can_enter_06_reason(validation: ValidationResult) -> str:
    if validation.missing_required:
        return "否：必填字段不完整。"
    if production_todo_items(validation):
        return "否：先补齐05大纲里的P0素材，再确认是否生成06完整脚本包。"
    if validation.fact_check_points:
        return "否：先确认事实边界，再生成06完整脚本包。"
    return "待确认：05只做大纲确认，人工确认后再生成06完整脚本包。"


def decision_summary(topic: dict[str, Any], validation: ValidationResult, status: str) -> str:
    if validation.missing_required:
        return f"{status}：这条还不能进入脚本大纲确认，先补齐必填字段：{md_list(validation.missing_required)}。"
    if production_todo_items(validation):
        return f"{status}：大纲方向成立，但生成06完整脚本包前，先把P0素材补成可展示画面。"
    if validation.notes:
        return f"{status}：大纲方向基本成立，但还要补足奥斯汀自己的判断、取舍或人工修正点。"
    if validation.fact_check_points:
        return f"{status}：大纲可以确认，但生成06前必须确认事实边界，避免把推演或公开信息说过头。"
    return f"{status}：这条可以由你确认大纲；确认后再进入06生成完整脚本和执行方案。"


def split_production_todos(text: str) -> list[str]:
    candidate = text
    for marker in ["待制作可拍摄素材：", "待制作素材：", "待制作："]:
        if marker in candidate:
            candidate = candidate.split(marker, 1)[1]
            break
    candidate = candidate.replace("。", "；").replace("，", "、")
    parts = re.split(r"[；;、]+", candidate)
    return [part.strip() for part in parts if part.strip()]


def is_public_evidence_item(text: str) -> bool:
    public_terms = ["公开资料已补齐", "公示", "附件", "官网", "官方", "原文", "公开资料"]
    production_terms = ["待制作", "字段表", "样张", "失败样例", "错误", "补拍", "截图", "录屏", "人工修正"]
    return any(term in text for term in public_terms) and not any(term in text for term in production_terms)


def unique_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = normalized_text(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def public_evidence_items(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    items = [item for item in validation.evidence_gaps if is_public_evidence_item(item)]
    for item in topic.get("demo_materials", []):
        if is_public_evidence_item(item):
            items.append(item)
    return unique_items(items)


def production_todo_items(validation: ValidationResult) -> list[str]:
    production_mode = any("公开资料已补齐" in item or "待制作" in item for item in validation.evidence_gaps)
    todos: list[str] = []
    for item in validation.evidence_gaps:
        if is_public_evidence_item(item):
            continue
        split = split_production_todos(item)
        if split and ("待制作" in item or production_mode):
            todos.extend(split)
        else:
            todos.append(item)
    return unique_items(todos)


def next_action_items(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    if validation.missing_required:
        return [f"补齐字段：{field}" for field in validation.missing_required[:4]]

    actions = [f"补成可展示画面：{item}" for item in production_todo_items(validation)]
    if actions:
        return actions[:3]

    if validation.fact_check_points:
        return [f"核验：{item}" for item in validation.fact_check_points[:3]]
    if validation.notes:
        return [f"补人工判断：{item}" for item in validation.notes[:3]]
    return ["人工确认05大纲方向是否成立；确认后进入06生成完整脚本包。"]


def done_criteria(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    criteria = [
        "大纲读完后，能马上判断这条视频的方向是否值得继续。",
        "核心观点不是一句口号，而是有论点、论据、钩子和资产收束。",
        "视频大纲按时间线展开，不是散点重点清单。",
    ]
    if production_todo_items(validation):
        criteria.append("P0素材、事实边界和私有表达边界已进入给06的生成输入。")
    else:
        criteria.append("人工确认大纲通过后，才进入06生成提词器、录屏清单、后期交接和发布包。")
    return criteria


def short_text(value: Any, fallback: str = "待补") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def trim_end_punctuation(value: Any, fallback: str = "待补") -> str:
    return short_text(value, fallback).rstrip("。.!！?？；;，, ")


def clip_text(value: Any, limit: int = 56, fallback: str = "待补") -> str:
    text = trim_end_punctuation(value, fallback)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip("，,；;、 ") + "…"


def topic_blob(topic: dict[str, Any]) -> str:
    keys = ["topic_title", "content_pillar", "core_thesis", "pain_point", "old_workflow", "ai_intervention", "takeaway_asset"]
    return " ".join(str(topic.get(key, "")) for key in keys).lower()


def workflow_object(topic: dict[str, Any]) -> str:
    text = topic_blob(topic)
    if any(term in text for term in ["汽车", "智能驾驶", "辅助驾驶", "l3", "l4", "国标"]):
        return "汽车AI内容"
    if any(term in text for term in ["候选池", "excel", "批量补字段", "飞书候选"]):
        return "候选池"
    if any(term in text for term in ["选题", "brief", "主编", "门控"]):
        return "选题台"
    if any(term in text for term in ["ppt", "pptx", "导出", "样式", "视觉", "baoyu", "设计"]):
        return "视觉交付"
    if any(term in text for term in ["封面"]):
        return "封面流程"
    if any(term in text for term in ["agent", "claude", "codex", "自动执行", "任务拆解"]):
        return "Agent任务"
    return "AI流程"


def display_asset(topic: dict[str, Any]) -> str:
    text = topic_blob(topic)
    title = normalized_text(topic.get("topic_title"))
    raw_asset = clip_text(topic.get("takeaway_asset"), 34, "可复用资产")
    if "验收表" in title or "项目验收表" in text:
        if "claude" in text:
            return "AI项目验收表"
        return "项目验收表"
    if "风险复核" in title or "风险字段" in text:
        return "汽车内容风险复核线"
    if "导出" in text and ("ppt" in text or "pptx" in text):
        return "PPTX导出验收表"
    if "候选池" in text and "补字段" in text:
        return "候选池字段验收表"
    if "选题台" in text and ("门控" in text or "字段" in text):
        return "选题台门控字段表"
    if "非技术Agent任务拆解模板" in raw_asset:
        if any(term in text for term in ["openrouter", "portkey", "llm网关", "多模型", "模型路由"]):
            return "多模型调用核验表"
        if any(term in text for term in ["输入输出", "失败回滚", "异常", "验收", "任务样本"]):
            return "Agent任务验收记录"
        return "待确认的Agent任务资产"
    return raw_asset


def hook_problem(topic: dict[str, Any]) -> str:
    obj = workflow_object(topic)
    mapping = {
        "汽车AI内容": "汽车内容最怕的不是慢，是一句卖点把边界说过头",
        "候选池": "表格AI最怕的不是填得少，是填满以后没人知道对不对",
        "选题台": "选题台最怕的不是没灵感，是每条看起来都能做",
        "视觉交付": "AI视觉最怕的不是不好看，是导出那一刻全变形",
        "封面流程": "封面自动化最怕的不是不出图，是每张都像另一个账号",
        "Agent任务": "AI任务最怕的不是没跑完，是跑完以后没人知道错在哪",
        "AI流程": "AI流程最怕的不是慢，是快到没人知道哪里该验收",
    }
    return mapping.get(obj, mapping["AI流程"])


def key_judgment_extras(topic: dict[str, Any]) -> list[str]:
    obj = workflow_object(topic)
    asset = display_asset(topic)
    mapping = {
        "汽车AI内容": [
            "汽车内容里的AI提效，必须先过功能边界和证据线。",
            "热点只能给入口，能不能上线要看风险复核。",
        ],
        "候选池": [
            "候选池自动化的价值，不是填满表格，是把判断成本降下来。",
            "AI补字段能不能用，关键看人改错的地方能不能回写成规则。",
        ],
        "选题台": [
            "选题台不是灵感池，是把能做和不该做分开的判断系统。",
            "AI可以帮我初筛，但升级或放弃必须留下理由。",
        ],
        "视觉交付": [
            "视觉AI能不能进交付，不看截图好不好看，看导出后还剩多少人工修正。",
            "最后一公里不稳定，前面的生成效率都不算数。",
        ],
        "封面流程": [
            "封面自动化不是让AI随机出图，是把账号视觉和标题规则锁住。",
            "如果每张封面都要重新解释风格，自动化就没有成立。",
        ],
        "Agent任务": [
            f"Agent任务能不能进流程，不看跑没跑完，看有没有「{asset}」能验收。",
            "真正的AI改造，是把人的判断、异常和回滚留在现场。",
        ],
        "AI流程": [
            f"{obj}能不能进流程，不看生成多快，看有没有「{asset}」能复用。",
            "AI改造的价值，是把判断留在流程里，而不是把步骤藏进黑箱。",
        ],
    }
    return mapping.get(obj, mapping["AI流程"])


def golden_line_pool(topic: dict[str, Any]) -> list[str]:
    obj = workflow_object(topic)
    asset = display_asset(topic)
    mapping = {
        "汽车AI内容": [
            "汽车内容先守边界，再谈效率。",
            "卖点可以被AI放大，责任不能。",
            "能过风险线的内容，才配上线。",
        ],
        "候选池": [
            "填满表格不叫自动化，改少判断才叫自动化。",
            "能回写规则的错误，才值得让AI继续犯。",
            "候选池不是越满越好，是越清楚越好。",
        ],
        "选题台": [
            "选题不是灵感池，是判断系统。",
            "能说清为什么不做，才算真的会选题。",
            "好的选题台，先挡住泛资讯。",
        ],
        "视觉交付": [
            "好看的截图不等于可交付文件。",
            "导不出来的设计，不算交付。",
            "AI视觉真正的终点，是验收通过。",
        ],
        "封面流程": [
            "封面不是出图，是账号识别。",
            "随机好看，不如稳定像我。",
            "能复用的风格，才是封面系统。",
        ],
        "Agent任务": [
            f"没有「{asset}」的Agent，只是跑得更快的黑箱。",
            "能追责的AI，才配进流程。",
            "自动化不是省人，是把人的判断固定下来。",
        ],
        "AI流程": [
            f"没有「{asset}」的AI，只是一次演示。",
            "能复用的才叫流程，不能复用的只是表演。",
            "AI越快，验收越要慢半拍。",
        ],
    }
    return mapping.get(obj, mapping["AI流程"])


def readable_evidence_item(value: str) -> str:
    text = trim_end_punctuation(value, "")
    replacements = [
        ("需要实际跑10条候选，补回填截图", "10条候选的回填截图"),
        ("需要跑一次固定样张，补导出对比截图", "固定样张的导出对比截图"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    for prefix in ["需要先补", "需要补", "先补", "待制作", "待补", "补", "需要"]:
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip("：:，,、 ")
            break
    return text or value


def evidence_phrase(topic: dict[str, Any], validation: ValidationResult) -> str:
    evidence = [
        readable_evidence_item(item)
        for item in key_evidence_items(topic, validation)
        if not str(item).strip().startswith("不能")
    ]
    if not evidence:
        return "一组能看见输入、输出和人工验收的画面"
    if len(evidence) == 1:
        return evidence[0]
    return "、".join(evidence[:2])


def headline_result(topic: dict[str, Any], validation: ValidationResult) -> str:
    asset = display_asset(topic)
    evidence_text = evidence_phrase(topic, validation)
    return f"最后要让画面落到「{asset}」，中间至少看到{evidence_text}"


def old_new_contrast(topic: dict[str, Any]) -> str:
    pain = trim_end_punctuation(topic.get("old_workflow") or topic.get("pain_point"), "旧流程靠经验和人工盯结果")
    ai_action = trim_end_punctuation(topic.get("ai_intervention"), "AI介入一个可验证的小环节")
    return f"旧流程是「{pain}」，新动作是「{ai_action}」"


def opening_hook_options(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    obj = workflow_object(topic)
    asset = display_asset(topic)
    evidence_text = clip_text(evidence_phrase(topic, validation), 38, "输入、输出和人工验收画面")
    return unique_items([
        f"{hook_problem(topic)}。",
        f"我今天不看{obj}做得多快，只看它能不能留下「{asset}」。",
        f"拿不出{evidence_text}，这条就不是实战。",
    ])


def key_judgment_lines(topic: dict[str, Any]) -> list[str]:
    judgment = clip_text(topic.get("unique_judgment"), 54, "AI不能只看生成结果，必须回到业务验收")
    return unique_items([judgment] + key_judgment_extras(topic))


def golden_lines(topic: dict[str, Any]) -> list[str]:
    return unique_items(golden_line_pool(topic))


def shootability_snapshot(topic: dict[str, Any], validation: ValidationResult) -> str:
    hooks = opening_hook_options(topic, validation)
    judgments = key_judgment_lines(topic)
    lines = [
        "**开头钩子候选**",
        md_numbered(hooks),
        "",
        "**中段关键判断**",
        md_bullets(judgments),
        "",
        "**可用金句**",
        md_bullets(golden_lines(topic)),
    ]
    return "\n".join(lines)


def core_viewpoint(topic: dict[str, Any], validation: ValidationResult) -> str:
    title = clip_text(topic.get("topic_title"), 42, "这条选题")
    core = clip_text(topic.get("core_thesis"), 72, title)
    pain = clip_text(topic.get("old_workflow") or topic.get("pain_point"), 72, "旧流程里有一个真实低效或高风险环节")
    ai_action = clip_text(topic.get("ai_intervention"), 72, "用AI介入一个可验证的小环节")
    judgment = clip_text(topic.get("unique_judgment"), 72, "AI只能辅助判断，最终取舍仍然要回到人的业务标准")
    asset = display_asset(topic)
    evidence_text = clip_text(evidence_phrase(topic, validation), 60, "输入、输出和人工验收画面")
    hook = opening_hook_options(topic, validation)[0]
    return (
        f"我的判断：{judgment}。\n\n"
        f"论据：旧流程卡在「{pain}」，过程和异常很容易丢。\n\n"
        f"这条从「{title}」切入，只测「{core}」。录屏只看「{ai_action}」。\n\n"
        f"钩子：{hook}。开头直接给{evidence_text}。能拿出证据，就进入06；拿不出，先补素材。最后收成「{asset}」。"
    )


def outline_segments(topic: dict[str, Any], validation: ValidationResult | None = None) -> list[str]:
    pain = clip_text(topic.get("old_workflow") or topic.get("pain_point"), 58, "旧流程里的真实痛点")
    judgment = clip_text(topic.get("unique_judgment"), 58, "我的判断和边界")
    ai_action = clip_text(topic.get("ai_intervention"), 58, "AI介入动作")
    asset = display_asset(topic)
    evidence_text = clip_text(evidence_phrase(topic, validation), 48, "关键截图、录屏或前后对比") if validation else "关键截图、录屏或前后对比"
    hook = opening_hook_options(topic, validation)[0] if validation else f"今天先看能不能跑出「{asset}」。"
    return [
        f"00:00-00:08｜开场给证据：展示「{asset}」或失败截图。说：{hook}",
        f"00:08-00:30｜旧流程：打开旧任务/旧表格。指出：{pain}。",
        f"00:30-00:50｜真人判断：{judgment}。",
        f"00:50-02:10｜录屏实验：{ai_action}。按输入 -> 执行 -> 验收拍。",
        f"02:10-03:00｜证据段：放{evidence_text}。失败样例也放。",
        f"03:00-03:30｜收尾：回到「{asset}」。下一次先按它跑。",
    ]


def key_evidence_items(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    return unique_items(public_evidence_items(topic, validation) + production_todo_items(validation) + list(topic.get("demo_materials", [])[:3]))


def outline_summary(topic: dict[str, Any], template: str, validation: ValidationResult) -> str:
    core = clip_text(topic.get("core_thesis"), 64, "待确认核心观点")
    return f"{template}｜{core}｜05只确认大纲，06再展开脚本和执行包。"


def generation_input_for_06(topic: dict[str, Any], template: str, template_reason: str, validation: ValidationResult, private_cases: list[dict[str, Any]]) -> str:
    evidence = key_evidence_items(topic, validation)
    p0_todos = production_todo_items(validation)
    fact_checks = validation.fact_check_points
    boundaries = private_boundaries(private_cases)[:3]
    ai_action = clip_text(topic.get("ai_intervention"), 82, "待确认实操主线")
    lines = [
        f"- 模板：{template}（{clip_text(template_reason, 54, '按题材和实验类型判断')}）",
        f"- 主线：{ai_action}",
        f"- 开头：{opening_hook_options(topic, validation)[0]}",
        f"- 判断：{inline_items(key_judgment_lines(topic), item_limit=46)}",
        f"- 金句：{inline_items(golden_lines(topic), item_limit=36)}",
        f"- 必拍：{inline_items(evidence, '待补：至少明确一个可展示证据', limit=4, item_limit=34)}",
        f"- 先补：{inline_items(p0_todos, '无P0素材缺口，直接人工确认大纲', limit=2, item_limit=36)}",
        f"- 核验：{inline_items(fact_checks, '无额外事实核验点', limit=2, item_limit=44)}",
        f"- 边界：{inline_items(boundaries, '无额外私有边界提醒', limit=2, item_limit=38)}",
        f"- 案例锚点：{private_case_names(private_cases)}",
        "- 06再生成：完整脚本、提词器、录屏清单、剪辑交接、发布包、QA。",
    ]
    return "\n".join(lines)


def render_execution_blocks(rows: list[dict[str, str]]) -> str:
    blocks = []
    for row in rows:
        blocks.append(
            f"### {row.get('时间段')}｜{row.get('段落目的')}\n\n"
            f"这一段的任务：{row.get('人工QA点')}\n\n"
            f"- 真人说什么：{row.get('口播轨')}\n"
            f"- 屏幕给什么：{row.get('画面/录屏轨')}\n"
            f"- 后期强调：{row.get('字幕重点')}；{row.get('后期提示')}"
        )
    return "\n\n".join(blocks)


def render_private_case_section(private_cases: list[dict[str, Any]]) -> str:
    if not private_cases:
        return "- 未自动匹配私有案例锚点，需人工判断是否有真实或相邻业务现场。"
    lines: list[str] = []
    for case in private_cases[:2]:
        evidence = next((str(item) for item in case.get("shootable_evidence", []) if str(item).strip()), "待人工选择")
        lines.append(f"- {case.get('name', '私有案例')}：优先借用「{evidence}」")
    return "\n".join(lines)


def execution_rows(topic: dict[str, Any], template: str) -> list[dict[str, str]]:
    demos = topic.get("demo_materials") or ["待补：关键录屏/截图/结果对比"]
    demo_text = "；".join(demos[:3])
    return [
        {
            "#": "01",
            "时间段": "00:00-00:08",
            "段落目的": "结果或冲突钩子",
            "口播轨": f"我会先用这条实验证明：{topic.get('core_thesis')}",
            "画面/录屏轨": f"结果闪现：{demos[0]}",
            "字幕重点": topic.get("topic_title", ""),
            "后期提示": "快切到结果或冲突，不铺背景。",
            "人工QA点": "8秒内是否能看出为什么值得看。",
        },
        {
            "#": "02",
            "时间段": "00:08-00:30",
            "段落目的": "真实痛点",
            "口播轨": f"以前的问题是：{topic.get('old_workflow')}",
            "画面/录屏轨": "旧流程截图、空白表格、混乱素材或待处理任务。",
            "字幕重点": topic.get("pain_point", ""),
            "后期提示": "真人小窗加旧流程画面，节奏稳定。",
            "人工QA点": "痛点是否来自真实业务现场。",
        },
        {
            "#": "03",
            "时间段": "00:30-01:00",
            "段落目的": "奥斯汀判断",
            "口播轨": topic.get("unique_judgment") or "这里补我的主观判断和取舍标准。",
            "画面/录屏轨": "方法卡、流程图或字段变化截图。",
            "字幕重点": "不是工具演示，是工作流验收。",
            "后期提示": "切回真人大画面强调判断。",
            "人工QA点": "是否像本人会说的话。",
        },
        {
            "#": "04",
            "时间段": "01:00-03:00",
            "段落目的": "三步实操",
            "口播轨": f"实操只看三步：输入是什么、AI改哪一步、输出怎么验收。AI介入点：{topic.get('ai_intervention')}",
            "画面/录屏轨": demo_text,
            "字幕重点": "Step 1 输入 / Step 2 AI处理 / Step 3 验收",
            "后期提示": "屏幕为主，等待过程快进，关键字段放大。",
            "人工QA点": "实操是否超过3步；画面是否能证明观点。",
        },
        {
            "#": "05",
            "时间段": "03:00-04:00",
            "段落目的": "边界与修正",
            "口播轨": "这里必须补AI哪里做不好、我怎么人工修正、什么情况下不能直接用。",
            "画面/录屏轨": "错误结果、修改前后、验收打勾/打叉。",
            "字幕重点": "AI做不好什么 / 人要验什么",
            "后期提示": "做前后对比和局部放大。",
            "人工QA点": "是否出现真人手痕和边界提醒。",
        },
        {
            "#": "06",
            "时间段": "04:00-05:00",
            "段落目的": "资产收束",
            "口播轨": f"最后把这次实验沉淀成：{display_asset(topic)}",
            "画面/录屏轨": "展示模板、清单、Skill、SOP或表格资产。",
            "字幕重点": display_asset(topic),
            "后期提示": "真人收尾加成果页，不做硬广口吻。",
            "人工QA点": "是否有明确可带走资产。",
        },
    ]


def render_table_rows(rows: list[dict[str, str]], headers: list[str]) -> str:
    rendered = []
    for row in rows:
        rendered.append("| " + " | ".join(str(row.get(header, "")).replace("\n", "<br>") for header in headers) + " |")
    return "\n".join(rendered)


def qa_rows(validation: ValidationResult) -> list[dict[str, str]]:
    return [
        {"检查项": "必填字段", "结果": "blocked" if validation.missing_required else "pass", "说明": md_list(validation.missing_required, "完整")},
        {"检查项": "实操证据", "结果": "revise" if validation.evidence_gaps else "pass", "说明": md_list(validation.evidence_gaps, "已有证据")},
        {"检查项": "真人判断", "结果": "revise" if validation.notes else "pass", "说明": md_list(validation.notes, "已有人工判断")},
        {"检查项": "事实核验", "结果": "revise" if validation.fact_check_points else "pass", "说明": md_list(validation.fact_check_points, "无额外核验点")},
        {"检查项": "是否进入06", "结果": "blocked", "说明": "v0.2不自动拆06，需人工确认已确认可制作。"},
    ]


def demo_rows(topic: dict[str, Any], private_cases: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in topic.get("demo_materials", []):
        rows.append({"素材类型": "已有/计划证据", "需要内容": item, "用途": "证明流程可复现或结果可用", "优先级": "高", "状态": "待确认"})
    for item in topic.get("missing_evidence", []):
        rows.append({"素材类型": "待补证据", "需要内容": item, "用途": "补足可信度和可拍摄性", "优先级": "高", "状态": "待补"})
    for case in private_cases or []:
        for item in case.get("shootable_evidence", [])[:3]:
            rows.append({
                "素材类型": "私有案例证据建议",
                "需要内容": str(item),
                "用途": f"借用「{case.get('name', '私有案例')}」证明这不是泛讲观点",
                "优先级": "中",
                "状态": "待确认",
            })
    if not rows:
        rows.append({"素材类型": "待补证据", "需要内容": "至少补一组截图、录屏或结果对比", "用途": "证明不是空泛讲述", "优先级": "高", "状态": "待补"})
    return rows


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def render_topic_package(topic: dict[str, Any], output_root: Path, run_date: str | None = None) -> dict[str, Any]:
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")
    private_runtime = load_private_runtime()
    private_cases = matched_private_cases(topic, private_runtime)
    template, template_reason = classify_template(topic)
    validation = validate_topic(topic)
    summary = outline_summary(topic, template, validation)
    viewpoint = core_viewpoint(topic, validation)
    snapshot = shootability_snapshot(topic, validation)
    outline = outline_segments(topic, validation)
    generation_input = generation_input_for_06(topic, template, template_reason, validation, private_cases)
    folder = output_root / run_date / f"{slugify(str(topic.get('topic_id', 'topic')))}_{slugify(topic.get('topic_title', 'topic'))}"
    folder.mkdir(parents=True, exist_ok=True)
    status = script_status_from_validation(validation)
    document_path = folder / OUTPUT_FILES[0]
    write_text(document_path, f"""# {topic.get('topic_title')}

## 05 脚本大纲确认

### 先看能不能拍

{snapshot}

### 核心观点

{viewpoint}

### 视频大纲

{md_numbered(outline)}

### 给06的生成输入

{generation_input}
""")

    return {
        "topic_id": topic.get("topic_id"),
        "topic_title": topic.get("topic_title"),
        "output_dir": str(folder),
        "document_path": str(document_path),
        "recommended_template": template,
        "template_reason": template_reason,
        "director_summary": summary,
        "core_thesis": topic.get("core_thesis"),
        "opening_hooks": opening_hook_options(topic, validation),
        "key_judgments": key_judgment_lines(topic),
        "golden_lines": golden_lines(topic),
        "core_viewpoint": viewpoint,
        "outline_segments": outline,
        "generation_input_06": generation_input,
        "key_evidence": key_evidence_items(topic, validation),
        "p0_todos": production_todo_items(validation),
        "reader_summary": f"{status}｜{template}｜{topic.get('core_thesis')}",
        "qa_status": validation.status,
        "missing_required": validation.missing_required,
        "evidence_gaps": validation.evidence_gaps,
        "fact_check_points": validation.fact_check_points,
        "notes": validation.notes,
        "private_case_anchors": [case.get("name", "") for case in private_cases],
        "generated_files": OUTPUT_FILES,
        "version": SKILL_VERSION,
    }


def load_records(input_path: Path) -> list[dict[str, Any]]:
    if input_path.suffix.lower() == ".json":
        data = json.loads(input_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if isinstance(data.get("topics"), list):
                return [dict(item) for item in data["topics"]]
            return [data]
        if isinstance(data, list):
            return [dict(item) for item in data]
        raise ValueError("JSON input must be an object, list, or object with topics list.")
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def render_records(records: list[dict[str, Any]], output_root: Path, run_date: str | None = None, limit: int = 0) -> list[dict[str, Any]]:
    selected = records[:limit] if limit else records
    summaries = []
    for index, record in enumerate(selected, 1):
        topic = normalize_topic(record, record_id=str(record.get("record_id") or f"T{datetime.now().strftime('%Y%m%d')}-{index:03d}"))
        summaries.append(render_topic_package(topic, output_root=output_root, run_date=run_date))
    return summaries
