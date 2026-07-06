#!/usr/bin/env python3
"""Shared AR-020/AR-026 topic-flow policy helpers.

The helpers in this module organize source governance and candidate evidence.
They intentionally do not write Feishu. Production writes remain in the
existing pipeline scripts and must be explicitly requested there.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POLLUTED_SOURCE_NAMES = {
    "琼玩车",
    "UDG终极梦想车库",
    "潜云说-姚捷",
    "异世界的光某",
    "鲍俞成AI获客",
    "羽森说AI赋能IP",
    "润宇创业笔记",
    "AI短视频工坊",
}

ACTIVE_COMPETITOR_ROLES = {"current_main_competitor", "current_aux_competitor"}
QUARANTINED_ROLE = "quarantined_source"
AIHOT_SOURCE_TYPE = "AIHOT热点"
COMPETITOR_SOURCE_TYPES = {"公众号文章", "对标视频", "competitor_article", "competitor_video"}
AIHOT_IMPORTANCE_WEIGHT = 0.15

MAJOR_AI_HOT_TERMS = [
    "GPT-5",
    "GPT5",
    "Claude 4",
    "Gemini 3",
    "Sora",
    "重大",
    "发布",
    "模型能力",
    "多模态",
    "Agent",
    "智能体",
    "工作流",
    "视频",
    "API",
    "降价",
    "开源",
    "监管",
    "行业变化",
]

DIRECTION_TERMS = {
    "AI业务定调": ["商业", "行业", "战略", "趋势", "监管", "产品发布", "价格", "模型"],
    "真实工作流改造": ["Codex", "Claude Code", "ChatGPT", "Cursor", "飞书", "PPT", "自动化", "工作流", "流程", "Agent", "智能体"],
    "AI导演工作流": ["AIGC", "AI视频", "导演", "分镜", "短片", "短剧", "镜头", "成片", "剪辑"],
    "汽车与内容营销": ["汽车", "品牌", "营销", "素材", "增长", "客户", "信任", "获客"],
    "AI项目复盘": ["复盘", "项目", "上线", "发布", "失败", "成本", "权限", "协作", "产品化"],
}

MARKET_VALIDATION_TERMS = ["播放", "点赞", "评论", "收藏", "爆", "热门", "实战", "教程", "拆解", "清单", "模板"]
AUSTIN_RELEVANCE_TERMS = [
    "AI", "Agent", "智能体", "Codex", "Claude", "ChatGPT", "Cursor", "AIGC", "自动化", "工作流",
    "脚本", "分镜", "PPT", "飞书", "Obsidian", "知识库", "内容", "营销", "品牌", "项目", "复盘",
    "工具", "流程", "视频", "剪辑", "交付", "Prompt", "提示词", "Shell", "CI/CD",
]
STRONG_AUSTIN_RELEVANCE_TERMS = [
    "AI", "Agent", "智能体", "Codex", "Claude", "ChatGPT", "Cursor", "AIGC", "自动化", "工作流",
    "脚本", "分镜", "PPT", "飞书", "Obsidian", "知识库", "项目复盘", "Prompt", "提示词", "Shell", "CI/CD",
]
IRRELEVANT_TO_AUSTIN_TERMS = [
    "招生", "报考", "大专", "中专", "学校", "美食推荐", "体育", "食堂", "旅游攻略", "天气",
    "相亲", "星座", "减肥", "穿搭", "娱乐八卦",
]

GENERIC_TRANSLATION_PATTERNS = [
    "吸收它的选题承诺和结构",
    "转成自己的业务语言",
    "不露出对标账号",
    "不照搬表达",
]

THEME_RULES = [
    {
        "key": "knowledge_base",
        "label": "知识库/内容资产流转",
        "direction": "真实工作流改造",
        "terms": ["知识库", "Obsidian", "RAG", "第二大脑", "双链", "资料沉淀", "素材沉淀", "内容资产"],
        "translation": "转成 Austin 的信息雷达复盘：资料进来以后，能不能从 03 收件箱走到 04 选题、06 脚本和复盘，下次还找得到、用得上。",
        "needed": "补一条素材从收件箱、选题、脚本到复盘的真实路径截图或字段记录。",
        "cluster_note": "这类内容看资料如何变成后续可复用的判断，而不是讲搭库教程。",
    },
    {
        "key": "ai_video_workflow",
        "label": "AI视频/导演交付",
        "direction": "AI导演工作流",
        "terms": ["AI视频", "AIGC", "短剧", "短片", "分镜", "故事板", "成片", "剪辑", "镜头", "Storyboard", "清道夫"],
        "translation": "转成 AI 视频交付现场：脚本、角色、分镜、素材、字幕和返修验收哪些能交给 AI，哪些还必须由人做导演判断。",
        "needed": "补一个自己的视频题目、分镜草稿、素材截图或返修记录。",
        "cluster_note": "这类内容看 AI 是否接住视频交付链路，而不是只看工具效果。",
    },
    {
        "key": "agent_workflow",
        "label": "Agent/自动化任务验收",
        "direction": "真实工作流改造",
        "terms": ["Agent", "智能体", "Claude Code", "Codex", "Cursor", "MCP", "Shell", "CI/CD", "Skill", "自动化任务", "工作流执行"],
        "translation": "转成非技术 Agent 任务验收：旧流程哪一步最卡、工具接住哪一环、输入输出怎么定义、失败时怎么回滚和复核。",
        "needed": "补一个自己的重复任务、执行日志、失败样例或验收清单。",
        "cluster_note": "这类内容看任务边界、执行结果和验收方式，而不是只讲工具技巧。",
    },
    {
        "key": "office_workflow",
        "label": "办公文档/表格交付",
        "direction": "真实工作流改造",
        "terms": ["Excel", "表格", "PPT", "Word", "飞书文档", "飞书表格", "可编辑", "办公"],
        "translation": "转成办公交付现场：原始资料怎么变成可编辑文档、PPT 或表格，人工在哪里复核口径、格式和可交付结果。",
        "needed": "补一份旧文档/表格输入、一版 AI 输出和人工改动前后对比。",
        "cluster_note": "这类内容看办公产物能否进入真实交付，而不是只看生成演示。",
    },
    {
        "key": "business_ai",
        "label": "AI业务定调/增长判断",
        "direction": "AI业务定调",
        "terms": ["商业", "增长", "获客", "品牌", "企业", "营销", "转化", "行业", "客户", "产品化"],
        "translation": "转成业务定调：这条变化到底改变了什么购买理由、信任成本或组织协作，Austin 要用自己的项目判断它是否成立。",
        "needed": "补一个业务场景、客户/团队判断或上线前检查清单。",
        "cluster_note": "这类内容看业务判断是否成立，而不是复述宏观趋势。",
    },
]


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_hash(value: Any, length: int = 12) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def source_name(value: Any) -> str:
    if isinstance(value, dict):
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else value
        return normalize_space(
            fields.get("account_name")
            or fields.get("账号名/公众号名")
            or fields.get("来源名称")
            or fields.get("作者/账号")
            or fields.get("名称")
            or fields.get("account")
        )
    return normalize_space(getattr(value, "account_name", ""))


def source_type(value: Any) -> str:
    if isinstance(value, dict):
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else value
        return normalize_space(fields.get("source_type") or fields.get("来源类型"))
    return normalize_space(getattr(value, "source_type", ""))


def source_role(value: dict[str, Any]) -> str:
    return normalize_space(value.get("source_role") or value.get("source_group") or value.get("来源角色"))


def is_polluted_source_name(name: str) -> bool:
    return normalize_space(name) in POLLUTED_SOURCE_NAMES


def is_quarantined_source(value: Any) -> bool:
    return is_polluted_source_name(source_name(value))


def is_aihot(value: Any) -> bool:
    return source_type(value) == AIHOT_SOURCE_TYPE


def is_competitor_content(value: Any) -> bool:
    return source_type(value) in COMPETITOR_SOURCE_TYPES and not is_quarantined_source(value)


def source_text(value: Any) -> str:
    if isinstance(value, dict):
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else value
        return " ".join(normalize_space(fields.get(key)) for key in [
            "内容标题",
            "原始来源标题",
            "来源内容",
            "摘要/片段",
            "正文/全文",
            "一句话Brief",
            "推荐理由",
            "我的蹭热点角度",
            "业务场景",
            "我要做的实验",
        ])
    return " ".join(normalize_space(getattr(value, key, "")) for key in [
        "title",
        "body_snippet",
        "cover_text",
        "comment_questions",
        "ocr_text",
        "learn_focus",
        "convert_direction",
    ])


def raw_source_text(value: Any) -> str:
    if isinstance(value, dict):
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else value
        return " ".join(normalize_space(fields.get(key)) for key in [
            "内容标题",
            "原始来源标题",
            "来源内容",
            "摘要/片段",
            "正文/全文",
        ])
    return " ".join(normalize_space(getattr(value, key, "")) for key in [
        "title",
        "body_snippet",
        "cover_text",
        "comment_questions",
        "ocr_text",
    ])


def is_irrelevant_to_austin(value: Any) -> bool:
    text = raw_source_text(value)
    if not text:
        return False
    irrelevant_hits = [term for term in IRRELEVANT_TO_AUSTIN_TERMS if term.lower() in text.lower() or term in text]
    if not irrelevant_hits:
        return False
    strong_hits = [term for term in STRONG_AUSTIN_RELEVANCE_TERMS if term.lower() in text.lower() or term in text]
    return len(strong_hits) == 0


def aihot_significance_reason(value: Any) -> str:
    text = source_text(value)
    hits = [term for term in MAJOR_AI_HOT_TERMS if term.lower() in text.lower() or term in text]
    if len(hits) >= 2:
        return f"重大 AI Hot：命中 {', '.join(hits[:4])}，允许凭重大性进入候选判断。"
    if hits and any(term in text for term in ["工作流", "Agent", "智能体", "视频", "API", "行业变化"]):
        return f"AI Hot 可观察：命中 {hits[0]}，但需要证明能落到 Austin 工作流。"
    return "普通 AI Hot：只作为低权重热点观察，不应压过高适配对标内容。"


def is_major_aihot(value: Any) -> bool:
    reason = aihot_significance_reason(value)
    return reason.startswith("重大 AI Hot")


def source_influence_weight(value: Any) -> float:
    if is_quarantined_source(value):
        return 0.0
    if is_aihot(value):
        return AIHOT_IMPORTANCE_WEIGHT
    if is_competitor_content(value):
        return 1.0
    return 0.45


def mapped_direction(value: Any, fallback: str = "") -> str:
    text = source_text(value)
    scores: dict[str, int] = {}
    for direction, terms in DIRECTION_TERMS.items():
        scores[direction] = sum(1 for term in terms if term.lower() in text.lower() or term in text)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else fallback


def contains_term(text: str, term: str) -> bool:
    return term.lower() in text.lower() or term in text


def source_theme(value: Any) -> dict[str, str]:
    text = source_text(value)
    best: dict[str, str] | None = None
    best_score = 0
    for rule in THEME_RULES:
        score = sum(1 for term in rule["terms"] if contains_term(text, term))
        if score > best_score:
            best_score = score
            best = rule
    if best:
        return {
            "key": best["key"],
            "label": best["label"],
            "direction": best["direction"],
            "translation": best["translation"],
            "needed": best["needed"],
            "cluster_note": best["cluster_note"],
            "quality": "具体可转译",
            "quality_reason": f"原始来源命中 {best['label']} 证据，可落到 Austin 的真实场景。",
        }
    return {
        "key": "needs_evidence",
        "label": "待补证据",
        "direction": "真实工作流改造",
        "translation": "",
        "needed": "补一个 Austin 自己的案例、工具截图或工作流前后对比。",
        "cluster_note": "当前来源还缺足够具体的 Austin 使用现场。",
        "quality": "证据不足",
        "quality_reason": "原始来源没有明显命中知识库、Agent、AI视频、办公交付或业务定调场景。",
    }


def angle_conflicts_with_theme(angle: str, theme: dict[str, str]) -> bool:
    if not angle or theme["key"] == "needs_evidence":
        return False
    if theme["key"] == "knowledge_base" and any(term in angle for term in ["表格", "Excel", "PPT", "运营表格"]):
        return True
    if theme["key"] == "ai_video_workflow" and any(term in angle for term in ["知识库", "表格", "Excel"]):
        return True
    if theme["key"] == "office_workflow" and any(term in angle for term in ["知识库", "分镜", "短剧"]):
        return True
    return False


def title_conflicts_with_theme(title: str, theme: dict[str, str]) -> bool:
    if not title or theme["key"] == "needs_evidence":
        return False
    if theme["key"] == "knowledge_base" and any(term in title for term in ["Excel", "表格", "AI视频", "分镜", "导演"]):
        return True
    if theme["key"] == "ai_video_workflow" and any(term in title for term in ["知识库", "表格", "Excel"]):
        return True
    if theme["key"] == "office_workflow" and any(term in title for term in ["知识库", "分镜", "短剧"]):
        return True
    return False


def is_generic_translation(text: str) -> bool:
    compact = normalize_space(text)
    return any(pattern in compact for pattern in GENERIC_TRANSLATION_PATTERNS)


def concrete_translation_angle(topic: dict[str, Any], item: Any, theme: dict[str, str]) -> str:
    candidate = normalize_space(topic.get("我的蹭热点角度") or topic.get("推荐理由") or "")
    if candidate and not is_generic_translation(candidate) and not angle_conflicts_with_theme(candidate, theme):
        return candidate
    if theme["translation"]:
        return theme["translation"]
    title = normalize_space(topic.get("来源内容") or topic.get("原始来源标题") or getattr(item, "title", ""))
    return f"先暂存观察：目前只能看出《{title[:36]}》可能和 Austin 工作流有关，但还缺具体案例、工具或流程证据。"


def theme_topic_title(topic: dict[str, Any], item: Any, theme: dict[str, str]) -> str:
    original = normalize_space(topic.get("来源内容") or topic.get("原始来源标题") or getattr(item, "title", ""))
    anchor = re.split(r"[，,。#｜|]", original)[0][:24] or "这条来源"
    if theme["key"] == "knowledge_base":
        return f"{anchor}，真正值得看的是资料能不能变成后面能用的内容资产"
    if theme["key"] == "ai_video_workflow":
        return f"{anchor}，真正值得看的是 AI 能不能接住视频交付"
    if theme["key"] == "agent_workflow":
        return f"{anchor}，真正值得看的是任务边界和结果验收"
    if theme["key"] == "office_workflow":
        return f"{anchor}，真正值得看的是文档表格能不能进入交付"
    if theme["key"] == "business_ai":
        return f"{anchor}，真正值得看的是业务判断能不能落到真实项目"
    return normalize_space(topic.get("我的选题标题") or topic.get("选题命题") or anchor)


def align_topic_visible_fields(topic: dict[str, Any], item: Any, translation: dict[str, str]) -> None:
    theme = source_theme(item)
    if theme["key"] == "needs_evidence":
        return
    current_title = normalize_space(topic.get("我的选题标题") or topic.get("选题命题"))
    if title_conflicts_with_theme(current_title, theme):
        fixed_title = theme_topic_title(topic, item, theme)
        topic["我的选题标题"] = fixed_title
        topic["选题命题"] = fixed_title
        topic["内部切入角度"] = fixed_title
        if "一句话Brief" in topic:
            topic["一句话Brief"] = f"{fixed_title}：重点不是复述来源，而是验证它能不能落到 Austin 的真实流程。"
    topic["对应方向"] = translation["Austin映射方向"]
    topic["对应栏目"] = translation["Austin映射方向"]
    for key in ["我的蹭热点角度", "我的思考点", "我能讲出的独特角度"]:
        if key in topic and (not topic.get(key) or is_generic_translation(topic.get(key, "")) or angle_conflicts_with_theme(str(topic.get(key, "")), theme)):
            topic[key] = translation["Austin转译角度"]
    if topic.get("推荐理由") and (is_generic_translation(topic["推荐理由"]) or angle_conflicts_with_theme(topic["推荐理由"], theme)):
        topic["推荐理由"] = translation["Austin转译角度"]


def account_translation_fields(topic: dict[str, Any], item: Any) -> dict[str, str]:
    theme = source_theme(item)
    detected_direction = theme["direction"] if theme["key"] != "needs_evidence" else mapped_direction(item, "真实工作流改造")
    direction = normalize_space(detected_direction or topic.get("对应方向") or topic.get("对应栏目") or "真实工作流改造")
    original = normalize_space(topic.get("来源内容") or topic.get("原始来源标题") or getattr(item, "title", ""))
    text = source_text(item)
    validation_hits = [term for term in MARKET_VALIDATION_TERMS if term in text]
    source_label = source_name(item)
    validation = (
        f"{source_label} 属于有效对标来源，内容已有账号/平台验证；"
        + (f"文本命中 {', '.join(validation_hits[:3])}。" if validation_hits else "需要在发布前补播放/互动或同类账号证据。")
    )
    angle = concrete_translation_angle(topic, item, theme)
    needed = normalize_space(topic.get("需要补的证据") or theme["needed"])
    return {
        "原始来源账号": source_label,
        "原始来源标题": original,
        "市场验证依据": validation,
        "Austin映射方向": direction,
        "Austin转译角度": angle,
        "需要补的案例/工具/工作流": needed,
        "Austin转译质量": theme["quality"],
        "Austin转译质量原因": theme["quality_reason"],
        "主题簇": theme["label"],
        "主题簇说明": theme["cluster_note"],
    }


def aihot_translation_fields(topic: dict[str, Any], item: Any) -> dict[str, str]:
    theme = source_theme(item)
    reason = aihot_significance_reason(item)
    direction = theme["direction"] if theme["key"] != "needs_evidence" else mapped_direction(item, normalize_space(topic.get("对应方向") or topic.get("对应栏目") or "AI业务定调"))
    if is_major_aihot(item):
        angle = theme["translation"] or "作为重大 AI Hot 观察：先判断它改变了什么工作流、接口、成本或行业默认动作，再决定 Austin 是否有自己的项目案例可以讲。"
        quality = "具体可转译" if theme["key"] != "needs_evidence" else "需补重大性落地证据"
    else:
        angle = "普通 AI Hot 只保留观察：除非补到 Austin 的真实工作流影响或重大行业变化，否则不应压过高适配对标内容。"
        quality = "低权重观察"
    return {
        "Austin映射方向": direction,
        "Austin转译角度": angle,
        "对标转译角度": angle,
        "Austin转译质量": quality,
        "Austin转译质量原因": reason,
        "主题簇": theme["label"] if theme["key"] != "needs_evidence" else "AI Hot 观察",
        "主题簇说明": theme["cluster_note"] if theme["key"] != "needs_evidence" else "只凭热点摘要还不能证明适合 Austin，需要重大性和工作流影响。",
        "需要补的案例/工具/工作流": normalize_space(topic.get("需要补的证据") or "补官方来源、产品事实和 Austin 自己的落地影响判断。"),
    }


def source_weight_label(value: Any) -> str:
    if is_quarantined_source(value):
        return "污染来源隔离"
    if is_aihot(value):
        return "AI Hot 低权重热点源"
    if is_competitor_content(value):
        return "有效对标账号核心源"
    return "其他辅助源"


def enrich_topic_record(topic: dict[str, Any], item: Any) -> dict[str, Any]:
    weight = source_influence_weight(item)
    topic["来源权重类型"] = source_weight_label(item)
    topic["来源影响权重"] = f"{weight:.2f}"
    topic["来源构成"] = f"{topic.get('来源类型', source_type(item))} / {source_name(item) or '未知来源'}"
    if is_aihot(item):
        topic["AIHOT重大性说明"] = aihot_significance_reason(item)
        topic.update(aihot_translation_fields(topic, item))
    elif is_competitor_content(item):
        translation = account_translation_fields(topic, item)
        topic.update(translation)
        align_topic_visible_fields(topic, item, translation)
        topic["对标转译角度"] = translation["Austin转译角度"]
        topic["AIHOT重大性说明"] = ""
    else:
        topic["AIHOT重大性说明"] = ""
        topic["对标转译角度"] = normalize_space(topic.get("我的蹭热点角度") or topic.get("推荐理由"))
        theme = source_theme(item)
        topic.setdefault("Austin映射方向", theme["direction"])
        topic.setdefault("Austin转译角度", topic["对标转译角度"])
        topic.setdefault("Austin转译质量", theme["quality"])
        topic.setdefault("Austin转译质量原因", theme["quality_reason"])
        topic.setdefault("主题簇", theme["label"])
        topic.setdefault("主题簇说明", theme["cluster_note"])
    return topic


def source_composition(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter: collections.Counter[str] = collections.Counter()
    for row in rows:
        counter[normalize_space(row.get("来源权重类型") or row.get("来源类型") or "未知来源")] += 1
    return dict(counter)


@dataclass
class ReverseEvaluationRow:
    content_fingerprint: str
    source_type: str
    source_name: str
    source_title: str
    editor_score: int
    persona_score: int
    selected: bool
    potentially_better: bool
    reason: str


def int_cell(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except ValueError:
        return 0


def reviewable_rejection_reason(row: dict[str, Any], selected_floor: int, selected_count: int, max_selected: int) -> str:
    explicit = normalize_space(row.get("不建议做的原因") or row.get("降级原因") or row.get("推荐动作原因"))
    if explicit and explicit != "暂无明显不做理由。":
        return explicit
    reasons: list[str] = []
    if is_irrelevant_to_austin(row):
        reasons.append("原始来源主题明显偏离 Austin 账号方向")
    if normalize_space(row.get("是否有足够内容支撑")) == "不足":
        reasons.append("内容支撑不足")
    if normalize_space(row.get("AI味风险")) == "高":
        reasons.append("AI味风险高")
    if int_cell(row.get("编辑判断分")) < selected_floor:
        reasons.append(f"编辑判断分低于本轮已选最低分 {selected_floor}")
    if selected_count >= max_selected:
        reasons.append(f"候选池上限 {max_selected} 导致排序截断")
    if not reasons:
        reasons.append("需要人工复核：高适配对标内容未入选，当前规则未给出足够理由")
    return "；".join(reasons)


def merged_into_selected_reason(row: dict[str, Any], selected: list[dict[str, Any]]) -> str:
    title = normalize_space(row.get("来源内容") or row.get("原始来源标题") or row.get("我的选题标题"))
    if not title:
        return ""
    compact_title = re.sub(r"\s+", "", title)
    for selected_row in selected:
        related = normalize_space(selected_row.get("相关来源"))
        if compact_title and compact_title in re.sub(r"\s+", "", related):
            selected_title = normalize_space(selected_row.get("我的选题标题") or selected_row.get("来源内容"))
            return f"同主题已合并到已选候选：{selected_title[:80]}"
    return ""


def reverse_evaluation_rows(
    selected: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    item_by_fp: dict[str, Any],
    *,
    max_selected: int = 36,
) -> list[ReverseEvaluationRow]:
    selected_fps = {normalize_space(row.get("内容指纹")) for row in selected}
    selected_floor = min([int_cell(row.get("编辑判断分")) for row in selected] or [0])
    selected_count = len(selected)
    rows: list[ReverseEvaluationRow] = []
    for row in candidates:
        fp = normalize_space(row.get("内容指纹"))
        item = item_by_fp.get(fp)
        if not item:
            continue
        editor = int_cell(row.get("编辑判断分"))
        persona = int_cell(row.get("人设匹配分"))
        selected_flag = fp in selected_fps
        merged_reason = "" if selected_flag else merged_into_selected_reason(row, selected)
        potentially_better = (
            not selected_flag
            and not merged_reason
            and not is_irrelevant_to_austin(row)
            and is_competitor_content(item)
            and editor >= max(70, selected_floor)
            and persona >= 60
            and row.get("AI味风险") != "高"
        )
        reason = merged_reason or row.get("不建议做的原因") or row.get("降级原因") or row.get("推荐动作原因") or "未给出明确未选原因"
        if not selected_flag and not merged_reason and is_irrelevant_to_austin(row):
            reason = "原始来源主题明显偏离 Austin 账号方向。"
        if potentially_better:
            reason = reviewable_rejection_reason(
                row,
                selected_floor,
                selected_count,
                max_selected,
            )
        rows.append(ReverseEvaluationRow(
            content_fingerprint=fp,
            source_type=source_type(item),
            source_name=source_name(item),
            source_title=normalize_space(getattr(item, "title", "") or row.get("来源内容")),
            editor_score=editor,
            persona_score=persona,
            selected=selected_flag,
            potentially_better=potentially_better,
            reason=reason,
        ))
    return sorted(rows, key=lambda row: (row.selected, row.potentially_better, row.editor_score, row.persona_score), reverse=True)


def write_reverse_evaluation(path: Path, rows: list[ReverseEvaluationRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "内容指纹",
            "来源类型",
            "来源名称",
            "原始来源标题",
            "编辑判断分",
            "人设匹配分",
            "是否已选",
            "是否疑似更适合Austin但未选",
            "未选/保留理由",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "内容指纹": row.content_fingerprint,
                "来源类型": row.source_type,
                "来源名称": row.source_name,
                "原始来源标题": row.source_title,
                "编辑判断分": row.editor_score,
                "人设匹配分": row.persona_score,
                "是否已选": "是" if row.selected else "否",
                "是否疑似更适合Austin但未选": "是" if row.potentially_better else "否",
                "未选/保留理由": row.reason,
            })


def load_json_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_governance_plan(sources: list[dict[str, Any]]) -> dict[str, Any]:
    active = []
    quarantined = []
    dry_run_actions = []
    for source in sources:
        name = source_name(source)
        role = source_role(source)
        enabled = bool(source.get("default_enabled", True))
        sampling = bool(source.get("participates_main_sampling", True))
        row = {
            "id": source.get("id", ""),
            "name": name,
            "platform": source.get("platform", ""),
            "role": role,
            "enabled": enabled,
            "participates_main_sampling": sampling,
            "homepage_url_present": bool(source.get("url")),
        }
        if is_polluted_source_name(name):
            quarantined.append(row)
            dry_run_actions.append({
                "action": "quarantine_source",
                "name": name,
                "reason": "用户确认的截图污染来源；未来采集和候选链路隔离，历史 03 不动。",
                "would_set": {
                    "source_role": QUARANTINED_ROLE,
                    "default_enabled": False,
                    "participates_main_sampling": False,
                },
            })
        elif role in ACTIVE_COMPETITOR_ROLES and enabled and sampling:
            active.append(row)
    return {
        "active_competitor_accounts": active,
        "active_competitor_count": len(active),
        "polluted_matches": quarantined,
        "polluted_match_count": len(quarantined),
        "dry_run_actions": dry_run_actions,
        "dry_run_only": True,
    }


def collection_coverage_report(sources: list[dict[str, Any]], probe_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    plan = source_governance_plan(sources)
    active_names = [row["name"] for row in plan["active_competitor_accounts"] if row["name"]]
    probe_rows = probe_rows or []
    by_name = {normalize_space(row.get("account_name") or row.get("name")): row for row in probe_rows}
    attempted = [name for name in active_names if name in by_name]
    success = [name for name in attempted if normalize_space(by_name[name].get("status")) == "success"]
    failures = [
        {
            "account_name": name,
            "status": by_name[name].get("status", "not_attempted") if name in by_name else "not_attempted",
            "failure_reason": by_name[name].get("failure_reason", "") if name in by_name else "未在探针结果中出现",
        }
        for name in active_names
        if name not in success
    ]
    artifacts = {name: artifact_count(by_name[name]) for name in attempted}
    return {
        "planned_account_count": len(active_names),
        "planned_accounts": active_names,
        "attempted_account_count": len(attempted),
        "successful_account_count": len(success),
        "failed_accounts": failures,
        "per_account_artifact_counts": artifacts,
        "polluted_source_count": plan["polluted_match_count"],
        "polluted_sources": [row["name"] for row in plan["polluted_matches"]],
        "future_batching": {
            "enabled_now": False,
            "reason": "当前几十个账号先全量覆盖；保留按账号列表切片/频控的报告结构。",
        },
    }


def artifact_count(row: dict[str, Any]) -> int:
    for key in ["resolved_items", "artifact_count", "content_items"]:
        value = row.get(key)
        if value is not None and value != "":
            try:
                return int(float(str(value)))
            except ValueError:
                pass
    links = row.get("video_links") or row.get("artifacts") or row.get("items")
    if isinstance(links, list):
        return len(links)
    if isinstance(links, str):
        text = links.strip()
        if not text:
            return 0
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return 1 if text.startswith(("http://", "https://")) else 0
        if isinstance(parsed, list):
            return len(parsed)
        if isinstance(parsed, dict):
            return len(parsed)
        return 0
    if isinstance(links, dict):
        return len(links)
    return 0
