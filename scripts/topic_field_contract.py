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
    "主编判断摘要",
    "标题思路",
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
    "主编判断摘要",
    "标题思路",
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
VISIBLE_REASON_FIELDS = [
    "主编判断摘要",
    "标题思路",
    "主编自由稿",
    "主编判断",
    "推荐理由",
    "不建议做的原因",
]
HINT_FIELDS = ["Austin映射方向", "Austin转译角度", "对标转译角度", "主题簇", "主题簇说明"]
VISIBLE_MAIN_FIELDS = [
    "选题标题",
    "我的选题标题",
    "选题命题",
    "一句话Brief",
    "我要做的实验",
    "我的工作流痛点",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "重点体现",
]
GENERIC_REASON_PATTERNS = [
    "吸收它的选题承诺和结构",
    "转成自己的业务语言",
    "落到我的真实流程",
    "形成可执行动作",
    "适合 Austin",
    "有一定参考价值",
]
OBSERVE_PLACEHOLDER_PATTERNS = [
    "待补实验动作：写清输入材料、1-2个动作、输出物和通过/失败标准。",
    "待补实验动作",
    "写清输入材料、1-2个动作、输出物和通过/失败标准",
]
SOURCE_EVIDENCE_MARKERS = ["来源", "原始", "对标", "账号", "热点", "这条", "内容", "证据", "标题"]
AUSTIN_SCENE_MARKERS = ["我", "Austin", "工作流", "业务", "交付", "内容", "选题", "脚本", "飞书", "视频"]
ACTION_MARKERS = EXPERIMENT_ACTION_TERMS + ["动作", "实验", "验证", "生成", "补证据"]
TRADEOFF_MARKERS = ["但", "不过", "风险", "缺", "不能", "先", "如果", "边界", "取舍", "暂存", "补"]
TITLE_TEMPLATE_PATTERNS = [
    ("test_can", re.compile(r"(想用|准备用|用|拿).{0,18}(测试|验证).{0,28}能不能")),
    ("first_test", re.compile(r"(先|先拿|先用).{0,28}(测试|验证|过一遍|跑一轮)")),
    ("acceptance", re.compile(r"(验收|返修|交付).{0,18}(能不能|能否|可不可以)")),
    ("can_or_not", re.compile(r"能不能|能否|会不会|可不可以")),
    ("try_once", re.compile(r"试一次|试一遍|跑一轮")),
]
TITLE_TASK_TONE_TERMS = [
    "先看",
    "先拿",
    "先用",
    "先从",
    "先接",
    "先做",
    "我先",
    "先把",
    "测试",
    "验证",
    "能不能",
    "会不会",
    "试一次",
    "试一遍",
    "我想看的是",
    "正好拿来",
    "可以进",
    "但不能只看",
]
TITLE_REFLECTION_SHELL_TERMS = [
    "给我的提醒",
    "只能提醒我",
    "我会把",
    "我先把",
    "我想看的是",
    "正好拿来",
    "翻译成",
    "放进",
    "可以进",
    "但不能只看",
]


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


def blocking_issues(issues: list[ContractIssue]) -> list[ContractIssue]:
    return [issue for issue in issues if issue.severity == "block"]


def warning_issues(issues: list[ContractIssue]) -> list[ContractIssue]:
    return [issue for issue in issues if issue.severity != "block"]


def title_for_quality(row: dict[str, Any]) -> str:
    return normalize_space(
        row.get("可发布标题")
        or row.get("我的选题标题")
        or row.get("选题命题")
        or row.get("选题标题")
    )


def title_pattern_family(title: str) -> str:
    text = normalize_space(title)
    if not text:
        return "empty"
    for family, pattern in TITLE_TEMPLATE_PATTERNS:
        if pattern.search(text):
            return family
    if "：" in text or ":" in text:
        return "colon_split"
    if "？" in text or "?" in text:
        return "question"
    return "freeform"


