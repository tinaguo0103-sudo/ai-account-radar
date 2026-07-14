#!/usr/bin/env python3
"""AR-020D editorial schemas, validators, and current-task stage applicators.

This module never starts a model and contains no alternate editorial engine.
The outer Codex task authors stage payloads; Python validates ownership and
fails closed before business output when a stage is missing or inconsistent.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import topic_field_contract as field_contract
from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
REPO_SKILL_DIR = ROOT / "skills" / "ai-account-editorial-director"
GLOBAL_SKILL_DIR = Path.home() / ".codex" / "skills" / "ai-account-editorial-director"
SKILL_DIR = REPO_SKILL_DIR
SKILL_MD = SKILL_DIR / "SKILL.md"
RUNNER_VERSION = "ar020d_current_task_state_machine_v1"
CANONICAL_DECISIONS = {"select", "observe", "reject"}
RECOMMENDATION_STATUSES = {"生成脚本包", "补证据", "存素材", "观察", "不做"}
GLOBAL_DAILY_LEVELS = {"推荐制作", "暂存观察", "不建议制作"}


def skill_reference_dirs() -> list[Path]:
    return [SKILL_DIR]


def skill_reference_path(name: str) -> Path:
    for directory in skill_reference_dirs():
        candidate = directory / "references" / name
        if candidate.exists():
            return candidate
    return SKILL_DIR / "references" / name


SKILL_REFERENCE = skill_reference_path("persona-and-cases.md")
SKILL_PERSONA_BRIEF = skill_reference_path("persona-brief.md")
APPROVED_SELECTION_LEARNING_MD = ROOT / "output" / "selection_learning" / "approved_selection_learning.md"

EXTRA_FIELDS = [
    "editorial_architecture",
    "editorial_decision_json",
    "editorial_decision_id",
    "editorial_decision_hash",
    "global_rank_id",
    "global_rank_hash",
    "locked_decision",
    "locked_recommendation_status",
    "locked_daily_level",
    "locked_should_produce",
    "locked_title_permission",
    "locked_global_rank_position",
    "locked_global_tradeoff_reason",
    "stage2_invariant_status",
    "stage2_invariant_issues",
    "raw_stage2_owner_fields_json",
    "raw_stage2_payload_json",
    "raw_stage2_drift_status",
    "raw_stage2_drift_issues",
    "guard_blocked",
    "guard_blocked_reason",
    "global_ranking_json",
    "persona_style_reference_state",
    "persona_style_hash",
    "主编筛选",
    "主编自由稿",
    "标题工作坊",
    "标题自审",
    "editorial_thinking_json",
    "field_mapping_json",
    "主编判断摘要",
    "标题思路",
    "原始标题钩子",
    "Austin改写理由",
    "标题体感风险",
    "title_pattern_family",
    "title_quality_status",
    "title_quality_issues",
    "hint_leak_risk",
    "点击钩子",
    "观众为什么会点",
    "title_permission",
    "我的真实矛盾",
    "选题命题",
    "我要做的实验",
    "热点触发点",
    "我的工作流痛点",
    "选题判断",
    "原始钩子",
    "我的切入",
    "我准备怎么讲",
    "可展示证据",
    "热点钩子",
    "普通人会怎么讲",
    "我会怎么讲",
    "场景依据",
    "真实/相邻案例",
    "我的改造动作",
    "需要补的证据",
    "关联母场景",
    "借用方式",
    "不能声称的部分",
    "我的真实/相邻场景",
    "候选状态",
    "推荐等级",
    "对应方向",
    "一句话Brief",
    "我的场景拆解",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "我的思考点",
    "重点体现",
    "可调用案例",
    "内容核心冲突",
    "视频呈现方式",
    "证据强度",
    "Skill编辑层",
    "Skill参考文件",
    "field_contract_status",
    "field_contract_issues",
    "field_contract_owner",
]

SKILL_FIELDS = [
    "主编筛选",
    "主编自由稿",
    "editorial_thinking_json",
    "field_mapping_json",
    "主编判断摘要",
    "标题思路",
    "原始标题钩子",
    "Austin改写理由",
    "标题体感风险",
    "点击钩子",
    "观众为什么会点",
    "title_permission",
    "我的真实矛盾",
    "选题命题",
    "我要做的实验",
    "热点触发点",
    "我的工作流痛点",
    "选题判断",
    "原始钩子",
    "我的切入",
    "我准备怎么讲",
    "可展示证据",
    "热点钩子",
    "普通人会怎么讲",
    "我会怎么讲",
    "场景依据",
    "真实/相邻案例",
    "我的改造动作",
    "需要补的证据",
    "关联母场景",
    "借用方式",
    "不能声称的部分",
    "我的真实/相邻场景",
    "候选状态",
    "推荐等级",
    "可发布标题",
    "标题备选",
    "对应方向",
    "一句话Brief",
    "我的场景拆解",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "我的思考点",
    "重点体现",
    "可调用案例",
    "内容核心冲突",
    "视频呈现方式",
    "证据强度",
    "推荐动作",
    "不建议做的原因",
    "推荐理由",
    "主编判断",
    "今日建议级别",
    "是否建议进入制作",
    "编辑判断分",
    "标题质量分",
    "AI味风险",
]

STAGE2_OWNER_FIELDS = {
    "主编筛选",
    "主编自由稿",
    "editorial_thinking_json",
    "主编判断摘要",
    "标题思路",
    "选题命题",
    "我的选题标题",
    "选题标题",
    "可发布标题",
    "标题备选",
    "候选状态",
    "推荐等级",
    "推荐动作",
    "今日建议级别",
    "是否建议进入制作",
    "title_permission",
    "选题判断",
    "推荐理由",
    "主编判断",
    "我的切入",
    "我准备怎么讲",
    "我会怎么讲",
}

STAGE2_OPERATIONAL_FIELDS = [field for field in SKILL_FIELDS if field not in STAGE2_OWNER_FIELDS]

# AR-020D owns these surfaces through Stage 1 or the operational mapper. Values
# inherited from the pre-Skill deterministic candidate builder are stale authoring,
# not source facts, and must not leak back into the final row.
AR020D_LEGACY_CREATIVE_FIELDS = {
    "标题备选", "内部切入角度", "为什么今天值得做", "我的账号为什么能讲",
    "我能讲出的独特角度", "真实用户问题", "可展示结果", "推荐理由",
    "普通AI资讯号会怎么讲", "我的蹭热点角度", "Austin映射方向",
    "Austin转译角度", "对标转译角度", "主题簇", "主题簇说明",
    "关联母场景", "真实/相邻案例", "可调用案例", "我的真实/相邻场景",
    "field_contract_status", "field_contract_issues", "guard_blocked", "guard_blocked_reason",
    "主编判断", "主编筛选", "主编自由稿", "标题工作坊", "标题自审",
    "推荐动作原因", "降级原因", "模板词命中情况", "标题是否过度内部化",
    "标题改写原因", "推荐分", "标题结构模板", "标题生成规则",
    "是否只是资讯搬运", "是否有足够内容支撑", "为什么今天值得做",
}

STAGE2_REQUIRED_OPERATIONAL_FIELDS = [
    "field_mapping_json",
    "我要做的实验",
    "我的工作流痛点",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "对应方向",
    "一句话Brief",
    "我的场景拆解",
    "我的思考点",
    "重点体现",
    "可展示证据",
    "内容核心冲突",
    "视频呈现方式",
    "证据强度",
    "需要补的证据",
    "不建议做的原因",
]

STAGE2_RAW_OWNER_EXPECTATIONS = {
    "主编筛选": "locked_decision",
    "主编自由稿": "public_decision_summary",
    "主编判断摘要": "public_decision_summary",
    "标题思路": "title_rationale",
    "选题命题": "selected_visible_title",
    "我的选题标题": "selected_visible_title",
    "选题标题": "selected_visible_title",
    "候选状态": "locked_daily_level",
    "推荐等级": "locked_daily_level",
    "推荐动作": "locked_recommendation_status",
    "今日建议级别": "locked_daily_level",
    "是否建议进入制作": "locked_should_produce",
    "title_permission": "locked_title_permission",
}

EDITORIAL_DECISION_FIELDS = [
    "research_dossier_hash",
    "research_evidence_ids",
    "audience_hook",
    "hook_evidence_ids",
    "source_read",
    "research_confidence",
    "decision",
    "why_i_would_choose",
    "why_i_would_not_choose",
    "rejected_common_take",
    "natural_austin_angle",
    "title_directions",
    "selected_visible_title",
    "title_rationale",
    "source_title_hook",
    "source_hook_usage",
    "recommendation_status",
    "near_miss_reason",
    "public_decision_summary",
    "proposed_content_structure",
    "state_or_gap",
    "hook_first_rationale",
    "hard_fact_usage",
    "fact_boundary_note",
    "editorial_expression_mode",
]

NON_AUTHORITATIVE_HINT_FIELDS = {
    "对标转译角度",
    "Austin映射方向",
    "Austin转译角度",
    "Austin转译质量",
    "Austin转译质量原因",
    "主题簇",
    "主题簇说明",
    "需要补的案例/工具/工作流",
    "内部切入角度",
    "我的蹭热点角度",
    "我能讲出的独特角度",
    "推荐理由",
}

EXISTING_VISIBLE_FIELD_FIELDS = {
    "我的选题标题",
    "选题命题",
    "我要做的实验",
    "我的工作流痛点",
    "可发布标题",
    "旧流程痛点",
    "AI介入点",
    "可展示结果",
    "可沉淀资产",
    "推荐动作",
    "今日建议级别",
}

STAGE1_FORBIDDEN_SOURCE_FIELDS = set(EXISTING_VISIBLE_FIELD_FIELDS) | NON_AUTHORITATIVE_HINT_FIELDS | {
    "关联母场景",
    "借用方式",
    "不能声称的部分",
    "我的真实/相邻场景",
    "关联母场景候选",
    "我要做的实验",
    "验证方式",
    "可沉淀资产",
    "我的工作流痛点",
    "旧流程痛点",
    "AI介入点",
    "可发布标题",
    "选题命题",
    "我的选题标题",
    "选题标题",
    "标题思路",
    "主编判断摘要",
    "real_tension",
}

CANDIDATE_CONTEXT_FIELDS = [
    "我的选题标题",
    "选题命题",
    "我要做的实验",
    "热点触发点",
    "我的工作流痛点",
    "可发布标题",
    "内部切入角度",
    "来源内容",
    "来源类型",
    "原始来源标题",
    "来源链接",
    "对应栏目",
    "热点切入方式",
    "业务场景",
    "旧流程痛点",
    "AI介入点",
    "可展示结果",
    "可沉淀资产",
    "推荐理由",
    "推荐动作",
    "推荐分",
    "内容可信度",
    "是否有足够内容支撑",
    "真实用户问题",
    "为什么今天值得做",
    "我能讲出的独特角度",
    "我的账号为什么能讲",
    "是否只是资讯搬运",
    "不建议做的原因",
    "人设匹配分",
    "编辑判断分",
    "标题质量分",
    "AI味风险",
    "今日建议级别",
    "相关来源",
    "事件锚点",
    "业务变化判断",
    "候选来源方式",
    "内容指纹",
    "来源权重类型",
    "来源影响权重",
    "来源构成",
    "原始来源账号",
    "AIHOT重大性说明",
    "对标转译角度",
    "Austin映射方向",
    "Austin转译角度",
    "Austin转译质量",
    "Austin转译质量原因",
    "主题簇",
    "主题簇说明",
    "市场验证依据",
    "需要补的案例/工具/工作流",
]

VISIBLE_TEXT_REPLACEMENTS = {
    "用户当前正在": "我现在正在",
    "用户当前": "我现在",
    "用户自己的": "我自己的",
    "用户真实": "我的真实",
    "用户作为": "我作为",
    "适合用户": "适合我",
    "帮助用户": "帮我",
    "用户可以": "我可以",
    "用户会": "我会",
    "用户要": "我要",
    "这条内容资产流": "这套内容资产流",
    "这条内容": "这个选题",
    "这条视频": "这个视频钩子",
    "业务动作": "具体改造点",
    "可执行动作": "具体改造点",
    "业务验收清单": "项目验收记录",
    "自查表": "检查表",
    "少做一小时": "少掉一轮人工返修",
    "这类更新": "这次变化",
    "先看任务怎么验收": "先看它能不能留下验收记录",
    "该先判断": "要先判断",
    "最该重排": "真正要改掉",
}

def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def contains_chinese(value: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


def short_sentence(value: Any, limit: int = 120) -> str:
    text = normalize_space(value)
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def source_title_values(row: dict[str, str]) -> list[str]:
    values: list[str] = []
    for field in ["原始来源标题", "来源内容", "来源标题"]:
        value = (row.get(field, "") or "").strip()
        if value:
            values.append(value)
    return values


def clean_source_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u200b", " ").replace("\ufeff", " ")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s*#[^\s#]+", "", text)
    return re.sub(r"\s+", " ", text).strip(" ，,。；;：:|-")


def extract_original_title(value: Any) -> str:
    text = clean_source_text(value)
    if not text:
        return ""
    first_sentence = re.split(r"[。！？!?]\s*", text, maxsplit=1)[0].strip()
    if 8 <= len(first_sentence) <= 56:
        return first_sentence
    first_chunk = first_sentence.split(" ", 1)[0].strip()
    if 8 <= len(first_chunk) <= 42:
        return first_chunk
    source = first_sentence or text
    return source[:56].rstrip(" ，,。；;：:") + ("..." if len(source) > 56 else "")


def original_title_hook_from(row: dict[str, str]) -> str:
    title = extract_original_title(row.get("原始来源标题"))
    if not title:
        return ""
    hook_terms: list[str] = []
    if any(term in title for term in ["Codex", "Obsidian", "PPT", "Mx-Shell", "Skill", "Claude", "Agent", "MIRA"]):
        hook_terms.append("工具组合")
    if any(term in title for term in ["知识库", "可编辑", "一键", "简单", "无需", "开放公测", "联动", "搭建"]):
        hook_terms.append("结果承诺")
    if any(term in title for term in ["教程", "实战", "手把手", "5步", "必备"]):
        hook_terms.append("学习入口")
    label = " / ".join(hook_terms) if hook_terms else "来源表达"
    return f"{label}：{title}"


def sanitize_visible_language(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    fields = [
        "选题命题", "我要做的实验", "热点触发点", "我的工作流痛点", "我的真实矛盾", "选题判断", "原始钩子", "我的切入", "我准备怎么讲", "可展示证据",
        "推荐理由", "主编判断", "一句话Brief", "我的场景拆解", "我的思考点", "重点体现",
        "旧流程痛点", "AI介入点", "验证方式", "可发布标题", "标题备选", "我的选题标题", "选题标题", "内部切入角度",
        "主编判断摘要", "标题思路",
    ]
    for field in fields:
        value = out.get(field, "")
        if not value:
            continue
        for old, new in VISIBLE_TEXT_REPLACEMENTS.items():
            value = value.replace(old, new)
        out[field] = value
    return out


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_provenance() -> dict[str, Any]:
    """Describe the exact editorial runtime without exposing private text."""
    return {
        "runner_version": RUNNER_VERSION,
        "stage_architecture": "editorial_decision_then_field_mapping",
        "skill_dir": str(SKILL_DIR),
        "skill_md_path": str(SKILL_MD),
        "skill_md_sha256": file_sha256(SKILL_MD),
        "repo_mirror_skill_path": str(REPO_SKILL_DIR / "SKILL.md"),
        "repo_mirror_skill_sha256": file_sha256(REPO_SKILL_DIR / "SKILL.md"),
        "persona_brief_path": str(SKILL_PERSONA_BRIEF),
        "persona_brief_sha256": file_sha256(SKILL_PERSONA_BRIEF),
        "persona_style_path": str(SKILL_REFERENCE),
        "persona_style_sha256": file_sha256(SKILL_REFERENCE),
        "persona_style_embedded": SKILL_REFERENCE.exists(),
        "persona_style_reference_only": True,
        "persona_style_role": "style_reference_only_not_source_evidence",
        "strict_fail_closed": True,
        "prohibited_path_count": 0,
    }


def safe_source_facts(row: dict[str, str]) -> dict[str, str]:
    source_title = row.get("原始来源标题") or ""
    source_title_hook = row.get("原始标题钩子") or ""
    return {
        "source_title": source_title,
        "source_title_hook": source_title_hook,
        "source_excerpt": short_sentence(row.get("来源内容") or "", 360),
        "source_account": row.get("原始来源账号") or row.get("账号名/公众号名") or "",
        "source_link": row.get("来源链接") or "",
        "source_type": row.get("来源类型") or "",
        "source_weight_label": row.get("来源权重类型") or row.get("来源类型") or "",
        "source_influence_weight": row.get("来源影响权重") or "",
        "source_composition": row.get("来源构成") or "",
        "aihot_major_news": row.get("AIHOT重大性说明") or "",
        "market_validation": row.get("市场验证依据") or "",
        "content_fingerprint": row.get("内容指纹") or "",
    }


def stage1_candidate_payload(row: dict[str, str], index: int) -> dict[str, Any]:
    """Payload for the free editorial decision stage.

    This intentionally excludes old 04/Topic Card visible fields, workflow
    experiment text, mother-scene conclusions, deterministic title/angle hints,
    and real_tension-style helper output.
    """
    facts = safe_source_facts(row)
    return {
        "index": index,
        "content_fingerprint": facts["content_fingerprint"],
        "source_facts": facts,
        "original_title": facts["source_title"],
        "title_hook_reference": facts["source_title_hook"],
        "account_directions": ["AI业务定调", "真实工作流改造", "AI导演工作流", "汽车与内容营销", "AI项目复盘"],
        "source_weight_context": {
            "label": facts["source_weight_label"],
            "influence_weight": facts["source_influence_weight"],
            "composition": facts["source_composition"],
            "aihot_major_news": facts["aihot_major_news"],
            "market_validation": facts["market_validation"],
        },
        "stage1_forbidden_inputs": sorted(STAGE1_FORBIDDEN_SOURCE_FIELDS),
    }


def stage2_candidate_payload(row: dict[str, str], index: int, decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": index,
        "content_fingerprint": row.get("内容指纹") or "",
        "source_facts": safe_source_facts(row),
        "locked_editorial_decision": decision,
        "stage2_rule": (
            "只能把 locked_editorial_decision 映射成运营字段；不得替换 selected_visible_title、"
            "natural_austin_angle、title_rationale、public_decision_summary、decision、"
            "recommendation_status、locked_daily_level 或 locked_should_produce。"
        ),
    }


def canonical_decision(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned in CANONICAL_DECISIONS:
        return cleaned
    if any(token in cleaned for token in ["reject", "不做", "不建议", "放弃", "排除"]):
        return "reject"
    if any(token in cleaned for token in ["select", "选择", "生成脚本包", "今日最值得", "值得做", "推进"]):
        return "select"
    if any(token in cleaned for token in ["observe", "观察", "暂存", "补证据", "存素材"]):
        return "observe"
    return "observe"


def canonical_recommendation_status(value: Any, decision: str) -> str:
    cleaned = str(value or "").strip()
    lowered = cleaned.lower()
    if decision == "reject":
        return "不做"
    if any(token in cleaned for token in ["生成脚本包", "做脚本", "进制作", "制作"]) or "script" in lowered:
        return "生成脚本包" if decision == "select" else "补证据"
    if any(token in cleaned for token in ["补证据", "补素材", "补充证据"]):
        return "补证据"
    if any(token in cleaned for token in ["存素材", "素材"]):
        return "存素材"
    if any(token in cleaned for token in ["不做", "放弃", "不建议"]):
        return "不做" if decision == "reject" else "观察"
    if any(token in cleaned for token in ["观察", "暂存", "observe"]):
        return "观察"
    return "生成脚本包" if decision == "select" else "观察"


def default_global_level(decision: str, recommendation_status: str) -> str:
    if decision == "select":
        return "推荐制作"
    if decision == "reject" or recommendation_status == "不做":
        return "不建议制作"
    return "暂存观察"


def locked_should_produce(decision: str, recommendation_status: str, daily_level: str) -> str:
    if decision == "select" and recommendation_status == "生成脚本包" and daily_level == "推荐制作":
        return "是"
    return "否"


def locked_title_permission(decision: str, daily_level: str, should_produce: str) -> str:
    if decision == "select" and daily_level == "推荐制作" and should_produce == "是":
        return "可发布标题"
    if decision == "select":
        return "内部测试标题"
    return "不生成标题"


def global_rank_hash(decision: dict[str, Any]) -> str:
    stable = {
        "editorial_decision_hash": decision.get("editorial_decision_hash", ""),
        "locked_decision": decision.get("locked_decision", ""),
        "locked_recommendation_status": decision.get("locked_recommendation_status", ""),
        "locked_daily_level": decision.get("locked_daily_level", ""),
        "locked_should_produce": decision.get("locked_should_produce", ""),
        "locked_title_permission": decision.get("locked_title_permission", ""),
        "locked_global_rank_position": decision.get("locked_global_rank_position", ""),
        "locked_global_tradeoff_reason": decision.get("locked_global_tradeoff_reason", ""),
    }
    return sha256_text(json.dumps(stable, ensure_ascii=False, sort_keys=True))


def editorial_decision_hash(decision: dict[str, Any]) -> str:
    stable = {
        "decision": decision.get("decision", ""),
        "recommendation_status": decision.get("recommendation_status", ""),
        "natural_austin_angle": decision.get("natural_austin_angle", ""),
        "selected_visible_title": decision.get("selected_visible_title", ""),
        "title_rationale": decision.get("title_rationale", ""),
        "public_decision_summary": decision.get("public_decision_summary", ""),
        "hook_first_rationale": decision.get("hook_first_rationale", ""),
        "hard_fact_usage": decision.get("hard_fact_usage", ""),
        "fact_boundary_note": decision.get("fact_boundary_note", ""),
        "editorial_expression_mode": decision.get("editorial_expression_mode", ""),
    }
    return sha256_text(json.dumps(stable, ensure_ascii=False, sort_keys=True))


def editorial_decision_id(index: int, decision_hash: str) -> str:
    return f"ar020d_decision_{index:03d}_{decision_hash[:12]}"


def normalize_decision(raw: dict[str, Any], index: int, source: dict[str, Any]) -> dict[str, Any]:
    decision = {field: str(raw.get(field, "") or "") for field in EDITORIAL_DECISION_FIELDS}
    decision["index"] = index
    decision["decision"] = canonical_decision(decision.get("decision"))
    decision["recommendation_status"] = canonical_recommendation_status(
        decision.get("recommendation_status"),
        decision["decision"],
    )
    decision_hash = editorial_decision_hash(decision)
    decision["editorial_decision_hash"] = decision_hash
    decision["editorial_decision_id"] = editorial_decision_id(index, decision_hash)
    decision["locked_decision"] = decision["decision"]
    decision["locked_recommendation_status"] = decision["recommendation_status"]
    decision["locked_daily_level"] = default_global_level(decision["decision"], decision["recommendation_status"])
    decision["locked_should_produce"] = locked_should_produce(
        decision["decision"],
        decision["recommendation_status"],
        decision["locked_daily_level"],
    )
    decision["locked_title_permission"] = locked_title_permission(
        decision["locked_decision"], decision["locked_daily_level"], decision["locked_should_produce"]
    )
    decision["locked_global_rank_position"] = ""
    decision["locked_global_tradeoff_reason"] = ""
    decision["global_rank_hash"] = global_rank_hash(decision)
    decision["global_rank_id"] = f"ar020d_rank_{index:03d}_{decision['global_rank_hash'][:12]}"
    decision["persona_style_role"] = "style_reference_only_not_source_evidence"
    return decision


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required Skill file: {path}")
    return path.read_text(encoding="utf-8")


def strip_yaml_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) == 3:
        return parts[2].lstrip()
    return text


def editorial_decision_output_schema() -> dict[str, Any]:
    row_properties: dict[str, Any] = {"index": {"type": "integer", "minimum": 0}}
    for field in EDITORIAL_DECISION_FIELDS:
        row_properties[field] = {"type": "string"}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "engine": {"type": "string"},
            "editorial_decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": row_properties,
                    "required": ["index", *EDITORIAL_DECISION_FIELDS],
                },
            },
            "batch_notes": {"type": "string"},
        },
        "required": ["engine", "editorial_decisions", "batch_notes"],
    }


def global_ranking_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "engine": {"type": "string"},
            "ranking_rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "index": {"type": "integer", "minimum": 0},
                        "editorial_decision_id": {"type": "string"},
                        "editorial_decision_hash": {"type": "string"},
                        "input_global_rank_hash": {"type": "string"},
                        "global_daily_level": {"type": "string"},
                        "final_recommendation_status": {"type": "string"},
                        "global_rank_position": {"type": "string"},
                        "global_tradeoff_reason": {"type": "string"},
                    },
                    "required": [
                        "index",
                        "editorial_decision_id",
                        "editorial_decision_hash",
                        "input_global_rank_hash",
                        "global_daily_level",
                        "final_recommendation_status",
                        "global_rank_position",
                        "global_tradeoff_reason",
                    ],
                },
            },
            "global_ranking_notes": {"type": "string"},
        },
        "required": ["engine", "ranking_rows", "global_ranking_notes"],
    }


def field_mapping_output_schema() -> dict[str, Any]:
    row_properties: dict[str, Any] = {
        "index": {"type": "integer", "minimum": 0},
        "editorial_decision_id": {"type": "string"},
        "editorial_decision_hash": {"type": "string"},
        "global_rank_id": {"type": "string"},
        "global_rank_hash": {"type": "string"},
    }
    for field in STAGE2_OPERATIONAL_FIELDS:
        row_properties[field] = {"type": "string"}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "engine": {"type": "string"},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": row_properties,
                    "required": [
                        "index",
                        "editorial_decision_id",
                        "editorial_decision_hash",
                        "global_rank_id",
                        "global_rank_hash",
                        *STAGE2_REQUIRED_OPERATIONAL_FIELDS,
                    ],
                },
            },
            "batch_notes": {"type": "string"},
        },
        "required": ["engine", "rows", "batch_notes"],
    }


def codex_output_schema() -> dict[str, Any]:
    return field_mapping_output_schema()


def validate_stage1_payload(
    rows: list[dict[str, str]],
    stage1_payload: dict[str, Any],
    *,
    start_index: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stage1_inputs = [stage1_candidate_payload(row, start_index + idx) for idx, row in enumerate(rows)]
    raw_decisions: dict[int, dict[str, Any]] = {}
    seen_indices: set[int] = set()
    for item in stage1_payload.get("editorial_decisions", []):
        missing = [field for field in EDITORIAL_DECISION_FIELDS if not str(item.get(field) or "").strip()]
        if missing:
            raise RuntimeError(
                f"Stage 1 output is incomplete; zero-fallback contract forbids filling: {', '.join(missing)}"
            )
        try:
            raw_idx = int(item.get("index"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Stage 1 output has invalid index: {item.get('index')!r}") from exc
        if start_index <= raw_idx < start_index + len(stage1_inputs):
            idx = raw_idx
            source = stage1_inputs[idx - start_index]
        elif 0 <= raw_idx < len(stage1_inputs):
            idx = start_index + raw_idx
            source = stage1_inputs[raw_idx]
        else:
            raise RuntimeError(f"Stage 1 output has unknown index: {raw_idx}")
        if idx in seen_indices:
            raise RuntimeError(f"Stage 1 output has duplicate index: {idx}")
        seen_indices.add(idx)
        raw_decisions[idx] = normalize_decision(item, idx, source)
    decisions: list[dict[str, Any]] = []
    for offset in range(len(rows)):
        idx = start_index + offset
        if idx not in raw_decisions:
            raise RuntimeError(f"Stage 1 output missing row index {idx}")
        decisions.append(raw_decisions[idx])
    if len(stage1_payload.get("editorial_decisions", [])) != len(rows):
        raise RuntimeError(
            f"Stage 1 row count mismatch: expected {len(rows)}, got {len(stage1_payload.get('editorial_decisions', []))}"
        )
    return decisions, {
        "stage1_rows": len(decisions),
        "stage1_batch_notes": stage1_payload.get("batch_notes", ""),
        "runner_version": RUNNER_VERSION,
        "execution_surface": "current_codex_task",
        "provenance_manifest": runtime_provenance(),
    }


def normalize_global_rank_row(item: dict[str, Any], decision: dict[str, Any]) -> dict[str, str]:
    locked_decision = str(decision.get("locked_decision") or decision.get("decision") or "")
    stage1_recommendation = str(decision.get("locked_recommendation_status") or decision.get("recommendation_status") or "")
    level = default_global_level(locked_decision, stage1_recommendation)
    recommendation = stage1_recommendation
    should_produce = locked_should_produce(locked_decision, recommendation, level)
    title_permission = locked_title_permission(locked_decision, level, should_produce)
    position = str(item.get("global_rank_position") or "").strip()
    reason = str(item.get("global_tradeoff_reason") or "").strip()
    return {
        "index": str(decision.get("index", "")),
        "editorial_decision_id": str(decision.get("editorial_decision_id", "")),
        "editorial_decision_hash": str(decision.get("editorial_decision_hash", "")),
        "locked_decision": locked_decision,
        "locked_recommendation_status": recommendation,
        "locked_daily_level": level,
        "locked_should_produce": should_produce,
        "locked_title_permission": title_permission,
        "locked_global_rank_position": position,
        "locked_global_tradeoff_reason": reason,
    }


def validate_global_ranking_bijection(
    decisions: list[dict[str, Any]],
    ranking_rows: list[dict[str, Any]],
) -> None:
    if len(ranking_rows) != len(decisions):
        raise RuntimeError(
            f"Global ranking row count mismatch: expected {len(decisions)}, got {len(ranking_rows)}"
        )
    expected_by_id = {str(item.get("editorial_decision_id") or ""): item for item in decisions}
    expected_by_index = {int(item.get("index")): item for item in decisions}
    if len(expected_by_id) != len(decisions) or len(expected_by_index) != len(decisions):
        raise RuntimeError("Stage 1 decisions are not uniquely identifiable")
    seen_ids: set[str] = set()
    seen_indices: set[int] = set()
    positions: list[int] = []
    for row in ranking_rows:
        required_fields = {
            "index",
            "editorial_decision_id",
            "editorial_decision_hash",
            "input_global_rank_hash",
            "global_daily_level",
            "final_recommendation_status",
            "global_rank_position",
            "global_tradeoff_reason",
        }
        missing_fields = sorted(field for field in required_fields if field not in row)
        if missing_fields:
            raise RuntimeError(f"Global ranking row missing required fields: {', '.join(missing_fields)}")
        row_id = str(row.get("editorial_decision_id") or "")
        try:
            row_index = int(row.get("index"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Global ranking has invalid index: {row.get('index')!r}") from exc
        if row_id in seen_ids:
            raise RuntimeError(f"Global ranking duplicate editorial_decision_id: {row_id}")
        if row_index in seen_indices:
            raise RuntimeError(f"Global ranking duplicate index: {row_index}")
        seen_ids.add(row_id)
        seen_indices.add(row_index)
        if row_id not in expected_by_id:
            raise RuntimeError(f"Global ranking unknown editorial_decision_id: {row_id}")
        if row_index not in expected_by_index:
            raise RuntimeError(f"Global ranking unknown index: {row_index}")
        expected = expected_by_id[row_id]
        if int(expected.get("index")) != row_index:
            raise RuntimeError(f"Global ranking id/index mismatch: {row_id} != index {row_index}")
        if str(row.get("editorial_decision_hash") or "") != str(expected.get("editorial_decision_hash") or ""):
            raise RuntimeError(f"Global ranking decision hash mismatch at index {row_index}")
        if str(row.get("input_global_rank_hash") or "") != str(expected.get("global_rank_hash") or ""):
            raise RuntimeError(f"Global ranking input rank hash mismatch at index {row_index}")
        expected_level = default_global_level(
            str(expected.get("locked_decision") or expected.get("decision") or ""),
            str(expected.get("locked_recommendation_status") or expected.get("recommendation_status") or ""),
        )
        if str(row.get("global_daily_level") or "") != expected_level:
            raise RuntimeError(f"Global ranking attempted to change eligibility at index {row_index}")
        if str(row.get("final_recommendation_status") or "") != str(
            expected.get("locked_recommendation_status") or expected.get("recommendation_status") or ""
        ):
            raise RuntimeError(f"Global ranking attempted to change recommendation at index {row_index}")
        if not str(row.get("global_tradeoff_reason") or "").strip():
            raise RuntimeError(f"Global ranking row missing public tradeoff reason at index {row_index}")
        try:
            position = int(str(row.get("global_rank_position") or "").strip())
        except ValueError as exc:
            raise RuntimeError(f"Global ranking has invalid position at index {row_index}") from exc
        if position < 1:
            raise RuntimeError(f"Global ranking position must be positive at index {row_index}")
        positions.append(position)
    if sorted(positions) != list(range(1, len(decisions) + 1)):
        raise RuntimeError("Global ranking positions must be a lossless 1..N ordering")


def apply_global_ranking(decisions: list[dict[str, Any]], ranking_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    validate_global_ranking_bijection(decisions, ranking_rows)
    by_id = {str(row.get("editorial_decision_id") or ""): row for row in ranking_rows}
    ranked: list[dict[str, Any]] = []
    for decision in decisions:
        item = by_id[str(decision.get("editorial_decision_id") or "")]
        lock = normalize_global_rank_row(item, decision)
        next_decision = {**decision, **lock}
        rank_hash = global_rank_hash(next_decision)
        next_decision["global_rank_hash"] = rank_hash
        next_decision["global_rank_id"] = f"ar020d_rank_{int(next_decision.get('index') or 0):03d}_{rank_hash[:12]}"
        ranked.append(next_decision)
    return sorted(ranked, key=lambda row: int(row["locked_global_rank_position"]))


def stage2_invariant_issues(decision: dict[str, Any], row: dict[str, str]) -> list[str]:
    issues: list[str] = []
    expected_id = str(decision.get("editorial_decision_id", ""))
    expected_hash = str(decision.get("editorial_decision_hash", ""))
    expected_title = normalize_space(decision.get("selected_visible_title", ""))
    expected_angle = normalize_space(decision.get("natural_austin_angle", ""))
    expected_rationale = normalize_space(decision.get("title_rationale", ""))
    expected_summary = normalize_space(decision.get("public_decision_summary", ""))
    expected_decision = normalize_space(decision.get("locked_decision") or decision.get("decision", ""))
    expected_recommendation = normalize_space(decision.get("locked_recommendation_status") or decision.get("recommendation_status", ""))
    expected_level = normalize_space(decision.get("locked_daily_level", ""))
    expected_should = normalize_space(decision.get("locked_should_produce", ""))
    expected_title_permission = normalize_space(decision.get("locked_title_permission", ""))

    if normalize_space(row.get("editorial_decision_id")) != expected_id:
        issues.append("editorial_decision_id mismatch")
    if normalize_space(row.get("editorial_decision_hash")) != expected_hash:
        issues.append("editorial_decision_hash mismatch")
    if normalize_space(row.get("locked_selected_visible_title")) != expected_title:
        issues.append("locked_selected_visible_title mismatch")
    if normalize_space(row.get("选题命题")) != expected_title:
        issues.append("选题命题 diverged from Stage 1 selected_visible_title")
    if normalize_space(row.get("locked_natural_austin_angle")) != expected_angle:
        issues.append("locked_natural_austin_angle mismatch")
    if normalize_space(row.get("locked_title_rationale")) != expected_rationale:
        issues.append("locked_title_rationale mismatch")
    if normalize_space(row.get("locked_public_decision_summary")) != expected_summary:
        issues.append("locked_public_decision_summary mismatch")
    if normalize_space(row.get("title_permission")) == "可发布标题" and normalize_space(row.get("可发布标题")) != expected_title:
        issues.append("可发布标题 diverged from Stage 1 selected_visible_title")
    if normalize_space(row.get("locked_decision")) != expected_decision:
        issues.append("locked_decision mismatch")
    if normalize_space(row.get("locked_recommendation_status")) != expected_recommendation:
        issues.append("locked_recommendation_status mismatch")
    if normalize_space(row.get("locked_daily_level")) != expected_level:
        issues.append("locked_daily_level mismatch")
    if normalize_space(row.get("locked_should_produce")) != expected_should:
        issues.append("locked_should_produce mismatch")
    if normalize_space(row.get("locked_title_permission")) != expected_title_permission:
        issues.append("locked_title_permission mismatch")
    if normalize_space(row.get("今日建议级别")) != expected_level:
        issues.append("今日建议级别 diverged from global ranking")
    if normalize_space(row.get("候选状态")) != expected_level:
        issues.append("候选状态 diverged from global ranking")
    if normalize_space(row.get("推荐动作")) != expected_recommendation:
        issues.append("推荐动作 diverged from Stage 1 recommendation_status")
    if normalize_space(row.get("是否建议进入制作")) != expected_should:
        issues.append("是否建议进入制作 diverged from global ranking")
    if normalize_space(row.get("title_permission")) != expected_title_permission:
        issues.append("title_permission diverged from global ranking")
    if normalize_space(row.get("主编判断摘要")) != expected_summary:
        issues.append("主编判断摘要 diverged from Stage 1 public_decision_summary")
    if normalize_space(row.get("标题思路")) != expected_rationale:
        issues.append("标题思路 diverged from Stage 1 title_rationale")
    if normalize_space(row.get("主编筛选")) != expected_decision:
        issues.append("主编筛选 diverged from Stage 1 decision")
    return issues


def raw_stage2_drift_issues(decision: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for field, decision_field in STAGE2_RAW_OWNER_EXPECTATIONS.items():
        if field not in raw:
            continue
        actual = normalize_space(raw.get(field))
        expected = normalize_space(decision.get(decision_field, ""))
        if actual != expected:
            issues.append(f"raw Stage 2 {field} diverged from locked {decision_field}")
    if "可发布标题" in raw:
        actual = normalize_space(raw.get("可发布标题"))
        expected = normalize_space(decision.get("selected_visible_title", ""))
        permission = normalize_space(raw.get("title_permission"))
        if permission == "可发布标题" and actual != expected:
            issues.append("raw Stage 2 可发布标题 diverged from locked selected_visible_title")
    if "editorial_thinking_json" in raw:
        issues.append("raw Stage 2 attempted to author editorial_thinking_json")
    lock_echo_expectations = {
        "editorial_decision_id": "editorial_decision_id",
        "editorial_decision_hash": "editorial_decision_hash",
        "locked_selected_visible_title": "selected_visible_title",
        "locked_natural_austin_angle": "natural_austin_angle",
        "locked_title_rationale": "title_rationale",
        "locked_public_decision_summary": "public_decision_summary",
    }
    for field, decision_field in lock_echo_expectations.items():
        if field in raw and normalize_space(raw.get(field)) != normalize_space(decision.get(decision_field, "")):
            issues.append(f"raw Stage 2 {field} mismatch")
    return issues


def reapply_locked_stage2_fields(row: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Re-apply locked Stage 1 and global-ranking ownership fields."""
    out = dict(row)
    expected_title = str(decision.get("selected_visible_title", ""))
    expected_summary = str(decision.get("public_decision_summary", ""))
    expected_tradeoff = str(decision.get("locked_global_tradeoff_reason", ""))

    out["locked_decision"] = str(decision.get("locked_decision") or decision.get("decision", ""))
    out["locked_recommendation_status"] = str(
        decision.get("locked_recommendation_status") or decision.get("recommendation_status", "")
    )
    out["locked_daily_level"] = str(decision.get("locked_daily_level", ""))
    out["locked_should_produce"] = str(decision.get("locked_should_produce", ""))
    out["locked_title_permission"] = str(decision.get("locked_title_permission", ""))
    out["locked_global_rank_position"] = str(decision.get("locked_global_rank_position", ""))
    out["locked_global_tradeoff_reason"] = expected_tradeoff
    out["locked_selected_visible_title"] = expected_title
    out["locked_natural_austin_angle"] = str(decision.get("natural_austin_angle", ""))
    out["locked_title_rationale"] = str(decision.get("title_rationale", ""))
    out["locked_public_decision_summary"] = expected_summary

    out["今日建议级别"] = out["locked_daily_level"]
    out["候选状态"] = out["locked_daily_level"]
    out["推荐动作"] = out["locked_recommendation_status"]
    out["是否建议进入制作"] = out["locked_should_produce"]
    out["title_permission"] = out["locked_title_permission"]
    out["选题命题"] = expected_title
    out["我的选题标题"] = expected_title
    out["选题标题"] = expected_title

    if out["locked_title_permission"] == "可发布标题":
        out["可发布标题"] = expected_title
    else:
        out["可发布标题"] = ""
        out["标题备选"] = ""

    out["主编筛选"] = out["locked_decision"]
    out["主编自由稿"] = expected_summary
    out["主编判断摘要"] = expected_summary
    out["标题思路"] = str(decision.get("title_rationale", ""))
    out["选题判断"] = expected_summary
    out["推荐理由"] = expected_tradeoff or expected_summary
    out["主编判断"] = expected_summary
    out["我的切入"] = str(decision.get("natural_austin_angle", ""))
    out["我准备怎么讲"] = str(decision.get("natural_austin_angle", ""))
    out["我会怎么讲"] = str(decision.get("natural_austin_angle", ""))
    return out


