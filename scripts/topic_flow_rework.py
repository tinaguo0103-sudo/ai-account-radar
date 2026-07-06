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


def account_translation_fields(topic: dict[str, Any], item: Any) -> dict[str, str]:
    direction = normalize_space(topic.get("对应方向") or topic.get("对应栏目") or mapped_direction(item, "真实工作流改造"))
    original = normalize_space(topic.get("来源内容") or topic.get("原始来源标题") or getattr(item, "title", ""))
    text = source_text(item)
    validation_hits = [term for term in MARKET_VALIDATION_TERMS if term in text]
    source_label = source_name(item)
    validation = (
        f"{source_label} 属于有效对标来源，内容已有账号/平台验证；"
        + (f"文本命中 {', '.join(validation_hits[:3])}。" if validation_hits else "需要在发布前补播放/互动或同类账号证据。")
    )
    angle = normalize_space(topic.get("我的蹭热点角度") or topic.get("推荐理由") or "")
    needed = normalize_space(topic.get("需要补的证据") or "补一个 Austin 自己的案例、工具截图或工作流前后对比。")
    return {
        "原始来源账号": source_label,
        "原始来源标题": original,
        "市场验证依据": validation,
        "Austin映射方向": direction,
        "Austin转译角度": angle or f"把原内容转成 Austin 自己的 {direction} 现场，不复述原作者。",
        "需要补的案例/工具/工作流": needed,
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
        topic["对标转译角度"] = ""
    elif is_competitor_content(item):
        translation = account_translation_fields(topic, item)
        topic.update(translation)
        topic["对标转译角度"] = translation["Austin转译角度"]
        topic["AIHOT重大性说明"] = ""
    else:
        topic["AIHOT重大性说明"] = ""
        topic["对标转译角度"] = normalize_space(topic.get("我的蹭热点角度") or topic.get("推荐理由"))
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
