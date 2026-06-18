#!/usr/bin/env python3
"""Run the global ai-account-editorial-director Skill on topic candidates.

The collection pipeline still handles source capture, normalization, dedupe, and
rough candidate generation. This runner is the editorial layer: by default it
loads the global Skill and its persona/case reference, asks the locally
authenticated Codex CLI to make the batch judgement, and writes the Skill output
contract back to the candidate CSV.

`--engine deterministic` is kept only as an explicit emergency fallback for
offline debugging. It is not the default path.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = Path.home() / ".codex" / "skills" / "ai-account-editorial-director"
SKILL_MD = SKILL_DIR / "SKILL.md"
SKILL_REFERENCE = SKILL_DIR / "references" / "persona-and-cases.md"
SKILL_PERSONA_BRIEF = SKILL_DIR / "references" / "persona-brief.md"

EXTRA_FIELDS = [
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
    "我的思考点",
    "重点体现",
    "可调用案例",
    "内容核心冲突",
    "视频呈现方式",
    "证据强度",
    "Skill编辑层",
    "Skill参考文件",
]

SKILL_FIELDS = [
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

CANDIDATE_CONTEXT_FIELDS = [
    "我的选题标题",
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
]

MOTHER_SCENES = [
    {
        "name": "AI账号信息雷达 / 飞书执行台",
        "keywords": ["飞书", "选题", "Brief", "内容收件箱", "信息雷达", "AIHOT", "候选池", "主编", "Skill", "公众号", "抖音采样"],
        "borrow": "借用用户正在搭的 AI账号信息雷达：采集、去重、摘要、字段化判断、主编Skill、从热点到Brief。",
        "can_show": "飞书字段、候选池、判断规则、主编备注、状态流转、从内容到Brief的链路。",
        "cannot_claim": "不能说已经完全自动替用户决定观点；只能说系统辅助筛选和主编判断。",
    },
    {
        "name": "商业视频 / AI导演工作流",
        "keywords": ["视频", "短剧", "短片", "分镜", "镜头", "导演", "成片", "剪辑", "口播", "配音", "Runway", "Kling", "Luma", "Seedance", "广告视频", "动画"],
        "borrow": "借用用户商业视频交付经验：Brief、分镜、角色资产、镜头、返修、审美和成片验收。",
        "can_show": "分镜表、镜头验收、角色一致性、成片前后对比、返修清单。",
        "cannot_claim": "没有转写或完整素材时，不能声称看过口播全文、评论区、镜头结构或完整视频。",
    },
    {
        "name": "内容生产自动化 Skill",
        "keywords": ["封面", "首图", "卡片", "小红书", "图文", "长文", "PPT", "排版", "HTML", "CSS", "品牌一致性", "视觉物料", "导出"],
        "borrow": "借用用户封面自动化、公众号长文转小红书、PPT/图文卡片等内容生产Skill经验。",
        "can_show": "脚本理解、标题提炼、卡片页规划、排版QA、品牌规则、跨平台适配。",
        "cannot_claim": "不能把单个生图/设计更新说成已解决完整内容策略。",
    },
    {
        "name": "Agent / AI业务系统",
        "keywords": ["Agent", "智能体", "Claude", "Codex", "MCP", "自动化", "任务", "验收", "目录", "文件夹", "状态", "框架", "Eve", "Omnigent"],
        "borrow": "借用用户把AI接进业务系统的思路：任务边界、输入输出、状态、失败记录、验收标准和资产化。",
        "can_show": "任务表、验收表、目录结构、状态流转、失败样例、输入输出字段。",
        "cannot_claim": "不能说非技术人无成本完成复杂工程系统；只能讲轻量任务边界和业务验收。",
    },
    {
        "name": "汽车与内容营销",
        "keywords": ["汽车", "车企", "车主", "发布会", "品牌", "营销", "传播", "CEO", "IP", "信任", "带货", "素材审核", "Shein"],
        "borrow": "借用用户车企/品牌/内容营销业务现场：传统传播慢、内容资产复用差、AI Native改造营销流程。",
        "can_show": "内容资产流、品牌一致性审核、素材风险清单、车主运营/发布会传播复盘。",
        "cannot_claim": "不能做普通车评、行情判断或泛汽车资讯。",
    },
]

HOT_HOOK_TERMS = [
    "Claude Code",
    "Claude Design",
    "Claude",
    "Codex",
    "Vercel Eve",
    "Eve",
    "Omnigent",
    "MCP",
    "Agent",
    "Seedance",
    "小云雀",
    "Runway",
    "Kling",
    "Luma",
    "Sora",
    "Midjourney",
    "Ideogram",
    "豆包",
    "Gemini",
    "GPT",
    "OpenAI",
    "MOSS-TTS",
    "SGLang-Omni",
    "Kickart",
    "baoyu-design",
]

ALLOWED_LEVELS = {"今日最值得做", "可选候选", "暂存观察", "不建议制作"}
LEVEL_ALIASES = {
    "备选": "可选候选",
    "备选候选": "可选候选",
    "备选，不占今日前三": "可选候选",
    "候选": "可选候选",
    "可做候选": "可选候选",
    "进入候选": "可选候选",
    "观察": "暂存观察",
    "暂存": "暂存观察",
    "待观察": "暂存观察",
    "不做": "不建议制作",
    "放弃": "不建议制作",
    "不推荐": "不建议制作",
}
NON_PUBLISH_LEVELS = {"暂存观察", "不建议制作"}

DIRECTION_ALIASES = {
    "AI汽车与品牌增长": "汽车与内容营销",
    "AI导演工作流与视频交付": "AI导演工作流",
    "内容团队选题到Brief流程": "真实工作流改造",
    "Agent任务验收": "真实工作流改造",
}

CASE_RULES = [
    (
        ("分镜", "镜头", "AI视频", "短剧", "成片", "导演", "Runway", "Kling", "Luma", "Seedance", "视频模型"),
        "Neurovia AI全球宣传片导演工作流 / Austin AIGC商业视频交付Skill",
    ),
    (
        ("封面", "首图", "卡片", "小红书", "长文", "图文", "视觉物料", "公众号"),
        "Social Media Cover封面自动化Skill / 公众号长文转小红书图文卡片",
    ),
    (
        ("飞书", "选题", "Brief", "内容收件箱", "信息雷达", "AIHOT", "候选池"),
        "从全网AI热点到飞书选题台",
    ),
    (
        ("Agent", "Claude", "Codex", "MCP", "自动化", "项目", "验收", "生产环境", "工作流"),
        "从全网AI热点到飞书选题台 / RunBY AI CMO Agent",
    ),
    (
        ("PPT", "方案", "汇报", "页面", "商业表达"),
        "RunBY AI CMO Agent / MuseIn产品化与出海传播判断",
    ),
    (
        ("汽车", "车企", "车主", "高管IP", "发布会", "品牌", "营销", "传播", "信任"),
        "电车奥利奥与车企内容营销场景 / RunBY AI CMO Agent",
    ),
    (
        ("产品化", "出海", "GTM", "社区", "模板", "MuseIn"),
        "MuseIn产品化与出海传播判断",
    ),
]


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def atomic_write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", delete=False, dir=str(path.parent)) as handle:
        tmp = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def intish(value: Any) -> int:
    try:
        return int(float(str(value or 0)))
    except ValueError:
        return 0


def compact_text(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", (value or "").lower())


def source_title_values(row: dict[str, str]) -> list[str]:
    values: list[str] = []
    for field in ["原始来源标题", "来源内容", "来源标题"]:
        value = (row.get(field, "") or "").strip()
        if value:
            values.append(value)
    return values


def is_same_as_source(title: str, row: dict[str, str]) -> bool:
    normalized = compact_text(title)
    if not normalized:
        return False
    for source in source_title_values(row):
        source_norm = compact_text(source)
        if normalized and source_norm and normalized == source_norm:
            return True
    return False


def normalize_level(value: str) -> str:
    cleaned = (value or "").strip()
    if cleaned in ALLOWED_LEVELS:
        return cleaned
    if cleaned in LEVEL_ALIASES:
        return LEVEL_ALIASES[cleaned]
    for key, target in LEVEL_ALIASES.items():
        if key and key in cleaned:
            return target
    if "最值得" in cleaned or cleaned in {"S", "强推"}:
        return "今日最值得做"
    if "不建议" in cleaned or "放弃" in cleaned:
        return "不建议制作"
    if "暂存" in cleaned or "观察" in cleaned:
        return "暂存观察"
    if cleaned:
        return "可选候选"
    return "暂存观察"


def normalize_skill_row(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    level = normalize_level(out.get("今日建议级别") or out.get("候选状态"))
    out["今日建议级别"] = level
    out["候选状态"] = level

    publishable = (out.get("可发布标题", "") or "").strip()
    alternatives = (out.get("标题备选", "") or "").strip()
    score = intish(out.get("编辑判断分") or out.get("推荐分"))
    title_score = intish(out.get("标题质量分"))

    if level in NON_PUBLISH_LEVELS:
        if publishable or alternatives:
            reason = out.get("不建议做的原因") or out.get("降级原因") or out.get("推荐动作原因")
            extra = "暂存/不建议项不生成可发布标题，避免把内部判断误当成发布选题。"
            out["不建议做的原因"] = f"{reason}；{extra}".strip("；")
        out["可发布标题"] = ""
        out["标题备选"] = ""
        out["是否建议进入制作"] = "否"
        if level == "不建议制作":
            out["推荐动作"] = "放弃"
        elif out.get("推荐动作") not in {"补证据", "存素材", "观察"}:
            out["推荐动作"] = "观察"
        return out

    if not publishable:
        reason = out.get("不建议做的原因") or out.get("降级原因") or out.get("推荐动作原因")
        extra = "Skill 没有给出可发布标题，先降级为暂存观察，避免无标题候选进入前台。"
        out["今日建议级别"] = "暂存观察"
        out["候选状态"] = "暂存观察"
        out["可发布标题"] = ""
        out["标题备选"] = ""
        out["是否建议进入制作"] = "否"
        out["推荐动作"] = "观察"
        out["降级原因"] = f"{reason}；{extra}".strip("；")
        out["不建议做的原因"] = out["降级原因"]
        return out

    if publishable and is_same_as_source(publishable, out):
        reason = out.get("降级原因") or out.get("推荐动作原因") or out.get("不建议做的原因")
        extra = "可发布标题与原始来源标题相同，说明还没有转成用户自己的表达，先降级为暂存观察。"
        out["今日建议级别"] = "暂存观察"
        out["候选状态"] = "暂存观察"
        out["可发布标题"] = ""
        out["标题备选"] = ""
        out["是否建议进入制作"] = "否"
        out["推荐动作"] = "观察"
        out["降级原因"] = f"{reason}；{extra}".strip("；")
        out["不建议做的原因"] = out["降级原因"]
        return out

    if level == "今日最值得做":
        out["是否建议进入制作"] = "是"
    elif out.get("推荐等级") == "S" and "是" in (out.get("是否建议进入制作") or "") and score >= 90 and title_score >= 85:
        out["今日建议级别"] = "今日最值得做"
        out["候选状态"] = "今日最值得做"
        out["是否建议进入制作"] = "是"
    elif not out.get("是否建议进入制作"):
        out["是否建议进入制作"] = "否"
    if publishable:
        out["我的选题标题"] = publishable
        out["选题标题"] = publishable
    return out


def wants_top_today(row: dict[str, str]) -> bool:
    if row.get("今日建议级别") == "今日最值得做":
        return True
    if row.get("推荐等级") != "S":
        return False
    if "是" not in (row.get("是否建议进入制作") or ""):
        return False
    text = "\n".join([
        row.get("主编判断", ""),
        row.get("推荐理由", ""),
        row.get("一句话Brief", ""),
    ])
    return any(term in text for term in ["今日必须", "今日值得", "必须做", "最值得", "强人设", "主选题"])


def normalize_batch(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized = [normalize_skill_row(row) for row in rows]
    top_candidates = [idx for idx, row in enumerate(normalized) if wants_top_today(row)]
    if not top_candidates:
        scored = [
            (intish(row.get("编辑判断分")), intish(row.get("标题质量分")), idx)
            for idx, row in enumerate(normalized)
            if row.get("推荐等级") == "S"
            and "是" in (row.get("是否建议进入制作") or "")
            and row.get("可发布标题", "").strip()
        ]
        top_candidates = [idx for _score, _title_score, idx in sorted(scored, reverse=True)[:3]]
    for idx in top_candidates[:3]:
        normalized[idx]["今日建议级别"] = "今日最值得做"
        normalized[idx]["候选状态"] = "今日最值得做"
        normalized[idx]["是否建议进入制作"] = "是"
    for idx in top_candidates[3:]:
        if normalized[idx].get("今日建议级别") == "今日最值得做":
            normalized[idx]["今日建议级别"] = "可选候选"
            normalized[idx]["候选状态"] = "可选候选"
    return normalized


def blob(row: dict[str, str]) -> str:
    return "\n".join(str(row.get(key, "")) for key in [
        "原始来源标题", "来源内容", "来源标题", "可发布标题", "内部切入角度", "我的蹭热点角度", "业务场景",
        "旧流程痛点", "AI介入点", "可展示结果", "可沉淀资产", "推荐理由",
    ])


def matched_mother_scenes(row: dict[str, str], limit: int = 3) -> list[dict[str, str]]:
    text = blob(row).lower()
    matches: list[tuple[int, dict[str, str]]] = []
    for scene in MOTHER_SCENES:
        score = sum(1 for keyword in scene["keywords"] if keyword.lower() in text)
        if score:
            matches.append((score, scene))
    if not matches:
        direction = normalize_direction(row.get("对应栏目", ""))
        fallback_name = {
            "AI业务定调": "AI账号信息雷达 / 飞书执行台",
            "真实工作流改造": "AI账号信息雷达 / 飞书执行台",
            "AI导演工作流": "商业视频 / AI导演工作流",
            "汽车与内容营销": "汽车与内容营销",
            "AI项目复盘": "Agent / AI业务系统",
        }.get(direction, "AI账号信息雷达 / 飞书执行台")
        matches = [(1, scene) for scene in MOTHER_SCENES if scene["name"] == fallback_name]
    return [scene for _score, scene in sorted(matches, key=lambda item: item[0], reverse=True)[:limit]]


def hot_hook(row: dict[str, str]) -> str:
    text = " ".join([
        row.get("原始来源标题", ""),
        row.get("来源内容", ""),
        row.get("来源标题", ""),
        row.get("我的选题标题", ""),
        row.get("事件锚点", ""),
        row.get("相关来源", ""),
    ])
    lower_text = text.lower()
    found: list[str] = []
    for term in HOT_HOOK_TERMS:
        if term.lower() in lower_text and term not in found:
            found.append(term)
    if found:
        return " / ".join(found[:3])
    tokens = re.findall(r"\b[A-Z][A-Za-z0-9._-]{2,}(?:\s+[A-Z][A-Za-z0-9._-]{2,})?\b", text)
    ignored = {"AI", "API", "RSS", "URL", "HTML", "CSS", "PPT", "TTS", "ASR", "QA"}
    for token in tokens:
        cleaned = token.strip()
        if cleaned and cleaned not in ignored:
            return cleaned[:80]
    return ""


def normalize_direction(value: str) -> str:
    value = (value or "").strip()
    value = DIRECTION_ALIASES.get(value, value)
    if value in {"AI业务定调", "真实工作流改造", "AI导演工作流", "汽车与内容营销", "AI项目复盘"}:
        return value
    return "真实工作流改造"


def grade(row: dict[str, str]) -> str:
    score = intish(row.get("编辑判断分") or row.get("推荐分"))
    level = row.get("今日建议级别", "")
    if level == "今日最值得做" or score >= 90:
        return "S"
    if level == "可选候选" or score >= 78:
        return "A"
    if level == "暂存观察" or score >= 68:
        return "B"
    return "C"


def evidence_strength(row: dict[str, str]) -> str:
    credibility = row.get("内容可信度", "")
    support = row.get("是否有足够内容支撑", "")
    if credibility == "全文" or support == "足够":
        return "强"
    if credibility in {"AIHOT摘要", "摘要", "摘要可用", "抖音浅层", "抖音转写"} or support in {"摘要可用", "浅层"}:
        return "中"
    return "弱"


def callable_case(row: dict[str, str]) -> str:
    if row.get("关联母场景"):
        borrow = row.get("借用方式", "")
        return f"{row['关联母场景']}；{borrow}".strip("；")
    text = blob(row)
    for terms, case in CASE_RULES:
        if any(term.lower() in text.lower() for term in terms):
            return case
    direction = normalize_direction(row.get("对应栏目", ""))
    fallback = {
        "AI业务定调": "RunBY AI CMO Agent / MuseIn产品化与出海传播判断",
        "真实工作流改造": "从全网AI热点到飞书选题台",
        "AI导演工作流": "Neurovia AI全球宣传片导演工作流",
        "汽车与内容营销": "电车奥利奥与车企内容营销场景",
        "AI项目复盘": "Austin AIGC商业视频交付Skill",
    }
    return fallback.get(direction, "从全网AI热点到飞书选题台")


def scene_basis(row: dict[str, str]) -> str:
    text = blob(row).lower()
    true_case_terms = [
        "飞书", "信息雷达", "选题台", "brief", "商业动画", "austin", "neurovia",
        "封面", "小红书", "runby", "musein", "汽车", "车企", "电车奥利奥",
    ]
    if any(term in text for term in true_case_terms):
        return "真实案例"
    if matched_mother_scenes(row):
        return "相邻推演"
    return "仅热点观察"


def ordinary_take(row: dict[str, str]) -> str:
    hook = hot_hook(row) or row.get("来源内容", "")[:24]
    return f"普通资讯号大概率会讲「{hook}」发布了什么、能力多强、怎么使用。"


def my_take(row: dict[str, str]) -> str:
    scene = matched_mother_scenes(row)[0]["name"]
    hook = hot_hook(row)
    prefix = f"我会保留「{hook}」这个热点入口，" if hook else "我会借这个话题，"
    return f"{prefix}但落点不是资讯，而是把它放进「{scene}」里验证旧流程哪一步能被AI重写。"


def transformation_action(row: dict[str, str]) -> str:
    basis = scene_basis(row)
    scene = matched_mother_scenes(row)[0]["name"]
    if basis == "真实案例":
        return f"拿已有「{scene}」相关案例做一次改造复盘：旧流程、AI介入点、人保留的判断、可展示资产都要说清。"
    if basis == "相邻推演":
        return f"基于「{scene}」做相邻验证：先设计字段表、流程图或验收清单，再决定是否进入Brief。"
    return "先只观察热度和来源证据，等能接到具体业务动作或案例素材后再做。"


def scene_breakdown(row: dict[str, str]) -> str:
    scene = row.get("业务场景") or normalize_direction(row.get("对应栏目", ""))
    pain = row.get("旧流程痛点", "")
    intervention = row.get("AI介入点", "")
    result = row.get("可展示结果", "")
    if pain and intervention and result:
        return f"我会把它接到「{scene}」：旧流程卡在{pain}；AI介入点是{intervention}；最后要展示{result}。"
    angle = row.get("我的蹭热点角度") or row.get("我能讲出的独特角度") or row.get("内部切入角度")
    return f"我会把它接到「{scene}」里讲，不停留在来源事件本身，而是拆它如何进入我的内容生产、交付或业务判断。{angle}".strip()


def thinking_point(row: dict[str, str]) -> str:
    direction = normalize_direction(row.get("对应栏目", ""))
    source = row.get("来源内容", "")
    if direction == "AI导演工作流":
        return "我不只看它能不能生成，而是看它能不能进入分镜、资产、镜头、返修和验收这条导演式交付链。"
    if direction == "汽车与内容营销":
        return "我会把它放进品牌传播、车主运营或内容资产流里，看 AI 改的是哪段人力密集流程。"
    if direction == "AI业务定调":
        return "我会先判断这个变化是否真的改变业务现场，而不是把它当成一条 AI 新闻复述。"
    if direction == "AI项目复盘":
        return "我会用项目复盘方式讲它：需求、执行、异常、验收和资产沉淀哪一步被改变。"
    if "抖音" in row.get("来源类型", "") or "对标视频" in row.get("来源类型", ""):
        return "我会吸收它的选题承诺和结构，但标题和表达要转成自己的生产现场，不露出对标账号。"
    if source:
        return "我会从来源里抽出一个真实流程问题，再用自己的项目经验判断它值不值得推进。"
    return "我会先问：它能不能改造一个真实流程，能不能展示结果，能不能沉淀资产。"


def key_emphasis(row: dict[str, str]) -> str:
    asset = row.get("可沉淀资产", "")
    result = row.get("可展示结果", "")
    case = callable_case(row)
    if asset and result:
        return f"重点体现：不是讲来源多热，而是展示{result}，并沉淀成{asset}。可调用案例：{case}。"
    if asset:
        return f"重点体现：把这条内容变成可复用资产：{asset}。可调用案例：{case}。"
    return f"重点体现：用真实案例证明 AI 进入流程后的变化，而不是停在工具介绍。可调用案例：{case}。"


def core_conflict(row: dict[str, str]) -> str:
    direction = normalize_direction(row.get("对应栏目", ""))
    if direction == "AI导演工作流":
        return "漂亮生成片段 vs 可交付成片；工具演示 vs 导演式执行工作流。"
    if direction == "汽车与内容营销":
        return "传统人力密集传播流程 vs AI Native 内容资产流。"
    if direction == "AI业务定调":
        return "AI资讯复述 vs 业务现场判断。"
    if direction == "AI项目复盘":
        return "功能能跑 vs 项目可验收、可复用、可交付。"
    return "旧流程靠人肉搬运 vs AI把判断、流程、资产和结果重新编排。"


def presentation(row: dict[str, str]) -> str:
    direction = normalize_direction(row.get("对应栏目", ""))
    if direction == "AI导演工作流":
        return "口播 + 分镜/镜头/成片对比 + 返修或验收画面"
    if direction == "汽车与内容营销":
        return "口播 + 发布前后内容资产流流程图 + 车企/品牌场景拆解"
    if direction == "AI业务定调":
        return "口播短评 + 业务影响三段式 + 自己系统里的验证点"
    if direction == "AI项目复盘":
        return "项目复盘 + 飞书/流程图/验收字段展示"
    return "口播 + 屏幕录制 + 旧流程/新流程对比图"


def one_sentence_brief(row: dict[str, str]) -> str:
    title = row.get("可发布标题") or row.get("我的选题标题") or row.get("来源内容", "")
    scene = row.get("业务场景") or normalize_direction(row.get("对应栏目", ""))
    asset = row.get("可沉淀资产", "")
    if asset:
        return f"用「{scene}」这个真实场景，把 {title} 拆成一个能沉淀为「{asset}」的流程判断。"
    return f"用「{scene}」这个真实场景，判断 {title} 能不能变成我的业务现场选题。"


def enrich(row: dict[str, str]) -> dict[str, str]:
    direction = normalize_direction(row.get("对应栏目", ""))
    scene = matched_mother_scenes(row)[0]
    out = dict(row)
    out["热点钩子"] = row.get("热点钩子") or hot_hook(row)
    out["普通人会怎么讲"] = row.get("普通人会怎么讲") or ordinary_take(row)
    out["我会怎么讲"] = row.get("我会怎么讲") or my_take(row)
    out["场景依据"] = row.get("场景依据") or scene_basis(row)
    out["真实/相邻案例"] = row.get("真实/相邻案例") or callable_case(out)
    out["我的改造动作"] = row.get("我的改造动作") or transformation_action(row)
    out["需要补的证据"] = row.get("需要补的证据") or ("补自己的流程截图、字段表、验收清单或项目素材。" if out["场景依据"] != "真实案例" else "补可展示截图或过程证据，避免只讲观点。")
    out["关联母场景"] = row.get("关联母场景") or scene["name"]
    out["借用方式"] = row.get("借用方式") or scene["borrow"]
    out["不能声称的部分"] = row.get("不能声称的部分") or scene["cannot_claim"]
    out["我的真实/相邻场景"] = row.get("我的真实/相邻场景") or scene["can_show"]
    out["候选状态"] = row.get("今日建议级别") or row.get("是否建议进入制作") or "暂存观察"
    out["推荐等级"] = grade(row)
    out["对应方向"] = direction
    out["一句话Brief"] = row.get("一句话Brief") or one_sentence_brief(out)
    out["我的场景拆解"] = row.get("我的场景拆解") or scene_breakdown(out)
    out["我的思考点"] = row.get("我的思考点") or thinking_point(out)
    out["重点体现"] = row.get("重点体现") or key_emphasis(out)
    out["可调用案例"] = row.get("可调用案例") or callable_case(out)
    out["内容核心冲突"] = row.get("内容核心冲突") or core_conflict(out)
    out["视频呈现方式"] = row.get("视频呈现方式") or presentation(out)
    out["证据强度"] = row.get("证据强度") or evidence_strength(out)
    out["Skill编辑层"] = "ai-account-editorial-director"
    out["Skill参考文件"] = str(SKILL_REFERENCE)
    return out


def fieldnames_for(rows: list[dict[str, str]], original: list[str]) -> list[str]:
    names = list(original)
    for field in EXTRA_FIELDS:
        if field not in names:
            names.append(field)
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)
    return names


def compact_candidate(row: dict[str, str], index: int) -> dict[str, str | int]:
    payload: dict[str, str | int] = {"index": index}
    for field in CANDIDATE_CONTEXT_FIELDS:
        value = row.get(field, "")
        if value:
            payload[field] = value[:1800]
    payload["关联母场景候选"] = json.dumps(matched_mother_scenes(row), ensure_ascii=False)
    payload["热点钩子候选"] = hot_hook(row)
    payload["场景依据候选"] = scene_basis(row)
    return payload


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required Skill file: {path}")
    return path.read_text(encoding="utf-8")


def codex_output_schema() -> dict[str, Any]:
    row_properties: dict[str, Any] = {"index": {"type": "integer", "minimum": 0}}
    for field in SKILL_FIELDS:
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
                    "required": ["index", *SKILL_FIELDS],
                },
            },
            "batch_notes": {"type": "string"},
        },
        "required": ["engine", "rows", "batch_notes"],
    }


def build_codex_prompt(rows: list[dict[str, str]]) -> str:
    skill_text = load_text(SKILL_MD)
    persona_brief = load_text(SKILL_PERSONA_BRIEF)
    candidates = [compact_candidate(row, idx) for idx, row in enumerate(rows)]
    return f"""你现在必须使用全局 Skill `ai-account-editorial-director` 做主编判断。

