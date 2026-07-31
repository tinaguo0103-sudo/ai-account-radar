import json
import tempfile
import unittest
from pathlib import Path

from daily_workflow import DailyWorkflow
from publish_website_projection import (
    ProjectionError,
    build_workflow_projection,
)


class WebsiteProjectionTest(unittest.TestCase):
    def test_video_understanding_is_bound_to_exact_content_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            run_id = "run_20260727_080000"
            flow.begin(run_id, "2026-07-27")
            package = {
                "source_url": "https://www.douyin.com/video/1",
                "status": "completed",
                "caption_timeline": [{"start": 1.0, "text": "字幕"}],
            }
            collection = {"content_items": [{
                "item_id": "douyin:1", "source": "douyin",
                "title": "title", "source_url": "https://www.douyin.com/video/1",
            }], "candidates": [], "source_runs": [],
                "understanding_results": [{"package": package}]}
            flow.commit_stage(
                run_id, "collection_enrichment", collection, "completed",
            )
            flow.commit_stage(run_id, "editorial", {"run_id": run_id, "topics": []}, "completed")
            flow.commit_stage(
                run_id, "scripts", {"run_id": run_id, "scripts": [], "failures": []}, "completed",
            )
            flow.complete(run_id, "completed", f"terminal:{run_id}")
            payload = build_workflow_projection(
                path, run_id, "qa-private"
            )
            self.assertEqual(payload["collected_items"][0]["video_understanding"], package)
            self.assertNotIn("payload_sha256", payload)
            self.assertEqual(
                payload["collected_items"][0]["video_understanding"]["caption_timeline"][0]["start"],
                1.0,
            )

    def test_durable_and_canonical_video_shapes_share_projection_contract(self):
        from publish_website_projection import normalize_video_understanding

        durable = normalize_video_understanding({
            "status": "completed",
            "keyframes": [{"time_second": 61, "sha256": "k", "path": "k.jpg"}],
            "screen_text": [{"kind": "tool_name", "value": "Claude", "verified": True}],
        })
        self.assertEqual(durable["keyframes"][0]["start"], 61)
        self.assertEqual(durable["screen_text"][0]["text"], "Claude")
        self.assertIsNone(durable["screen_text"][0]["start"])

        canonical = normalize_video_understanding({
            "status": "completed",
            "keyframes": [{"start": 9, "sha256": "k2", "path": "k2.jpg"}],
            "screen_text": [{"kind": "number", "text": "42", "start": 9}],
        })
        self.assertEqual(canonical["keyframes"][0]["start"], 9)
        self.assertEqual(canonical["screen_text"][0]["text"], "42")
        self.assertEqual(canonical["screen_text"][0]["start"], 9)

    def test_nonterminal_workflow_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            run_id = "run_20260727_080000"
            flow.begin(run_id, "2026-07-27")
            with self.assertRaisesRegex(ProjectionError, "workflow_terminal_not_committed"):
                build_workflow_projection(path, run_id, "qa-private")

    def test_hotspot_topic_projects_structured_sources_and_cluster_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            run_id = "run_20260731_080000"
            flow.begin(run_id, "2026-07-31")
            event_id = "trend:event-one"
            collection = {
                "content_items": [{
                    "item_id": "douyin:1", "source": "douyin", "title": "source",
                    "source_url": "https://www.douyin.com/video/1",
                }],
                "candidates": [{
                    "candidate_id": event_id, "trend_event_id": event_id,
                    "representative_item_id": "douyin:1",
                    "sources": [
                        {
                            "source_id": "source-1",
                            "url": "https://www.douyin.com/video/1",
                            "source_role": "traffic_signal",
                            "understanding_status": "analyzed",
                        },
                        {
                            "source_id": "source-2",
                            "url": "https://example.test/official",
                            "source_role": "original_or_official",
                            "understanding_status": "metadata_only",
                        },
                    ],
                    "cluster_synthesis": {"event_name": "same event"},
                    "traffic_opportunity": {"status": "evidence_present"},
                    "persona_stability": {"status": "reviewable"},
                    "differentiation": {"primary_angle": ""},
                }, {
                    "candidate_id": "trend:observe", "trend_event_id": "trend:observe",
                    "representative_item_id": "douyin:1",
                    "event_name": "observed event",
                    "sources": [{
                        "source_id": "source-3",
                        "url": "https://www.douyin.com/video/1",
                        "source_role": "independent_view",
                        "understanding_status": "metadata_only",
                    }],
                }],
                "source_runs": [],
                "understanding_results": [],
            }
            flow.commit_stage(run_id, "collection_enrichment", collection, "completed")
            flow.commit_stage(run_id, "editorial", {
                "run_id": run_id,
                "topics": [{
                    "candidate_id": event_id, "decision": "select",
                    "title": "title", "selection_reason": "reason",
                    "hook": "hook", "structure": "structure",
                    "unique_judgment": "Austin 从交付责任切入",
                }, {
                    "candidate_id": "trend:observe", "decision": "observe",
                    "selection_reason": "evidence is still limited",
                    "unique_judgment": (
                        "Finance teams should automate reconciliation traces while "
                        "keeping judgment with accountable reviewers."
                    ),
                    "differentiation": {"primary_angle": (
                        "Finance teams should automate reconciliation traces while "
                        "keeping judgment with accountable reviewers."
                    )},
                    "cluster_synthesis": {"primary_angle": (
                        "Finance teams should automate reconciliation traces while "
                        "keeping judgment with accountable reviewers."
                    )},
                }],
            }, "completed")
            flow.commit_stage(run_id, "scripts", {
                "run_id": run_id,
                "scripts": [{
                    "topic_id": event_id, "title": "title", "hook": "hook",
                    "structure": "structure", "body": "body",
                }],
                "failures": [],
            }, "completed")
            flow.complete(run_id, "completed", f"terminal:{run_id}")
            topics = build_workflow_projection(path, run_id, "qa-private")["topics"]
            self.assertEqual(len(topics), 2)
            topic = topics[0]
            self.assertEqual(topic["trend_event_id"], event_id)
            self.assertEqual(len(topic["sources"]), 2)
            self.assertEqual(topic["cluster_synthesis"]["event_name"], "same event")
            self.assertEqual(
                topic["differentiation"]["primary_angle"],
                "Austin 从交付责任切入",
            )
            self.assertEqual(
                topic["cluster_synthesis"]["primary_angle"],
                "Austin 从交付责任切入",
            )
            self.assertEqual(topics[1]["status"], "observe")
            self.assertEqual(topics[1]["generation_status"], "not_applicable")
            self.assertEqual(topics[1]["title"], "observed event")
            self.assertEqual(
                topics[1]["differentiation"]["primary_angle"],
                topics[1]["cluster_synthesis"]["primary_angle"],
            )
            self.assertNotEqual(
                topics[1]["selection_reason"],
                topics[1]["differentiation"]["primary_angle"],
            )

    def test_source_ledger_is_preserved_when_legacy_source_runs_are_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            run_id = "run_20260729_080000"
            flow.begin(run_id, "2026-07-29")
            collection = {
                "content_items": [],
                "candidates": [],
                "source_runs": [],
                "source_ledger": [
                    {
                        "source": "configured_account", "attempted": True,
                        "status": "completed", "discovered_count": 2,
                        "reason": "", "captured_at": "2026-07-29T00:00:00Z",
                    },
                    {
                        "source": "recommendation", "attempted": True,
                        "status": "completed_empty", "discovered_count": 0,
                        "reason": "no_safe_visible_candidates",
                        "captured_at": "2026-07-29T00:00:01Z",
                    },
                    {
                        "source": "dynamic_search", "attempted": True,
                        "status": "completed", "discovered_count": 16,
                        "reason": "", "captured_at": "2026-07-29T00:00:02Z",
                    },
                ],
                "understanding_results": [],
            }
            flow.commit_stage(run_id, "collection_enrichment", collection, "completed")
            flow.commit_stage(run_id, "editorial", {"run_id": run_id, "topics": []}, "completed")
            flow.commit_stage(
                run_id, "scripts", {"run_id": run_id, "scripts": [], "failures": []},
                "completed",
            )
            flow.complete(run_id, "completed", f"terminal:{run_id}")
            payload = build_workflow_projection(path, run_id, "qa-private")
            self.assertEqual(
                [row["source"] for row in payload["source_runs"]],
                ["configured_account", "recommendation", "dynamic_search"],
            )
            self.assertEqual(
                [row["item_count"] for row in payload["source_runs"]], [2, 0, 16],
            )
            self.assertEqual(
                payload["source_runs"][1]["error_summary"],
                "no_safe_visible_candidates",
            )


if __name__ == "__main__":
    unittest.main()
