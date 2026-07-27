from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import douyin_video_understanding as vu

RUN_ID = "run_20260727_080000"
DATE = "2026-07-27"


def candidate(number: int, source: str, title: str, duration: int = 60, likes: int = 0):
    aweme = str(7000 + number)
    return {
        "run_id": RUN_ID, "aweme_id": aweme,
        "source_url": f"https://www.douyin.com/video/{aweme}",
        "author": f"account-{number}", "title": title, "published_at": DATE,
        "duration_seconds": duration, "discovery_source": source,
        "likes": likes, "comments": 1, "favorites": 1, "shares": 1,
        "raw_identity": f"raw-{number}",
    }


def package(row, status="completed"):
    return {
        "run_id": RUN_ID, "aweme_id": row["aweme_id"], "source_url": row["source_url"],
        "status": status,
        "caption_timeline": [{"start": 0, "end": 1, "text": "字幕", "frame_sha256": "f"}],
        "asr": {"primary_model": "SenseVoiceSmall+FSMN-VAD", "fills": []},
        "screen_text": [{"kind": "prompt", "text": "写一个方案", "start": 1, "verified": True}],
        "keyframes": [{"start": 1, "sha256": "k", "path": "keyframes/k.jpg"}],
        "unresolved_terms": [], "failures": [], "temporary_media_remaining": 0,
    }


class VideoUnderstandingTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((Path(__file__).parents[1] / "config/douyin_video_understanding_policy.json").read_text())

    def test_or_semantics_exploration_and_near_duplicate(self):
        rows = vu.merge_candidates([[
            candidate(1, "configured_account", "标题价值"),
            candidate(2, "recommendation", "互动价值", likes=100),
            candidate(3, "dynamic_search", "探索角度"),
            candidate(4, "dynamic_search", "标题价值", likes=200),
        ]], RUN_ID)
        decisions = [
            {"candidate_id": rows[0]["id"], "selected": True, "reasons": ["title_value"]},
            {"candidate_id": rows[1]["id"], "selected": True, "reasons": ["engagement_relative"]},
            {"candidate_id": rows[2]["id"], "selected": True, "reasons": ["exploration"]},
            {"candidate_id": rows[3]["id"], "selected": True, "reasons": ["title_value"]},
        ]
        planned = vu.budget_selection(vu.fold_near_duplicates(
            vu.apply_policy_decisions(rows, decisions, self.policy)), self.policy)
        reasons = {reason for row in planned["selected"] for reason in row["selection_reasons"]}
        self.assertTrue({"title_value", "engagement_relative", "exploration"} <= reasons)
        self.assertEqual(len(planned["selected"]), 3)

    def test_double_gate_and_turbo_are_rejected(self):
        row = vu.merge_candidates([[candidate(1, "configured_account", "A")]], RUN_ID)[0]
        with self.assertRaisesRegex(vu.VideoUnderstandingError, "selection_double_gate_forbidden"):
            vu.apply_policy_decisions([row], [{
                "candidate_id": row["id"], "selected": True, "reasons": ["title_value"],
                "requires_title_and_engagement": True,
            }], self.policy)
        value = package(row)
        value["asr"]["primary_model"] = "whisper-large-v3-turbo"
        with self.assertRaisesRegex(vu.VideoUnderstandingError, "turbo_initial_runtime_forbidden"):
            vu.validate_package(value, row)

    def test_budget_allows_less_and_caps_duration(self):
        rows = vu.merge_candidates([[
            candidate(number, "dynamic_search", f"独立标题{number}", duration=900)
            for number in range(1, 6)
        ]], RUN_ID)
        decisions = [{"candidate_id": row["id"], "selected": True, "reasons": ["title_value"]} for row in rows]
        plan = vu.budget_selection(vu.apply_policy_decisions(rows, decisions, self.policy), self.policy)
        self.assertEqual(plan["selected_count"], 3)
        self.assertEqual(plan["total_duration_seconds"], 2700)
        self.assertTrue(plan["under_target_allowed"])

    def test_materialize_is_no_churn_and_failure_is_local(self):
        raw = [candidate(1, "configured_account", "A"), candidate(2, "recommendation", "B")]
        rows = vu.merge_candidates([raw], RUN_ID)
        decisions = [{"candidate_id": row["id"], "selected": True, "reasons": ["title_value"]} for row in rows]
        with tempfile.TemporaryDirectory() as tmp:
            first = vu.materialize(
                run_id=RUN_ID, business_date=DATE, candidates=rows, decisions=decisions,
                packages=[package(rows[0])], policy=self.policy, output_root=Path(tmp),
            )
            second = vu.materialize(
                run_id=RUN_ID, business_date=DATE, candidates=rows, decisions=decisions,
                packages=[package(rows[0])], policy=self.policy, output_root=Path(tmp),
            )
            self.assertEqual(first["completed_count"], 1)
            self.assertEqual(first["failed_count"], 1)
            self.assertEqual(second["understanding_results"][0]["action"], "noop")
            self.assertEqual(first["substitute_count"], 0)

    def test_on_demand_adds_unplanned_candidate_without_fallback(self):
        rows = vu.merge_candidates([[
            candidate(1, "configured_account", "A"), candidate(2, "dynamic_search", "B"),
        ]], RUN_ID)
        decisions = [
            {"candidate_id": rows[0]["id"], "selected": True, "reasons": ["title_value"]},
            {"candidate_id": rows[1]["id"], "selected": False, "reasons": []},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = vu.materialize(
                run_id=RUN_ID, business_date=DATE, candidates=rows, decisions=decisions,
                packages=[package(rows[0]), package(rows[1])], policy=self.policy,
                output_root=Path(tmp), on_demand_ids={rows[1]["id"]},
            )
            self.assertEqual(result["completed_count"], 2)
            self.assertEqual({row["trigger"] for row in result["understanding_results"]},
                             {"automatic", "on_demand"})

    def test_failed_package_is_visible_but_not_completed(self):
        rows = vu.merge_candidates([[candidate(1, "dynamic_search", "A")]], RUN_ID)
        decisions = [{"candidate_id": rows[0]["id"], "selected": True, "reasons": ["title_value"]}]
        failed = {
            "run_id": RUN_ID,
            "aweme_id": rows[0]["aweme_id"],
            "source_url": rows[0]["source_url"],
            "status": "failed",
            "failure": "video_media_fetch_failed",
            "substitute_count": 0,
            "temporary_media_remaining": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = vu.materialize(
                run_id=RUN_ID, business_date=DATE, candidates=rows,
                decisions=decisions, packages=[failed], policy=self.policy,
                output_root=Path(tmp),
            )
            self.assertEqual(result["completed_count"], 0)
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual(result["understanding_results"][0]["package"]["status"], "failed")
            self.assertEqual(result["substitute_count"], 0)

    def test_public_cli_replay_is_no_churn(self):
        raw = [[candidate(1, "configured_account", "A")]]
        row = vu.merge_candidates(raw, RUN_ID)[0]
        decisions = [{"candidate_id": row["id"], "selected": True, "reasons": ["title_value"]}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = {
                "candidates.json": raw,
                "decisions.json": decisions,
                "packages.json": [package(row)],
                "policy.json": self.policy,
            }
            for name, payload in inputs.items():
                (root / name).write_text(json.dumps(payload))
            command = [
                sys.executable, str(Path(vu.__file__)),
                "--run-id", RUN_ID,
                "--business-date", DATE,
                "--candidates", str(root / "candidates.json"),
                "--decisions", str(root / "decisions.json"),
                "--packages", str(root / "packages.json"),
                "--policy", str(root / "policy.json"),
                "--output-root", str(root / "output"),
            ]
            first = subprocess.run(command, check=True, capture_output=True, text=True)
            second = subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(first.stdout)["understanding_results"][0]["action"], "created")
            self.assertEqual(json.loads(second.stdout)["understanding_results"][0]["action"], "noop")


if __name__ == "__main__":
    unittest.main()