这是一次生产管线里的批量选题筛选，不是泛泛润色。请直接基于下面的 Skill 和案例库判断候选是否适合用户账号。

重要边界：
- 不要生成完整成稿。
- 不要模仿或暴露对标博主名字；吸收热点、话题、结构和角度，转成用户自己的语言。
- 不要为了凑数量强行推荐。
- 抖音浅层内容可以进入候选，也可以成为今日最值得做；但只能基于标题、文案、封面、公开元数据推断，不能声称看过口播、评论或镜头结构。
- 暂存观察不要生成可发布标题和标题备选。
- 标题和字段必须落到用户自己的真实场景：AI账号系统、飞书执行台、AI导演工作流、商业视频交付、封面Skill、公众号长文转小红书卡片、RunBY、MuseIn、车企/内容营销等。
- 必须先使用 `关联母场景候选` 判断这条内容能借用哪个用户场景，再输出标题。不要先套标题再补解释。
- 每行必须输出 `关联母场景`、`借用方式`、`不能声称的部分`、`我的真实/相邻场景`，并让它们影响 `可发布标题`、`我的场景拆解` 和 `主编判断`。
- 如果 `热点钩子候选` 存在，标题或一句话Brief里优先保留一个最有识别度的工具/模型/产品名；但后半句必须落到用户自己的业务动作。
- 每行必须输出 `热点钩子`、`普通人会怎么讲`、`我会怎么讲`、`场景依据`、`真实/相邻案例`、`我的改造动作`、`需要补的证据`。
- `场景依据` 只能是：真实案例、相邻推演、仅热点观察。真实案例可以更强推荐；相邻推演可以进入候选但要写清“我会拿它去测/改/验证”；仅热点观察原则上暂存。
- 生成标题前，必须先完成“业务现场复盘”：旧流程原来怎么做、谁参与、卡在哪里；AI具体改哪一步；用户保留什么判断；最后展示什么证据。这个复盘必须写进 `我的场景拆解`、`我的思考点`、`重点体现`、`我的改造动作`。
- 禁止只写“我会把 X 放进 Y 流程里看”。这只是分类，不是用户现场。必须补出具体旧流程、具体改造动作和具体证据。
- 标题不能只停在“X + 流程/系统/验收”。如果用了“我会/我想测/我更想看”，后半句必须具体到一轮返修、一次改版、一张字段表、一套验收记录、一步 Brief 压缩、一次内容资产复用。
- 输出必须严格符合 JSON Schema；不要输出 Markdown。

