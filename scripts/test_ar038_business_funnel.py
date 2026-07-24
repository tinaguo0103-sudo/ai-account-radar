from __future__ import annotations

import hashlib
import unittest

import content_sampler
import topic_research_contract as research


def item(source_type: str) -> content_sampler.ContentItem:
    return content_sampler.ContentItem(
        source_type=source_type,
        platform=source_type,
        account_name="account",
        title="Agent workflow checklist",
        url="",
        content_shape="artifact",
        cover_text="",
        body_snippet="Agent workflow checklist for content teams",
        published_at="",
        comment_questions="",
        ocr_text="",
        fetch_method="trusted_artifact",
        fetch_status="success",
        failure_reason="",
        fingerprint=f"fp-{source_type}",
    )


def topic(fp: str, source: str, action: str = "暂存观察") -> dict:
    return {
        "内容指纹": fp,
        "来源类型": source,
        "来源内容": f"shared theme {fp}",
        "我的选题标题": f"title {fp}",
        "可发布标题": f"visible {fp}",
        "对应栏目": "真实工作流改造",
        "热点切入方式": "对标内容拆解",
        "业务场景": "same scene",
        "可沉淀资产": "same asset",
        "标题生成规则": "specific",
        "是否建议进入制作": "暂存观察" if action == "暂存观察" else "是",
        "推荐动作": action,
        "编辑判断分": 75,
        "标题质量分": 75,
        "人设匹配分": 75,
        "推荐分": 75,
        "AI味风险": "低",
        "内容可信度": "摘要可用",
        "是否只是资讯搬运": "否",
        "是否有足够内容支撑": "充足",
        "候选来源方式": "trusted artifact",
    }


class AR038BusinessFunnelTests(unittest.TestCase):
    def test_equal_content_has_equal_base_score_across_sources(self) -> None:
        scores = [content_sampler.score_item(item(source), "真实工作流改造") for source in ("AIHOT热点", "对标视频", "公众号文章")]
        self.assertEqual(len(set(scores)), 1)

    def test_observe_same_theme_and_seven_rows_all_reach_skill(self) -> None:
        rows = [topic(f"fp-{index}", ("AIHOT热点", "对标视频", "公众号文章")[index % 3]) for index in range(7)]
        clustered = content_sampler.merge_same_theme(rows)
        self.assertEqual(len(clustered), 7)
        selected = content_sampler.select_skill_review_candidates(clustered)
        self.assertEqual(len(selected), 7)
        self.assertEqual([row["来源类型"] for row in selected[:3]], ["AIHOT热点", "对标视频", "公众号文章"])
        self.assertEqual(content_sampler.assign_action_quotas(selected), selected)
        self.assertTrue(all(row["推荐动作"] == "暂存观察" for row in selected))

    def test_link_failure_keeps_sufficient_artifact(self) -> None:
        candidate = {
            "content_fingerprint": "fp-1",
            "source_type": "对标视频",
            "source_account": "account",
            "artifact_text": "A concrete workflow viewpoint",
            "csv_title": "A concrete workflow viewpoint",
            "exact_url": "",
            "local_trace_hash": hashlib.sha256(b"row").hexdigest(),
        }
        result = research.validate_source_open(candidate, {"open_status": "failed", "failure_reason": "link unavailable"})
        self.assertTrue(result["eligible"])
        self.assertTrue(result["link_unavailable"])
        self.assertEqual(result["evidence_level"], "trusted_collection_artifact")

    def test_raw_high_risk_words_do_not_prequalify_candidate_for_research(self) -> None:
        candidate = {
            "content_fingerprint": "fp-2",
            "source_type": "AIHOT",
            "source_account": "AIHOT",
            "artifact_text": "Official financing reached 10亿元 on a stated release date",
        }
        result = research.validate_source_open(candidate, {"open_status": "failed"})
        self.assertTrue(result["eligible"])
        self.assertNotIn("needs_verification", result)


if __name__ == "__main__":
    unittest.main()