def visible_reason_quality_issues(row: dict[str, Any]) -> list[ContractIssue]:
    """Check that the public editorial trace is concrete enough for users."""
    if not is_actionable(row):
        return []
    reason = joined_text(row, VISIBLE_REASON_FIELDS)
    if not reason:
        return [ContractIssue("missing_editorial_trace", "缺少主编判断摘要/标题思路，用户看不到为什么选这条")]
    if any(pattern in reason for pattern in GENERIC_REASON_PATTERNS):
        return [ContractIssue("generic_editorial_trace", "主编判断摘要仍是模板化泛话，没有说明来源证据、Austin 场景和取舍")]
    checks = {
        "source": contains_any(reason, SOURCE_EVIDENCE_MARKERS),
        "scene": contains_any(reason, AUSTIN_SCENE_MARKERS),
        "action": contains_any(reason, ACTION_MARKERS) or has_experiment_action(normalize_space(row.get("我要做的实验"))),
        "tradeoff": contains_any(reason, TRADEOFF_MARKERS),
    }
    missing = [name for name, ok in checks.items() if not ok]
    if missing:
        return [ContractIssue(
            "blackbox_editorial_trace",
            f"主编判断摘要缺少可审查要素：{','.join(missing)}",
        )]
    return []


def hint_leak_issues(row: dict[str, Any]) -> list[ContractIssue]:
    """Prevent deterministic hints from becoming visible fields without Skill trace."""
    thinking = normalize_space(row.get("editorial_thinking_json") or row.get("主编判断摘要") or row.get("标题思路"))
    if not thinking:
        return []
    visible = joined_text(row, VISIBLE_MAIN_FIELDS)
    issues: list[ContractIssue] = []
    for field in HINT_FIELDS:
        hint = normalize_space(row.get(field))
        if len(hint) < 18:
            continue
        snippet = hint[:24]
        if snippet in visible and snippet not in thinking:
            issues.append(ContractIssue(
                "hint_leak_without_skill_trace",
                f"{field} 的 pre-Skill hint 直接进入可见主字段，但主编判断摘要没有解释为什么采用",
            ))
            break
    return issues


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
        if permission != "可发布标题":
            issues.append(ContractIssue("script_title_not_ready", "生成脚本包需要 title_permission=可发布标题，内部测试标题只能补证据或观察"))

    issues.extend(visible_reason_quality_issues(row))
    issues.extend(hint_leak_issues(row))
    return issues


def mark_contract_result(row: dict[str, Any], issues: list[ContractIssue]) -> dict[str, Any]:
    out = dict(row)
    if blocking_issues(issues):
        out["field_contract_status"] = "fail"
    elif warning_issues(issues):
        out["field_contract_status"] = "warn"
    else:
        out["field_contract_status"] = "pass"
    out["field_contract_issues"] = issue_messages(issues)
    out["field_contract_owner"] = "ai-account-editorial-director"
    return out


def downgrade_for_contract(row: dict[str, Any], issues: list[ContractIssue]) -> dict[str, Any]:
    """Make contract failures visible and prevent unsafe promotion."""
    out = mark_contract_result(row, issues)
    blockers = blocking_issues(issues)
    if not blockers:
        return out
    reason = issue_messages(blockers)
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


