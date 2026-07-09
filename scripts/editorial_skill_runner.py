#!/usr/bin/env python3
"""Run ai-account-editorial-director rules on topic candidates.

The collection pipeline still handles source capture, normalization, dedupe, and
rough candidate generation. This runner is the editorial layer: by default it
loads the global private Skill text, asks the locally authenticated Codex CLI to
make the batch judgement, and writes the editorial output contract back to the
candidate CSV. The repository Skill is a sanitized mirror for
sync/bootstrap/testing and is never used as an implicit fallback.

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

import topic_field_contract as field_contract
from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
REPO_SKILL_DIR = ROOT / "skills" / "ai-account-editorial-director"
GLOBAL_SKILL_DIR = Path.home() / ".codex" / "skills" / "ai-account-editorial-director"
SKILL_DIR = Path(os.getenv("EDITORIAL_SKILL_DIR", str(GLOBAL_SKILL_DIR))).expanduser()
SKILL_MD = SKILL_DIR / "SKILL.md"


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
    "editorial_engine",
    "fallback_only",
    "not_editorial_quality",
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

MOTHER_SCENES = [
    {
        "name": "AI账号信息雷达 / 飞书执行台",
        "keywords": ["飞书", "选题", "Brief", "内容收件箱", "信息雷达", "AIHOT", "候选池", "主编", "Skill", "公众号", "抖音采样"],
        "borrow": "借用用户正在搭的 AI账号信息雷达：采集、去重、摘要、字段化判断、主编Skill、从热点到06脚本包输入。",
        "can_show": "飞书字段、候选池、判断规则、主编备注、状态流转、从内容到06脚本包的链路。",
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
TITLE_PERMISSIONS = {"可发布标题", "内部测试标题", "不生成标题"}
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

TITLE_FORBIDDEN_TERMS = [
    "字段表",
    "交付QA",
    "验收记录的目录",
    "素材风险清单",
    "选题复核点",
    "要证明的是",
    "先交出",
    "先把每一步输入输出写进执行台",
    "别急着夸",
    "不稀奇",
    "我最怕",
    "我最想",
    "听起来很美",
    "别再给Agent起名字",
]

EXPERIMENT_ACTION_TERMS = [
    "测试", "验证", "改造", "压缩", "录成", "接进", "变成", "写回", "沉淀",
    "做成", "复用", "拆成", "跑一轮", "对比", "进入", "重写", "少掉",
    "选择", "选", "记录", "导出", "输出", "标出", "标注", "检查", "统计",
    "回填", "输入", "补", "决定", "复核",
]
FALLBACK_EXPERIMENT_PROMPT = "待补实验动作：写清输入材料、1-2个动作、输出物和通过/失败标准。"

PROPOSITION_OVERLOAD_TERMS = [
    "旧流程", "AI介入", "验证方式", "需要补", "还缺", "我要证明", "可沉淀",
    "痛点是", "介入点是", "最后能", "同时输出",
]
GENERIC_ASSET_PACKS = [
    "Workflow SOP / 字段规则 / 脚本包输入模板 / 飞书任务检查表",
    "Workflow SOP/字段规则/脚本包输入模板/飞书任务检查表",
    "导演工作流 SOP / 分镜验收表 / 成片 QA 清单",
    "内容资产流 SOP / 发布前后素材清单 / 复盘模板",
    "项目验收清单 / 复盘模板 / 异常处理记录",
]
GENERIC_ASSET_TERMS = ["通用", "资产包", "模板包", "方法论", "闭环", "待补具体资产"]
GENERIC_ASSET_VALUES = {
    "主编Skill",
    "输入字段",
    "输出字段",
    "飞书字段",
    "品牌规则",
    "视觉规则",
    "字体规则",
    "案例规则",
    "失败样例",
    "人工确认点",
    "状态",
    "输入一条候选内容",
    "再跑一条候选检查",
    "按五段任务表",
    "检查结果能不能写回飞书任务单",
    "跑完后写回飞书任务单",
    "就进入封面Skill",
}
ASSET_NOISE_PHRASES = [
    "输入一条",
    "再跑一条",
    "按五段",
    "能不能",
    "是否",
    "如果",
    "若",
    "检查结果",
    "跑完后",
    "就进入",
    "不进入",
    "就判定",
]

DIRECTION_ALIASES = {
    "AI汽车与品牌增长": "汽车与内容营销",
    "AI导演工作流与视频交付": "AI导演工作流",
    "内容团队选题到脚本包流程": "真实工作流改造",
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


def load_selection_learning_context(limit: int = 2400) -> str:
    """Load user-approved topic-selection learning notes for the editor."""
    if not APPROVED_SELECTION_LEARNING_MD.exists():
        return "暂无已确认选择学习摘要。"
    text = APPROVED_SELECTION_LEARNING_MD.read_text(encoding="utf-8").strip()
    if not text:
        return "暂无已确认选择学习摘要。"
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n...（已截断，完整摘要见本地 selection_learning 输出）"


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
    title = extract_original_title(row.get("原始来源标题") or row.get("来源内容") or row.get("来源标题"))
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


def has_experiment_action(value: str) -> bool:
    return any(term in (value or "") for term in EXPERIMENT_ACTION_TERMS)


def is_generic_asset(value: str) -> bool:
    text = " ".join((value or "").split())
    if not text:
        return False
    if text in GENERIC_ASSET_PACKS:
        return True
    return any(term in text for term in GENERIC_ASSET_TERMS)


def derived_asset_from_skill_text(row: dict[str, str]) -> str:
    """Extract asset nouns from Skill-written experiment/validation text.

    This is not a creative fallback. It only promotes concrete asset names that
    the Skill already wrote elsewhere, such as "短片Agent验收表" or "封面返工记录".
    """
    text = "；".join(
        row.get(field, "")
        for field in ["我要做的实验", "验证方式", "可展示证据", "重点体现", "一句话Brief", "我的场景拆解"]
        if row.get(field, "")
    )
    candidates: list[str] = []

    def add_candidate(raw: str) -> None:
        value = raw.strip("，。、；：: ").replace(" ", "")
        for marker in ["沉淀成", "整理成", "写进", "接进", "输出", "导出", "生成"]:
            if marker in value:
                value = value.split(marker)[-1]
        value = re.sub(r"^(把|并|和|或|及|与|给|将|结果|这套|一个|一张|一次|一条|3版|五段)", "", value).strip()
        value = re.sub(r"^(通过和失败点)", "", value).strip()
        if "和" in value and len(value) > 10:
            for part in value.split("和"):
                add_candidate(part)
            return
        if "、" in value:
            for part in value.split("、"):
                add_candidate(part)
            return
        if value in GENERIC_ASSET_VALUES:
            return
        if any(phrase in value for phrase in ASSET_NOISE_PHRASES):
            return
        if not value or is_generic_asset(value):
            return
        if len(value) < 3 or value in candidates:
            return
        candidates.append(value)

    for match in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9 /_-]{2,30}(?:表|清单|规则|Skill|记录|模板|检查|截图|案例库|流程图|QA|字段|对比|任务单))", text):
        add_candidate(match.group(1))
    return " / ".join(candidates[:3])


def workflow_trigger_for(row: dict[str, str]) -> str:
    for field in ["热点触发点", "热点钩子", "原始钩子", "事件锚点", "原始来源标题", "来源内容", "来源标题"]:
        value = short_sentence(row.get(field, ""), 80)
        if value:
            return value
    hook = hot_hook(row)
    return hook or "这条外部素材"


def workflow_pain_for(row: dict[str, str]) -> str:
    for field in ["我的工作流痛点", "我的真实矛盾", "业务场景", "旧流程痛点", "内容核心冲突"]:
        value = short_sentence(row.get(field, ""), 120)
        if value:
            return value
    scene = matched_mother_scenes(row)[0]["name"]
    return f"{scene}里还缺一套能被记录、复跑和验收的流程。"


def old_flow_pain_for(row: dict[str, str]) -> str:
    value = short_sentence(row.get("旧流程痛点", ""), 180)
    if value:
        return value
    return "待补旧流程痛点：写清过去谁在做、卡在哪一步、为什么难交接或难复盘。"


def ai_intervention_for(row: dict[str, str]) -> str:
    value = short_sentence(row.get("AI介入点", ""), 180)
    if value:
        return value
    experiment = row.get("我要做的实验") or row.get("我的改造动作") or ""
    if experiment:
        return f"让 AI 承接实验里的可记录步骤：{short_sentence(experiment, 120)}。"
    return "待补AI介入点：写清AI具体接管哪一步，以及哪一步仍由我人工验收。"


def validation_for(row: dict[str, str]) -> str:
    for field in ["验证方式", "可展示证据", "可展示结果"]:
        value = short_sentence(row.get(field, ""), 160)
        if value:
            return value
    return "待补最小验证步骤：写清输入材料、1-2个动作、输出物和通过/失败标准。"


def asset_for(row: dict[str, str]) -> str:
    value = short_sentence(row.get("可沉淀资产", ""), 160)
    if value and not is_generic_asset(value):
        return value
    derived = derived_asset_from_skill_text(row)
    if derived:
        return derived
    return "待补具体资产：命名这条选题会留下的表、清单、Skill、记录或对比物。"


def context_specific_asset(row: dict[str, str]) -> str:
    """Name a concrete asset when the Skill repeats a mismatched asset.

    This is intentionally narrow: it only chooses from assets implied by the
    candidate text/experiment. It is not a scoring or title-generation fallback.
    """
    text = "\n".join(
        row.get(field, "")
        for field in [
            "选题命题",
            "我要做的实验",
            "热点触发点",
            "我的工作流痛点",
            "原始来源标题",
            "来源内容",
            "内部切入角度",
            "验证方式",
        ]
    )
    lowered = text.lower()
    rules = [
        (("claude code", "团队原则", "项目验收", "异常记录"), "AI项目验收清单 / Agent异常记录表"),
        (("baoyu", "图文", "pptx", "导出", "配图"), "图文导出验收表 / 视觉层级检查记录"),
        (("obsidian", "知识库", "任务回填", "来源整理"), "信息雷达任务回填记录 / 来源整理目录规范"),
        (("l3", "l4", "自动驾驶", "汽车", "国标"), "汽车内容投放前风险复核表 / 品牌证据检查清单"),
        (("adobe", "photoshop", "premiere", "creative cloud", "返修"), "素材返修任务表 / 品牌一致性验收字段"),
        (("豆包", "excel", "表格", "字段回填"), "候选表字段回填规则 / 错误字段标注记录"),
        (("小云雀", "短剧", "分镜", "成片", "视频"), "短片Agent验收表 / 分镜返修记录表"),
        (("选题", "候选", "brief", "字段", "主编"), "选题判断字段表 / 脚本包输入回填记录"),
    ]
    for keywords, asset in rules:
        if any(keyword in lowered or keyword in text for keyword in keywords):
            return asset
    derived = derived_asset_from_skill_text(row)
    if derived and not is_generic_asset(derived):
        return derived
    return ""


def experiment_for(row: dict[str, str]) -> str:
    for field in ["我要做的实验", "我的改造动作"]:
        value = short_sentence(row.get(field, ""), 130)
        if value and has_experiment_action(value):
            return value
    return FALLBACK_EXPERIMENT_PROMPT


def tension_looks_classification(value: str) -> bool:
    return any(term in (value or "") for term in ["来源摘要", "栏目", "我会把"])


def lived_tension_for(row: dict[str, str]) -> str:
    text = blob(row) + "\n" + "\n".join(row.get(field, "") for field in ["选题命题", "我要做的实验", "热点触发点"])
    lower = text.lower()
    if any(term in text for term in ["豆包", "表格", "Excel", "字段回填"]):
        return "我不是不会整理表格，而是每次从来源、判断到状态回填都断成几步，复盘时追不回为什么选它。"
    if any(term in lower for term in ["obsidian", "知识库"]) or any(term in text for term in ["任务回填", "来源整理"]):
        return "我不是缺一个知识库，而是缺一条能把来源、判断、任务和复盘串起来的回填链路。"
    if any(term in text for term in ["baoyu", "PPTX", "图文", "导出"]):
        return "我不是缺一张漂亮图，而是导出、改版和交付时还要反复人工检查版式有没有丢。"
    if any(term in text for term in ["选题", "候选", "Brief", "主编"]):
        return "我不是缺热点，而是缺一套能解释为什么选它、怎么推进到脚本包输入 的判断链路。"
    return "这个素材如果要进入我的账号，必须先变成一个我能亲自测试、改造和复盘的业务现场。"


def proposition_is_short_and_clean(value: str) -> bool:
    text = " ".join((value or "").split())
    if not text or len(text) > 90:
        return False
    return not any(term in text for term in PROPOSITION_OVERLOAD_TERMS)


def proposition_for(row: dict[str, str]) -> str:
    """Return a short workflow-experiment proposition, not the full card or publishable title."""
    for field in [
        "选题命题",
        "我要做的实验",
    ]:
        value = " ".join((row.get(field, "") or "").split())
        if proposition_is_short_and_clean(value):
            return value
    experiment = experiment_for(row)
    if experiment != FALLBACK_EXPERIMENT_PROMPT and proposition_is_short_and_clean(experiment):
        return experiment
    hook = workflow_trigger_for(row)
    action = "先暂存，等补出具体实验动作"
    if experiment != FALLBACK_EXPERIMENT_PROMPT:
        action = short_sentence(experiment, 56)
    proposition = f"{short_sentence(hook, 24)}触发的实验：{action}"
    return short_sentence(proposition, 90)


def is_same_as_source(title: str, row: dict[str, str]) -> bool:
    normalized = compact_text(title)
    if not normalized:
        return False
    for source in source_title_values(row):
        source_norm = compact_text(source)
        if normalized and source_norm and normalized == source_norm:
            return True
    return False


def humanize_publishable_title(row: dict[str, str], title: str) -> str:
    """Only clean title text; never rewrite by a fixed template.

    The editorial Skill owns title invention. Code may prevent obvious leakage
    and whitespace noise, but it must not replace a weak title with another
    hard-coded pattern, otherwise every batch slowly converges to the same few
    sentence shapes.
    """
    cleaned = (title or "").strip()
    if not cleaned:
        return cleaned
    return " ".join(cleaned.split())


def parse_json_object(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def compact_public_trace(parts: list[str], limit: int = 180) -> str:
    text = "；".join(part.strip("； ") for part in parts if part and part.strip())
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def derive_editorial_summary(row: dict[str, str]) -> str:
    thinking = parse_json_object(row.get("editorial_thinking_json", ""))
    if thinking:
        return compact_public_trace([
            str(thinking.get("source_read") or ""),
            str(thinking.get("why_i_would_choose") or ""),
            str(thinking.get("why_i_would_not_choose") or thinking.get("tradeoff") or ""),
            str(thinking.get("decision") or ""),
        ])
    return compact_public_trace([
        row.get("主编自由稿", ""),
        row.get("主编判断", ""),
        row.get("推荐理由", ""),
        row.get("不建议做的原因", ""),
    ])


def derive_title_thinking(row: dict[str, str]) -> str:
    thinking = parse_json_object(row.get("editorial_thinking_json", ""))
    if thinking:
        options = thinking.get("angle_options")
        if isinstance(options, list):
            options_text = " / ".join(str(item) for item in options[:3])
        else:
            options_text = str(options or "")
        return compact_public_trace([
            str(thinking.get("chosen_angle") or ""),
            str(thinking.get("title_thinking") or ""),
            options_text,
        ], limit=160)
    return compact_public_trace([row.get("标题工作坊", ""), row.get("标题自审", "")], limit=160)


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
    def finalize(candidate: dict[str, str]) -> dict[str, str]:
        if not candidate.get("主编判断摘要"):
            candidate["主编判断摘要"] = derive_editorial_summary(candidate)
        if not candidate.get("标题思路"):
            candidate["标题思路"] = derive_title_thinking(candidate)
        sanitized = sanitize_visible_language(candidate)
        return field_contract.downgrade_for_contract(sanitized, field_contract.validate_field_contract(sanitized))  # type: ignore[return-value]

    real_skill = out.get("editorial_engine") == "codex" and out.get("fallback_only") != "true"
    if real_skill:
        out["热点触发点"] = out.get("热点触发点") or workflow_trigger_for(out)
        if not out.get("我的工作流痛点"):
            out["我的工作流痛点"] = "待补工作流痛点：Skill 未写清这条来源对应我的哪个真实卡点。"
        if not out.get("旧流程痛点"):
            out["旧流程痛点"] = "待补旧流程痛点：Skill 未写清过去谁在做、卡在哪一步。"
        if not out.get("可沉淀资产") or is_generic_asset(out.get("可沉淀资产", "")):
            out["可沉淀资产"] = derived_asset_from_skill_text(out) or "待补具体资产：Skill 未命名这条选题会留下的表、清单、记录或对比物。"
        if not out.get("我要做的实验"):
            out["我要做的实验"] = FALLBACK_EXPERIMENT_PROMPT
        if not out.get("AI介入点"):
            out["AI介入点"] = "待补AI介入点：Skill 未写清 AI 具体接管哪一步。"
        if not out.get("验证方式"):
            out["验证方式"] = "待补最小验证步骤：Skill 未写清输入材料、动作、输出物和通过/失败标准。"
        if not out.get("选题命题"):
            out["选题命题"] = proposition_for(out)
    else:
        out["主编判断摘要"] = out.get("主编判断摘要") or "fallback_only：离线兜底只补字段完整性，不能作为主编质量证据。"
        out["标题思路"] = out.get("标题思路") or "fallback_only：不生成可发布标题判断。"
        out["热点触发点"] = workflow_trigger_for(out)
        out["我的工作流痛点"] = workflow_pain_for(out)
        out["旧流程痛点"] = old_flow_pain_for(out)
        out["可沉淀资产"] = asset_for(out)
        out["我要做的实验"] = experiment_for(out)
        out["AI介入点"] = ai_intervention_for(out)
        out["验证方式"] = validation_for(out)
        out["选题命题"] = proposition_for(out)
    out["我的选题标题"] = out["选题命题"]
    out["选题标题"] = out["选题命题"]
    if tension_looks_classification(out.get("我的真实矛盾", "")):
        out["我的真实矛盾"] = lived_tension_for(out)
    level = normalize_level(out.get("今日建议级别") or out.get("候选状态"))
    out["今日建议级别"] = level
    out["候选状态"] = level
    if out.get("我要做的实验") == FALLBACK_EXPERIMENT_PROMPT:
        reason = out.get("不建议做的原因") or out.get("降级原因") or out.get("推荐动作原因")
        extra = "还没有形成可执行的最小实验动作，先不进入前台可选候选。"
        out["今日建议级别"] = "暂存观察"
        out["候选状态"] = "暂存观察"
        out["是否建议进入制作"] = "否"
        out["推荐动作"] = "观察"
        out["降级原因"] = f"{reason}；{extra}".strip("；")
        out["不建议做的原因"] = out["降级原因"]
        level = "暂存观察"

    publishable = (out.get("可发布标题", "") or "").strip()
    alternatives = (out.get("标题备选", "") or "").strip()
    score = intish(out.get("编辑判断分") or out.get("推荐分"))
    title_score = intish(out.get("标题质量分"))
    title_permission = (out.get("title_permission", "") or "").strip()
    if title_permission not in TITLE_PERMISSIONS:
        if level in NON_PUBLISH_LEVELS:
            title_permission = "不生成标题"
        elif publishable:
            title_permission = "可发布标题"
        else:
            title_permission = "内部测试标题"
    evidence = (out.get("证据强度", "") or "").strip()
    scene_basis_value = (out.get("场景依据", "") or "").strip()

    if level == "今日最值得做" and (evidence == "弱" or scene_basis_value == "仅热点观察"):
        reason = out.get("不建议做的原因") or out.get("降级原因") or out.get("推荐动作原因")
        extra = "证据或场景依据还不够强，不能作为今日最值得做。"
        out["今日建议级别"] = "可选候选"
        out["候选状态"] = "可选候选"
        out["是否建议进入制作"] = "否"
        out["推荐动作"] = out.get("推荐动作") or "补证据"
        out["降级原因"] = f"{reason}；{extra}".strip("；")
        out["不建议做的原因"] = out["降级原因"]
        level = "可选候选"
        if not publishable:
            title_permission = "内部测试标题"

    if level in NON_PUBLISH_LEVELS:
        if publishable or alternatives:
            reason = out.get("不建议做的原因") or out.get("降级原因") or out.get("推荐动作原因")
            extra = "暂存/不建议项不生成可发布标题，避免把内部判断误当成发布选题。"
            out["不建议做的原因"] = f"{reason}；{extra}".strip("；")
        out["可发布标题"] = ""
        out["标题备选"] = ""
        out["title_permission"] = "不生成标题"
        out["是否建议进入制作"] = "否"
        if level == "不建议制作":
            out["推荐动作"] = "放弃"
        elif out.get("推荐动作") not in {"补证据", "存素材", "观察"}:
            out["推荐动作"] = "观察"
        return finalize(out)

    if title_permission != "可发布标题":
        if publishable or alternatives:
            reason = out.get("不建议做的原因") or out.get("降级原因") or out.get("推荐动作原因")
            extra = f"title_permission={title_permission}，该条不能把内部判断伪装成可发布标题。"
            out["不建议做的原因"] = f"{reason}；{extra}".strip("；")
        out["可发布标题"] = ""
        out["标题备选"] = ""
        out["title_permission"] = title_permission
        out["是否建议进入制作"] = "否"
        if out.get("推荐动作") not in {"补证据", "存素材", "观察"}:
            out["推荐动作"] = "补证据" if title_permission == "内部测试标题" else "观察"
        if level == "今日最值得做":
            out["今日建议级别"] = "可选候选"
            out["候选状态"] = "可选候选"
        return finalize(out)

    if not publishable:
        reason = out.get("不建议做的原因") or out.get("降级原因") or out.get("推荐动作原因")
        extra = "Skill 没有给出可发布标题，本轮只保留选题命题，不做标题包装。"
        title_permission = "内部测试标题"
        out["可发布标题"] = ""
        out["标题备选"] = ""
        out["title_permission"] = title_permission
        out["是否建议进入制作"] = "否"
        if out.get("推荐动作") not in {"补证据", "存素材", "观察"}:
            out["推荐动作"] = "补证据"
        out["降级原因"] = f"{reason}；{extra}".strip("；")
        if not out.get("不建议做的原因"):
            out["不建议做的原因"] = out["降级原因"]
        if level == "今日最值得做":
            out["今日建议级别"] = "可选候选"
            out["候选状态"] = "可选候选"
        return finalize(out)

    if publishable and is_same_as_source(publishable, out):
        reason = out.get("降级原因") or out.get("推荐动作原因") or out.get("不建议做的原因")
        extra = "可发布标题与原始来源标题相同，说明还没有转成用户自己的表达，先降级为暂存观察。"
        out["今日建议级别"] = "暂存观察"
        out["候选状态"] = "暂存观察"
        out["可发布标题"] = ""
        out["标题备选"] = ""
        out["title_permission"] = "不生成标题"
        out["是否建议进入制作"] = "否"
        out["推荐动作"] = "观察"
        out["降级原因"] = f"{reason}；{extra}".strip("；")
        out["不建议做的原因"] = out["降级原因"]
        return out

    publishable = humanize_publishable_title(out, publishable)
    out["可发布标题"] = publishable
    out["title_permission"] = "可发布标题"

    if level == "今日最值得做":
        out["是否建议进入制作"] = "是"
    elif not out.get("是否建议进入制作"):
        out["是否建议进入制作"] = "否"
    if publishable:
        out["我的选题标题"] = out["选题命题"]
        out["选题标题"] = out["选题命题"]
    return finalize(out)


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


def wants_top_today(row: dict[str, str]) -> bool:
    return row.get("今日建议级别") == "今日最值得做"


def normalize_batch(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized = [normalize_skill_row(row) for row in rows]
    top_candidates = [idx for idx, row in enumerate(normalized) if wants_top_today(row)]
    for idx in top_candidates[:3]:
        normalized[idx]["今日建议级别"] = "今日最值得做"
        normalized[idx]["候选状态"] = "今日最值得做"
        normalized[idx]["是否建议进入制作"] = "是"
    for idx in top_candidates[3:]:
        if normalized[idx].get("今日建议级别") == "今日最值得做":
            normalized[idx]["今日建议级别"] = "可选候选"
            normalized[idx]["候选状态"] = "可选候选"
    visible_asset_counts: dict[str, int] = {}
    for row in normalized:
        if row.get("今日建议级别") in {"今日最值得做", "可选候选"}:
            asset = (row.get("可沉淀资产", "") or "").strip()
            if asset:
                visible_asset_counts[asset] = visible_asset_counts.get(asset, 0) + 1
    for row in normalized:
        asset = (row.get("可沉淀资产", "") or "").strip()
        if row.get("今日建议级别") in {"今日最值得做", "可选候选"} and visible_asset_counts.get(asset, 0) > 2:
            replacement = context_specific_asset(row)
            if replacement and replacement != asset:
                row["可沉淀资产"] = replacement
    guarded = field_contract.apply_batch_quality_guards(normalized)
    return [sanitize_visible_language(row) for row in guarded]


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


def real_tension(row: dict[str, str]) -> str:
    """Surface the user's own business conflict before title generation."""
    scene = matched_mother_scenes(row)[0]["name"]
    hook = hot_hook(row)
    direction = normalize_direction(row.get("对应栏目", ""))
    text = blob(row)
    lower = text.lower()
    if any(term in text for term in ["飞书", "选题", "Brief", "AIHOT", "内容收件箱", "候选池"]):
        return "我现在不是缺热点，而是缺一套能解释为什么选它、怎么把它推进到脚本包输入 的判断系统。"
    if direction == "AI导演工作流" or any(term in text for term in ["视频", "短片", "分镜", "成片", "配音", "TTS", "剪辑"]):
        if hook:
            return f"{hook}再热，我真正要验证的是它能不能进入分镜、返修和成片验收，而不是只生成一个好看的片段。"
        return "AI视频真正卡住的不是生成，而是分镜、返修和成片验收这套交付流程。"
    if direction == "汽车与内容营销" or any(term in text for term in ["汽车", "车企", "品牌", "营销", "发布会", "车主"]):
        return "我关心的不是又多一个营销工具，而是它能不能把车企内容从人力堆稿改成可复用的内容资产流。"
    if any(term in text for term in ["Agent", "智能体", "Claude", "Codex", "MCP", "验收", "状态"]):
        return "我现在缺的不是一个会聊天的 Agent，而是它做完事后能不能留下任务、状态、异常和验收记录。"
    if any(term in text for term in ["封面", "首图", "卡片", "PPT", "排版", "设计", "品牌一致", "视觉"]):
        return "我不想要一个会生成漂亮素材的工具，我想要它少掉内容生产里那轮人工理解、改版和 QA。"
    if "douyin" in lower or "抖音" in text:
        return "这条对标内容如果要借鉴，必须先转成我的生产现场，而不是复述别人怎么讲。"
    if hook:
        return f"{hook}对我有用的前提，不是它又更新了什么，而是它能不能改掉「{scene}」里的一个真实低效环节。"
    return f"这条如果要做，必须先证明它能改掉「{scene}」里的一个真实卡点，而不是只当成热点复述。"


