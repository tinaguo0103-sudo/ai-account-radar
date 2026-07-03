#!/usr/bin/env python3
"""Regression tests for AR-009 voice baseline and research-material handling."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VOICE_SKILL_DIR = ROOT / "skills" / "austin-voice-scriptwriter"
VOICE_MODULE_PATH = VOICE_SKILL_DIR / "scripts" / "austin_voice.py"
SCRIPTING_MODULE_PATH = ROOT / "skills" / "austin-no-overtime-scripting" / "scripts" / "austin_scripting.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VOICE = load_module(VOICE_MODULE_PATH, "austin_voice_research_fusion")
SCRIPTING = load_module(SCRIPTING_MODULE_PATH, "austin_scripting_research_fusion")


def render_package_document(raw_topic: dict[str, str]) -> str:
    topic = SCRIPTING.normalize_topic(raw_topic, record_id=raw_topic["record_id"])
    old_env = os.environ.get("AUSTIN_VOICE_SCRIPT_SKILL_DIR")
    os.environ["AUSTIN_VOICE_SCRIPT_SKILL_DIR"] = str(VOICE_SKILL_DIR)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = SCRIPTING.render_full_execution_package(topic, Path(temp_dir), run_date="2026-07-02")
            return Path(result["document_path"]).read_text(encoding="utf-8")
    finally:
        if old_env is None:
            os.environ.pop("AUSTIN_VOICE_SCRIPT_SKILL_DIR", None)
        else:
            os.environ["AUSTIN_VOICE_SCRIPT_SKILL_DIR"] = old_env


def markdown_section(document: str, heading: str) -> str:
    start = document.index(heading)
    next_start = document.find("\n### ", start + len(heading))
    if next_start == -1:
        return document[start:]
    return document[start:next_start]


def markdown_range(document: str, start_heading: str, end_heading: str) -> str:
    start = document.index(start_heading)
    end = document.index(end_heading, start + len(start_heading))
    return document[start:end]


FORBIDDEN_VOICE_PHRASES = [
    "这条最后要看的不是概念讲得多完整",
    "能不能回到我的真实流程",
    "很多语音 Agent 内容会先讲几分钟搭一个会对话的 Agent",
    "很多知识库内容会先讲 Obsidian 图谱",
    "借这个选题回头检查自己的内容系统",
    "资料进来以后，有没有真的沉淀成后面能用的资产",
    "一条素材能不能从 03 收件箱走到 04 选题，再走到 06 脚本和复盘",
    "不要让 AI 看起来完成了",
    "同类资料讲法偏浅",
    "保留 voice agent",
    "丢弃未核验",
    "融合到 30 秒口播脚本",
    "如果真要拿",
    "这条真正要做什么",
    "围绕「",
    "我先看三个动作",
    "能不能继续做，最后看的是",
    "最后还是回到我自己判断",
]

FORBIDDEN_FALLBACK_TEMPLATE_PHRASES = [
    "我先不讲「",
    "这一段不急着解释工具",
    "这个动作不求完整演示",
    "不让它变成整条视频的主角",
    "如果这一段只剩",
    "拍之前我至少还要补",
    "补不上，这条就先停在草稿",
]

INTERNAL_STATUS_BOUNDARIES = [
    "如果当天还没生成06",
    "如果当天没有生成 06",
    "如果当天没生成",
    "如果今天没有完整生成到最后一步",
    "没有完整生成到最后一步",
    "选题系统复盘",
]

USER_VISIBLE_CREATIVE_SECTIONS = [
    "### 视频结构",
    "### 口播全文",
    "### 分段执行方案",
    "### 录屏与素材清单",
    "### 剪辑交接",
    "### 发布包草稿",
]


def voice_section(raw_topic: dict[str, str]) -> str:
    document = render_package_document(raw_topic)
    return markdown_range(document, "### 口播全文", "### 分段执行方案")


def spoken_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("###"):
            continue
        if len(clean) < 12:
            continue
        lines.append(clean)
    return lines


def markdown_line(document: str, prefix: str) -> str:
    for line in document.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"Cannot find line starting with {prefix!r}")


def voice_subheadings(document: str) -> list[str]:
    section = markdown_range(document, "### 口播全文", "### 分段执行方案")
    return [line.strip() for line in section.splitlines() if line.startswith("### 00:")]


def execution_plan_purposes(document: str) -> list[str]:
    section = markdown_range(document, "### 分段执行方案", "### 录屏与素材清单")
    purposes: list[str] = []
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("|---") or "段落" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 2:
            purposes.append(cells[1])
    return purposes


KNOWLEDGE_TOPIC = {
    "record_id": "recvoaOc5dT6vv",
    "选题命题": "Codex+Obsidian知识库这个选题，我会反过来检查自己的信息雷达有没有沉淀资产",
    "对应方向": "真实工作流改造",
    "一句话Brief": "借 Codex+Obsidian 知识库视频，讲我为什么把重点放在内容生产过程中的自动沉淀，而不是再搭一个资料仓库。",
    "我的工作流痛点": "知识库容易变成资料仓库，但内容生产真正需要的是资料在流程里自动变成判断、脚本和复盘资产。",
    "旧流程痛点": "资料存进去以后，写内容时还是要重新找、重新判断、重新组织。",
    "AI介入点": "AI负责把资料摘要、选题判断、脚本输入和输出路径结构化，人负责确认哪些值得沉淀。",
    "可沉淀资产": "信息雷达内容资产沉淀检查清单",
    "可展示证据": "03内容收件箱；04选题字段；06文档路径；脚本包文件",
    "需要补的证据": "补一张从03到04再到06的路径截图；如果当天还没生成06，就只作为选题系统复盘。",
    "主编判断": "同类资料讲法偏浅，但能转成我自己的内容系统复盘。",
    "搜索来源摘要": "Obsidian 官方文档显示笔记可以链接到文件、标题和块；Obsidian Graph 文档把笔记和关系显示成节点与链接。",
    "表达模式拆解": "同类知识库内容常从“第二大脑、自动整理、图谱”进入，但容易变教程。",
    "融合说明": "保留链接、关系、可检索这三个信息点；丢弃插件安装、库结构教程；融合到信息雷达 03 收件箱 -> 04 选题判断 -> 06 脚本包路径。",
    "概念浅显解释": "知识库不是一个大仓库，更像给每条素材贴一张流转单：它从哪里来、为什么值得看、最后变成哪条选题或脚本，下次才能被叫回来用。",
    "风格基线保护": "沿用真实痛点、旧流程、三步动作、边界收尾；先讲我的内容生产现场，再引出知识库作为解决方案。",
}


VOICE_AGENT_TOPIC = {
    "record_id": "recvoaOc5dJfbS",
    "选题命题": "xAI Voice Agent Builder出来后，我想重看AI口播能不能进入视频交付",
    "对应方向": "AI导演工作流",
    "一句话Brief": "借 xAI Voice Agent Builder 热点，讲 AI口播进入视频交付前，必须过导演和成片验收。",
    "我的工作流痛点": "AI口播工具看起来很快，但商业视频交付还要处理语气、角色一致性、画面节奏、字幕和返修。",
    "旧流程痛点": "以前脚本、配音、字幕、分镜和剪辑验收是分开的，生成声音不等于可交付。",
    "AI介入点": "AI可以生成声音版本和尝试不同语气，人负责判断它是否匹配角色、节奏和最终成片。",
    "可沉淀资产": "AI视频Brief与分镜验收清单",
    "可展示证据": "一段口播脚本；分镜节点；字幕长度；口播验收清单",
    "需要补的证据": "补一个真实口播脚本片段和一页口播验收表；不需要证明 xAI 工具完整可用。",
    "主编判断": "热点新，且能落到我有优势的 AI导演工作流。",
    "不能声称的部分": "xAI Voice Agent 需要确认是否开放、是否收费、能力边界是什么。",
    "搜索来源摘要": "未找到可核验的 xAI Voice Agent Builder 官方资料；OpenAI Realtime voice-agent 文档显示语音 Agent 是音频/文本输入、响应、工具调用和会话事件一起运转。",
    "表达模式拆解": "同类 voice-agent 内容常用“几分钟搭好一个能接电话/能对话的 agent”开场，但容易停在工具体验。",
    "融合说明": "保留 voice agent 从声音 demo 进入工作流节点的趋势；丢弃未核验的 xAI 具体能力；融合到 30 秒口播脚本、角色语气、分镜节奏、字幕长度和返修验收这个视频交付现场。",
    "概念浅显解释": "声音只是素材，不是成片；就像演员把台词念出来之后，还要看角色、镜头、字幕和剪辑节奏能不能接得住。",
    "风格基线保护": "沿用真实痛点、旧流程、三步动作、边界收尾；外部信息只做输入，不把稿子写成工具新闻或功能教程。",
}


class AustinVoiceResearchFusionTest(unittest.TestCase):
    def test_research_context_enters_generation_input_and_package(self) -> None:
        topic = SCRIPTING.normalize_topic(VOICE_AGENT_TOPIC, record_id=VOICE_AGENT_TOPIC["record_id"])
        old_env = os.environ.get("AUSTIN_VOICE_SCRIPT_SKILL_DIR")
        os.environ["AUSTIN_VOICE_SCRIPT_SKILL_DIR"] = str(VOICE_SKILL_DIR)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                result = SCRIPTING.render_full_execution_package(topic, Path(temp_dir), run_date="2026-07-02")
                document = Path(result["document_path"]).read_text(encoding="utf-8")
        finally:
            if old_env is None:
                os.environ.pop("AUSTIN_VOICE_SCRIPT_SKILL_DIR", None)
            else:
                os.environ["AUSTIN_VOICE_SCRIPT_SKILL_DIR"] = old_env

        self.assertIn("搜索来源：未找到可核验的 xAI Voice Agent Builder 官方资料", result["production_context"])
        self.assertIn("表达模式：同类 voice-agent 内容常用", result["production_context"])
        self.assertIn("### 搜索与表达融合", document)
        self.assertIn("OpenAI Realtime voice-agent 文档", document)
        self.assertIn("30 秒口播脚本、角色语气、分镜节奏、字幕长度和返修验收", document)
        self.assertIn("结果：draft", document)
        self.assertIn("草稿，待 PM 验收，待 QA", document)
        self.assertNotIn("结果：pass", document)
        self.assertNotIn("可进入拍摄准备", document)

    def test_concept_tool_method_enters_generation_input_without_template_lock(self) -> None:
        topic = SCRIPTING.normalize_topic(KNOWLEDGE_TOPIC, record_id=KNOWLEDGE_TOPIC["record_id"])
        validation = SCRIPTING.validate_topic(topic)
        template, template_reason = SCRIPTING.classify_template(topic)
        context = SCRIPTING.generation_input_for_06(topic, template, template_reason, validation, [])

        self.assertIn("概念/工具型生成前判断（内部素材，不要逐条念，不要固定段落顺序）", context)
        self.assertIn("旧方式/普通替代方式：资料存进去以后", context)
        self.assertIn("真实工作卡点：知识库容易变成资料仓库", context)
        self.assertIn("为什么现在需要它：AI负责把资料摘要、选题判断", context)
        self.assertIn("只是当前案例，不是全文主角", context)
        self.assertNotIn("固定段落顺序：", context)
        self.assertNotIn("每条必须显性出现", context)

    def test_concept_tool_opening_starts_from_old_way_not_naked_concept(self) -> None:
        knowledge_doc = render_package_document(KNOWLEDGE_TOPIC)
        voice_agent_doc = render_package_document(VOICE_AGENT_TOPIC)
        knowledge_opening = markdown_line(knowledge_doc, "- 开头钩子：")
        voice_agent_opening = markdown_line(voice_agent_doc, "- 开头钩子：")

        self.assertIn("资料存进去以后", knowledge_opening)
        self.assertNotIn("- 开头钩子：知识库", knowledge_opening)
        self.assertIn("以前脚本、配音、字幕、分镜和剪辑验收是分开的", voice_agent_opening)
        self.assertNotIn("- 开头钩子：xAI Voice Agent Builder", voice_agent_opening)

    def test_method_update_does_not_hardcode_user_examples_or_sample_helpers(self) -> None:
        paths = [
            ROOT / "scripts" / "codex_script_package_runner.py",
            ROOT / "skills" / "austin-no-overtime-scripting" / "SKILL.md",
            ROOT / "skills" / "austin-no-overtime-scripting" / "scripts" / "austin_scripting.py",
            ROOT / "skills" / "austin-voice-scriptwriter" / "SKILL.md",
            ROOT / "skills" / "austin-voice-scriptwriter" / "scripts" / "austin_voice.py",
        ]
        source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

        for phrase in ["knowledge_base_opening", "tts_opening", "传统 TTS", "音色像不像", "iCloud", "Word", "备忘录"]:
            self.assertNotIn(phrase, source)

    def test_knowledge_base_material_stays_in_report_not_voice_mapping(self) -> None:
        topic = SCRIPTING.normalize_topic(KNOWLEDGE_TOPIC, record_id=KNOWLEDGE_TOPIC["record_id"])
        text = VOICE.render_voice_text(topic, SCRIPTING.voice_skill_context(topic, SCRIPTING.validate_topic(topic)))
        document = render_package_document(KNOWLEDGE_TOPIC)

        self.assertIn("知识库不是一个大仓库", markdown_section(document, "### 搜索与表达融合"))
        self.assertIn("给每条素材贴一张流转单", markdown_section(document, "### 搜索与表达融合"))
        self.assertIn("fallback_draft", text)
        self.assertIn("not_style_qa", text)
        self.assertNotIn("沉淀资产", text)
        for phrase in FORBIDDEN_VOICE_PHRASES + FORBIDDEN_FALLBACK_TEMPLATE_PHRASES:
            self.assertNotIn(phrase, text)
        self.assertNotIn("如果当天还没生成06", text)
        self.assertNotIn("先给真实场景", text)
        self.assertNotIn("对标拆解后再转译", text)
        self.assertNotIn("我会参考同类内容里这个讲法", text)
        self.assertNotIn("但最后会收回到我的表达", text)
        self.assertNotIn("这里守住一个基线", text)

    def test_deterministic_voice_is_not_style_qa_fallback(self) -> None:
        topic = SCRIPTING.normalize_topic(VOICE_AGENT_TOPIC, record_id=VOICE_AGENT_TOPIC["record_id"])
        text = VOICE.render_voice_text(topic, SCRIPTING.voice_skill_context(topic, SCRIPTING.validate_topic(topic)))

        self.assertIn("fallback_draft", text)
        self.assertIn("not_style_qa", text)
        self.assertIn("只用于字段、格式和安全边界兜底", text)
        self.assertIn("不代表 Austin 风格质量验收", text)
        self.assertNotIn("### 00:00-00:35｜真实痛点", text)
        self.assertNotIn("### 00:35-01:05｜旧流程", text)
        self.assertNotIn("### 01:05-01:35｜这条真正要做什么", text)
        self.assertNotIn("### 01:35-02:50｜三个动作", text)
        self.assertNotIn("### 00:00-00:30｜先给真实场景", text)
        self.assertNotIn("沿用真实痛点、旧流程、三步动作、边界收尾", text)
        for phrase in FORBIDDEN_FALLBACK_TEMPLATE_PHRASES:
            self.assertNotIn(phrase, text)

    def test_voice_agent_keeps_research_material_out_of_voice_body(self) -> None:
        topic = SCRIPTING.normalize_topic(VOICE_AGENT_TOPIC, record_id=VOICE_AGENT_TOPIC["record_id"])
        text = VOICE.render_voice_text(topic, SCRIPTING.voice_skill_context(topic, SCRIPTING.validate_topic(topic)))
        document = render_package_document(VOICE_AGENT_TOPIC)
        voice_text = markdown_range(document, "### 口播全文", "### 分段执行方案")

        self.assertIn("同类 voice-agent 内容常用", markdown_section(document, "### 搜索与表达融合"))
        self.assertIn("30 秒口播脚本、角色语气、分镜节奏、字幕长度和返修验收", markdown_section(document, "### 搜索与表达融合"))
        self.assertIn("xAI Voice Agent 需要确认是否开放", text)
        for term in ["脚本", "声音", "角色", "分镜", "字幕"]:
            self.assertIn(term, voice_text)
        self.assertTrue("剪辑" in voice_text or "返修" in voice_text)
        self.assertNotIn("很多人现在用 Agent 做项目", voice_text)
        for phrase in FORBIDDEN_VOICE_PHRASES + FORBIDDEN_FALLBACK_TEMPLATE_PHRASES:
            self.assertNotIn(phrase, text)
        self.assertNotIn("xAI Voice Agent Builder 已经", text)
        self.assertNotIn("我会参考同类内容里这个讲法", text)
        self.assertNotIn("但最后会收回到我的表达", text)
        self.assertNotIn("这里守住一个基线", text)

    def test_voice_script_has_no_ar009_fixed_body_mapping(self) -> None:
        source = VOICE_MODULE_PATH.read_text(encoding="utf-8")

        self.assertFalse(hasattr(VOICE, "research_spoken_lines"))
        self.assertFalse(hasattr(VOICE, "spoken_judgment"))
        self.assertNotIn("内容资产沉淀", source)
        self.assertNotIn("AI口播交付", source)
        for phrase in FORBIDDEN_VOICE_PHRASES:
            self.assertNotIn(phrase, source)

    def test_two_real_samples_do_not_reuse_fixed_voice_lines(self) -> None:
        voice_agent_text = voice_section(VOICE_AGENT_TOPIC)
        knowledge_text = voice_section(KNOWLEDGE_TOPIC)
        for phrase in FORBIDDEN_VOICE_PHRASES + FORBIDDEN_FALLBACK_TEMPLATE_PHRASES:
            self.assertNotIn(phrase, voice_agent_text)
            self.assertNotIn(phrase, knowledge_text)
        self.assertNotIn("沉淀资产", knowledge_text)
        self.assertIn("not_style_qa", voice_agent_text)
        self.assertIn("not_style_qa", knowledge_text)

        voice_agent_lines = spoken_lines(voice_agent_text)
        knowledge_lines = spoken_lines(knowledge_text)
        shared_lines = sorted(set(voice_agent_lines) & set(knowledge_lines))
        self.assertLessEqual(len(shared_lines), 3, shared_lines)
        self.assertLessEqual(
            len(shared_lines) / min(len(voice_agent_lines), len(knowledge_lines)),
            0.35,
            shared_lines,
        )

    def test_two_real_samples_do_not_share_fixed_section_scaffold(self) -> None:
        voice_agent_doc = render_package_document(VOICE_AGENT_TOPIC)
        knowledge_doc = render_package_document(KNOWLEDGE_TOPIC)
        fixed_scaffold_terms = [
            "真实痛点",
            "旧流程",
            "这条真正要做什么",
            "三个动作",
            "前后对比",
            "边界和收尾",
        ]

        for document in [voice_agent_doc, knowledge_doc]:
            voice_text = markdown_range(document, "### 口播全文", "### 分段执行方案")
            plan_text = markdown_range(document, "### 分段执行方案", "### 录屏与素材清单")
            self.assertIn("not_style_qa", voice_text)
            self.assertIn("not_style_qa", plan_text)
            for term in fixed_scaffold_terms:
                self.assertNotIn(term, voice_text)
                self.assertNotIn(term, plan_text)
            for phrase in FORBIDDEN_VOICE_PHRASES + FORBIDDEN_FALLBACK_TEMPLATE_PHRASES:
                self.assertNotIn(phrase, voice_text)
                self.assertNotIn(phrase, plan_text)

        self.assertIn("PM/用户内容质量验收必须走 codex exec + -ar009-test", markdown_section(voice_agent_doc, "### QA"))
        self.assertIn("PM/用户内容质量验收必须走 codex exec + -ar009-test", markdown_section(knowledge_doc, "### QA"))

    def test_missing_real_scene_gets_qa_warning_without_voice_fill(self) -> None:
        raw_topic = {
            "record_id": "rec_missing_scene",
            "选题命题": "一个抽象AI工作流选题",
            "对应方向": "真实工作流改造",
            "一句话Brief": "讲AI工作流为什么要被验收。",
            "我的工作流痛点": "工作流跑完以后不知道怎么判断。",
            "旧流程痛点": "以前只看结果。",
            "AI介入点": "AI先整理结果。",
            "可沉淀资产": "复盘资产",
            "可展示证据": "",
            "需要补的证据": "",
            "主编判断": "AI不能只看生成结果，必须回到业务验收。",
        }
        document = render_package_document(raw_topic)

        self.assertIn("真实案例/现场不足", markdown_section(document, "### QA"))
        self.assertNotIn("真实案例/现场不足", markdown_section(document, "### 口播全文"))

    def test_internal_boundaries_stay_out_of_shootable_sections(self) -> None:
        checks = [
            (VOICE_AGENT_TOPIC, "不需要证明 xAI 工具完整可用"),
            (KNOWLEDGE_TOPIC, "如果当天还没生成06，就只作为选题系统复盘"),
        ]

        for raw_topic, forbidden in checks:
            with self.subTest(topic=raw_topic["record_id"]):
                document = render_package_document(raw_topic)
                self.assertIn(forbidden, markdown_section(document, "### 一屏结论"))
                self.assertIn(forbidden, markdown_section(document, "### QA"))

                for heading in [
                    "### 视频结构",
                    "### 口播全文",
                    "### 分段执行方案",
                    "### 录屏与素材清单",
                ]:
                    self.assertNotIn(forbidden, markdown_section(document, heading))

        knowledge_doc = render_package_document(KNOWLEDGE_TOPIC)
        self.assertIn("03 收件箱、04 选题字段、06 文档路径和脚本包路径截图", knowledge_doc)
        self.assertNotIn("同类资料讲法偏浅", knowledge_doc)

    def test_final_markdown_sanitizes_user_visible_copy(self) -> None:
        document = render_package_document(KNOWLEDGE_TOPIC)

        self.assertNotIn("沉淀资产", document)
        self.assertNotIn("结果：pass", document)
        self.assertIn("结果：draft", document)

        one_screen = markdown_section(document, "### 一屏结论")
        self.assertNotIn("沉淀资产", one_screen)
        shooting_line = markdown_line(document, "- 拍摄前待办：")
        opening_line = markdown_line(document, "- 开头钩子：")
        for term in INTERNAL_STATUS_BOUNDARIES:
            self.assertNotIn(term, shooting_line)
            self.assertNotIn(term, opening_line)

        for heading in USER_VISIBLE_CREATIVE_SECTIONS:
            section = markdown_section(document, heading)
            self.assertNotIn("沉淀资产", section)
            for term in INTERNAL_STATUS_BOUNDARIES:
                self.assertNotIn(term, section)


if __name__ == "__main__":
    unittest.main()