请重写/覆盖这些字段：
{json.dumps(SKILL_FIELDS, ensure_ascii=False)}

候选状态只能是：今日最值得做、可选候选、暂存观察、不建议制作。
推荐等级只能是：S、A、B、C。
对应方向只能是：AI业务定调、真实工作流改造、AI导演工作流、汽车与内容营销、AI项目复盘。
证据强度只能是：强、中、弱。
今日最值得做最多 3 条。

<SKILL.md>
{skill_text}
</SKILL.md>

<persona-brief.md>
{persona_brief}
</persona-brief.md>

完整案例库路径：{SKILL_REFERENCE}
本次不把完整长文全部塞入上下文；你必须优先使用上面的压缩底稿，以及每条候选里的 `关联母场景候选`。

<candidate_rows_json>
{json.dumps(candidates, ensure_ascii=False, indent=2)}
</candidate_rows_json>
"""


def run_codex_skill(rows: list[dict[str, str]], model: str, timeout: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="editorial-skill-") as tmpdir:
        tmp = Path(tmpdir)
        schema_path = tmp / "schema.json"
        output_path = tmp / "codex_output.json"
        schema_path.write_text(json.dumps(codex_output_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-C",
            str(ROOT),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")
        proc = subprocess.run(
            command,
            input=build_codex_prompt(rows),
            text=True,
            capture_output=True,
            timeout=timeout,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "Codex Skill execution failed "
                f"(code={proc.returncode}). stderr={proc.stderr[-2000:]} stdout={proc.stdout[-1000:]}"
            )
        if not output_path.exists():
            raise RuntimeError(f"Codex Skill execution did not produce output file. stdout={proc.stdout[-2000:]}")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    by_index: dict[int, dict[str, str]] = {}
    for item in payload.get("rows", []):
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        by_index[idx] = {field: str(item.get(field, "") or "") for field in SKILL_FIELDS}
    enriched: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        out = dict(row)
        judgement = by_index.get(idx)
        if not judgement:
            raise RuntimeError(f"Codex Skill output missing row index {idx}")
        out.update(judgement)
        out["Skill编辑层"] = "ai-account-editorial-director"
        out["Skill参考文件"] = str(SKILL_REFERENCE)
        enriched.append(out)
    return normalize_batch(enriched), {
        "codex_rows": len(by_index),
        "batch_notes": payload.get("batch_notes", ""),
        "model": model or "codex-default",
    }


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
    parser = argparse.ArgumentParser(description="Run ai-account-editorial-director on a candidate CSV.")
    parser.add_argument("--input", required=True, help="Candidate CSV from content_sampler.py.")
    parser.add_argument("--output", required=True, help="Enriched candidate CSV.")
    parser.add_argument("--report", default="", help="Optional JSON report path.")
    parser.add_argument(
        "--engine",
        choices=["codex", "deterministic"],
        default=os.getenv("EDITORIAL_SKILL_ENGINE", "codex"),
        help="Default codex uses the global Skill through Codex CLI. deterministic is an explicit offline fallback.",
    )
    parser.add_argument("--codex-model", default=os.getenv("EDITORIAL_SKILL_CODEX_MODEL", ""), help="Optional Codex model override.")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("EDITORIAL_SKILL_TIMEOUT", "900")), help="Codex execution timeout in seconds.")
    parser.add_argument(
        "--allow-deterministic-fallback",
        action="store_true",
        help="If Codex execution fails, fall back to deterministic field filling instead of failing.",
    )
    args = parser.parse_args()

    load_local_env()
    input_path = Path(args.input)
    output_path = Path(args.output)
    rows, original_fields = read_csv(input_path)
    engine = args.engine
    engine_meta: dict[str, Any] = {}
    try:
        if args.engine == "codex":
            enriched, engine_meta = run_codex_skill(rows, args.codex_model, args.timeout)
        else:
            enriched = normalize_batch([enrich(row) for row in rows])
            engine_meta = {"mode": "explicit_deterministic"}
    except Exception as exc:
        if not args.allow_deterministic_fallback:
            raise
        engine = "deterministic"
        enriched = normalize_batch([enrich(row) for row in rows])
        engine_meta = {"fallback_after_error": str(exc)}
    fields = fieldnames_for(enriched, original_fields)
    if input_path.resolve() == output_path.resolve():
        atomic_write_csv(output_path, enriched, fields)
    else:
        write_csv(output_path, enriched, fields)
    report_path = Path(args.report) if args.report else output_path.with_suffix(".editorial_skill_report.json")
    write_report(report_path, enriched, input_path, output_path, engine, engine_meta)
    print(json.dumps({
        "ok": True,
        "rows": len(enriched),
        "engine": engine,
        "engine_meta": engine_meta,
        "input": str(input_path),
        "output": str(output_path),
        "report": str(report_path),
        "skill_reference": str(SKILL_REFERENCE),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
