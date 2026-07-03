#!/usr/bin/env python3
"""Regression tests for AR-009 scene-first Austin voice rules."""
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


VOICE = load_module(VOICE_MODULE_PATH, "austin_voice_scene_rules")
SCRIPTING = load_module(SCRIPTING_MODULE_PATH, "austin_scripting_scene_rules")


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
    "需要补的证据": "补一张从03到04再到06的路径截图。",
    "主编判断": "对标视频浅，但能转成我自己的内容系统复盘。",
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
}


class AustinVoiceSceneRulesTest(unittest.TestCase):
    def test_knowledge_base_topic_starts_with_content_production_scene(self) -> None:
        topic = SCRIPTING.normalize_topic(KNOWLEDGE_TOPIC, record_id=KNOWLEDGE_TOPIC["record_id"])
        text = VOICE.render_voice_text(topic)

        self.assertIn("03 收件箱", text)
        self.assertIn("04 变成选题判断", text)
        self.assertIn("重新找、重新判断、重新组织", text)
        self.assertIn("我不会照着讲教程", text)
        self.assertIn("资料有没有一路变成判断、脚本和可复用资产", text)
        self.assertLess(text.index("03 收件箱"), text.index("知识库"))

    def test_voice_agent_topic_mentions_delivery_details_before_tool_concept(self) -> None:
        topic = SCRIPTING.normalize_topic(VOICE_AGENT_TOPIC, record_id=VOICE_AGENT_TOPIC["record_id"])
        text = VOICE.render_voice_text(topic)

        self.assertIn("30 秒口播脚本", text)
        self.assertIn("角色语气、分镜节奏、字幕长度和返修验收", text)
        self.assertIn("它表面在讲一个 Voice Agent 工具", text)
        self.assertIn("声音能不能被角色、分镜、字幕和返修流程接住", text)
        self.assertLess(text.index("30 秒口播脚本"), text.index("Voice Agent 工具"))

    def test_full_package_uses_scene_first_voice_skill(self) -> None:
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

        self.assertIn("对标拆解后再转译", document)
        self.assertIn("30 秒口播脚本", document)
        self.assertIn("角色语气、分镜节奏、字幕长度和返修验收", document)
        self.assertIn("场景化表达规则", result["production_context"])


if __name__ == "__main__":
    unittest.main()