def transformation_action(row: dict[str, str]) -> str:
    basis = scene_basis(row)
    scene = matched_mother_scenes(row)[0]["name"]
    if basis == "真实案例":
        return f"拿已有「{scene}」相关案例做一次改造复盘：旧流程、AI介入点、人保留的判断、可展示资产都要说清。"
    if basis == "相邻推演":
        return f"基于「{scene}」做相邻验证：先设计字段表、流程图或验收清单，再决定是否生成脚本包。"
    return "先只观察热度和来源证据，等能接到具体业务动作或案例素材后再做。"


def editorial_judgement(row: dict[str, str]) -> str:
    level = row.get("今日建议级别") or row.get("候选状态") or "暂存观察"
    hook = hot_hook(row)
    basis = scene_basis(row)
    if level == "今日最值得做":
        return f"值得优先做：{hook or '这个来源'}能接到用户真实业务现场，并且有机会展示流程改造或资产结果。"
    if level == "可选候选":
        return f"可以进入候选：{hook or '这个来源'}有可借的角度，但还需要补证据或补一轮自己的测试。"
    if level == "不建议制作":
        return f"不建议制作：目前只能停在资讯或工具层，和用户业务现场关系弱。"
    return f"暂存观察：{basis}，还没形成足够清楚的用户现场和展示证据。"