def title_quality_issues(rows: list[dict[str, Any]]) -> dict[int, list[ContractIssue]]:
    """Batch-level title/template checks.

    Generated/actionable rows are blocked when too many share the same title
    skeleton. Observe rows are only flagged, because weak-but-visible wording is
    still useful for PM review as long as it is not presented as ready to make.
    """
    issues_by_index: dict[int, list[ContractIssue]] = {idx: [] for idx, _row in enumerate(rows)}
    actionable: list[tuple[int, str]] = []
    observe_reason_counts: dict[str, list[int]] = {}
    observe_placeholder_indices: list[int] = []
    observe_template_indices: list[int] = []
    observe_count = 0
    for idx, row in enumerate(rows):
        title = title_for_quality(row)
        family = title_pattern_family(title)
        if normalize_space(row.get("推荐动作")) == "生成脚本包":
            actionable.append((idx, family))
        else:
            observe_count += 1
            reason = normalize_space(row.get("主编判断摘要") or row.get("不建议做的原因") or row.get("推荐理由"))
            if reason:
                observe_reason_counts.setdefault(reason[:40], []).append(idx)
            observe_text = joined_text(row, ["选题命题", "我的选题标题", "选题标题", "我要做的实验", "验证方式", "标题思路"])
            has_placeholder = contains_any(observe_text, OBSERVE_PLACEHOLDER_PATTERNS)
            if has_placeholder:
                observe_placeholder_indices.append(idx)
            if not has_placeholder and (
                family != "freeform"
                or contains_any(title, TITLE_TASK_TONE_TERMS)
                or contains_any(title, TITLE_REFLECTION_SHELL_TERMS)
            ):
                observe_template_indices.append(idx)
        rows[idx]["title_pattern_family"] = family

    if actionable:
        counts: dict[str, list[int]] = {}
        for idx, family in actionable:
            counts.setdefault(family, []).append(idx)
        for family, indices in counts.items():
            ratio = len(indices) / max(1, len(actionable))
            if family != "freeform" and len(indices) > 1 and ratio > 0.30:
                for idx in indices:
                    issues_by_index[idx].append(ContractIssue(
                        "title_skeleton_collision",
                        f"标题骨架重复过高：{family} 占 {ratio:.0%}；该风险会阻止进入生成脚本包",
                    ))
        phrase_hits = [
            idx for idx, _family in actionable
            if contains_any(title_for_quality(rows[idx]), ["能不能", "测试", "验证", "验收", "试一次", "试一遍"])
        ]
        if len(phrase_hits) / max(1, len(actionable)) > 0.40 and len(phrase_hits) > 1:
            for idx in phrase_hits:
                issues_by_index[idx].append(ContractIssue(
                    "title_template_phrase_family",
                    "标题里测试/验证/能不能类骨架占比过高；该风险会阻止进入生成脚本包",
                ))

    for reason, indices in observe_reason_counts.items():
        if len(indices) > 2:
            for idx in indices:
                issues_by_index[idx].append(ContractIssue(
                    "observe_placeholder_repeat",
                    "观察/补证据候选主编判断摘要重复，存在模板化风险",
                    severity="warn",
                ))
    if len(observe_placeholder_indices) >= 1:
        repeated = len(observe_placeholder_indices) > 1
        for idx in observe_placeholder_indices:
            issues_by_index[idx].append(ContractIssue(
                "observe_placeholder_title_or_body",
                "观察/补证据候选仍含待补实验动作占位文案" + ("，且批内重复" if repeated else ""),
                severity="warn",
            ))
    if observe_template_indices:
        severity = "block" if len(observe_template_indices) > 2 or (len(observe_template_indices) / max(1, observe_count)) > 0.30 else "warn"
        for idx in observe_template_indices:
            issues_by_index[idx].append(ContractIssue(
                "observe_title_task_tone",
                "观察/补证据标题仍像内部测试任务或同构反思壳，请改成来源矛盾、Austin 场景或缺证据摘要",
                severity=severity,
            ))
    return issues_by_index


def apply_batch_quality_guards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    guarded = [dict(row) for row in rows]
    batch_issues = title_quality_issues(guarded)
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(guarded):
        issues = validate_field_contract(row) + batch_issues.get(idx, [])
        row["title_quality_status"] = "fail" if blocking_issues(batch_issues.get(idx, [])) else ("warn" if batch_issues.get(idx) else "pass")
        row["title_quality_issues"] = issue_messages(batch_issues.get(idx, []))
        out.append(downgrade_for_contract(row, issues))
    return out
