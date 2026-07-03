#!/usr/bin/env python3
"""Deterministic renderer for Austin no-overtime scripting packages."""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SKILL_VERSION = "austin-production-packager-v0.6"
SKILL_ROOT = Path(__file__).resolve().parents[1]
VOICE_SKILL_NAME = "austin-voice-scriptwriter"
VOICE_SKILL_VERSION = "austin-voice-scriptwriter-v0.1"

REQUIRED_FIELDS = [
    "topic_title",
    "core_thesis",
    "pain_point",
    "target_audience",
    "old_workflow",
    "ai_intervention",
    "takeaway_asset",
]

FULL_PACKAGE_FILE = "full_script_execution_package.md"
LEGACY_OUTLINE_FILE = "script_outline_brief.md"
OUTPUT_FILES = [FULL_PACKAGE_FILE]

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


def split_research_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [normalized_text(item) for item in value if not is_emptyish_value(item)]
    text = normalized_text(value)
    if is_emptyish_value(text):
        return []
    parts = re.split(r"[；;\n]+", text)
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
    return md_list([str(case.get("name", "")) for case in private_cases], "未自动匹配可选案例参考，06再人工选择是否借用。")


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


INTERNAL_JUDGMENT_PREFIXES = [
    "同类资料讲法偏浅",
    "同类内容讲法偏浅",
    "对标资料讲法偏浅",
    "对标内容讲法偏浅",
    "对标视频讲法偏浅",
]


def voice_facing_judgment(value: Any) -> str:
    judgment = trim_end_punctuation(value, "")
    for prefix in INTERNAL_JUDGMENT_PREFIXES:
        if judgment.startswith(prefix) and "但" in judgment:
            return trim_end_punctuation(judgment.split("但", 1)[1], judgment)
    return judgment


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
        "production_direction": first_non_empty(fields, ["production_direction", "我的制作补充", "制作方向", "使用案例", "人工制作补充"], skip_placeholders=False),
        "unique_judgment": voice_facing_judgment(first_non_empty(fields, ["unique_judgment", "人工一句话判断", "我的思考点", "主编判断", "选题判断", "我的切入"])),
        "takeaway_asset": first_non_empty(fields, ["takeaway_asset", "可沉淀资产", "资料包承接方式", "重点体现"]),
        "research_sources": split_research_items(first_non_empty(fields, ["research_sources", "搜索来源摘要", "同类来源摘要"], skip_placeholders=False)),
        "expression_patterns": split_research_items(first_non_empty(fields, ["expression_patterns", "表达模式拆解", "对标表达拆解"], skip_placeholders=False)),
        "fusion_notes": split_research_items(first_non_empty(fields, ["fusion_notes", "融合说明", "取舍说明"], skip_placeholders=False)),
        "plain_explanation": first_non_empty(fields, ["plain_explanation", "概念浅显解释", "知识库浅显解释"], skip_placeholders=False),
        "style_baseline_notes": split_research_items(first_non_empty(fields, ["style_baseline_notes", "风格基线保护"], skip_placeholders=False)),
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
        return "Skill公开型", "内容重点是把高频流程讲清，并保留可复用方向。"
    return "真实工作流改造型", "默认按真实业务场景、旧流程、新流程和证据判断来拍。"


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

    if any(term in fact_text for term in ["OpenAI", "ChatGPT", "Codex", "Claude", "飞书", "价格", "规则", "更新", "发布"]):
        add_fact_check("涉及产品能力、平台规则或更新信息，发布前需事实核验。")
    if any(term in fact_text for term in ["政策", "法规", "国标", "强制性", "公示", "实施", "监管", "国家标准", "行业标准"]):
        add_fact_check("涉及政策、法规、国标、公示或实施时间，发布前需核验权威原文和具体日期。")
    if any(term in fact_text for term in ["L3", "L4", "自动驾驶", "智能驾驶", "辅助驾驶", "功能安全"]):
        add_fact_check("涉及智能驾驶等级、功能安全或汽车功能边界，发布前需核验官方定义，不能扩大声称。")

    if missing_required:
        status = "blocked"
    elif notes or any("缺少可展示证据" in item or "至少补一组截图" in item for item in evidence_gaps):
        status = "revise"
    else:
        status = "pass"
    return ValidationResult(status, missing_required, evidence_gaps, fact_check_points, notes)


def director_summary(topic: dict[str, Any], template: str, private_cases: list[dict[str, Any]] | None = None) -> str:
    summary = (
        f"这条视频按「{template}」处理：先用 Austin 口播风格写出完整真人稿，"
        f"再把「{topic['topic_title']}」拆成时间线、录屏画面、素材待办、剪辑交接和发布前 QA。"
    )
    if private_cases:
        summary += f" 可选案例参考：{private_case_names(private_cases)}。"
    return summary


def md_list(items: list[str], fallback: str = "待补") -> str:
    clean = [item for item in items if item]
    return "；".join(clean) if clean else fallback


def inline_items(items: list[str], fallback: str = "待补", limit: int = 3, item_limit: int = 42) -> str:
    clean = [clip_text(item, item_limit, "") for item in items if str(item).strip()]
    clean = [item for item in clean if item]
    return "；".join(clean[:limit]) if clean else fallback


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