def original_hook(row: dict[str, str]) -> str:
    source_hook = row.get("原始标题钩子") or original_title_hook_from(row)
    if source_hook:
        return source_hook
    hook = hot_hook(row)
    if hook:
        return hook
    source = row.get("原始来源标题") or row.get("来源内容") or row.get("我的选题标题")
    return short_sentence(source, 80)


def short_sentence(value: str, limit: int = 120) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def my_entry(row: dict[str, str]) -> str:
    scene = matched_mother_scenes(row)[0]["name"]
    action = transformation_action(row)
    return f"不按资讯讲，落到「{scene}」：{action}"


def how_i_would_tell(row: dict[str, str]) -> str:
    breakdown = scene_breakdown(row)
    thinking = thinking_point(row)
    return f"开头先点出来源钩子，再立刻转到我的现场。主体讲：{breakdown} 我的判断是：{thinking}"


def showable_evidence(row: dict[str, str]) -> str:
    evidence = row.get("可展示结果") or row.get("可沉淀资产")
    if evidence:
        return evidence
    basis = scene_basis(row)
    if basis == "真实案例":
        return "可展示已有流程截图、字段表、Brief、验收表、分镜/封面/成片片段或改造前后对比。"
    if basis == "相邻推演":
        return "需要补一轮自己的测试截图、字段表、流程图或失败样例，再决定是否制作。"
    return "目前缺少可展示证据，只适合观察。"


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
    title = row.get("选题命题") or row.get("我的真实矛盾") or row.get("我的选题标题") or row.get("来源内容", "")
    scene = row.get("业务场景") or normalize_direction(row.get("对应栏目", ""))
    asset = row.get("可沉淀资产", "")
    if asset:
        return f"用「{scene}」这个真实场景，把 {title} 拆成一个能沉淀为「{asset}」的流程判断。"
    return f"用「{scene}」这个真实场景，判断 {title} 能不能变成我的业务现场选题。"


