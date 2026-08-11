#!/usr/bin/env python3
"""Austin-style voice script helpers.

This module is intentionally deterministic. It turns a normalized Topic Card
into short, spoken sections that follow the approved third-round voice style.
"""
from __future__ import annotations

from typing import Any


VOICE_SKILL_VERSION = "austin-voice-scriptwriter-v0.1"
VOICE_STYLE_QA_STATUS = "fallback_draft / not_style_qa"
FALLBACK_NOTICE = (
    "仓库 deterministic fallback：只用于字段、格式和安全边界兜底，"
    "不代表 Austin 风格质量验收；真实内容质量请走 codex exec + -ar009-test 私有测试 Skill。"
)


def text(value: Any, fallback: str = "") -> str:
    raw = "" if value is None else str(value)
    cleaned = raw.strip().strip("。.!！?？；;，,、 ")
    return cleaned or fallback


def clip(value: Any, limit: int = 72, fallback: str = "") -> str:
    cleaned = text(value, fallback)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)].rstrip("，,；;、 ") + "…"


PUBLIC_SPOKEN_REPLACEMENTS = [
    ("有没有真的沉淀资产", "后面还能不能用"),
    ("是否沉淀资产", "后面还能不能用"),
    ("怎么沉淀资产", "怎么留下后面能用的东西"),
    ("沉淀资产", "后面能用的东西"),
    ("内容资产" + "沉淀", "内容以后还能复用"),
]


def spoken_text(value: Any, fallback: str = "") -> str:
    spoken = text(value, fallback)
    for old, new in PUBLIC_SPOKEN_REPLACEMENTS:
        spoken = spoken.replace(old, new)
    return spoken


def spoken_title(value: Any, fallback: str = "这条选题") -> str:
    return spoken_text(value, fallback)


def blob(topic: dict[str, Any]) -> str:
    keys = [
        "topic_title",
        "content_pillar",
        "core_thesis",
        "pain_point",
        "old_workflow",
        "ai_intervention",
        "production_direction",
        "unique_judgment",
        "takeaway_asset",
    ]
    return " ".join(str(topic.get(key, "")) for key in keys).lower()


def workflow_object(topic: dict[str, Any]) -> str:
    content = blob(topic)
    knowledge_like = any(term in content for term in ["知识库", "obsidian", "资料仓库", "沉淀资产", "信息雷达"])
    video_like = any(term in content for term in ["voice agent", "口播", "配音", "声音", "字幕", "分镜", "剪辑", "视频交付", "成片"])
    if any(term in content for term in ["汽车", "智能驾驶", "辅助驾驶", "l3", "l4", "国标"]):
        return "汽车内容"
    if video_like:
        return "视频交付"
    if knowledge_like:
        return "内容资料流转"
    if any(term in content for term in ["封面"]):
        return "封面"
    if any(term in content for term in ["agent", "claude", "codex", "自动执行", "任务拆解", "验收"]) and not knowledge_like:
        return "Agent项目"
    if any(term in content for term in ["选题", "brief", "主编", "候选池", "飞书"]):
        return "选题"
    if any(term in content for term in ["ppt", "pptx", "导出", "样式", "视觉", "设计"]):
        return "视觉交付"
    return "AI工作流"


def audience_activity(obj: str) -> str:
    return {
        "视频交付": "做 AI 口播和视频交付",
        "汽车内容": "用 AI 写汽车内容",
        "内容资料流转": "处理资料和内容线索",
        "封面": "做短视频封面",
        "选题": "做账号选题",
        "视觉交付": "把 AI 设计稿拿去交付",
        "Agent项目": "用 Agent 做项目",
        "AI工作流": "把 AI 接进工作流",
    }.get(obj, "把 AI 接进工作流")


def core_pain(obj: str) -> str:
    return {
        "视频交付": "不是声音生成不出来，而是脚本、声音、角色、分镜、字幕和剪辑验收接不住",
        "汽车内容": "不是写不出卖点，而是你不知道哪句话能说，哪句话必须停下来复核",
        "内容资料流转": "不是资料没有保存，而是写内容时还要重新找、重新判断、重新组织",
        "封面": "不是做不出图，而是每次都要重新想标题、比例、人物和排版",
        "选题": "不是没有热点，而是看到一堆候选后，不知道哪条真的值得拍",
        "视觉交付": "不是画面不好看，而是不知道它能不能导出、能不能改、能不能交付",
        "Agent项目": "不是它不会执行，而是它执行完以后，你反而更不确定了",
        "AI工作流": "不是工具不够多，而是工具跑完以后，你不知道结果到底能不能用",
    }.get(obj, "不是工具不够多，而是工具跑完以后，你不知道结果到底能不能用")


