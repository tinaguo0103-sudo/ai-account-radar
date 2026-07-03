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


if __name__ == "__main__":
    unittest.main()
