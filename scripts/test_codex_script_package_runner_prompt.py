#!/usr/bin/env python3
"""Tests for script package runner prompt Skill routing."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import codex_script_package_runner as runner


class CodexScriptPackageRunnerPromptTest(unittest.TestCase):
    def test_topic_prompt_defaults_to_production_skill_names(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            prompt = runner.topic_prompt({"topic_title": "测试选题"})

        self.assertIn(
            "请使用本机全局 Skill `$austin-no-overtime-scripting` 和 `$austin-voice-scriptwriter` 的方法",
            prompt,
        )
        self.assertNotIn("austin-no-overtime-scripting-ar009-test", prompt)
        self.assertNotIn("austin-voice-scriptwriter-ar009-test", prompt)

    def test_topic_prompt_can_route_to_ar009_test_skill_names(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SCRIPT_PACKAGE_SKILL_NAME": "austin-no-overtime-scripting-ar009-test",
                "SCRIPT_PACKAGE_VOICE_SKILL_NAME": "austin-voice-scriptwriter-ar009-test",
            },
            clear=False,
        ):
            prompt = runner.topic_prompt({"topic_title": "测试选题"})

        self.assertIn(
            "请使用本机全局 Skill `$austin-no-overtime-scripting-ar009-test` 和 `$austin-voice-scriptwriter-ar009-test` 的方法",
            prompt,
        )
        self.assertIn("内部状态边界只能留在 `发布前核验`、`QA 风险与防错` 或 `发布前提醒`", prompt)
        self.assertIn("`沉淀资产` 是内部抽象词，不得出现在用户可见创作内容中", prompt)
        self.assertIn("`qa_status` 不要自评为 `pass`", prompt)
        self.assertIn("概念/工具型选题，先做生成前判断", prompt)
        self.assertIn("只作为素材组织方式，不是口播模板", prompt)
        self.assertIn("不要把用户举例写成固定规则", prompt)


if __name__ == "__main__":
    unittest.main()
