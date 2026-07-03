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
]


def voice_section(raw_topic: dict[str, str]) -> str:
    document = render_package_document(raw_topic)
    return markdown_section(document, "### 口播全文")


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

    def test_knowledge_base_material_stays_in_report_not_voice_mapping(self) -> None:
        topic = SCRIPTING.normalize_topic(KNOWLEDGE_TOPIC, record_id=KNOWLEDGE_TOPIC["record_id"])
        text = VOICE.render_voice_text(topic, SCRIPTING.voice_skill_context(topic, SCRIPTING.validate_topic(topic)))
        document = render_package_document(KNOWLEDGE_TOPIC)

        self.assertIn("知识库不是一个大仓库", markdown_section(document, "### 搜索与表达融合"))
        self.assertIn("给每条素材贴一张流转单", markdown_section(document, "### 搜索与表达融合"))
        self.assertIn("能转成我自己的内容系统复盘", text)
        for phrase in FORBIDDEN_VOICE_PHRASES:
            self.assertNotIn(phrase, text)
        self.assertNotIn("如果当天还没生成06", text)
        self.assertNotIn("先给真实场景", text)
        self.assertNotIn("对标拆解后再转译", text)
        self.assertNotIn("我会参考同类内容里这个讲法", text)
        self.assertNotIn("但最后会收回到我的表达", text)
        self.assertNotIn("这里守住一个基线", text)

    def test_stable_voice_baseline_headings_are_preserved(self) -> None:
        topic = SCRIPTING.normalize_topic(VOICE_AGENT_TOPIC, record_id=VOICE_AGENT_TOPIC["record_id"])
        text = VOICE.render_voice_text(topic, SCRIPTING.voice_skill_context(topic, SCRIPTING.validate_topic(topic)))

        self.assertIn("### 00:00-00:35｜真实痛点", text)
        self.assertIn("### 00:35-01:05｜旧流程", text)
        self.assertIn("### 01:05-01:35｜这条真正要做什么", text)
        self.assertIn("### 01:35-02:50｜三个动作", text)
        self.assertNotIn("### 00:00-00:30｜先给真实场景", text)
        self.assertNotIn("沿用真实痛点、旧流程、三步动作、边界收尾", text)

    def test_voice_agent_keeps_research_material_out_of_voice_body(self) -> None:
        topic = SCRIPTING.normalize_topic(VOICE_AGENT_TOPIC, record_id=VOICE_AGENT_TOPIC["record_id"])
        text = VOICE.render_voice_text(topic, SCRIPTING.voice_skill_context(topic, SCRIPTING.validate_topic(topic)))
        document = render_package_document(VOICE_AGENT_TOPIC)

        self.assertIn("同类 voice-agent 内容常用", markdown_section(document, "### 搜索与表达融合"))
        self.assertIn("30 秒口播脚本、角色语气、分镜节奏、字幕长度和返修验收", markdown_section(document, "### 搜索与表达融合"))
        self.assertIn("xAI Voice Agent 需要确认是否开放", text)
        for phrase in FORBIDDEN_VOICE_PHRASES:
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
        for phrase in FORBIDDEN_VOICE_PHRASES:
            self.assertNotIn(phrase, voice_agent_text)
            self.assertNotIn(phrase, knowledge_text)

        shared_lines = sorted(set(spoken_lines(voice_agent_text)) & set(spoken_lines(knowledge_text)))
        self.assertLessEqual(len(shared_lines), 8, shared_lines)

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


if __name__ == "__main__":
    unittest.main()
