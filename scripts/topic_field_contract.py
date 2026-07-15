"""AR-020B field ownership and invariant checks.

The editorial Skill owns the visible topic fields. This module only validates
that those fields agree with the source evidence before they reach 04, Topic
Card, or 06. It may downgrade unsafe rows, but it should not invent new
editorial angles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


SKILL_OWNED_MAIN_FIELDS = [
    "选题标题",
    "我的选题标题",
    "选题命题",
    "一句话Brief",
    "我要做的实验",
    "我的工作流痛点",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "我的思考点",
    "重点体现",
    "对应方向",
    "推荐动作",
    "今日建议级别",
    "title_permission",
    "可发布标题",
]

SOURCE_EVIDENCE_FIELDS = [
    "来源内容",
    "原始来源标题",
    "来源标题",
    "内容标题",
    "这条内容讲了什么",
    "来源构成",
    "来源类型",
    "原始来源账号",
    "账号名/公众号名",
    "来源链接",
    "AIHOT重大性说明",
    "市场验证依据",
]

MAIN_CONTRACT_FIELDS = [
    "选题标题",
    "我的选题标题",
    "选题命题",
    "一句话Brief",
    "我要做的实验",
    "我的工作流痛点",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "我的思考点",
    "重点体现",
    "对应方向",
    "推荐动作",
    "今日建议级别",
    "对标转译角度",
    "Austin转译角度",
]

EXPERIMENT_ACTION_TERMS = [
    "测试", "验证", "改造", "压缩", "录成", "接进", "变成", "写回", "沉淀",
    "做成", "复用", "拆成", "跑一轮", "对比", "进入", "重写", "少掉",
    "选择", "选", "记录", "导出", "输出", "标出", "标注", "检查", "统计",
    "回填", "输入", "补", "决定", "复核",
]

KNOWLEDGE_TERMS = ["知识库", "Obsidian", "RAG", "第二大脑", "双链", "内容资产", "资料沉淀", "素材沉淀"]
VIDEO_TERMS = ["AI视频", "视频交付", "分镜", "成片验收", "短剧", "短片", "镜头", "导演", "剪辑", "故事板", "Storyboard"]
OFFICE_TERMS = ["Excel", "表格", "PPT", "Word", "飞书文档", "飞书表格", "办公"]
AIHOT_MAJOR_TERMS = ["重大", "发布", "模型", "多模态", "Agent", "智能体", "工作流", "视频", "API", "降价", "开源", "监管", "行业变化"]
AIHOT_SOURCE_LABELS = {"AIHOT热点", "AI Hot 低权重热点源"}
ACTIONABLE_ACTIONS = {"生成脚本包", "立即蹭热点"}
ACTIONABLE_LEVELS = {"今日最值得做", "可选候选"}


@dataclass
class ContractIssue:
    code: str
    message: str
    severity: str = "block"


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def joined_text(row: dict[str, Any], fields: list[str]) -> str:
    return "\n".join(normalize_space(row.get(field, "")) for field in fields if normalize_space(row.get(field, "")))


def contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered or term in text for term in terms)


def has_experiment_action(value: str) -> bool:
    return contains_any(value, EXPERIMENT_ACTION_TERMS)


def is_actionable(row: dict[str, Any]) -> bool:
    return (
        normalize_space(row.get("推荐动作")) in ACTIONABLE_ACTIONS
        or normalize_space(row.get("今日建议级别")) in ACTIONABLE_LEVELS
        or normalize_space(row.get("是否建议进入制作")) == "是"
    )


def is_aihot(row: dict[str, Any]) -> bool:
    return (
        normalize_space(row.get("来源类型")) in AIHOT_SOURCE_LABELS
        or normalize_space(row.get("来源权重类型")) in AIHOT_SOURCE_LABELS
    )


def issue_messages(issues: list[ContractIssue]) -> str:
    return "；".join(issue.message for issue in issues)


def validate_field_contract(row: dict[str, Any]) -> list[ContractIssue]:
    """Return invariant violations for one topic row."""
    source = joined_text(row, SOURCE_EVIDENCE_FIELDS)
    main = joined_text(row, MAIN_CONTRACT_FIELDS)
    combined = f"{source}\n{main}"
    issues: list[ContractIssue] = []

    source_has_knowledge = contains_any(source, KNOWLEDGE_TERMS)
    source_has_video = contains_any(source, VIDEO_TERMS)
    source_has_office = contains_any(source, OFFICE_TERMS)
    main_has_knowledge = contains_any(main, KNOWLEDGE_TERMS)
    main_has_video = contains_any(main, VIDEO_TERMS)
    main_has_office = contains_any(main, OFFICE_TERMS)

    if source_has_knowledge and main_has_video and not source_has_video:
        issues.append(ContractIssue(
            "knowledge_video_mismatch",
            "知识库/Obsidian/RAG 来源的主字段残留 AI视频/分镜/成片验收表达",
        ))
    if source_has_video and main_has_knowledge and not source_has_knowledge:
        issues.append(ContractIssue(
            "video_knowledge_mismatch",
            "AI视频/分镜来源的主字段错落到知识库/内容资产表达",
        ))
    if source_has_office and main_has_video and not source_has_video:
        issues.append(ContractIssue(
            "office_video_mismatch",
            "办公文档/表格来源的主字段错落到 AI视频/分镜/成片验收表达",
        ))

    direction = normalize_space(row.get("对应方向"))
    if direction == "AI导演工作流" and (main_has_knowledge or main_has_office) and not source_has_video:
        issues.append(ContractIssue(
            "direction_main_field_mismatch",
            "对应方向为 AI导演工作流，但来源证据不足且主字段更像知识库/办公流程",
        ))
    if direction == "真实工作流改造" and main_has_video and source_has_knowledge and not source_has_video:
        issues.append(ContractIssue(
            "direction_video_leak",
            "真实工作流改造/知识库来源里泄漏视频交付字段",
        ))

    if is_aihot(row) and is_actionable(row):
        major = normalize_space(row.get("AIHOT重大性说明"))
        angle = normalize_space(row.get("对标转译角度") or row.get("Austin转译角度") or row.get("一句话Brief"))
        if not major or not contains_any(f"{major}\n{source}", AIHOT_MAJOR_TERMS) or not angle:
            issues.append(ContractIssue(
                "aihot_actionable_without_major_evidence",
                "AI Hot 进入可行动候选但缺重大性说明或 Austin 角度",
            ))

    if normalize_space(row.get("推荐动作")) == "生成脚本包":
        experiment = normalize_space(row.get("我要做的实验"))
        validation = normalize_space(row.get("验证方式"))
        proposition = normalize_space(row.get("选题命题") or row.get("我的选题标题") or row.get("选题标题"))
        permission = normalize_space(row.get("title_permission"))
        if not proposition:
            issues.append(ContractIssue("script_missing_proposition", "生成脚本包缺少选题命题"))
        if not experiment or not has_experiment_action(experiment):
            issues.append(ContractIssue("script_missing_experiment", "生成脚本包缺少可执行实验动作"))
        if not validation or not has_experiment_action(validation):
            issues.append(ContractIssue("script_missing_validation", "生成脚本包缺少可执行验证方式"))
        if permission == "不生成标题":
            issues.append(ContractIssue("script_title_not_ready", "生成脚本包不能同时标记 title_permission=不生成标题"))

    return issues


def mark_contract_result(row: dict[str, Any], issues: list[ContractIssue]) -> dict[str, Any]:
    out = dict(row)
    out["field_contract_status"] = "fail" if issues else "pass"
    out["field_contract_issues"] = issue_messages(issues)
    out["field_contract_owner"] = "ai-account-editorial-director"
    return out


def downgrade_for_contract(row: dict[str, Any], issues: list[ContractIssue]) -> dict[str, Any]:
    """Make contract failures visible and prevent unsafe promotion."""
    out = mark_contract_result(row, issues)
    if not issues:
        return out
    reason = issue_messages(issues)
    existing = normalize_space(out.get("不建议做的原因") or out.get("降级原因"))
    out["今日建议级别"] = "暂存观察"
    out["候选状态"] = "暂存观察"
    out["是否建议进入制作"] = "否"
    out["推荐动作"] = "暂存观察"
    out["title_permission"] = "不生成标题"
    out["可发布标题"] = ""
    out["标题备选"] = ""
    out["降级原因"] = "；".join(part for part in [existing, f"字段契约失败：{reason}"] if part)
    out["不建议做的原因"] = out["降级原因"]
    return out