def concrete_questions(obj: str) -> list[str]:
    return {
        "视频交付": ["这段声音像不像这个角色？", "停顿能不能卡住分镜？", "字幕长度压不压得住？", "剪辑返修时到底改脚本、改语气，还是重新生成？"],
        "汽车内容": ["这句话有没有夸大？", "依据来自哪里？", "是不是把辅助能力说成了自动驾驶？", "发布前谁来复核？"],
        "内容资料流转": ["这条资料下次还能不能找到？", "为什么当时觉得它值得看？", "它后来有没有进选题、脚本或复盘？", "如果又要重新判断一遍，保存这一步到底省了什么？"],
        "封面": ["标题放在哪？", "人物放左边还是右边？", "不同平台比例要不要重做？", "这次风格会不会又跑偏？"],
        "选题": ["这条能不能讲？", "和我的账号有没有关系？", "观众为什么要听？", "怎么不写成新闻搬运？"],
        "视觉交付": ["导出以后样式会不会乱？", "客户要改一版时怎么办？", "哪些地方必须人工修？", "最后能不能进入交付？"],
        "Agent项目": ["它有没有漏掉关键资料？", "它中间有没有自己脑补？", "哪一步是确定的？", "哪一步其实只是猜的？"],
        "AI工作流": ["这个结果到底能不能用？", "哪些地方是 AI 猜的？", "我要改提示词，还是改任务本身？", "最后谁来判断？"],
    }.get(obj, ["这个结果到底能不能用？", "哪些地方是 AI 猜的？", "我要改提示词，还是改任务本身？", "最后谁来判断？"])


def action_names(obj: str) -> tuple[str, str, str]:
    return {
        "视频交付": ("先把脚本和角色定清楚", "把声音放回分镜、字幕和剪辑节奏里", "用返修验收决定能不能交付"),
        "汽车内容": ("先把边界说清楚", "让依据留下来", "最后人工复核"),
        "内容资料流转": ("先看资料从哪里进来", "再看它有没有接到选题和脚本", "最后看下次能不能直接用"),
        "封面": ("先读脚本", "把版式固定下来", "用规则约束质量"),
        "选题": ("先把候选翻成人话", "用账号定位筛一遍", "最后只选能拍的"),
        "视觉交付": ("先定交付标准", "再跑一次导出", "最后看人工修正点"),
        "Agent项目": ("先把任务说清楚", "让过程留下痕迹", "人工验收不能消失"),
        "AI工作流": ("先把任务说清楚", "让过程留下痕迹", "最后人工判断"),
    }.get(obj, ("先把任务说清楚", "让过程留下痕迹", "最后人工判断"))


def old_flow_bridge(obj: str, title: str) -> str:
    if obj == "视频交付":
        return "声音出来只是中间产物。脚本、角色、画面节奏、字幕和剪辑有一环接不上，最后都还是返修。"
    if obj == "内容资料流转":
        return "资料被收藏不等于进了内容系统。到写稿时还要重新判断、重新组织，这条资料就没有真的留下来。"
    if obj == "选题":
        return "候选被收进来不等于值得拍。到定题时还要重新判断，说明前面的筛选没有帮上忙。"
    return f"如果「{clip(title, 28, '这个题')}」只停在结果页，后面还是要靠人一点点判断。"


def action_close_line(obj: str, judgment: str) -> str:
    if obj == "视频交付":
        return "声音、字幕、分镜和返修表要一起看，单独一段配音不能说明它能交付。"
    if obj == "内容资料流转":
        return "如果这条资料最后不能回到选题、脚本或复盘里，就先别把它讲成方法。"
    return f"最后能不能继续拍，还是回到这个判断：{clip(judgment, 48, '结果能不能被人验收')}。"