def _apply_stage2_payload(
    rows: list[dict[str, str]],
    decisions: list[dict[str, Any]],
    model: str,
    timeout: int,
    artifact_dir: Path | None = None,
    *,
    stage2_payload_override: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    stage2_inputs = [stage2_candidate_payload(row, int(decisions[idx].get("index", idx)), decisions[idx]) for idx, row in enumerate(rows)]
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "stage2_input_sanitized.json").write_text(
            json.dumps({
                "runner_version": RUNNER_VERSION,
                "stage": "field_mapping",
                "rows": stage2_inputs,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if stage2_payload_override is None:
        raise RuntimeError("Stage 2 requires a current-task output artifact; nested execution is prohibited")
    stage2_payload = stage2_payload_override
    if artifact_dir:
        (artifact_dir / "stage2_field_mapping_output.json").write_text(
            json.dumps(stage2_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    by_index: dict[int, dict[str, str]] = {}
    for item in stage2_payload.get("rows", []):
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        fields = {
            field: str(item.get(field, "") or "")
            for field in [
                "editorial_decision_id",
                "editorial_decision_hash",
                "global_rank_id",
                "global_rank_hash",
                "locked_selected_visible_title",
                "locked_natural_austin_angle",
                "locked_title_rationale",
                "locked_public_decision_summary",
                *STAGE2_OPERATIONAL_FIELDS,
            ]
        }
        fields["raw_stage2_owner_fields_json"] = json.dumps(
            {field: item.get(field) for field in STAGE2_OWNER_FIELDS if field in item},
            ensure_ascii=False,
            sort_keys=True,
        )
        fields["raw_stage2_payload_json"] = json.dumps(item, ensure_ascii=False, sort_keys=True)
        by_index[idx] = fields
    enriched: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        out = dict(row)
        for field in AR020D_LEGACY_CREATIVE_FIELDS:
            out[field] = ""
        judgement = by_index.get(idx)
        global_idx = int(decisions[idx].get("index", idx))
        if not judgement and global_idx != idx:
            judgement = by_index.get(global_idx)
        if not judgement:
            raise RuntimeError(f"Codex Stage 2 output missing row index {global_idx}")
        decision = decisions[idx]
        try:
            raw_stage2_payload = json.loads(judgement.get("raw_stage2_payload_json") or "{}")
        except json.JSONDecodeError:
            raw_stage2_payload = {}
        raw_issues = raw_stage2_drift_issues(decision, raw_stage2_payload)
        for field in ["editorial_decision_id", "editorial_decision_hash", "global_rank_id", "global_rank_hash"]:
            if normalize_space(raw_stage2_payload.get(field)) != normalize_space(decision.get(field)):
                raw_issues.append(f"raw Stage 2 {field} mismatch")
        out.update(judgement)
        for lock_field in [
            "global_rank_id",
            "global_rank_hash",
            "locked_decision",
            "locked_recommendation_status",
            "locked_daily_level",
            "locked_should_produce",
            "locked_title_permission",
            "locked_global_rank_position",
            "locked_global_tradeoff_reason",
        ]:
            out[lock_field] = str(decision.get(lock_field, ""))
        out["global_ranking_json"] = json.dumps({
            "global_rank_id": decision.get("global_rank_id", ""),
            "global_rank_hash": decision.get("global_rank_hash", ""),
            "locked_daily_level": decision.get("locked_daily_level", ""),
            "locked_should_produce": decision.get("locked_should_produce", ""),
            "locked_global_rank_position": decision.get("locked_global_rank_position", ""),
            "locked_global_tradeoff_reason": decision.get("locked_global_tradeoff_reason", ""),
        }, ensure_ascii=False, sort_keys=True)
        out["今日建议级别"] = str(decision.get("locked_daily_level", ""))
        out["候选状态"] = str(decision.get("locked_daily_level", ""))
        out["推荐动作"] = str(decision.get("locked_recommendation_status", ""))
        out["是否建议进入制作"] = str(decision.get("locked_should_produce", ""))
        out["editorial_architecture"] = RUNNER_VERSION
        out["editorial_decision_json"] = json.dumps(decision, ensure_ascii=False, sort_keys=True)
        out["editorial_thinking_json"] = out.get("editorial_thinking_json") or out["editorial_decision_json"]
        out["editorial_decision_id"] = str(decision.get("editorial_decision_id", ""))
        out["editorial_decision_hash"] = str(decision.get("editorial_decision_hash", ""))
        out["主编判断摘要"] = str(decision.get("public_decision_summary", ""))
        out["标题思路"] = str(decision.get("title_rationale", ""))
        out["原始标题钩子"] = str(decision.get("source_title_hook", ""))
        out["Austin改写理由"] = str(decision.get("source_hook_usage", ""))
        out["研究摘要"] = str(decision.get("source_read") or "")
        out["受众钩子"] = str(decision.get("audience_hook") or "")
        out["研究置信度"] = str(decision.get("research_confidence") or out.get("研究置信度") or "")
        out["内容结构"] = str(decision.get("proposed_content_structure") or "")
        if not contains_chinese(out["研究摘要"]):
            raw_issues.append("user-visible research summary must be concise Chinese")
        if not contains_chinese(out["受众钩子"]):
            raw_issues.append("user-visible audience hook must be concise Chinese")
        if not out["研究置信度"]:
            raw_issues.append("user-visible research confidence is required")
        if normalize_space(out["研究摘要"]) == normalize_space(out["受众钩子"]):
            raw_issues.append("research summary must state facts and differ from audience hook")
        if normalize_space(decision.get("natural_austin_angle")) == normalize_space(out.get("对应方向")):
            raw_issues.append("natural Austin angle must not collapse to direction category")
        # AR-020D: persona/case material is style reference only, never row evidence.
        for case_field in ["真实/相邻案例", "可调用案例", "关联母场景", "借用方式", "我的真实/相邻场景"]:
            out[case_field] = ""
        out["不能声称的部分"] = out.get("不能声称的部分") or "不能把 persona/style 案例当成这条来源的事实证据。"
        # Before normalization/reapply, only inspect the raw model payload for
        # owner-field attempts. Full field invariants run after locked values
        # have been mapped into the final row.
        invariant_issues = list(raw_issues)
        out["raw_stage2_drift_status"] = "fail" if raw_issues else "pass"
        out["raw_stage2_drift_issues"] = "；".join(raw_issues)
        out["stage2_invariant_status"] = "fail" if invariant_issues else "pass"
        out["stage2_invariant_issues"] = "；".join(invariant_issues)
        out["persona_style_reference_state"] = "embedded_style_reference_not_source_evidence"
        out["persona_style_hash"] = file_sha256(SKILL_REFERENCE)
        out["Skill编辑层"] = "ai-account-editorial-director"
        out["Skill参考文件"] = str(SKILL_REFERENCE)
        out["strict_fail_closed"] = "true"
        enriched.append(out)
    # AR-020D never sends Stage 2 rows through the legacy deterministic
    # normalizer. That path authors defaults, downgrades decisions, and applies
    # a batch-local selection cap. Stage 2 output is operational-only; validators may
    # block it, but must not rewrite Stage 1 or ranking ownership.
    normalized = [sanitize_visible_language(row) for row in enriched]
    decision_by_id = {str(decision.get("editorial_decision_id", "")): decision for decision in decisions}
    normalized = [
        reapply_locked_stage2_fields(row, decision_by_id[str(row.get("editorial_decision_id", ""))])
        if str(row.get("editorial_decision_id", "")) in decision_by_id
        else row
        for row in normalized
    ]
    normalized = apply_final_stage2_invariants(normalized)
    provenance = runtime_provenance()
    if artifact_dir:
        (artifact_dir / "ar020d_provenance_manifest.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return normalized, {
        "codex_rows": len(by_index),
        "stage1_rows": len(decisions),
        "stage2_rows": len(by_index),
        "batch_notes": stage2_payload.get("batch_notes", ""),
        "model": model or "codex-default",
        "runner_version": RUNNER_VERSION,
        "provenance_manifest": provenance,
        "stage_architecture": "editorial_decision_then_field_mapping",
        "approved_selection_learning": str(APPROVED_SELECTION_LEARNING_MD) if APPROVED_SELECTION_LEARNING_MD.exists() else "",
    }


def apply_stage2_payload(
    rows: list[dict[str, str]],
    decisions: list[dict[str, Any]],
    stage2_payload: dict[str, Any],
    *,
    artifact_dir: Path | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    return _apply_stage2_payload(
        rows,
        decisions,
        model="current-codex-task",
        timeout=0,
        artifact_dir=artifact_dir,
        stage2_payload_override=stage2_payload,
    )


def apply_final_stage2_invariants(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    checked: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        decision: dict[str, Any] = {}
        try:
            decision = json.loads(str(out.get("editorial_decision_json") or "{}"))
        except json.JSONDecodeError:
            decision = {}
        preserved_raw_issues = normalize_space(out.get("raw_stage2_drift_issues"))
        issues = stage2_invariant_issues(decision, out) if decision else ["missing editorial_decision_json"]
        if preserved_raw_issues:
            issues = [preserved_raw_issues, *issues]
        if issues:
            existing = normalize_space(out.get("stage2_invariant_issues"))
            out["stage2_invariant_status"] = "fail"
            out["stage2_invariant_issues"] = "；".join(part for part in [existing, "；".join(issues)] if part)
            out["guard_blocked"] = "true"
            out["guard_blocked_reason"] = out["stage2_invariant_issues"]
        else:
            out["stage2_invariant_status"] = "pass"
            out["stage2_invariant_issues"] = ""
        checked.append(out)
    final_rows: list[dict[str, Any]] = []
    for row in checked:
        out = dict(row)
        contract_issues = field_contract.validate_field_contract(out)
        blocking = [issue for issue in contract_issues if issue.severity == "block"]
        out["field_contract_status"] = "fail" if blocking else "pass"
        out["field_contract_issues"] = "；".join(issue.message for issue in contract_issues)
        if blocking:
            out["guard_blocked"] = "true"
            out["guard_blocked_reason"] = "；".join(
                part for part in [out.get("guard_blocked_reason", ""), out["field_contract_issues"]] if part
            )
        final_rows.append(out)
    return final_rows  # type: ignore[return-value]


def write_report(
    path: Path,
    rows: list[dict[str, str]],
    input_path: Path,
    output_path: Path,
    engine: str,
    engine_meta: dict[str, Any] | None = None,
) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("候选状态", "")] = counts.get(row.get("候选状态", ""), 0) + 1
    payload = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "skill": "ai-account-editorial-director",
        "skill_dir": str(SKILL_DIR),
        "skill_reference": str(SKILL_REFERENCE),
        "input": str(input_path),
        "output": str(output_path),
        "engine": engine,
        "engine_meta": engine_meta or {},
        "rows": len(rows),
        "candidate_status_counts": counts,
        "fields_added": EXTRA_FIELDS,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="AR-020D schema/validator library; it has no standalone editorial execution mode.")
    parser.parse_args()
    parser.error("Use topic_editorial_state_machine.py. This module performs no business I/O and exposes no alternate editorial engine.")


if __name__ == "__main__":
    raise SystemExit(main())
