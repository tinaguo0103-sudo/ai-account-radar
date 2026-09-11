from __future__ import annotations

import hashlib
import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "austin-voice-scriptwriter"
PROJECT_ROOT = Path(os.environ.get("AI_ACCOUNT_WORKFLOW_ROOT", str(ROOT.parent)))
SOURCE_DIR = PROJECT_ROOT / "00_资料库"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AustinVoiceReferenceArchitectureTests(unittest.TestCase):
    def test_managed_reference_tree_is_discoverable_and_provenanced(self) -> None:
        expected = [
            SKILL_DIR / "SKILL.md",
            SKILL_DIR / "references" / "austin-profile.md",
            SKILL_DIR / "references" / "case-index.md",
            SKILL_DIR / "references" / "argument-development.md",
            SKILL_DIR / "references" / "content-methodology.md",
            SKILL_DIR / "references" / "khazix-craft-reference.md",
            SKILL_DIR / "references" / "spoken-adaptation.md",
            SKILL_DIR / "references" / "voice-excerpts.md",
            SKILL_DIR / "references" / "revision.md",
            SKILL_DIR / "THIRD_PARTY_NOTICES.md",
            SKILL_DIR / "references" / "cases" / "radar-to-selection-board.md",
            SKILL_DIR / "references" / "cases" / "commercial-video-delivery.md",
            SKILL_DIR / "references" / "voice-samples" / "cover-skill-original.md",
            SKILL_DIR / "references" / "voice-samples" / "hotspot-radar-original.md",
        ]
        for path in expected:
            self.assertTrue(path.is_file(), path)
        profile = (SKILL_DIR / "references" / "austin-profile.md").read_text(encoding="utf-8")
        index = (SKILL_DIR / "references" / "case-index.md").read_text(encoding="utf-8")
        self.assertIn("我的案例库.docx", profile)
        self.assertIn("我的案例库.docx", index)
        self.assertIn("不是关键词、分数、embedding 或代码选择器", index)
        self.assertIn("不规定开头、结构或结尾", (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("大多数人看到这条热点", (SKILL_DIR / "references" / "argument-development.md").read_text(encoding="utf-8"))
        methodology = (SKILL_DIR / "references" / "content-methodology.md").read_text(encoding="utf-8")
        adaptation = (SKILL_DIR / "references" / "spoken-adaptation.md").read_text(encoding="utf-8")
        self.assertIn("判断随着材料变清楚", methodology)
        self.assertIn("文章先完整成立", methodology)
        self.assertIn("文章本身写完整", adaptation)
        self.assertIn("voice-samples/cover-skill-original.md", (SKILL_DIR / "references" / "voice-excerpts.md").read_text(encoding="utf-8"))
        self.assertIn("不使用扫描命中、数字、固定句式、模板分类或评分决定通过和拒绝", (SKILL_DIR / "references" / "revision.md").read_text(encoding="utf-8"))

    def test_khazix_craft_is_substantial_optional_and_identity_safe(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        craft = (SKILL_DIR / "references" / "khazix-craft-reference.md").read_text(encoding="utf-8")
        notice = (SKILL_DIR / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

        self.assertIn("按目录只读取 `references/khazix-craft-reference.md` 的相关部分", skill)
        self.assertIn("第三方 craft 示例只说明文章可以怎样形成、推进和修改", skill)
        self.assertIn("两者冲突时，以 Austin 身份和当前事实为准", skill)
        self.assertIn("不预先填写最终 thesis", skill)
        self.assertIn("references/spoken-adaptation.md", skill)
        for section in (
            "## 题目怎样展开",
            "## 文章怎样有运动",
            "## 表达怎样有温度",
            "## 开头与结尾",
            "## 偏好与避雷",
            "## 修改展示",
            "## 主观自检",
        ):
            self.assertIn(section, craft)
        for tool in (
            "调查实验型",
            "产品体验型",
            "现象解读型",
            "方法论分享型",
            "逐一展示与升番",
            "回环呼应",
            "反向论证",
            "人物画像",
            "尼玛",
            "4000 到 8000 字",
            "AI 初稿",
        ):
            self.assertIn(tool, craft)
        for excluded_identity_or_footer in (
            "在AI行业深耕三年",
            "公众号「数字生命卡兹克」",
            "wzglyay@virxact.com",
            "随手点个赞",
            "给我个星标",
        ):
            self.assertNotIn(excluded_identity_or_footer, craft)
        self.assertIn("3fa874169134f65b14e8a27164386510bc867037", notice)
        self.assertIn("Copyright (c) 2026 数字生命卡兹克", notice)
        self.assertIn("Permission is hereby granted", notice)
        self.assertNotIn("THIRD_PARTY_NOTICES.md", skill)

    def test_khazix_preferences_do_not_become_deterministic_gates(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        craft = (SKILL_DIR / "references" / "khazix-craft-reference.md").read_text(encoding="utf-8")
        revision = (SKILL_DIR / "references" / "revision.md").read_text(encoding="utf-8")
        self.assertIn("可选工具箱，不是人格来源、统一模板、逐项清单或质量门禁", skill)
        self.assertIn("不做零命中检查", craft)
        self.assertIn("不输出分数、不统计命中、不形成通过门禁", craft)
        self.assertIn("不使用扫描命中、数字、固定句式、模板分类或评分决定通过和拒绝", revision)

    def test_every_case_index_entry_resolves_without_source_docx(self) -> None:
        index = (SKILL_DIR / "references" / "case-index.md").read_text(encoding="utf-8")
        links = re.findall(r"`(cases/[^`]+\.md)`", index)
        self.assertEqual(len(links), 9)
        for relative in links:
            path = SKILL_DIR / "references" / relative
            self.assertTrue(path.is_file(), path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("来源：", text)
            self.assertTrue("不能" in text or "不允许" in text, path)
        self.assertNotIn("no separate managed excerpt", index)

    def test_voice_samples_are_exact_user_originals(self) -> None:
        parity = {
            SKILL_DIR / "references" / "voice-samples" / "cover-skill-original.md":
                SOURCE_DIR / "03_口播风格样稿" / "封面skill 口播稿final.md",
            SKILL_DIR / "references" / "voice-samples" / "hotspot-radar-original.md":
                SOURCE_DIR / "03_口播风格样稿" / "热点监控及脚本落地_1500字口播脚本.md",
        }
        for managed, original in parity.items():
            self.assertTrue(original.is_file(), original)
            self.assertEqual(sha256(managed), sha256(original), managed)

    def test_normal_entrypoint_does_not_reintroduce_retired_template_sources(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        managed = "\n".join(path.read_text(encoding="utf-8") for path in SKILL_DIR.rglob("*.md"))
        for retired in (
            "three_round_learning.md",
            "public_voice_style.md",
            "scripts/austin_voice.py",
            "austin-no-overtime-scripting",
            "production_context.md",
            "private_runtime.json",
            "full_topic_cards.json",
        ):
            self.assertNotIn(retired, skill)
        self.assertNotIn("AI 可以承担信息压缩、候选生成、视觉试错、版本扩展和部分中间工作；Austin 自己负责", skill)
        self.assertNotIn("three_round_learning.md", managed)
        self.assertNotIn("public_voice_style.md", managed)
        self.assertNotIn("derived style", managed.lower())
        self.assertNotIn("keyword selector", managed.lower())

    def test_route_is_progressive_and_not_a_quality_gate(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("每篇先读取 `references/austin-profile.md`", skill)
        self.assertIn("最相关的一到两份", skill)
        self.assertIn("完整 body", skill)
        self.assertIn("只在需要时打开一份", skill)
        for forbidden in ("字数门禁", "固定开场", "固定提纲", "正文评分器", "embedding"):
            self.assertNotIn(forbidden, skill)
        self.assertIn("完整文章", skill)
        self.assertIn("自然口播", skill)


if __name__ == "__main__":
    unittest.main()