def research_summary_lines(topic: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if topic.get("research_sources"):
        lines.append(f"搜索来源：{inline_items(topic.get('research_sources', []), limit=4, item_limit=74)}")
    if topic.get("expression_patterns"):
        lines.append(f"表达模式：{inline_items(topic.get('expression_patterns', []), limit=3, item_limit=74)}")
    if topic.get("fusion_notes"):
        lines.append(f"融合方式：{inline_items(topic.get('fusion_notes', []), limit=3, item_limit=74)}")
    if topic.get("plain_explanation"):
        lines.append(f"浅显解释：{clip_text(topic.get('plain_explanation'), 96, '')}")
    if topic.get("style_baseline_notes"):
        lines.append(f"风格基线：{inline_items(topic.get('style_baseline_notes', []), limit=3, item_limit=64)}")
    return lines


def research_markdown(topic: dict[str, Any]) -> str:
    blocks: list[str] = []
    if topic.get("research_sources"):
        blocks.append("**搜索/同类来源摘要**\n\n" + md_bullets(topic.get("research_sources", [])))
    if topic.get("expression_patterns"):
        blocks.append("**表达模式拆解**\n\n" + md_bullets(topic.get("expression_patterns", [])))
    if topic.get("fusion_notes"):
        blocks.append("**取舍与融合说明**\n\n" + md_bullets(topic.get("fusion_notes", [])))
    if topic.get("plain_explanation"):
        blocks.append(f"**概念浅显解释**\n\n{topic.get('plain_explanation')}")
    if topic.get("style_baseline_notes"):
        blocks.append("**风格基线保护**\n\n" + md_bullets(topic.get("style_baseline_notes", [])))
    return "\n\n".join(blocks)


def script_status_from_validation(validation: ValidationResult) -> str:
    if validation.missing_required:
        return "缺字段"
    if validation.notes:
        return "待补判断"
    if blocking_quality_issues(validation):
        return "待补关键证据"
    return "已生成完整执行包"


def can_enter_06_reason(validation: ValidationResult) -> str:
    if validation.missing_required:
        return "否：必填字段不完整。"
    if blocking_quality_issues(validation):
        return "否：先补关键判断或证据。"
    return "是：可进入拍摄准备。"


def decision_summary(topic: dict[str, Any], validation: ValidationResult, status: str) -> str:
    if validation.missing_required:
        return f"{status}：这条还不能生成完整执行包，先补齐必填字段：{md_list(validation.missing_required)}。"
    if validation.notes:
        return f"{status}：已生成脚本与执行包，但还要补足奥斯汀自己的判断、取舍或人工修正点。"
    if blocking_quality_issues(validation):
        return f"{status}：已生成脚本与执行包，但还缺少能支撑内容成立的关键证据。"
    return f"{status}：这条已生成完整口播稿和执行方案，可以进入拍摄准备。"


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

    actions = [f"补成可展示画面：{item}" for item in shooting_reminder_items(validation)]
    if actions:
        return actions[:3]

    release_items = release_reminder_items(validation)
    if release_items:
        return [f"核验：{item}" for item in release_items[:3]]
    if validation.notes:
        return [f"补人工判断：{item}" for item in validation.notes[:3]]
    return ["打开本地完整执行包，确认口播全文、录屏素材、剪辑交接和发布包是否可用。"]


def done_criteria(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    criteria = [
        "本地完整执行包打开后，能马上知道这条视频怎么说、怎么拍、缺什么素材。",
        "口播全文先由 austin-voice-scriptwriter 生成，再由本 Skill 编排录屏、剪辑、发布和 QA。",
        "执行方案按时间线展开，不是散点重点清单。",
    ]
    if shooting_reminder_items(validation) or release_reminder_items(validation):
        criteria.append("素材提醒、事实边界和私有表达边界已进入执行包待办。")
    else:
        criteria.append("人工确认执行包通过后，可以进入拍摄准备或拆 06 任务。")
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
    keys = ["topic_title", "content_pillar", "core_thesis", "pain_point", "old_workflow", "ai_intervention", "production_direction", "takeaway_asset"]
    return " ".join(str(topic.get(key, "")) for key in keys).lower()


def has_any(text: str, terms: list[str]) -> bool:
    return any(term.lower() in text for term in terms)


def workflow_object(topic: dict[str, Any]) -> str:
    text = topic_blob(topic)
    if any(term in text for term in ["voice agent", "口播", "配音", "声音", "字幕", "分镜"]):
        return "AI口播交付"
    if any(term in text for term in ["知识库", "obsidian", "资料仓库", "沉淀资产", "信息雷达"]):
        return "内容资产沉淀"
    if any(term in text for term in ["汽车", "智能驾驶", "辅助驾驶", "l3", "l4", "国标"]):
        return "汽车AI内容"
    if any(term in text for term in ["候选池", "excel", "批量补字段", "飞书候选"]):
        return "候选池"
    if any(term in text for term in ["选题", "brief", "主编", "门控"]):
        return "选题台"
    if any(term in text for term in ["adobe", "返修", "剪辑和设计工具", "创意agent"]):
        return "创意返修"
    if any(term in text for term in ["ppt", "pptx", "导出", "样式", "视觉", "baoyu", "设计"]):
        return "视觉交付"
    if any(term in text for term in ["封面"]):
        return "封面流程"
    if any(term in text for term in ["agent", "claude", "codex", "自动执行", "任务拆解"]):
        return "Agent任务"
    return "AI流程"


def hook_problem(topic: dict[str, Any]) -> str:
    obj = workflow_object(topic)
    mapping = {
        "AI口播交付": "AI口播最怕的不是声音不自然，是放进成片以后没人知道怎么返修",
        "内容资产沉淀": "内容资产最怕的不是资料没存，是写稿时还要重新找、重新判断、重新组织",
        "汽车AI内容": "汽车内容最怕的不是慢，是一句卖点把边界说过头",
        "候选池": "表格AI最怕的不是填得少，是填满以后没人知道对不对",
        "选题台": "选题台最怕的不是没灵感，是每条看起来都能做",
        "创意返修": "创意Agent最怕的不是入口多，是返修意见没有真的被命中",
        "视觉交付": "AI视觉最怕的不是不好看，是导出那一刻全变形",
        "封面流程": "封面自动化最怕的不是不出图，是每张都像另一个账号",
        "Agent任务": "AI任务最怕的不是没跑完，是跑完以后没人知道错在哪",
        "AI流程": "AI流程最怕的不是慢，是快到没人知道哪里该验收",
    }
    return mapping.get(obj, mapping["AI流程"])


def lead_hook(topic: dict[str, Any], validation: ValidationResult) -> str:
    text = topic_blob(topic)
    evidence_text = clip_text(evidence_phrase(topic, validation), 32, "关键证据")
    if validation.status == "blocked":
        gap = clip_text((validation.evidence_gaps or validation.missing_required or ["真实任务样本"])[0], 34, "真实任务样本")
        return f"这条先别急着拍，缺的不是标题，是{gap}"
    if has_any(text, ["claude", "团队原则"]):
        return "我不是想学Claude Code的原则，我想看AI任务交出去以后谁来验"
    if has_any(text, ["obsidian", "知识库"]):
        return "知识库如果不能把信息推回任务系统，就只是换个地方收藏"
    if has_any(text, ["voice agent", "口播", "配音"]):
        return "先把这段声音放进分镜和字幕里，再判断它能不能交付"
    if has_any(text, ["excel", "批量补字段", "候选池"]):
        return "表格自动化别先看填了多少，先看错了哪里能不能改回来"
    if has_any(text, ["adobe", "返修"]):
        return "创意Agent别先看功能入口，先看它能不能少掉一轮返修"
    if has_any(text, ["baoyu", "pptx", "导出"]):
        return "AI设计截图好看不算数，导出以后不变形才算数"
    if has_any(text, ["l3", "l4", "国标", "智能驾驶"]):
        return "汽车内容省一分钟可以，但一句卖点越界就不值得"
    if has_any(text, ["openrouter", "portkey", "网关"]):
        return "没有自己的调用场景，网关选型就只是技术热闹"
    if evidence_text:
        return f"先把{evidence_text}摆出来，再决定这条值不值得拍"
    return hook_problem(topic)


def second_hook(topic: dict[str, Any], validation: ValidationResult) -> str:
    obj = workflow_object(topic)
    mapping = {
        "AI口播交付": "声音自然只是第一步，能不能过分镜、字幕和返修才决定能不能交付",
        "内容资产沉淀": "不是再搭一个资料仓库，是检查资料有没有变成判断、脚本和复盘资产",
        "汽车AI内容": "这条不比谁更会写卖点，只比谁能守住证据和功能边界",
        "候选池": "我不缺更多候选，我缺的是每条为什么升降级都能看见",
        "选题台": "选题台不是帮我多想几个标题，是帮我挡掉不该做的题",
        "创意返修": "先把一条返修意见跑完，再谈它有没有进流程",
        "视觉交付": "演示效果再顺，导出后还要重修就不能进交付",
        "封面流程": "封面不是随机好看，是每次都还能像这个账号",
        "Agent任务": "Agent能不能用，不看它跑没跑完，看人还能不能追责",
        "AI流程": "AI流程能不能用，不看步骤多快，看证据能不能支撑判断",
    }
    if validation.status == "blocked":
        return "没有真实任务和证据，这条先停在观察，不要硬进06"
    return mapping.get(obj, mapping["AI流程"])


def key_judgment_extras(topic: dict[str, Any]) -> list[str]:
    obj = workflow_object(topic)
    mapping = {
        "汽车AI内容": [
            "汽车内容里的AI提效，必须先过功能边界和证据线。",
            "热点只能给入口，能不能上线要看风险复核。",
        ],
        "AI口播交付": [
            "AI口播能不能交付，不看声音多像真人，看角色、分镜、字幕和返修能不能接住。",
            "声音只是素材，过了导演验收才可能变成成片。",
        ],
        "内容资产沉淀": [
            "知识库只有接进生产流程，才不是另一个资料仓库。",
            "内容资产不是存起来，而是能回到判断、脚本和复盘。",
        ],
        "候选池": [
            "候选池自动化的价值，不是填满表格，是把判断成本降下来。",
            "AI补字段能不能用，关键看人改错的地方能不能回写成规则。",
        ],
        "选题台": [
            "选题台不是灵感池，是把能做和不该做分开的判断系统。",
            "AI可以帮我初筛，但升级或放弃必须留下理由。",
        ],
        "创意返修": [
            "创意Agent有没有价值，要看它能不能承接返修，不是看功能入口多不多。",
            "返修自动化不能只改画面，要能对上人的修改意见。",
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
            "Agent任务能不能进流程，不看跑没跑完，看输入、输出、异常和人工判断能不能对上。",
            "真正的AI改造，是把人的判断、异常和回滚留在现场。",
        ],
        "AI流程": [
            f"{obj}能不能进流程，不看生成多快，看关键证据能不能支撑判断。",
            "AI改造的价值，是把判断留在流程里，而不是把步骤藏进黑箱。",
        ],
    }
    return mapping.get(obj, mapping["AI流程"])


def golden_line_pool(topic: dict[str, Any]) -> list[str]:
    obj = workflow_object(topic)
    text = topic_blob(topic)
    specific: list[str] = []
    if has_any(text, ["claude", "团队原则"]):
        specific = ["原则不能收藏，要变成验收动作。", "AI任务不是交出去就结束，是验得回来才算数。"]
    elif has_any(text, ["voice agent", "口播", "配音"]):
        specific = ["声音自然不是交付，过了分镜、字幕和返修才算。", "AI口播不是少录一遍音，是少走一轮返修。"]
    elif has_any(text, ["obsidian", "知识库"]):
        specific = ["知识库不是仓库，是回到任务的路由。", "收藏不进入执行台，就只是换了一个地方躺着。"]
    elif has_any(text, ["excel", "批量补字段"]):
        specific = ["表格填满不是自动化，少改才是自动化。", "错得能回写，AI才值得继续用。"]
    elif has_any(text, ["adobe", "返修"]):
        specific = ["Agent的价值不在入口多，在返修少。", "能少改一轮，才算进了创意流程。"]
    elif has_any(text, ["baoyu", "pptx", "导出"]):
        specific = ["导出没过，设计就还没交付。", "好看的截图，不等于能交付的文件。"]
    elif has_any(text, ["openrouter", "portkey", "网关"]):
        specific = ["没有自己的调用场景，就别急着讲网关选型。", "工具比较没有任务约束，很快就会变成泛资讯。"]

    mapping = {
        "汽车AI内容": [
            "汽车内容先守边界，再谈效率。",
            "卖点可以被AI放大，责任不能。",
            "能过风险线的内容，才配上线。",
        ],
        "候选池": [
            "先看到错在哪里，再谈批量。",
            "字段不是越多越好，是越能解释判断越好。",
            "候选池不是越满越好，是越清楚越好。",
        ],
        "选题台": [
            "选题不是灵感池，是判断系统。",
            "能说清为什么不做，才算真的会选题。",
            "好的选题台，先挡住泛资讯。",
        ],
        "创意返修": [
            "返修意见要逐条对账。",
            "没命中的修改，生成再多也没用。",
            "能对上修改意见，比多生成十版更有用。",
        ],
        "视觉交付": [
            "导不出来的设计，不算交付。",
            "AI视觉真正的终点，是验收通过。",
            "导出稳定，才算真正进交付。",
        ],
        "封面流程": [
            "封面不是出图，是账号识别。",
            "随机好看，不如稳定像我。",
            "能复用的风格，才是封面系统。",
        ],
        "Agent任务": [
            "没有验收记录的Agent，只是跑得更快的黑箱。",
            "能追责的AI，才配进流程。",
            "自动化不是省人，是把人的判断固定下来。",
        ],
        "AI流程": [
            "没有证据链的AI，只是一次演示。",
            "能复用的才叫流程，不能复用的只是表演。",
            "AI越快，验收越要慢半拍。",
        ],
    }
    return unique_items(specific + mapping.get(obj, mapping["AI流程"]))


def readable_evidence_item(value: str) -> str:
    text = trim_end_punctuation(value, "")
    replacements = [
        ("需要实际跑10条候选，补回填截图", "10条候选的回填截图"),
        ("需要跑一次固定样张，补导出对比截图", "固定样张的导出对比截图"),
        ("一张从03到04再到06的路径截图", "03 收件箱、04 选题字段、06 文档路径和脚本包路径截图"),
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
    evidence_text = evidence_phrase(topic, validation)
    return f"先判断画面能不能支撑观点，至少看到{evidence_text}"


def old_new_contrast(topic: dict[str, Any]) -> str:
    pain = trim_end_punctuation(topic.get("old_workflow") or topic.get("pain_point"), "旧流程靠经验和人工盯结果")
    ai_action = trim_end_punctuation(topic.get("ai_intervention"), "AI介入一个可验证的小环节")
    return f"旧流程是「{pain}」，新动作是「{ai_action}」"


def opening_hook_options(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    evidence_text = clip_text(evidence_phrase(topic, validation), 38, "输入、输出和人工验收画面")
    return unique_items([
        f"{lead_hook(topic, validation)}。",
        f"{second_hook(topic, validation)}。",
        f"拿不出{evidence_text}，这条就不是实战。",
    ])


def topic_judgment_text(topic: dict[str, Any], fallback: str = "AI不能只看生成结果，必须回到业务验收") -> str:
    return trim_end_punctuation(topic.get("unique_judgment"), "") or fallback


def key_judgment_lines(topic: dict[str, Any]) -> list[str]:
    judgment = clip_text(topic_judgment_text(topic), 54, "AI不能只看生成结果，必须回到业务验收")
    return unique_items([judgment] + key_judgment_extras(topic))


def golden_lines(topic: dict[str, Any]) -> list[str]:
    return unique_items(golden_line_pool(topic))[:3]


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
    judgment = clip_text(topic_judgment_text(topic, "AI只能辅助判断，最终取舍仍然要回到人的业务标准"), 72, "AI只能辅助判断，最终取舍仍然要回到人的业务标准")
    evidence_text = clip_text(evidence_phrase(topic, validation), 60, "输入、输出和人工验收画面")
    hook = opening_hook_options(topic, validation)[0]
    if validation.status == "blocked":
        gap = inline_items(validation.evidence_gaps or validation.missing_required, "真实任务样本", limit=2, item_limit=34)
        return (
            f"这条现在先不拍。\n\n"
            f"原因不是话题弱，而是它还没有贴到我的真实现场：{gap}。\n\n"
            f"如果要救它，06之前必须先补一个真实任务、一次输入输出和一个能判断成败的证据。补不上，就继续观察，不要把技术热点硬讲成我的工作流。"
        )
    if workflow_object(topic) == "汽车AI内容":
        return (
            f"这条不是讲政策解读，而是借「{title}」补一条内容上线前的风险判断。\n\n"
            f"我的立场是：{judgment}。旧流程的问题在于：{pain}。\n\n"
            f"画面先给{evidence_text}，再录{ai_action}。具体案例和素材选择要服从真实材料，不能为了成稿硬指定。"
        )
    if workflow_object(topic) == "视觉交付":
        return (
            f"这条要拍成交付检查，不拍工具更新。\n\n"
            f"我的判断是：{judgment}。旧流程卡在：{pain}。\n\n"
            f"开头直接给{evidence_text}，中段只录{ai_action}。如果看不到导出、错位、人工修正这些画面，就先别进入拍摄。"
        )
    if workflow_object(topic) == "创意返修":
        return (
            f"这条要拍成一次返修命中测试，不拍工具功能更新。\n\n"
            f"我的判断是：{judgment}。旧流程卡在：{pain}。\n\n"
            f"开头先给{evidence_text}，中段只录{ai_action}。我不看它能生成多少版本，只看它能不能把人的修改意见对准、少掉一轮人工返修。"
        )
    return (
        f"我想把这条拍成一个小实验：{core}。\n\n"
        f"它真正要证明的是：{judgment}。旧流程里的卡点是：{pain}。\n\n"
        f"开头用「{hook}」切进去，画面先给{evidence_text}，中段再录{ai_action}。具体案例和素材选择要根据真实材料来定。"
    )


def outline_segments(topic: dict[str, Any], validation: ValidationResult | None = None) -> list[str]:
    validation = validation or ValidationResult("revise", [], [], [], [])
    obj = workflow_object(topic)
    pain = clip_text(topic.get("old_workflow") or topic.get("pain_point"), 58, "旧流程里的真实痛点")
    judgment = clip_text(topic_judgment_text(topic, "我的判断和边界"), 58, "我的判断和边界")
    ai_action = clip_text(topic.get("ai_intervention"), 58, "AI介入动作")
    evidence_text = clip_text(evidence_phrase(topic, validation), 48, "关键截图、录屏或前后对比")
    hook = opening_hook_options(topic, validation)[0]
    if validation.status == "blocked":
        gap = inline_items(validation.evidence_gaps or validation.missing_required, "真实任务样本", limit=2, item_limit=32)
        return [
            f"00:00-00:10｜先判停：直接说「{hook}」",
            f"00:10-00:40｜缺口上屏：列出现在缺的东西：{gap}",
            f"00:40-01:20｜最低补救：只设计一个小验证，不展开完整脚本；先补{evidence_text}",
            f"01:20-01:50｜边界：说明不能给选型、能力或业务结论，只能留作观察",
            "01:50-02:00｜收尾：不进入拍摄，等真实任务样本出现再重新生成",
        ]
    if obj == "汽车AI内容":
        return [
            f"00:00-00:08｜先给风险句：闪现{evidence_text}，让观众看到边界问题",
            f"00:08-00:35｜旧审核现场：说明{pain}",
            f"00:35-00:55｜真人定调：{judgment}",
            f"00:55-02:00｜录屏复核：{ai_action}，重点拍风险词、证据缺口和人工判断",
            "02:00-02:40｜反例：放一条容易说过头的卖点，说明为什么不能直接上线",
            "02:40-03:10｜收尾：只判断这条是否值得拍，不做法规结论",
        ]
    if obj == "视觉交付":
        return [
            f"00:00-00:08｜先给结果：展示导出前后或错位画面，说「{hook}」",
            f"00:08-00:30｜旧交付现场：指出{pain}",
            f"00:30-00:50｜真人判断：{judgment}",
            f"00:50-01:50｜录屏回归：{ai_action}，只拍导出、对比、修正",
            f"01:50-02:40｜验收段：放{evidence_text}，统计还剩几处人工修",
            "02:40-03:10｜收尾：这条能不能进06，取决于导出稳定性而不是截图好看",
        ]
    if obj == "创意返修":
        return [
            f"00:00-00:08｜先给对比：展示返修前后画面，说「{hook}」",
            f"00:08-00:30｜旧返修现场：指出{pain}",
            f"00:30-00:55｜真人判断：{judgment}",
            f"00:55-01:55｜跑一轮修改：{ai_action}，重点拍修改意见、Agent动作和人手接管点",
            f"01:55-02:40｜命中检查：放{evidence_text}，逐条看哪些意见命中、哪些还要人改",
            "02:40-03:10｜收尾：如果能少掉一轮返修，再进入拍摄；如果只是多生成几版，就不拍成教程",
        ]
    if obj in {"候选池", "选题台"}:
        return [
            f"00:00-00:08｜先给对比：旧候选/新候选并排，抛出「{hook}」",
            f"00:08-00:30｜旧流程卡点：说明{pain}",
            f"00:30-00:50｜真人判断：{judgment}",
            f"00:50-02:00｜小批量实验：{ai_action}，只跑几条，不演完整流水账",
            f"02:00-02:45｜错误回看：放{evidence_text}，重点看哪些字段还要人改",
            "02:45-03:10｜收尾：如果错误能回写规则，再继续制作；否则先改字段",
        ]
    if obj == "Agent任务":
        return [
            f"00:00-00:08｜先给异常：展示任务跑完但无法判断的画面，说「{hook}」",
            f"00:08-00:30｜交代旧交付：说明{pain}",
            f"00:30-00:55｜真人判断：{judgment}",
            f"00:55-02:10｜跑一个任务：{ai_action}，重点拍输入、输出、异常、人工复核",
            f"02:10-02:50｜失败也放：展示{evidence_text}，说明哪里必须人接手",
            "02:50-03:15｜收尾：能追责就进06，不能追责就不拍成教程",
        ]
    return [
        f"00:00-00:08｜开场：先闪现{evidence_text}，说「{hook}」",
        f"00:08-00:30｜旧流程：指出{pain}",
        f"00:30-00:50｜真人判断：{judgment}",
        f"00:50-02:10｜实操：{ai_action}，只拍关键动作",
        f"02:10-03:00｜证据：放{evidence_text}，补一个反例或人工修正",
        "03:00-03:20｜收尾：只判断是否继续制作",
    ]


def key_evidence_items(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    return unique_items(shooting_reminder_items(validation) + list(topic.get("demo_materials", [])[:3]) + public_evidence_items(topic, validation))


def outline_summary(topic: dict[str, Any], template: str, validation: ValidationResult) -> str:
    core = clip_text(topic.get("core_thesis"), 64, "待确认核心观点")
    return f"{template}｜{core}｜已按全文口播稿生成完整执行包，并写入06轻量记录。"


def generation_input_for_06(topic: dict[str, Any], template: str, template_reason: str, validation: ValidationResult, private_cases: list[dict[str, Any]]) -> str:
    evidence = key_evidence_items(topic, validation)
    p0_todos = shooting_reminder_items(validation)
    fact_checks = release_reminder_items(validation)
    boundaries = private_boundaries(private_cases)[:3]
    ai_action = clip_text(topic.get("ai_intervention"), 82, "待确认实操主线")
    production_direction = clip_text(topic.get("production_direction"), 110, "")
    research_lines = research_summary_lines(topic)
    lines = [
        f"- 模板：{template}（{clip_text(template_reason, 54, '按题材和实验类型判断')}）",
        *([f"- 人工制作补充：{production_direction}"] if production_direction else []),
        f"- 主线：{ai_action}",
        f"- 开头：{opening_hook_options(topic, validation)[0]}",
        f"- 判断：{inline_items(key_judgment_lines(topic), item_limit=46)}",
        f"- 金句：{inline_items(golden_lines(topic), item_limit=36)}",
        f"- 证据建议：{inline_items(evidence, '待补：至少明确一个可展示证据', limit=4, item_limit=34)}",
        f"- 待补素材：{inline_items(p0_todos, '无P0素材缺口，可人工确认执行包', limit=2, item_limit=36)}",
        f"- 核验：{inline_items(fact_checks, '无额外事实核验点', limit=2, item_limit=44)}",
        f"- 边界：{inline_items(boundaries, '无额外私有边界提醒', limit=2, item_limit=38)}",
        f"- 可选案例参考：{private_case_names(private_cases)}（仅供06选择，不强制使用）",
        *[f"- {line}（只作为素材，不改变稳定口播结构）" for line in research_lines],
        "- 本执行包已生成：完整口播稿、录屏清单、剪辑交接、发布包、QA；后续可按需拆任务。",
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
        lines.append(f"- {case.get('name', '私有案例')}：可参考「{evidence}」，是否使用由06结合真实素材决定")
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
            "口播轨": topic_judgment_text(topic, "这里补我的主观判断和取舍标准。"),
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
            "段落目的": "收尾判断",
            "口播轨": "最后只判断这条是否值得继续制作，案例和最终呈现形式等真实素材确定后再定。",
            "画面/录屏轨": "展示最终证据、待补素材或人工判断结论。",
            "字幕重点": "是否继续制作",
            "后期提示": "真人收尾加成果页，不做硬广口吻。",
            "人工QA点": "是否没有强行指定案例或资产。",
        },
    ]


def render_table_rows(rows: list[dict[str, str]], headers: list[str]) -> str:
    rendered = []
    for row in rows:
        rendered.append("| " + " | ".join(str(row.get(header, "")).replace("\n", "<br>") for header in headers) + " |")
    return "\n".join(rendered)


def qa_rows(validation: ValidationResult) -> list[dict[str, str]]:
    blocking_issues = blocking_quality_issues(validation)
    generic_evidence_gaps = [
        item for item in validation.evidence_gaps
        if "缺少可展示证据" in item or "至少补一组截图" in item
    ]
    production_evidence = [
        item for item in validation.evidence_gaps
        if not is_release_reminder_item(readable_evidence_item(item))
    ]
    release_items = release_reminder_items(validation)
    return [
        {"检查项": "必填字段", "结果": "blocked" if validation.missing_required else "pass", "说明": md_list(validation.missing_required, "完整")},
        {"检查项": "实操证据", "结果": "revise" if generic_evidence_gaps else "pass", "说明": md_list(production_evidence, "已有证据")},
        {"检查项": "真人判断", "结果": "revise" if validation.notes else "pass", "说明": md_list(validation.notes, "已有人工判断")},
        {"检查项": "事实核验", "结果": "reminder" if release_items else "pass", "说明": md_list(release_items, "无额外核验点")},
        {"检查项": "是否继续制作", "结果": "blocked" if validation.missing_required else ("revise" if blocking_issues else "pass"), "说明": "已生成完整执行包；具体拍摄和事实核验按提醒执行，是否拆任务仍需人工确认。"},
    ]


def demo_rows(topic: dict[str, Any], private_cases: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in topic.get("demo_materials", []):
        rows.append({"素材类型": "已有/计划证据", "需要内容": item, "用途": "证明流程可复现或结果可用", "优先级": "高", "状态": "待确认"})
    for item in production_todo_items(validate_topic(topic)):
        readable_item = readable_evidence_item(item)
        if is_release_reminder_item(readable_item):
            continue
        rows.append({"素材类型": "待补证据", "需要内容": readable_item, "用途": "补足可信度和可拍摄性", "优先级": "高", "状态": "待补"})
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
    """Legacy v0.4 outline renderer kept for old tests and explicit callers.

    The production path no longer calls this function. Use
    render_full_execution_package() for v0.6.
    """
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")
    display_title = topic.get("topic_title") or "未命名选题"
    private_runtime = load_private_runtime()
    private_cases = matched_private_cases(topic, private_runtime)
    template, template_reason = classify_template(topic)
    validation = validate_topic(topic)
    summary = outline_summary(topic, template, validation)
    viewpoint = core_viewpoint(topic, validation)
    snapshot = shootability_snapshot(topic, validation)
    outline = outline_segments(topic, validation)
    generation_input = generation_input_for_06(topic, template, template_reason, validation, private_cases)
    folder = output_root / run_date / f"{slugify(str(topic.get('topic_id', 'topic')))}_{slugify(display_title)}"
    folder.mkdir(parents=True, exist_ok=True)
    status = script_status_from_validation(validation)
    document_path = folder / LEGACY_OUTLINE_FILE
    write_text(document_path, f"""# {display_title}

## 历史兼容：脚本大纲确认

### 先看能不能拍

{snapshot}

### 核心观点

{viewpoint}

### 视频大纲

{md_numbered(outline)}

### 制作包生成输入

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
        "production_context": generation_input,
        "key_evidence": key_evidence_items(topic, validation),
        "p0_todos": shooting_reminder_items(validation),
        "reader_summary": f"{status}｜{template}｜{topic.get('core_thesis')}",
        "qa_status": validation.status,
        "missing_required": validation.missing_required,
        "evidence_gaps": validation.evidence_gaps,
        "fact_check_points": validation.fact_check_points,
        "notes": validation.notes,
        "private_case_anchors": [case.get("name", "") for case in private_cases],
        "generated_files": [LEGACY_OUTLINE_FILE],
        "version": SKILL_VERSION,
    }


def production_recommendation(validation: ValidationResult) -> str:
    if validation.missing_required:
        return "先不拍：核心字段缺失，容易写成泛讲观点。"
    if blocking_quality_issues(validation):
        return "先补关键判断或证据，否则容易写成泛讲观点。"
    return "草稿：待 PM 验收和独立 QA 后，再判断是否进入拍摄准备。"


def full_script_opening(topic: dict[str, Any], validation: ValidationResult) -> str:
    text = topic_blob(topic)
    if "claude" in text and ("agent" in text or "验收" in text):
        return "我这条不想讲 Claude Code 多强。我只拿它团队原则做一件事：把我的 AI 项目，从“交给 Agent 试试”，改成“交给 Agent 后能验收”。"
    if workflow_object(topic) == "汽车AI内容":
        return "我这条不做政策解读。我只拿它补一条上线前的风险线：AI生成的汽车卖点，哪些能说，哪些必须停下来复核。"
    if workflow_object(topic) == "视觉交付":
        return "我不想再看一张好看的截图。我只看它能不能导出、能不能复用、能不能进入真实交付。"
    if workflow_object(topic) == "封面流程":
        return "封面自动化最难的不是出图，是每次都像我，而且每次都能过标题和排版验收。"
    hook = opening_hook_options(topic, validation)[0]
    return hook.rstrip("。") + "。"


def voice_skill_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_dir = os.getenv("AUSTIN_VOICE_SCRIPT_SKILL_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.append(Path.home() / ".codex" / "skills" / VOICE_SKILL_NAME)
    return candidates


def load_voice_skill_module() -> Any | None:
    for skill_dir in voice_skill_candidates():
        module_path = skill_dir / "scripts" / "austin_voice.py"
        if not module_path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("austin_voice_scriptwriter", module_path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception:
            continue
    return None


def voice_skill_context(topic: dict[str, Any], validation: ValidationResult) -> dict[str, str]:
    return {
        "opening": full_script_opening(topic, validation),
        "evidence_text": inline_items(script_evidence_items(topic, validation), "输入、输出、错误点和人工修改记录", limit=3, item_limit=42),
        "todo_text": inline_items(shooting_reminder_items(validation), "一段真实录屏和一个失败样例", limit=2, item_limit=42),
        "fact_text": inline_items(voice_fact_check_items(validation), "", limit=2, item_limit=48),
        "voice_skill_version": VOICE_SKILL_VERSION,
    }


def voice_skill_sections(topic: dict[str, Any], validation: ValidationResult) -> list[tuple[str, str]]:
    module = load_voice_skill_module()
    if not module or not hasattr(module, "render_voice_sections"):
        return []
    try:
        sections = module.render_voice_sections(topic, voice_skill_context(topic, validation))
    except Exception:
        return []
    clean: list[tuple[str, str]] = []
    for item in sections or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        heading = str(item[0]).strip()
        body = str(item[1]).strip()
        if heading and body:
            clean.append((heading, body))
    return clean


def teleprompter_sections(topic: dict[str, Any], validation: ValidationResult) -> list[tuple[str, str]]:
    styled_sections = voice_skill_sections(topic, validation)
    if styled_sections:
        return styled_sections

    opening = full_script_opening(topic, validation)
    title = trim_end_punctuation(topic.get("topic_title"), "这条选题")
    core = trim_end_punctuation(topic.get("core_thesis"), title)
    pain = trim_end_punctuation(topic.get("pain_point") or topic.get("old_workflow"), "旧流程里有一个真实卡点")
    old = trim_end_punctuation(topic.get("old_workflow"), pain)
    ai_action = trim_end_punctuation(topic.get("ai_intervention"), "让 AI 介入一个可以验收的小环节")
    judgment = trim_end_punctuation(topic_judgment_text(topic, "AI 真正要进入业务流程，必须留下可验收的证据"), "AI 真正要进入业务流程，必须留下可验收的证据")
    asset = trim_end_punctuation(topic.get("takeaway_asset"), "一份可复用的流程清单")
    evidence = inline_items(script_evidence_items(topic, validation), "输入、输出、异常和人工验收画面", limit=3, item_limit=44)
    todos = inline_items(shooting_reminder_items(validation), "拍摄前不额外补P0素材", limit=2, item_limit=42)
    direction = trim_end_punctuation(topic.get("production_direction"), "")

    middle_direction = f"\n\n你如果已经在第二张卡里补了制作方向，我会按这个方向收住：{direction}。" if direction else ""
    return [
        ("00:00-00:10｜开场钩子", opening),
        (
            "00:10-00:40｜真实痛点",
            f"我现在做 AI 项目，最怕的不是它不会执行，而是它执行完以后，我不知道怎么验收。\n\n{pain}。{old}。所以最后经常会变成：Agent 跑了一堆东西，我还是要靠感觉判断能不能用。{middle_direction}",
        ),
        (
            "00:40-01:15｜核心判断",
            f"所以这条的重点不是复述「{title}」。我真正想测的是：{core}。\n\n我的判断很简单：{judgment}。\n\n一个 AI 工作流如果只有结果，没有状态、异常和验收记录，它只是看起来自动化了。真的进入业务，必须能追责、能回滚、能复盘。",
        ),
        (
            "01:15-02:30｜实操主线",
            f"我会拿一条真实小任务来跑，不做大而全。\n\n第一步，先把任务输入说清楚：我要处理什么资料，最后交付什么结果。\n\n第二步，把 AI 的动作限制住：{ai_action}。\n\n第三步，看验收表，而不是只看最终答案。这里至少要留下三类画面：{evidence}。",
        ),
        (
            "02:30-03:20｜失败和人工修正",
            f"这一段一定要放失败样例。因为我不想把它讲成“AI 一跑就对”。\n\n如果中间缺了输入、输出、异常原因，或者验收结论写不清楚，我会直接判失败。然后我再补一轮人工修正，看这张表到底能不能减少我的返工。\n\n拍摄前还要补：{todos}。",
        ),
        (
            "03:20-04:00｜收尾判断",
            f"最后我不会说这套东西已经完美解决 AI 项目管理。\n\n我只想证明一件事：AI 任务不是交出去就结束，而是从一开始就要设计它怎么被验收。\n\n如果这次能跑通，它后面可以沉淀成{asset}；如果跑不通，也很好，至少我知道问题不是模型不够强，而是我的任务拆解和验收字段还不够清楚。",
        ),
    ]


def render_teleprompter(topic: dict[str, Any], validation: ValidationResult) -> str:
    sections = []
    for heading, body in teleprompter_sections(topic, validation):
        sections.append(f"### {heading}\n\n{body}")
    return "\n\n".join(sections)


def script_evidence_items(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    return [readable_evidence_item(item) for item in key_evidence_items(topic, validation)]


def readable_todo_items(validation: ValidationResult) -> list[str]:
    return [readable_evidence_item(item) for item in production_todo_items(validation)]


def full_package_outline(topic: dict[str, Any], validation: ValidationResult) -> list[str]:
    pain = clip_text(topic.get("old_workflow") or topic.get("pain_point"), 70, "旧流程缺少验收字段")
    judgment = clip_text(topic_judgment_text(topic, "AI任务必须留下状态、异常和验收记录"), 70, "AI任务必须留下状态、异常和验收记录")
    ai_action = clip_text(topic.get("ai_intervention"), 76, "按验收表跑一次真实任务")
    evidence = inline_items(script_evidence_items(topic, validation), "任务跑表、输入输出、失败样例", limit=3, item_limit=34)
    todos = inline_items(shooting_reminder_items(validation), "无P0素材缺口", limit=2, item_limit=34)
    return [
        f"00:00-00:10｜开场钩子：{full_script_opening(topic, validation)}",
        f"00:10-00:40｜真实痛点：交代{pain}，画面给旧任务或缺失验收字段的现场。",
        f"00:40-01:15｜核心判断：切真人，说清{judgment}。",
        f"01:15-02:30｜实操主线：只跑一个小任务，展示{ai_action}。",
        f"02:30-03:20｜失败和修正：放出{evidence}，说明哪里必须人工接手。",
        f"03:20-04:00｜收尾判断：回到是否值得继续拍；拍摄前补齐{todos}。",
    ]


def execution_package_rows(topic: dict[str, Any], validation: ValidationResult) -> list[dict[str, str]]:
    rows = []
    for heading, body in teleprompter_sections(topic, validation):
        time_range, purpose = heading.split("｜", 1)
        rows.append({
            "时间": time_range,
            "段落": purpose,
            "真人口播": clip_text(body, 86, ""),
            "画面/录屏": capture_hint_for_segment(topic, validation, purpose),
            "剪辑重点": editing_hint_for_segment(purpose),
            "QA": qa_hint_for_segment(purpose),
        })
    return rows


def capture_hint_for_segment(topic: dict[str, Any], validation: ValidationResult, purpose: str) -> str:
    evidence = script_evidence_items(topic, validation)
    if "开场" in purpose:
        return clip_text(evidence[0] if evidence else topic.get("topic_title"), 72, "先闪现结果或冲突画面")
    if "痛点" in purpose or "旧流程" in purpose:
        return clip_text(topic.get("old_workflow") or topic.get("pain_point"), 72, "旧流程截图或任务卡住的画面")
    if "判断" in purpose or "真正要做什么" in purpose:
        return "切真人大画面，旁边给方法卡或验收字段草稿"
    if "实操" in purpose or "三个动作" in purpose:
        return clip_text(topic.get("ai_intervention"), 84, "录屏展示输入、AI处理和验收字段")
    if "失败" in purpose:
        return inline_items(shooting_reminder_items(validation), "错误结果、人工修正、验收打叉", limit=2, item_limit=38)
    if "对比" in purpose:
        return inline_items(evidence, "前后对比、验收字段、人工修改痕迹", limit=2, item_limit=38)
    if "边界" in purpose or "收尾" in purpose:
        return inline_items(shooting_reminder_items(validation), "待补素材和人工边界提醒", limit=2, item_limit=38)
    return inline_items(evidence, "真实录屏、字段表或人工修正画面", limit=2, item_limit=38)


def editing_hint_for_segment(purpose: str) -> str:
    if "开场" in purpose:
        return "0-3秒给结果，不铺背景；字幕只放一句冲突。"
    if "实操" in purpose:
        return "等待过程快进，只放输入、输出、验收三个关键节点。"
    if "失败" in purpose:
        return "错误点放大，前后对比，保留人工修改痕迹。"
    if "收尾" in purpose:
        return "切回真人，结尾停半拍，不做课程总结口吻。"
    return "真人和录屏交替，字幕强调判断句。"


def qa_hint_for_segment(purpose: str) -> str:
    if "开场" in purpose:
        return "8秒内是否有冲突、结果或反常识。"
    if "痛点" in purpose:
        return "痛点是否来自自己的工作流，不是泛泛说效率。"
    if "实操" in purpose:
        return "是否只保留三步；是否能看见真实输入和输出。"
    if "失败" in purpose:
        return "是否承认AI边界和人工修正点。"
    return "是否回到业务判断，不夸大结果。"


def capture_rows_for_full_package(topic: dict[str, Any], validation: ValidationResult, private_cases: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = demo_rows(topic, private_cases[:1])
    for row in rows:
        if row.get("状态") == "待确认":
            row["状态"] = "拍摄前确认"
        if row.get("素材类型") == "待补证据":
            row["需要内容"] = readable_evidence_item(row.get("需要内容", ""))
    return rows


def publish_package_lines(topic: dict[str, Any]) -> list[str]:
    title = trim_end_punctuation(topic.get("topic_title"), "AI工作流实战")
    obj = workflow_object(topic)
    if obj == "Agent任务":
        return [
            "标题1：我不再让 Agent 只交结果，我要它交验收记录",
            "标题2：Claude Code 团队原则，我只拆这一件事",
            "标题3：AI 项目最容易漏掉的不是执行，是验收",
            "封面大字：Agent 任务怎么验收？",
            "置顶评论：你现在用 Agent 时，会要求它留下失败和验收记录吗？",
        ]
    return [
        f"标题1：{title}",
        f"标题2：这条我不讲工具，只讲怎么进业务流程",
        f"标题3：AI提效之前，先把验收线画清楚",
        f"封面大字：这条能不能进流程？",
        "置顶评论：你想先看完整流程，还是先看失败样例？",
    ]


def blocking_quality_issues(validation: ValidationResult) -> list[str]:
    issues: list[str] = []
    generic_evidence_gaps = [
        item for item in validation.evidence_gaps
        if "缺少可展示证据" in item or "至少补一组截图" in item
    ]
    if generic_evidence_gaps:
        issues.append(f"缺关键证据：{md_list(generic_evidence_gaps)}")
    if validation.notes:
        issues.append(f"补人工判断：{md_list(validation.notes)}")
    return issues


INTERNAL_BOUNDARY_TERMS = [
    "不需要证明",
    "如果当天还没生成06",
    "只作为选题系统复盘",
]

RELEASE_REMINDER_TERMS = [
    "发布前",
    "核验",
    "原文",
    "避免说错",
    "事实",
    "不能声称",
    *INTERNAL_BOUNDARY_TERMS,
]

REAL_SCENE_TERMS = [
    "案例",
    "现场",
    "截图",
    "录屏",
    "样例",
    "脚本片段",
    "验收表",
    "路径",
    "文件",
    "素材",
    "分镜",
    "字幕",
    "收件箱",
    "字段",
    "文档",
    "清单",
    "对比",
]


def concrete_todo_items(validation: ValidationResult) -> list[str]:
    return [
        item for item in readable_todo_items(validation)
        if "缺少可展示证据" not in item and "至少补一组截图" not in item
    ]


def is_release_reminder_item(item: str) -> bool:
    return any(term in item for term in RELEASE_REMINDER_TERMS)


def is_internal_boundary_item(item: str) -> bool:
    return any(term in item for term in INTERNAL_BOUNDARY_TERMS)


def shooting_reminder_items(validation: ValidationResult) -> list[str]:
    return [
        item for item in concrete_todo_items(validation)
        if not is_release_reminder_item(item) and not is_internal_boundary_item(item)
    ]


def release_reminder_items(validation: ValidationResult) -> list[str]:
    return unique_items([
        *[item for item in concrete_todo_items(validation) if is_release_reminder_item(item)],
        *[
            readable_evidence_item(item) for item in validation.evidence_gaps
            if is_release_reminder_item(readable_evidence_item(item))
        ],
        *validation.fact_check_points,
    ])


def voice_fact_check_items(validation: ValidationResult) -> list[str]:
    return [
        item for item in release_reminder_items(validation)
        if not is_internal_boundary_item(item)
    ]


def production_reminders(validation: ValidationResult) -> list[str]:
    reminders: list[str] = []
    shoot_todos = shooting_reminder_items(validation)
    if shoot_todos:
        reminders.append(f"拍摄提醒：{md_list(shoot_todos)}")
    release_reminders = release_reminder_items(validation)
    if release_reminders:
        reminders.append(f"发布前提醒：{md_list(release_reminders)}")
    return reminders


def real_scene_quality_warnings(topic: dict[str, Any]) -> list[str]:
    scene_sources: list[str] = []
    for key in [
        "production_direction",
        "core_thesis",
        "pain_point",
        "old_workflow",
        "ai_intervention",
        "takeaway_asset",
    ]:
        value = str(topic.get(key) or "").strip()
        if value:
            scene_sources.append(value)
    scene_sources.extend(str(item) for item in topic.get("demo_materials", []) if str(item).strip())
    scene_sources.extend(str(item) for item in topic.get("missing_evidence", []) if str(item).strip())
    scene_text = " ".join(scene_sources)
    if any(term in scene_text for term in REAL_SCENE_TERMS):
        return []
    return ["真实案例/现场不足：会影响像用户程度，建议补“这条准备用哪个真实案例/现场讲”。"]


def full_package_qa(validation: ValidationResult) -> tuple[str, list[str]]:
    blocking_issues: list[str] = []
    if validation.missing_required:
        blocking_issues.append(f"缺字段：{md_list(validation.missing_required)}")
    blocking_issues.extend(blocking_quality_issues(validation))
    reminders = production_reminders(validation)
    if validation.missing_required:
        return "blocked", blocking_issues + reminders
    if blocking_issues:
        return "revise", blocking_issues + reminders
    return "draft", ["草稿，待 PM 验收，待 QA", *reminders]


def render_full_execution_package(topic: dict[str, Any], output_root: Path, run_date: str | None = None) -> dict[str, Any]:
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")
    display_title = topic.get("topic_title") or "未命名选题"
    private_runtime = load_private_runtime()
    private_cases = matched_private_cases(topic, private_runtime)
    template, template_reason = classify_template(topic)
    validation = validate_topic(topic)
    folder = output_root / run_date / f"{slugify(str(topic.get('topic_id', 'topic')))}_{slugify(display_title)}"
    folder.mkdir(parents=True, exist_ok=True)
    document_path = folder / FULL_PACKAGE_FILE
    qa_status, qa_issues = full_package_qa(validation)
    qa_issues = qa_issues + real_scene_quality_warnings(topic)
    outline = full_package_outline(topic, validation)
    rows = execution_package_rows(topic, validation)
    capture_rows = capture_rows_for_full_package(topic, validation, private_cases)
    headers = ["时间", "段落", "真人口播", "画面/录屏", "剪辑重点", "QA"]
    capture_headers = ["素材类型", "需要内容", "用途", "优先级", "状态"]
    research_section = research_markdown(topic)
    research_block = f"\n### 搜索与表达融合\n\n{research_section}\n" if research_section else ""
    write_text(document_path, f"""# {display_title}

## 06 完整口播稿与执行包

### 一屏结论

- 生产判断：{production_recommendation(validation)}
- 推荐模板：{template}
- 核心观点：{trim_end_punctuation(topic.get('core_thesis'), '待确认核心观点')}。
- 开头钩子：{full_script_opening(topic, validation)}
- 拍摄前待办：{inline_items(shooting_reminder_items(validation), '无P0素材缺口', limit=3, item_limit=44)}
- 发布前核验：{inline_items(release_reminder_items(validation), '无额外事实核验点', limit=3, item_limit=48)}
- 本条边界：{inline_items(private_boundaries(private_cases), '不夸大AI能力，不把实验说成已验证结论', limit=2, item_limit=44)}

### 视频结构

{md_numbered(outline)}
{research_block}

### 口播全文

{render_teleprompter(topic, validation)}

### 分段执行方案

| 时间 | 段落 | 真人口播 | 画面/录屏 | 剪辑重点 | QA |
|---|---|---|---|---|---|
{render_table_rows(rows, headers)}

### 录屏与素材清单

| 素材类型 | 需要内容 | 用途 | 优先级 | 状态 |
|---|---|---|---|---|
{render_table_rows(capture_rows, capture_headers)}

### 剪辑交接

- 开场直接给结果或冲突，不讲背景。
- 实操段只保留输入、AI动作、输出、验收四个画面。
- 失败样例和人工修正必须放出来，不要剪成全程顺利。
- 字幕突出判断句、验收线和边界提醒。
- 收尾回到真人判断，不强行卖课或卖工具。

### 发布包草稿

{md_bullets(publish_package_lines(topic))}

### QA

- 结果：{qa_status}
{md_bullets(qa_issues)}
""")
    return {
        "topic_id": topic.get("topic_id"),
        "topic_title": topic.get("topic_title"),
        "output_dir": str(folder),
        "document_path": str(document_path),
        "recommended_template": template,
        "template_reason": template_reason,
        "director_summary": director_summary(topic, template, private_cases),
        "core_thesis": topic.get("core_thesis"),
        "core_viewpoint": core_viewpoint(topic, validation),
        "outline_segments": outline,
        "production_context": generation_input_for_06(topic, template, template_reason, validation, private_cases),
        "opening_hook": full_script_opening(topic, validation),
        "reader_summary": f"{qa_status}｜{template}｜{full_script_opening(topic, validation)}",
        "qa_status": qa_status,
        "qa_issues": qa_issues,
        "quality_warnings": real_scene_quality_warnings(topic),
        "p0_todos": shooting_reminder_items(validation),
        "release_reminders": release_reminder_items(validation),
        "evidence_gaps": validation.evidence_gaps,
        "fact_check_points": validation.fact_check_points,
        "notes": validation.notes,
        "private_case_anchors": [case.get("name", "") for case in private_cases],
        "generated_files": [FULL_PACKAGE_FILE],
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
        summaries.append(render_full_execution_package(topic, output_root=output_root, run_date=run_date))
    return summaries
