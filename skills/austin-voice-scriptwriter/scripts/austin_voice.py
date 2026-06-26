#!/usr/bin/env python3
"""Austin-style voice script helpers.

This module is intentionally deterministic. It turns a normalized Topic Card
into short, spoken sections that follow the approved third-round voice style.
"""
from __future__ import annotations

from typing import Any


VOICE_SKILL_VERSION = "austin-voice-scriptwriter-v0.1"


def text(value: Any, fallback: str = "") -> str:
    raw = "" if value is None else str(value)
    cleaned = raw.strip().strip("。.!！?？；;，,、 ")
    return cleaned or fallback


def clip(value: Any, limit: int = 72, fallback: str = "") -> str:
    cleaned = text(value, fallback)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)].rstrip("，,；;、 ") + "…"


def blob(topic: dict[str, Any]) -> str:
    keys = [
        "topic_title",
        "content_pillar",
        "core_thesis",
        "pain_point",
        "old_workflow",
        "ai_intervention",
        "production_direction",
        "takeaway_asset",
    ]
    return " ".join(str(topic.get(key, "")) for key in keys).lower()


def workflow_object(topic: dict[str, Any]) -> str:
    content = blob(topic)
    if any(term in content for term in ["汽车", "智能驾驶", "辅助驾驶", "l3", "l4", "国标"]):
        return "汽车内容"
    if any(term in content for term in ["封面"]):
        return "封面"
    if any(term in content for term in ["选题", "brief", "主编", "候选池", "飞书"]):
        return "选题"
    if any(term in content for term in ["ppt", "pptx", "导出", "样式", "视觉", "设计"]):
        return "视觉交付"
    if any(term in content for term in ["agent", "claude", "codex", "自动执行", "任务拆解", "验收"]):
        return "Agent项目"
    return "AI工作流"


def audience_activity(obj: str) -> str:
    return {
        "汽车内容": "用 AI 写汽车内容",
        "封面": "做短视频封面",
        "选题": "做账号选题",
        "视觉交付": "把 AI 设计稿拿去交付",
        "Agent项目": "用 Agent 做项目",
        "AI工作流": "把 AI 接进工作流",
    }.get(obj, "把 AI 接进工作流")


def core_pain(obj: str) -> str:
    return {
        "汽车内容": "不是写不出卖点，而是你不知道哪句话能说，哪句话必须停下来复核",
        "封面": "不是做不出图，而是每次都要重新想标题、比例、人物和排版",
        "选题": "不是没有热点，而是看到一堆候选后，不知道哪条真的值得拍",
        "视觉交付": "不是画面不好看，而是不知道它能不能导出、能不能改、能不能交付",
        "Agent项目": "不是它不会执行，而是它执行完以后，你反而更不确定了",
        "AI工作流": "不是工具不够多，而是工具跑完以后，你不知道结果到底能不能用",
    }.get(obj, "不是工具不够多，而是工具跑完以后，你不知道结果到底能不能用")


def concrete_questions(obj: str) -> list[str]:
    return {
        "汽车内容": ["这句话有没有夸大？", "依据来自哪里？", "是不是把辅助能力说成了自动驾驶？", "发布前谁来复核？"],
        "封面": ["标题放在哪？", "人物放左边还是右边？", "不同平台比例要不要重做？", "这次风格会不会又跑偏？"],
        "选题": ["这条能不能讲？", "和我的账号有没有关系？", "观众为什么要听？", "怎么不写成新闻搬运？"],
        "视觉交付": ["导出以后样式会不会乱？", "客户要改一版时怎么办？", "哪些地方必须人工修？", "最后能不能进入交付？"],
        "Agent项目": ["它有没有漏掉关键资料？", "它中间有没有自己脑补？", "哪一步是确定的？", "哪一步其实只是猜的？"],
        "AI工作流": ["这个结果到底能不能用？", "哪些地方是 AI 猜的？", "我要改提示词，还是改任务本身？", "最后谁来判断？"],
    }.get(obj, ["这个结果到底能不能用？", "哪些地方是 AI 猜的？", "我要改提示词，还是改任务本身？", "最后谁来判断？"])


