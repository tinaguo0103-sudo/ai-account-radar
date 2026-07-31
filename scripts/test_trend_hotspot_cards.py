from __future__ import annotations

import unittest

from trend_hotspot_cards import (
    attach_understanding,
    build_hotspot_cards,
    representative_candidates,
    select_representative_sources,
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
