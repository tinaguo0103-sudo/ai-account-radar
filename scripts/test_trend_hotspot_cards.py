from __future__ import annotations

import unittest

from trend_hotspot_cards import (
    attach_understanding,
    build_hotspot_cards,
    complete_editorial_ledger,
    deep_read_counts,
    editorial_candidates,
    representative_candidates,
    select_representative_sources,
    validate_candidate_specific_decisions,
)


RUN_ID = "run_20260731_080142"


def candidate(
    aweme: str,
    event: str,
    title: str,
    *,
    platform: str = "抖音",
    likes: int | None = None,
    angle: str = "",
    viewpoint: str = "",
) -> dict:
    return {
        "candidate_id": f"douyin:{aweme}::angle:{aweme}",
        "item_id": f"douyin:{aweme}",
        "aweme_id": aweme,
        "source_url": f"https://www.douyin.com/video/{aweme}",
        "事件锚点": event,
        "原始来源标题": title,
        "title": title,
        "平台": platform,
        "likes": likes,
        "published_at_display": "今天",
        "我的账号为什么能讲": "这个热点能连接真实AI工作现场",
        "我的蹭热点角度": angle or f"{event}对真实任务的影响",
        "普通AI资讯号会怎么讲": f"复述{event}",
        "viewpoint_role": viewpoint,
    }


def items(rows: list[dict]) -> list[dict]:
    return [
        {
            "item_id": row["item_id"],
            "source_url": row["source_url"],
            "source": "douyin",
            "title": row["title"],
        }
        for row in rows
    ]