def contrast_line(obj: str, old: str, ai_action: str) -> str:
    if obj == "视频交付":
        return "以前是脚本、配音、字幕、分镜分开验；现在我要把声音放回角色、画面节奏和返修标准里一起看。"
    if obj == "内容资料流转":
        return "以前是资料先进库，写稿时再重新找；现在要看它能不能从收件箱走到选题判断，再走到脚本路径。"
    return f"以前是「{clip(old, 38, '旧流程靠感觉判断')}」；现在我只看「{clip(ai_action, 38, 'AI动作能不能被验收')}」有没有留下证据。"


def boundary_line(obj: str, fact_text: str) -> str:
    if fact_text:
        return f"涉及事实或平台能力的地方，发布前我还会再核一遍：{fact_text}。"
    if obj == "视频交付":
        return "这条不能把声音 demo 当成成片能力，最后还要回到视频交付验收。"
    if obj == "内容资料流转":
        return "这条不能把知识库讲成万能答案，它只是在检查素材有没有进入内容生产链路。"
    return "这条不能把一次生成当成最终结论，还是要看真实任务里的人工判断。"


def first_clause(value: Any, limit: int = 14, fallback: str = "这一段") -> str:
    cleaned = spoken_text(value, fallback)
    for sep in ["，", "。", "；", "：", "、"]:
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0]
            break
    return clip(cleaned, limit, fallback)


def section_label(value: Any, fallback: str) -> str:
    label = first_clause(value, 16, fallback)
    return label.replace("以前", "旧方式").replace("我会", "").strip("：:，,。 ")


def question_lines(questions: list[str], limit: int = 3) -> list[str]:
    return [question.rstrip("。！？?") + "？" for question in questions[:limit]]


def render_voice_sections(topic: dict[str, Any], context: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    context = context or {}
    obj = workflow_object(topic)
    title = spoken_title(topic.get("topic_title"), "这条选题")
    core = spoken_text(topic.get("core_thesis"), title)
    judgment = spoken_text(topic.get("unique_judgment"), core)
    pain = spoken_text(topic.get("pain_point") or topic.get("old_workflow"), core_pain(obj))
    old = spoken_text(topic.get("old_workflow"), pain)
    ai_action = spoken_text(topic.get("ai_intervention"), "让 AI 介入一个能被检查的小环节")
    evidence = spoken_text(context.get("evidence_text"), "输入、输出、错误点和人工修改记录")
    todos = spoken_text(context.get("todo_text"), "一段真实录屏和一个失败样例")
    fact_text = spoken_text(context.get("fact_text"), "")
    concept_material = spoken_text(context.get("concept_method_text"), "")
    boundary = boundary_line(obj, fact_text)

    return [
        (
            "00:00-00:20｜fallback_draft｜not_style_qa｜旧方式字段",
            "\n\n".join(
                [
                    FALLBACK_NOTICE,
                    f"选题：{clip(title, 64, '这条选题')}。",
                    f"旧方式字段：{old}。",
                    f"真实卡点字段：{pain}。",
                ]
            ),
        ),
        (
            "00:20-00:50｜fallback_draft｜not_style_qa｜方法素材",
            "\n\n".join(
                [
                    f"核心观点字段：{core}。",
                    f"当前工具/热点字段：{title}。",
                    f"生成前判断素材：{clip(concept_material, 120, '旧方式、真实卡点、为什么现在需要它、如何回到自己的工作流')}。",
                ]
            ).strip(),
        ),
        (
            "00:50-01:20｜fallback_draft｜not_style_qa｜验证素材",
            "\n\n".join(
                [
                    f"AI介入字段：{ai_action}。",
                    f"可见证据字段：{evidence}。",
                    f"拍摄待补字段：{todos}。",
                ]
            ),
        ),
        (
            "01:20-01:45｜fallback_draft｜not_style_qa｜边界字段",
            "\n\n".join(
                [
                    f"主编判断字段：{judgment}。",
                    f"事实/发布边界字段：{boundary}。",
                    "状态：not_style_qa，本段不能作为 PM 或用户验收 Austin 风格的样例。",
                ]
            ).strip(),
        ),
    ]


def render_voice_text(topic: dict[str, Any], context: dict[str, Any] | None = None) -> str:
    return "\n\n".join(f"### {heading}\n\n{body}" for heading, body in render_voice_sections(topic, context))
