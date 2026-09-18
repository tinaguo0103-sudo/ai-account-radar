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
UPSTREAM_COMMIT = "3fa874169134f65b14e8a27164386510bc867037"
UPSTREAM_HASHES = {
    "khazix-writer/SKILL.md": "081bfbf5bc0f0c9a2c5a6410eaaa9de18a0cc50f932a1195f45df04e30018602",
    "khazix-writer/references/content_methodology.md": "e9d12c563c04a8795cb1513b222c275df0ecfbc885149d02da2bc817a10bdcd2",
    "khazix-writer/references/style_examples.md": "6ebf6f03072099c5f705d620fcbd2deeac15a2d4340617b72fa861d7f00cce7f",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AustinVoiceReferenceArchitectureTests(unittest.TestCase):
    def test_managed_reference_tree_is_discoverable_and_provenanced(self) -> None:
        expected = [
            SKILL_DIR / "SKILL.md",
            SKILL_DIR / "references" / "austin-profile.md",
            SKILL_DIR / "references" / "case-index.md",
            SKILL_DIR / "references" / "khazix-writer-port.md",
            SKILL_DIR / "references" / "khazix-content-methodology.md",
            SKILL_DIR / "references" / "khazix-style-examples.md",
            SKILL_DIR / "references" / "spoken-adaptation.md",
            SKILL_DIR / "references" / "voice-excerpts.md",
            SKILL_DIR / "THIRD_PARTY_NOTICES.md",
            SKILL_DIR / "references" / "cases" / "radar-to-selection-board.md",
            SKILL_DIR / "references" / "cases" / "commercial-video-delivery.md",
            SKILL_DIR / "references" / "voice-samples" / "cover-skill-original.md",
            SKILL_DIR / "references" / "voice-samples" / "hotspot-radar-original.md",
        ]
        for path in expected:
            self.assertTrue(path.is_file(), path)
        retired_summaries = (
            "argument-development.md",
            "content-methodology.md",
            "khazix-craft-reference.md",
            "revision.md",
        )
        for name in retired_summaries:
            self.assertFalse((SKILL_DIR / "references" / name).exists(), name)
        profile = (SKILL_DIR / "references" / "austin-profile.md").read_text(encoding="utf-8")
        index = (SKILL_DIR / "references" / "case-index.md").read_text(encoding="utf-8")
        notice = (SKILL_DIR / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("我的案例库.docx", profile)
        self.assertIn("我的案例库.docx", index)
        self.assertIn("不是关键词、分数、embedding 或代码选择器", index)
        self.assertIn(UPSTREAM_COMMIT, notice)
        for source, digest in UPSTREAM_HASHES.items():
            self.assertIn(f"- `{source}`: `{digest}`", notice)
        self.assertIn("Copyright (c) 2026 数字生命卡兹克", notice)
        self.assertIn("Permission is hereby granted", notice)

    def test_complete_port_is_mandatory_and_preserves_upstream_system(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        port = (SKILL_DIR / "references" / "khazix-writer-port.md").read_text(encoding="utf-8")
        methodology = (SKILL_DIR / "references" / "khazix-content-methodology.md").read_text(encoding="utf-8")
        examples = (SKILL_DIR / "references" / "khazix-style-examples.md").read_text(encoding="utf-8")

        self.assertIn("每题先完整读取 `references/khazix-writer-port.md`", skill)
        self.assertIn("不是可选工具箱、摘要、模板菜单或按需摘取的参考", skill)
        self.assertIn("随后完整读取 `references/khazix-content-methodology.md`", skill)
        self.assertNotIn("按目录只读取", skill)
        self.assertNotIn("只在需要时", skill.split("文章冻结后", 1)[0])
        self.assertGreaterEqual(len(port.splitlines()), 350)
        self.assertGreaterEqual(len(methodology.splitlines()), 136)
        self.assertGreaterEqual(len(examples.splitlines()), 428)

        for section in (
            "## 核心价值观",
            "## 第一步：理解素材与选题判断",
            "## 第二步：明确AI的角色边界",
            "## 第三步：写作",
            "### 文章原型",
            "### 风格内核",
            "### 表达风险边界",
            "### 推荐口语化词组",
            "### 开头的几种必杀技",
            "### 逐一展示法（升番逻辑）",
            "### 创意案例的力量",
            "### 结构模板",
            "### 字数和格式",
            "## 第四步：四层自检体系",
            "### L1 硬性规则检查",
            "### L2 风格一致性检查",
            "### L3 内容质量检查",
            "### L4 活人感终审",
        ):
            self.assertIn(section, port)
        for preserved_method in (
            "HKR",
            "调查实验型",
            "产品体验型",
            "现象解读型",
            "方法论分享型",
            "回环呼应",
            "反向论证",
            "文化升维",
            "人物画像法",
            "英雄之旅叙事弧",
            "情绪表达",
            "4000-8000字",
        ):
            self.assertIn(preserved_method, port)
        self.assertIn("## 19. 谦逊铺垫的进阶写法", examples)
        self.assertIn("## 4. 创意案例工作法", methodology)
        self.assertIn("上游方法保留优先", methodology)
        self.assertIn("上游示例保留原文作为 craft 参考", examples)

    def test_austin_surface_voice_overrides_third_party_lexical_signatures(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        port = (SKILL_DIR / "references" / "khazix-writer-port.md").read_text(encoding="utf-8")
        examples = (SKILL_DIR / "references" / "khazix-style-examples.md").read_text(encoding="utf-8")
        adaptation = (SKILL_DIR / "references" / "spoken-adaptation.md").read_text(encoding="utf-8")

        self.assertIn("Austin 的表层语言以当前材料中的自然说话位置", skill)
        self.assertIn("不是候选措辞", skill)
        self.assertIn("不沿用示例的开头、连接词、口癖、粗口和收束方式", skill)
        read_order = skill.split("## 必读顺序", 1)[1]
        self.assertLess(read_order.index("references/voice-excerpts.md"), read_order.index("references/khazix-style-examples.md"))

        self.assertIn("上游文本中的第三方作者、案例、第一人称和示例只说明写法功能", port)
        self.assertIn("上游示例列出了一批高频口语化表达", port)
        self.assertIn("这些词组不是每句话都要塞", port)
        self.assertNotIn("这些词组可以主动、自然地使用", port)
        self.assertNotIn("太特么赤鸡了", port)
        self.assertNotIn("不是哥们", port)
        self.assertIn("上游示例保留原文作为 craft 参考", examples)
        self.assertIn("第三方写作示例只保留推进功能", adaptation)

    def test_surface_voice_review_remains_model_owned_and_non_deterministic(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        port = (SKILL_DIR / "references" / "khazix-writer-port.md").read_text(encoding="utf-8")

        self.assertIn("不得建立禁词表、短语替换、频次阈值", skill)
        self.assertIn("不是固定禁词表", port)
        self.assertIn("不做脱离上下文的短语替换", port)
        self.assertIn("不按词频、命中数或数字分数把结果交给 runtime", port)

    def test_identity_autonomy_and_output_boundaries(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        port = (SKILL_DIR / "references" / "khazix-writer-port.md").read_text(encoding="utf-8")
        examples = (SKILL_DIR / "references" / "khazix-style-examples.md").read_text(encoding="utf-8")
        notice = (SKILL_DIR / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

        self.assertIn("主动检索可核验的公开一手资料", skill)
        self.assertIn("不要求用户先提供提纲、核心观点、问题清单或个人经历", skill)
        self.assertIn("只有写作必须依赖尚未公开的 Austin 亲历", skill)
        self.assertIn("第三方示例只用于理解写法", skill)
        self.assertIn("这里的作者、人物、经历、观点、口癖和结尾全部是第三方写法示范", examples)
        self.assertIn("MIT License", notice)
        self.assertNotIn("wzglyay@virxact.com", port)
        self.assertNotIn("随手点个赞", port)
        self.assertNotIn("给我个星标", port)
        self.assertIn("没有固定公众号尾部", port)
        self.assertIn("topic_id/title/hook/structure/body", skill)
        self.assertNotIn("THIRD_PARTY_NOTICES.md", skill)

    def test_model_instructions_do_not_create_deterministic_prose_gates(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        port = (SKILL_DIR / "references" / "khazix-writer-port.md").read_text(encoding="utf-8")
        managed = "\n".join(path.read_text(encoding="utf-8") for path in SKILL_DIR.rglob("*.md"))
        self.assertIn("不得用 Python、正则、分数、计数或 deterministic prose gate", skill)
        self.assertIn("不是 Python、正则、关键词、长度、计数、评分或 runtime 拒绝条件", port)
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
        self.assertNotIn("three_round_learning.md", managed)
        self.assertNotIn("derived style", managed.lower())
        self.assertNotIn("keyword selector", managed.lower())

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

    def test_voice_samples_are_exact_user_originals(self) -> None:
        parity = {
            SKILL_DIR / "references" / "voice-samples" / "cover-skill-original.md":
                SOURCE_DIR / "03_口播风格样稿" / "封面skill 口播稿final.md",
            SKILL_DIR / "references" / "voice-samples" / "hotspot-radar-original.md":
                SOURCE_DIR / "03_口播风格样稿" / "热点监控及脚本落地_1500字口播脚本.md",
        }
        if not all(original.is_file() for original in parity.values()):
            self.skipTest("authoritative voice source tree is outside this portable checkout")
        for managed, original in parity.items():
            self.assertTrue(original.is_file(), original)
            self.assertEqual(sha256(managed), sha256(original), managed)


if __name__ == "__main__":
    unittest.main()