class TrendHotspotCardsTest(unittest.TestCase):
    def test_same_event_merges_sources_and_same_entity_different_event_splits(self):
        rows = [
            candidate("1", "Claude Code发布新版本", "官方发布"),
            candidate("2", "Claude Code发布新版本", "开发者解读"),
            candidate("3", "Claude账号封禁争议", "账号封禁"),
        ]
        cards = build_hotspot_cards(rows, items=items(rows), run_id=RUN_ID)
        self.assertEqual(len(cards), 2)
        release = next(row for row in cards if row["event_name"] == "Claude Code发布新版本")
        self.assertEqual(release["source_count"], 2)
        self.assertEqual(len(release["sources"]), 2)
        self.assertTrue(all(row["account_role"] == "auxiliary_signal" for row in release["sources"]))
        self.assertNotEqual(cards[0]["trend_event_id"], cards[1]["trend_event_id"])

    def test_same_url_and_repost_are_deduped_without_losing_canonical_link(self):
        first = candidate("1", "Agent发布", "同一条内容")
        second = {**first, "candidate_id": "second-angle", "我的蹭热点角度": "另一角度"}
        cards = build_hotspot_cards([first, second], items=items([first]), run_id=RUN_ID)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["source_count"], 1)
        self.assertEqual(cards[0]["sources"][0]["url"], first["source_url"])

    def test_conflicting_view_is_retained(self):
        rows = [
            candidate("1", "Agent定价调整", "支持降价"),
            candidate("2", "Agent定价调整", "质疑降价", viewpoint="conflicting_view"),
        ]
        cards = build_hotspot_cards(rows, items=items(rows), run_id=RUN_ID)
        cards, _ = attach_understanding(cards, [], [])
        self.assertEqual(
            cards[0]["cluster_synthesis"]["conflicting_views"],
            ["质疑降价"],
        )

    def test_traffic_persona_and_differentiation_are_separate(self):
        rows = [candidate("1", "新场景事件", "不属于旧四场景", likes=0)]
        card = build_hotspot_cards(rows, items=items(rows), run_id=RUN_ID)[0]
        self.assertEqual(card["traffic_opportunity"]["status"], "evidence_present")
        self.assertEqual(card["persona_stability"]["status"], "reviewable")
        self.assertFalse(card["persona_stability"]["legacy_four_scene_gate"])
        self.assertNotIn("score", card)
        self.assertNotIn("total", card["traffic_opportunity"])

    def test_representatives_default_to_three_and_stop_on_no_information_gain(self):
        sources = [
            {
                "source_id": str(index), "source_role": "independent_view",
                "title": "同义复述", "summary": "同一事实",
                "engagement": {"likes": index},
            }
            for index in range(8)
        ]
        self.assertEqual(len(select_representative_sources(sources)), 1)
        unique = [
            {**row, "title": f"独特信息{index}"}
            for index, row in enumerate(sources)
        ]
        self.assertEqual(len(select_representative_sources(unique)), 3)
        self.assertLessEqual(
            len(select_representative_sources(unique, default_count=9)),
            5,
        )

    def test_only_representative_video_rows_reach_producer(self):
        rows = [
            candidate(str(index), "同一热点", f"来源{index}", likes=index)
            for index in range(8)
        ]
        cards = build_hotspot_cards(rows, items=items(rows), run_id=RUN_ID)
        selected = representative_candidates(cards, rows)
        self.assertEqual(len(selected), 3)
        self.assertLess(len(selected), len(rows))

    def test_only_relative_traffic_and_broad_persona_fit_reach_deep_read(self):
        high = candidate("1", "AI工作台", "AI工作台实战", likes=900)
        low = candidate("2", "AI工作台小技巧", "AI工作台小技巧", likes=1)
        metadata = candidate("3", "Agent工作流", "Agent工作流", likes=None)
        cards = build_hotspot_cards([high, low, metadata], items=items([high, low, metadata]), run_id=RUN_ID)
        by_name = {row["event_name"]: row for row in cards}
        self.assertEqual(by_name["AI工作台"]["qualification"]["status"], "qualified")
        self.assertEqual(by_name["AI工作台小技巧"]["qualification"]["status"], "signal_only")
        self.assertIn("互动事实缺失", by_name["Agent工作流"]["qualification"]["traffic_reason"])
        selected = representative_candidates(cards, [high, low, metadata])
        self.assertEqual([row["source_url"] for row in selected], [high["source_url"]])

    def test_douyin_platform_aliases_share_one_relative_traffic_pool(self):
        low = candidate("1", "低互动工作流", "低互动工作流", platform="抖音", likes=10)
        middle = candidate("2", "中互动工作流", "中互动工作流", platform="douyin", likes=500)
        high = candidate("3", "高互动工作流", "高互动工作流", platform="抖音", likes=900)
        cards = build_hotspot_cards(
            [low, middle, high],
            items=items([low, middle, high]),
            run_id=RUN_ID,
        )
        by_name = {row["event_name"]: row for row in cards}
        self.assertEqual(
            by_name["低互动工作流"]["qualification"]["relative_basis"]["platform_observation_counts"],
            {"douyin": 3},
        )
        self.assertEqual(by_name["低互动工作流"]["qualification"]["status"], "signal_only")
        self.assertEqual(by_name["高互动工作流"]["qualification"]["status"], "qualified")

    def test_old_viral_item_does_not_raise_recent_cohort_bar(self):
        recent_low = candidate("1", "两天新工具", "两天新工具", likes=20)
        recent_high = candidate("2", "两天新模型", "两天新模型", likes=80)
        old_viral = candidate("3", "月级老爆款", "月级老爆款", likes=100000)
        recent_low["published_at_display"] = "2天前"
        recent_high["published_at_display"] = "1天前"
        old_viral["published_at_display"] = "2月前"
        cards = build_hotspot_cards(
            [recent_low, recent_high, old_viral],
            items=items([recent_low, recent_high, old_viral]),
            run_id=RUN_ID,
        )
        by_name = {row["event_name"]: row for row in cards}
        self.assertEqual(by_name["两天新模型"]["qualification"]["status"], "qualified")
        self.assertEqual(
            by_name["两天新模型"]["qualification"]["recency_cohorts"], ["0_2d"],
        )
        self.assertEqual(
            by_name["月级老爆款"]["qualification"]["recency_cohorts"], ["31d_plus"],
        )

    def test_event_specific_anchor_splits_animated_voiceover_and_obsidian_skills(self):
        voice = candidate("1", "Claude Code", "用 Claude Code 做动画配音")
        obsidian = candidate("2", "Claude Code", "Obsidian 5 Skills 工作流")
        cards = build_hotspot_cards(
            [voice, obsidian], items=items([voice, obsidian]), run_id=RUN_ID,
        )
        self.assertEqual(len(cards), 2)
        self.assertEqual({card["event_name"] for card in cards}, {"Claude Code"})
        self.assertNotEqual(cards[0]["trend_event_id"], cards[1]["trend_event_id"])

    def test_official_with_time_only_remains_signal_not_failure(self):
        official = candidate("1", "Gemini Robotics ER 2", "Gemini Robotics ER 2", platform="AIHOT")
        official.update({
            "source_url": "https://deepmind.google/discover/blog/gemini-robotics-er-2/",
            "published_at": "2026-07-31T02:00:00Z",
            "published_at_display": "今天",
            "likes": None,
        })
        cards = build_hotspot_cards([official], items=items([official]), run_id=RUN_ID)
        cards, _ = attach_understanding(cards, [], [])
        card = cards[0]
        self.assertEqual(card["qualification"]["status"], "signal_only")
        self.assertEqual(card["qualification"]["authenticity_state"], "official_with_time")
        self.assertEqual(card["deep_read"]["status"], "not_qualified")
        self.assertEqual(card["review_stage"], "signal_only")

    def test_eligible_without_attempt_cannot_be_understanding_failure(self):
        low = candidate("1", "低互动工作流", "低互动工作流", likes=1)
        high = candidate("2", "高互动工作流", "高互动工作流", likes=900)
        cards = build_hotspot_cards([low, high], items=items([low, high]), run_id=RUN_ID)
        cards, _ = attach_understanding(cards, [], [])
        card = next(row for row in cards if row["event_name"] == "高互动工作流")
        self.assertEqual(card["qualification"]["status"], "qualified")
        self.assertEqual(card["deep_read"]["attempted_count"], 0)
        self.assertEqual(card["deep_read"]["status"], "not_attempted")
        self.assertEqual(card["review_stage"], "signal_only")

    def test_real_typed_deep_read_failure_is_understanding_failed(self):
        low = candidate("1", "低互动工作流", "低互动工作流", likes=1)
        high = candidate("2", "高互动工作流", "高互动工作流", likes=900)
        cards = build_hotspot_cards([low, high], items=items([low, high]), run_id=RUN_ID)
        cards, _ = attach_understanding(cards, [], [{
            "item_id": high["item_id"], "reason": "media_unavailable",
        }])
        card = next(row for row in cards if row["event_name"] == "高互动工作流")
        self.assertEqual(card["deep_read"]["attempted_count"], 1)
        self.assertEqual(card["deep_read"]["failed_count"], 1)
        self.assertEqual(card["deep_read"]["status"], "understanding_failed")
        self.assertEqual(card["review_stage"], "understanding_failed")

    def test_deep_read_counts_reconcile_attempt_completion_failure_and_editorial(self):
        low = candidate("1", "低互动工作流", "低互动工作流", likes=1)
        completed = candidate("2", "高互动工作流", "高互动工作流", likes=900)
        failed = candidate("3", "多源工作流", "多源工作流", likes=800)
        cards = build_hotspot_cards(
            [low, completed, failed],
            items=items([low, completed, failed]),
            run_id=RUN_ID,
        )
        cards, _ = attach_understanding(cards, [{
            "status": "completed", "source_url": completed["source_url"],
            "title": completed["title"], "asr": {"text": "展示完整工作流动作。"},
        }], [{"item_id": failed["item_id"], "reason": "media_unavailable"}])
        self.assertEqual(deep_read_counts(cards), {
            "high_potential_total": 2,
            "deep_read_attempted_total": 2,
            "deep_read_completed_total": 1,
            "deep_read_failed_total": 1,
            "editorial_candidate_total": 1,
        })

    def test_missing_facts_are_non_punitive_signal(self):
        metadata = candidate("1", "Agent工作流", "Agent工作流", likes=None)
        cards = build_hotspot_cards([metadata], items=items([metadata]), run_id=RUN_ID)
        card = cards[0]
        self.assertEqual(card["qualification"]["status"], "signal_only")
        self.assertEqual(card["sources"][0]["account_role"], "auxiliary_signal")
        self.assertNotEqual(card["review_stage"], "unsuitable")

    def test_single_complete_source_synthesis_uses_understanding_not_only_title(self):
        row = candidate("1", "AI材料工作流", "AI材料工作流", likes=900)
        cards = build_hotspot_cards([row], items=items([row]), run_id=RUN_ID)
        packages = [{
            "status": "completed",
            "source_url": row["source_url"],
            "title": row["title"],
            "caption_timeline": [{"start": 0, "text": "先识别写作意图，再搭建逻辑"}],
            "asr": {"text": "<|zh|>素材数据和政策文件要分开校验。"},
            "screen_text": [{"kind": "tool_name", "value": "豆包"}],
            "keyframes": [{"time_second": 0, "sha256": "abc"}],
            "unresolved_terms": [],
        }]
        cards, results = attach_understanding(cards, packages, [])
        synthesis = cards[0]["cluster_synthesis"]
        self.assertEqual(cards[0]["review_stage"], "ready_for_editorial")
        self.assertEqual(synthesis["actual_understanding_source_count"], 1)
        self.assertIn("先识别写作意图", " ".join(synthesis["scenes_actions_consequences"]))
        self.assertEqual(synthesis["primary_angle"], "")
        self.assertEqual(len(editorial_candidates(cards)), 1)
        self.assertEqual(len(results), 1)

    def test_candidate_specific_editorial_contract_and_ledger_stages(self):
        row = candidate("1", "AI验收工作流", "AI验收工作流", likes=900)
        observed_row = candidate("2", "财务Agent对账", "财务Agent开始自动对账", likes=700)
        cards = build_hotspot_cards(
            [row, observed_row], items=items([row, observed_row]), run_id=RUN_ID,
        )
        cards, _ = attach_understanding(cards, [{
            "status": "completed", "source_url": row["source_url"],
            "title": row["title"], "asr": {"text": "先生成，再逐项验收交付结果。"},
        }, {
            "status": "completed", "source_url": observed_row["source_url"],
            "title": observed_row["title"],
            "asr": {"text": "自动下载回单并标记异常，财务人员复核后再入账。"},
        }], [])
        topic = {
            "candidate_id": cards[0]["candidate_id"], "decision": "select",
            "title": "真正卡住AI工作流的是验收", "hook": "生成很快，交付为什么还是卡住？",
            "structure": "生成冲突 -> 验收动作 -> 交付后果",
            "selection_reason": "900次可见点赞且输入给出了逐项验收动作，适合讲交付责任。",
            "unique_judgment": "AI把生成时间压缩了，却把交付验收的责任集中到了使用者身上。",
            "evidence_source_ids": [row["source_url"]],
            "decision_basis": {
                "traffic": "900次可见点赞", "content": "逐项验收动作",
                "persona": "Austin可讲交付责任", "differentiation": "不讲工具清单，讲责任转移",
            },
        }
        observed_topic = {
            "candidate_id": cards[1]["candidate_id"], "decision": "observe",
            "selection_reason": "对账动作可信，但客户规模与替代财务判断的说法尚无支持。",
            "unique_judgment": "财务Agent应先把回单下载、异常标记和复核留痕串成动作链，而不是替代财务人员作最终判断。",
            "evidence_source_ids": [observed_row["source_url"]],
            "decision_basis": {
                "traffic": "700次可见点赞", "content": "包含对账与复核动作",
                "persona": "Austin可讲AI如何进入财务工作流",
                "differentiation": "区分动作自动化与专业判断责任",
            },
        }
        validate_candidate_specific_decisions([topic, observed_topic], cards)
        ledger = complete_editorial_ledger(cards, [topic, observed_topic])
        self.assertEqual(ledger[0]["review_stage"], "recommended")
        self.assertEqual(ledger[1]["review_stage"], "observed_after_deep_read")
        self.assertEqual(
            ledger[1]["differentiation"]["primary_angle"],
            observed_topic["unique_judgment"],
        )
        self.assertEqual(
            ledger[1]["cluster_synthesis"]["primary_angle"],
            observed_topic["unique_judgment"],
        )
        self.assertNotEqual(
            ledger[1]["selection_reason"],
            ledger[1]["differentiation"]["primary_angle"],
        )
        invalid = {**topic, "unique_judgment": "换成自己的语言"}
        with self.assertRaisesRegex(ValueError, "editorial_primary_angle_not_concrete"):
            validate_candidate_specific_decisions([invalid], cards)
        missing_observe_angle = {**observed_topic, "unique_judgment": ""}
        with self.assertRaisesRegex(ValueError, "editorial_primary_angle_not_concrete"):
            validate_candidate_specific_decisions([topic, missing_observe_angle], cards)
        reused_reason = {
            **observed_topic,
            "unique_judgment": observed_topic["selection_reason"],
        }
        with self.assertRaisesRegex(ValueError, "editorial_primary_angle_reason_not_distinct"):
            validate_candidate_specific_decisions([topic, reused_reason], cards)

    def test_legacy_repeated_angle_becomes_event_cards_without_cross_event_merge(self):
        rows = [
            candidate(str(index), event, "Claude Code教程真正能借鉴的，是任务流程")
            for index, event in enumerate(
                ["Claude版本发布"] * 4
                + ["Claude账号封禁"] * 3
                + ["Agent学习路线"] * 5
                + ["WorkBuddy功能发布"] * 3
                + ["Codex浏览器验收"] * 3
            )
        ]
        cards = build_hotspot_cards(rows, items=items(rows), run_id=RUN_ID)
        self.assertEqual(len(rows), 18)
        self.assertEqual(len(cards), 5)
        self.assertEqual(sum(card["source_count"] for card in cards), 18)
        self.assertEqual(len({card["trend_event_id"] for card in cards}), 5)


if __name__ == "__main__":
    unittest.main()