def action_names(obj: str) -> tuple[str, str, str]:
    return {
        "汽车内容": ("先把边界说清楚", "让依据留下来", "最后人工复核"),
        "封面": ("先读脚本", "把版式固定下来", "用规则约束质量"),
        "选题": ("先把候选翻成人话", "用账号定位筛一遍", "最后只选能拍的"),
        "视觉交付": ("先定交付标准", "再跑一次导出", "最后看人工修正点"),
        "Agent项目": ("先把任务说清楚", "让过程留下痕迹", "人工验收不能消失"),
        "AI工作流": ("先把任务说清楚", "让过程留下痕迹", "最后人工判断"),
    }.get(obj, ("先把任务说清楚", "让过程留下痕迹", "最后人工判断"))


def render_voice_sections(topic: dict[str, Any], context: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    context = context or {}
    obj = workflow_object(topic)
    activity = audience_activity(obj)
    title = text(topic.get("topic_title"), "这条选题")
    core = text(topic.get("core_thesis"), title)
    pain = text(topic.get("pain_point") or topic.get("old_workflow"), core_pain(obj))
    old = text(topic.get("old_workflow"), pain)
    ai_action = text(topic.get("ai_intervention"), "让 AI 介入一个能被检查的小环节")
    direction = text(topic.get("production_direction"), "")
    evidence = context.get("evidence_text") or "输入、输出、错误点和人工修改记录"
    todos = context.get("todo_text") or "一段真实录屏和一个失败样例"
    fact_text = context.get("fact_text") or ""
    act1, act2, act3 = action_names(obj)
    questions = concrete_questions(obj)

    direction_line = f"\n\n如果这条你已经补了制作方向，我会按这个方向收住：{direction}。" if direction else ""
    fact_line = f"\n\n涉及事实或平台能力的地方，发布前我还会再核一遍：{fact_text}。" if fact_text else ""

    return [
        (
            "00:00-00:35｜真实痛点",
            "\n\n".join(
                [
                    f"很多人现在{activity}，最痛苦的{core_pain(obj)}。",
                    f"你可能也有这种感觉。",
                    *questions,
                    "你要是真的把它放进项目里，这些问题就不能靠感觉。",
                ]
            ),
        ),
        (
            "00:35-01:05｜旧流程",
            "\n\n".join(
                [
                    "因为最后很容易变成一个特别熟悉的流程。",
                    old,
                    "看起来是用了 AI，实际上只是把焦虑从开始之前，挪到了结果出来之后。",
                    direction_line.strip(),
                ]
            ).strip(),
        ),
        (
            "01:05-01:35｜这条真正要做什么",
            "\n\n".join(
                [
                    f"所以这条我不想把「{title}」讲成工具教程。",
                    f"我真正想做的是：{core}。",
                    "说白了，我不是想让 AI 多生成几段话。",
                    "我是想让它每次交付的时候，都把“我做了什么、哪里没把握、你该看哪里”一起交出来。",
                ]
            ),
        ),
        (
            "01:35-02:50｜三个动作",
            "\n\n".join(
                [
                    "我现在先把它拆成三个动作。",
                    f"第一个，不是先让 AI 开始，而是{act1}。",
                    "这一步如果不清楚，后面它做得越快，你越难判断。",
                    f"第二个，是{act2}。",
                    f"这里我会重点看：{evidence}。",
                    f"第三个，是{act3}。",
                    "我不会让 AI 自己给自己打通过。最后能不能用，还是要回到我的判断。",
                ]
            ),
        ),
        (
            "02:50-03:35｜前后对比",
            "\n\n".join(
                [
                    "你看，这才是我觉得 AI 真正进入工作流的关键。",
                    "不是它能不能生成内容。",
                    "而是它生成完以后，你有没有办法判断它、修正它、复用它。",
                    "以前是我把任务丢出去，等一个结果，再靠经验一点点看。",
                    "现在我希望任务一开始就带着规则，过程中留下痕迹，最后由我来判断能不能用。",
                ]
            ),
        ),
        (
            "03:35-04:10｜边界和收尾",
            "\n\n".join(
                [
                    "但这里也有边界。",
                    "它不是万能的。",
                    "它不能替你判断一个项目值不值得做，也不能替你判断一个观点有没有真正的业务价值。",
                    f"拍之前我至少还要补：{todos}。",
                    fact_line.strip(),
                    "它解决的是另一个问题。",
                    "不要让 AI 看起来完成了，但你心里还是没底。",
                    "AI 负责把事情往前推，规则负责把过程留下来，人负责最后判断能不能用。",
                ]
            ).strip(),
        ),
    ]


def render_voice_text(topic: dict[str, Any], context: dict[str, Any] | None = None) -> str:
    return "\n\n".join(f"### {heading}\n\n{body}" for heading, body in render_voice_sections(topic, context))