def enrich(row: dict[str, str]) -> dict[str, str]:
    direction = normalize_direction(row.get("对应栏目", ""))
    scene = matched_mother_scenes(row)[0]
    out = dict(row)
    out["主编自由稿"] = row.get("主编自由稿") or (
        f"{real_tension(row)} {editorial_judgement(row)} "
        f"{my_entry(row)} {showable_evidence(row)}"
    )
    out["主编筛选"] = row.get("主编筛选") or editorial_judgement(row)
    out["标题工作坊"] = row.get("标题工作坊", "")
    out["标题自审"] = row.get("标题自审", "")
    out["原始标题钩子"] = row.get("原始标题钩子") or original_title_hook_from(row)
    out["Austin改写理由"] = row.get("Austin改写理由") or "fallback_only：只记录可借来源钩子，不能作为标题质量证据。"
    out["点击钩子"] = row.get("点击钩子") or original_hook(row)
    out["观众为什么会点"] = row.get("观众为什么会点") or "这条要让人看到自己的真实工作卡点，而不是只看到一个AI工具更新。"
    out["我的真实矛盾"] = row.get("我的真实矛盾") or real_tension(row)
    out["热点触发点"] = row.get("热点触发点") or workflow_trigger_for(out)
    out["我的工作流痛点"] = row.get("我的工作流痛点") or workflow_pain_for(out)
    out["旧流程痛点"] = row.get("旧流程痛点") or old_flow_pain_for(out)
    out["可沉淀资产"] = asset_for(out)
    out["我要做的实验"] = row.get("我要做的实验") or experiment_for(out)
    out["AI介入点"] = row.get("AI介入点") or ai_intervention_for(out)
    out["验证方式"] = row.get("验证方式") or validation_for(out)
    out["选题命题"] = proposition_for(out)
    out["选题判断"] = row.get("选题判断") or editorial_judgement(row)
    out["原始钩子"] = row.get("原始钩子") or original_hook(row)
    out["我的切入"] = row.get("我的切入") or my_entry(row)
    out["我准备怎么讲"] = row.get("我准备怎么讲") or how_i_would_tell(row)
    out["可展示证据"] = row.get("可展示证据") or showable_evidence(row)
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
    out["title_permission"] = row.get("title_permission") or ("可发布标题" if row.get("可发布标题") else "内部测试标题")
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
    out["editorial_engine"] = "deterministic"
    out["fallback_only"] = "true"
    out["not_editorial_quality"] = "true"
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
    non_authoritative_hints: dict[str, str] = {}
    existing_visible_fields: dict[str, str] = {}
    for field in CANDIDATE_CONTEXT_FIELDS:
        value = row.get(field, "")
        if field == "可沉淀资产" and is_generic_asset(value):
            payload["旧可沉淀资产_不可沿用"] = value[:1800]
            continue
        if value:
            if field in NON_AUTHORITATIVE_HINT_FIELDS:
                non_authoritative_hints[field] = value[:1200]
                continue
            if field in EXISTING_VISIBLE_FIELD_FIELDS:
                existing_visible_fields[field] = value[:1200]
                continue
            payload[field] = value[:1800]
    source_title_hook = row.get("原始标题钩子") or original_title_hook_from(row)
    if source_title_hook:
        payload["原始标题钩子"] = source_title_hook[:800]
    payload["主编字段所有权"] = "Skill 输出为 04/Topic Card/06 主字段唯一质量来源；代码字段仅作来源事实、候选池治理和一致性校验。"
    payload["fallback_boundary"] = "若候选里已有 deterministic 主字段，只能作为参考或反例；最终可见主字段必须由本轮 Skill 判断重写或确认。"
    payload["source_facts"] = json.dumps({
        "source_title": row.get("原始来源标题") or row.get("来源内容") or row.get("来源标题") or "",
        "source_title_hook": source_title_hook,
        "source_account": row.get("原始来源账号") or row.get("账号名/公众号名") or "",
        "source_link": row.get("来源链接") or "",
        "source_type": row.get("来源类型") or "",
        "source_weight_label": row.get("来源权重类型") or row.get("来源类型") or "",
        "source_influence_weight": row.get("来源影响权重") or "",
        "source_composition": row.get("来源构成") or "",
        "aihot_major_news": row.get("AIHOT重大性说明") or "",
        "market_validation": row.get("市场验证依据") or "",
    }, ensure_ascii=False)
    payload["non_authoritative_hints"] = json.dumps(non_authoritative_hints, ensure_ascii=False)
    payload["existing_fields_do_not_copy"] = json.dumps(existing_visible_fields, ensure_ascii=False)
    payload["source_governance_evidence"] = json.dumps({
        "source_weight_label": row.get("来源权重类型") or row.get("来源类型") or "",
        "source_influence_weight": row.get("来源影响权重") or "",
        "source_composition": row.get("来源构成") or "",
        "aihot_major_news": row.get("AIHOT重大性说明") or "",
        "competitor_account": row.get("原始来源账号") or row.get("账号名/公众号名") or "",
        "market_validation": row.get("市场验证依据") or "",
        "source_title_hook": source_title_hook,
        "theme_hint": row.get("主题簇") or "",
        "translation_hint": row.get("Austin转译角度") or row.get("对标转译角度") or "",
        "translation_quality": row.get("Austin转译质量") or "",
    }, ensure_ascii=False)
    payload["field_contract_guardrails"] = json.dumps({
        "knowledge_terms_cannot_coexist_with_video_main_fields": True,
        "aihot_actionable_requires_major_news_and_austin_angle": True,
        "generate_script_requires_experiment_validation_and_title_permission": True,
        "topic_direction_experiment_pain_and_evidence_must_agree": True,
        "non_authoritative_hints_must_not_author_visible_fields": True,
        "generated_titles_with_repeated_skeletons_are_blocked": True,
    }, ensure_ascii=False)
    payload["关联母场景候选"] = json.dumps(matched_mother_scenes(row), ensure_ascii=False)
    payload["热点钩子候选"] = hot_hook(row)
    payload["场景依据候选"] = scene_basis(row)
    return payload


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
    skill_text = strip_yaml_frontmatter(load_text(SKILL_MD))
    persona_brief = load_text(SKILL_PERSONA_BRIEF)
    selection_learning = load_selection_learning_context()
    candidates = [compact_candidate(row, idx) for idx, row in enumerate(rows)]
    return f"""你现在按下面嵌入的主编规则文本做判断；不要再触发或审查外部 Skill，规则、底稿和候选已经完整提供。

这是一次生产管线里的批量选题筛选，不是标题润色，也不是代码 hint 的字段补全。请先像 Austin 本人做主编判断，再把判断映射成 04 / Topic Card / 06 主字段。

AR-020C 两段式输出：
1. `editorial_thinking_json`：写公开可展示的主编决策摘要，不要写隐藏推理链。它至少包含 `source_read`、`why_i_would_choose`、`why_i_would_not_choose`、`account_fit`、`source_to_me_translation`、`angle_options`、`chosen_angle`、`title_thinking`、`decision`、`near_miss_reason`。这些字段要让 PM/用户看得懂：我为什么会选、为什么不选、从什么角度切、标题为什么这样取。
2. `field_mapping_json`：说明第一段判断如何映射到主字段，包括 `选题命题`、`一句话Brief`、`我要做的实验`、`我的工作流痛点`、`旧流程痛点`、`AI介入点`、`验证方式`、`可沉淀资产`、`对应方向`、`推荐动作`、`今日建议级别`、`title_permission`、`可发布标题`。

同时必须填两个紧凑用户可见字段：
- `主编判断摘要`：80-180 字，必须提到来源证据、Austin 场景、动作/实验和一个取舍或风险。
- `标题思路`：40-160 字，说明标题/命题为什么这样切；如果不能给可发布标题，也要说明卡在哪里。
- `原始标题钩子`：从来源标题里提取可借的市场表达资产，例如工具组合、结果承诺、场景词、学习入口、强冲突或用户利益。
- `Austin改写理由`：说明你借用了哪个原始标题钩子，又如何转成 Austin 的业务现场判断；必须能看出不是照抄。

总边界：
- 不要在选题筛选阶段生成完整成稿；这里只产出工作流实验命题卡。
- 不要重新采集，不要虚构用户没做过的项目结果。
- 不要模仿或暴露对标博主名字；只能吸收热点、话题、结构和角度，转成用户自己的语言。
- 不要为了凑数量强行推荐；`今日最值得做` 最多 3 条。
- 如果没有足够强的候选，可以 0 条 `今日最值得做`；不要为了分数高就自动补强推。
- 抖音浅层内容可以进入候选，也可以成为强候选；但只能基于标题、文案、封面、公开元数据判断，不能声称看过口播、评论、镜头结构或完整视频。
- 前台字段必须第一人称，不要写“用户当前/用户可以/适合用户/帮助用户”。

AR-020B 字段契约：
- 你是 04 / Topic Card / 06 用户可见主字段的质量 owner。`选题命题`、`一句话Brief`、`我要做的实验`、`我的工作流痛点`、`旧流程痛点`、`AI介入点`、`验证方式`、`可沉淀资产`、`我的思考点`、`重点体现`、`对应方向`、`推荐动作`、`今日建议级别`、`title_permission`、`可发布标题` 必须由你重写或确认。
- 候选里的 `source_facts` / `source_governance_evidence`、`来源权重类型`、`来源构成`、`原始来源标题`、`原始来源账号`、`AIHOT重大性说明`、`市场验证依据` 是事实证据；可以引用、吸收和校验。
- 候选里的 `non_authoritative_hints` 只是代码在 Skill 之前给出的主题、转译、母场景线索。它们不能替代你的主编判断；不能原样复制进标题、命题、Brief、实验动作、主编判断摘要。若采用某个 hint，必须在 `source_to_me_translation` / `chosen_angle` / `主编判断摘要` 里说明它为什么和来源证据一致。
- 候选里的 `existing_fields_do_not_copy` 是旧 deterministic prefill 或上游字段，只能作为参考或反例，不能默认沿用。你必须检查它是否和原始来源一致；不一致就重写或降级。
- 知识库 / Obsidian / RAG / 内容资产来源，不能在主字段里写成 AI 视频、分镜、成片验收，除非原始来源本身就是 AI 视频知识库。
- AI 视频 / AIGC / 分镜来源，不能写成纯知识库或办公表格选题，除非来源明确在讲视频素材管理知识库。
- AI Hot 只有在有重大模型/产品/行业变化，并且能落到 Austin 的工作流影响时，才可以进入可行动候选；普通 AI Hot 只观察。
- `推荐动作=生成脚本包` 必须有可执行的 `我要做的实验`、`验证方式` 和不为 `不生成标题` 的 `title_permission`。否则请降级为 `暂存观察` 或 `不建议制作`。
- 如果你无法从来源证据写出具体 Austin 现场，不要用空泛句凑字段；直接标 `暂存观察`，写清缺什么证据。
- 不要批量使用同一标题骨架。尤其 `我想用 X 测试 Y 能不能 Z`、`先拿 X 验证 Y`、`X 能不能进入 Y`、`先测/验收/试一遍` 等骨架只能少量自然出现；如果一批候选都长这样，请重写或降级。

标题表达升级：
- 原始标题不是噪声。对标账号标题通常包含市场验证过的入口：工具组合、结果承诺、场景词、学习承诺、强冲突、用户利益。你应该先提取 `原始标题钩子`，再决定哪些可借、哪些要舍弃。
- 可发布标题优先融合“原始标题入口 + Austin 判断”。例如来源是 `Codex联动Obsidian，搭建超强知识库，手把手教程`，可以借 `Codex+Obsidian`、`搭知识库`、`手把手` 的入口，但落点必须变成我的选题台、长期记忆、判断留存或资料复用，而不是照抄教程标题。
- 不能直接复制原始标题，不能变回工具教程号、热点搬运号或保姆级教程号。`标题思路` / `Austin改写理由` 必须说清借了什么、改了什么、为什么这样更像 Austin。
- `选题命题`、`我的选题标题`、`可发布标题` 是用户会看的判断句，不是内部实验任务名。`测试 / 验证 / 验收 / 能不能 / 会不会 / 先试一遍` 这类词优先放进 `我要做的实验` 或 `验证方式`，不要集中出现在标题层。
- 每条标题先从四种素材里选一个钩子：来源承诺、我的真实矛盾、缺失证据、最终能留下的资产。一个批次里不要连续使用同一种钩子。
- 观察 / 补证据候选也要有人能读懂的观察标题。不要写成“先测它会不会...”的内部测试口吻；如果证据不足，就写清“这条暂时卡在什么证据/场景”，而不是包装成可生成命题。
- 观察 / 补证据候选的 `选题命题` 也不要用冒号拆句或反思壳，例如 `X 给我的提醒：...`、`只能提醒我...`、`我会把 X 翻译成...`、`我先把它放进...`。这些话可以放进 `主编判断摘要`，但前台命题要直接说明“来源有什么价值 / 卡在哪个证据 / 为什么暂时不做”。
- 如果一条观察候选只能写成内部测试标题，请宁可把 `选题命题` 写成证据缺口摘要，例如“CI/CD Shell 有发布边界启发，但还缺我的自动化失败样例”，不要写成“先写清 / 先接进 / 放进验收边界”。
- `标题思路` 不能只说“不写成教程/不跟热点”。它必须说明标题钩子来自哪条来源证据、落到我的哪个个人场景、舍弃了什么讲法，以及为什么这个表达比工具教程更像 Austin。
- 同批最终标题自检：如果两个以上用户可见标题都靠 `先 / 测 / 验证 / 验收 / 能不能 / 会不会 / 试一遍`、冒号拆句、`给我的提醒` 推进，请逐条重写；重写不了就降级并说明缺少的真实场景或证据。

执行顺序：
0. `editorial_thinking / 主编自由判断`
   先不填字段，先用公开摘要判断：这条来源到底讲什么；它为什么和 Austin 的账号方向有关或无关；如果要选，我会从哪个真实工作现场切；如果不选，卡在证据、案例、工具、工作流还是标题表达；标题应该避免哪种模板。
   输出 `editorial_thinking_json`、`主编判断摘要`、`标题思路`。这一步是质量来源。

1. `field_mapping / 字段映射`
   只把上一步已经成立的判断映射到结构化字段。字段映射不能发明新角度，不能把 `non_authoritative_hints` 或 `existing_fields_do_not_copy` 直接写成用户可见主字段。
   输出 `field_mapping_json` 和下面所有主字段。

安全检查仍按以下旧三段理解，但它们是 guardrail，不是标题模板：
1. `gate / 主编门控`
   先回答：这条和我的人设、四个方向、真实/相邻业务现场有没有关系？证据够不够？我能不能讲出旧流程痛点、AI介入点、人保留的判断、可展示结果、可带走资产？
   输出 `主编筛选`、`主编自由稿`、`我的真实矛盾`、`场景依据`、`证据强度`、`候选状态`、`推荐等级`、`推荐动作`、`title_permission`。
   `title_permission` 只能是：`可发布标题`、`内部测试标题`、`不生成标题`。

2. `workflow experiment card / 工作流实验命题卡`
   只有通过门控的候选，才整理成用户能看的工作流实验命题卡。核心输出是 `选题命题` 和 `我要做的实验`。
   `选题命题` 是短命题，不是发布标题，也不是完整拆解。建议 35-70 字，最多不超过 90 字。它只说明“外部触发点 + 我的业务动作/工作流实验”，不要把旧流程、AI介入、验证方式、缺证据全部塞进第一列。
   `选题命题` 要像我写给自己的工作台条目，可以短、自然、具体。不要让一批候选都写成“我想用 X 测试 Y 能不能 Z / 我准备用 X 验证 Y 能不能 Z / 用 X 测试 Y 能不能进入 Z”。这些句式偶尔可以用，但不能批量出现。
   `我要做的实验` 必须包含明确动作：测试、改造、压缩、录成 Skill、接进返修流程、变成验收表、写回飞书任务单、从多步压成一步、沉淀 SOP、变成字段规则、做成模板、验证能不能交付、验证能不能复盘、验证能不能进入内容生产系统。
   `验证方式` 必须是 1-2 个可执行的最小实验步骤，不能只写“检查是否可用 / 对比流程”。它要写清输入材料、动作、输出物和通过/失败标准。
   `可沉淀资产` 必须具体到这条选题，写 1-3 个资产名。不要写通用资产包，例如“Workflow SOP / 字段规则 / 脚本包输入模板 / QA 清单”。
   如果候选里出现 `旧可沉淀资产_不可沿用`，说明那是上一轮旧模板值，只能作为反例，必须重写成具体资产名，不能原样沿用。
   同时输出 `热点触发点`、`我的工作流痛点`、`旧流程痛点`、`AI介入点`、`验证方式`、`可沉淀资产`、`选题判断`、`原始钩子`、`我的切入`、`我准备怎么讲`、`可展示证据`、`一句话Brief`、`我的场景拆解`、`我的思考点`、`重点体现`、`可调用案例`、`内容核心冲突`、`视频呈现方式`、`不建议做的原因`。
   这些字段要像用户拍摄前写给自己的工作备忘，不要像助理汇报。

3. `title packaging / 标题包装`
   只有 `title_permission=可发布标题` 时，才生成 `可发布标题` 和 `标题备选`。
   如果是 `内部测试标题` 或 `不生成标题`，`可发布标题` 和 `标题备选` 必须留空。
   `标题工作坊`、`标题自审` 只是调试字段；本轮不要把它们当主流程必填，不要为了填它们而批量套标题。

门控规则：
- `场景依据=仅热点观察` 或 `证据强度=弱`：不能是 `今日最值得做`，通常 `可选候选` 或 `暂存观察`。
- 写不出 `我要做的实验`，或者实验动作只是“讲一讲/分析一下/介绍一下”：不能进入强候选。
- 写不出具体旧流程痛点、AI介入点、人保留的判断、可展示结果：不能给 `title_permission=可发布标题`。
- 写不出 `选题命题`：不能进入前台候选。
- 只像资讯、工具教程、泛观点：`暂存观察` 或 `不建议制作`。
- 公众号全文可以做结构和观点拆解；AIHOT 只能做热点入口；抖音浅层只能做标题/文案级选题判断。

表达规则：
- 标题必须像真实业务现场里说出来的话，有冲突、场景、判断、动作或结果差距。
- 热点工具/模型名可以保留做入口，但后半句必须落到用户自己的生产现场或业务判断。
- 不要把案例句式当模板。案例只给语气和判断方式，不能批量复制骨架。
- 标题层少用 `先 / 测 / 验证 / 验收 / 能不能 / 会不会 / 试一遍`；这些词可以出现在实验和验证方式里，但不能成为一批标题的共同骨架。
- 观察 / 补证据标题要像“我为什么暂时不做这条”的公开摘要，而不是内部测试工单。避免 `X 给我的提醒：...`、`我会把 X 翻译成...`、`我先把它放进...` 这类看似主编、实际同构的壳。
- 禁止模板化/抬杠化词句：`不稀奇`、`别急着夸`、`我最怕`、`听起来很美`、`最该重排`、`背后哪类工具失去壁垒`、`不该只看参数`、`某某最值得学`、`保姆级教程`。
- 如果标题只是“正确的一句话”，但没有点击理由，宁可不给可发布标题。
- `选题标题` 和 `我的选题标题` 在本系统里是兼容字段，内容也必须写成 `选题命题`，不要写成可发布标题。

校准例，只用于理解“热点触发工作流实验”，不要复制成固定模板：
- 外部热点：Codex Record / Record & Replay 新功能。
- 普通 AI 号讲法：Codex 可以录制工作流并生成 Skill。
- 我的选题命题：剪辑经验如果能被录下来，真正难的是保住人的节奏判断。
- 我要做的实验：录一段真实剪辑流程，检查 Codex Record 能不能生成可读、可复跑、能保留验收规则的剪辑 Skill。
- 我的工作流痛点：脚本、封面、素材生成相对容易结构化，但剪辑最依赖人的节奏、取舍、字幕、切屏和成片验收。
- 旧流程痛点：剪一条视频要人工理解脚本、找录屏重点、切真人画面、加字幕、做结果闪现和 CTA，很多判断靠经验，难交接也难复盘。
- AI介入点：用 Codex Record 录制一次标准剪辑流程，让它生成一个可复用的剪辑 Skill。
- 验证方式：录 3 分钟剪辑流程，导出 Record Skill 后复跑一次；记录复跑漏掉的 3 个判断点，能复跑且人工修正少于一轮才算通过。
- 可沉淀资产：剪辑判断节点表 / Record Skill 复跑记录 / 成片返修检查表。

请重写/覆盖这些字段：
{json.dumps(SKILL_FIELDS, ensure_ascii=False)}

候选状态只能是：今日最值得做、可选候选、暂存观察、不建议制作。
推荐等级只能是：S、A、B、C。
对应方向只能是：AI业务定调、真实工作流改造、AI导演工作流、汽车与内容营销、AI项目复盘。
证据强度只能是：强、中、弱。
今日最值得做最多 3 条。
候选数量不需要凑满 10 条；只把真的值得用户打开看的内容标为 `今日最值得做` 或 `可选候选`。

<editorial_rule_text>
{skill_text}
</editorial_rule_text>

<persona-brief.md>
{persona_brief}
</persona-brief.md>

案例/人设参考路径：{SKILL_REFERENCE}
本次不把完整长文全部塞入上下文；你必须优先使用上面的压缩底稿，以及每条候选里的 `关联母场景候选`。

<approved_selection_learning_context>
{selection_learning}
</approved_selection_learning_context>

使用已确认选择学习摘要的方式：
- 只有经过用户确认的学习摘要才会出现在这里；如果没有，不要猜测用户偏好。
- 它只代表近期偏好，不是硬规则；不要机械排除某个方向。
- 正向样本说明用户愿意推进的场景、证据和原因。
- 负向样本说明本轮不想做的原因，尤其是证据浅、像工具教程、缺个人经验的候选。
- 如果候选和正向偏好相似，优先补强 `场景依据 / 可展示证据 / 推荐理由`；如果和负向样本相似，优先降级或要求补证据。

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
        out["editorial_engine"] = "codex"
        out["fallback_only"] = "false"
        out["not_editorial_quality"] = "false"
        enriched.append(out)
    return normalize_batch(enriched), {
        "codex_rows": len(by_index),
        "batch_notes": payload.get("batch_notes", ""),
        "model": model or "codex-default",
        "approved_selection_learning": str(APPROVED_SELECTION_LEARNING_MD) if APPROVED_SELECTION_LEARNING_MD.exists() else "",
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
        help="Default codex embeds the global private Skill text. deterministic is an explicit offline emergency fallback.",
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
            engine_meta = {
                "mode": "explicit_deterministic",
                "fallback_only": True,
                "not_editorial_quality": True,
                "approved_selection_learning": str(APPROVED_SELECTION_LEARNING_MD) if APPROVED_SELECTION_LEARNING_MD.exists() else "",
            }
    except Exception as exc:
        if not args.allow_deterministic_fallback:
            raise
        engine = "deterministic"
        enriched = normalize_batch([enrich(row) for row in rows])
        engine_meta = {
            "fallback_after_error": str(exc),
            "fallback_only": True,
            "not_editorial_quality": True,
            "approved_selection_learning": str(APPROVED_SELECTION_LEARNING_MD) if APPROVED_SELECTION_LEARNING_MD.exists() else "",
        }
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
