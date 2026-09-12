from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from daily_workflow import DailyWorkflow, WorkflowConflict
from spoken_script_runtime import (
    article_artifact_path,
    ensure_checkpoint,
    first_unfinished_index,
    load_writer_contract,
    new_checkpoint,
    submit_article,
    submit_spoken_adaptation,
    topic_packet,
    topic_phase,
    validate_checkpoint,
    writer_authority_manifest,
)


RUN_ID = "run_20260912_155500"
BUSINESS_DATE = "2026-09-12"


class WriterExecutionStabilityTest(unittest.TestCase):
    def topics(self):
        return [
            {"topic_id": "topic:one", "source_evidence": {"source": {"title": "one"}}},
            {"topic_id": "topic:two", "source_evidence": {"source": {"title": "two"}}},
        ]

    def workflow(self, root: Path, topics=None):
        topics = topics or self.topics()
        workflow = DailyWorkflow(root / "workflow.sqlite3")
        workflow.begin(RUN_ID, BUSINESS_DATE)
        workflow.commit_stage(RUN_ID, "collection_enrichment", {"content_items": []}, "completed")
        workflow.commit_stage(RUN_ID, "editorial", {"topics": []}, "completed")
        checkpoint = ensure_checkpoint(
            workflow, RUN_ID, BUSINESS_DATE, topics, load_writer_contract(),
        )
        return workflow, checkpoint, topics

    def article_submission(self, packet, topic_id="topic:one", body="同题完整文章"):
        return {
            "packet_id": packet["topic_input"]["packet_id"],
            "article": {"topic_id": topic_id, "title": "同题标题", "body": body},
        }

    def spoken_submission(self, packet, topic_id="topic:one", body="同题完整口播"):
        return {
            "packet_id": packet["topic_input"]["packet_id"],
            "article_sha256": packet["topic_input"]["article_artifact"]["sha256"],
            "script": {
                "topic_id": topic_id,
                "title": "同题标题",
                "hook": "同题钩子",
                "structure": "同题推进",
                "body": body,
            },
        }

    def test_positive_two_phase_identity_and_publisher_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, checkpoint, topics = self.workflow(root)
            contract = load_writer_contract()
            article_packet = topic_packet(
                RUN_ID, BUSINESS_DATE, topics[0], 0, 2, 0, contract,
                checkpoint=checkpoint, artifact_root=root,
            )
            self.assertEqual(article_packet["action"], "article_required")
            self.assertEqual(article_packet["topic_input"]["topic_id"], "topic:one")
            article_outcome = submit_article(
                workflow, RUN_ID, BUSINESS_DATE, topics, checkpoint, contract,
                self.article_submission(article_packet), artifact_root=root,
            )
            self.assertFalse(article_outcome["complete"])
            self.assertEqual(article_outcome["handoff"]["action"], "spoken_adaptation_required")
            self.assertEqual(article_outcome["handoff"]["topic_input"]["topic_id"], "topic:one")
            article_path = Path(
                article_outcome["handoff"]["topic_input"]["article_artifact"]["path"]
            )
            self.assertTrue(article_path.is_file())
            self.assertIn("同题完整文章", article_path.read_text(encoding="utf-8"))
            self.assertEqual(
                hashlib.sha256(article_path.read_bytes()).hexdigest(),
                article_outcome["handoff"]["topic_input"]["article_artifact"]["artifact_sha256"],
            )
            stored = workflow.stage(RUN_ID, "scripts")["payload"]
            spoken_outcome = submit_spoken_adaptation(
                workflow, RUN_ID, BUSINESS_DATE, topics, stored, contract,
                self.spoken_submission(article_outcome["handoff"]), artifact_root=root,
            )
            self.assertFalse(spoken_outcome["complete"])
            self.assertEqual(spoken_outcome["handoff"]["action"], "article_required")
            self.assertEqual(spoken_outcome["handoff"]["topic_input"]["topic_id"], "topic:two")
            final = workflow.stage(RUN_ID, "scripts")["payload"]
            self.assertEqual(final["completed_items"][1]["kind"], "spoken")
            # Publishing is owned by the caller only after the scripts stage is
            # terminal; the article-only/partial stages expose no scripts result.
            self.assertNotIn("publisher", article_outcome)
            self.assertNotIn("publisher", spoken_outcome)

    def test_wrong_identity_and_spoken_before_article_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, checkpoint, topics = self.workflow(root)
            contract = load_writer_contract()
            packet = topic_packet(
                RUN_ID, BUSINESS_DATE, topics[0], 0, 2, 0, contract,
                checkpoint=checkpoint,
            )
            wrong = self.article_submission(packet, topic_id="topic:two")
            with self.assertRaisesRegex(WorkflowConflict, "article_topic_not_current"):
                submit_article(
                    workflow, RUN_ID, BUSINESS_DATE, topics, checkpoint, contract,
                    wrong, artifact_root=root,
                )
            with self.assertRaisesRegex(WorkflowConflict, "spoken_before_article"):
                submit_spoken_adaptation(
                    workflow, RUN_ID, BUSINESS_DATE, topics, checkpoint, contract,
                    {"packet_id": "x", "article_sha256": "x", "script": {}}, artifact_root=root,
                )
            self.assertFalse(article_artifact_path(root, RUN_ID, "topic:one").exists())

    def test_resume_duplicate_article_does_not_regenerate_and_wrong_article_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, checkpoint, topics = self.workflow(root)
            contract = load_writer_contract()
            packet = topic_packet(
                RUN_ID, BUSINESS_DATE, topics[0], 0, 2, 0, contract,
                checkpoint=checkpoint,
            )
            first = submit_article(
                workflow, RUN_ID, BUSINESS_DATE, topics, checkpoint, contract,
                self.article_submission(packet), artifact_root=root,
            )
            article_path = Path(first["handoff"]["topic_input"]["article_artifact"]["path"])
            before = article_path.read_bytes()
            resumed_checkpoint = workflow.stage(RUN_ID, "scripts")["payload"]
            self.assertEqual(first_unfinished_index(resumed_checkpoint), 0)
            self.assertEqual(topic_phase(resumed_checkpoint, "topic:one"), "spoken_adaptation_required")
            self.assertEqual(article_path.read_bytes(), before)
            duplicate = submit_article(
                workflow, RUN_ID, BUSINESS_DATE, topics, resumed_checkpoint, contract,
                self.article_submission(packet), artifact_root=root,
            )
            self.assertEqual(duplicate["handoff"]["action"], "spoken_adaptation_required")
            self.assertEqual(article_path.read_bytes(), before)
            altered = dict(self.spoken_submission(first["handoff"]))
            altered["article_sha256"] = "0" * 64
            with self.assertRaisesRegex(WorkflowConflict, "spoken_adaptation_article_identity_conflict"):
                submit_spoken_adaptation(
                    workflow, RUN_ID, BUSINESS_DATE, topics, resumed_checkpoint, contract,
                    altered, artifact_root=root,
                )

            forged = json.loads(json.dumps(resumed_checkpoint))
            forged["completed_items"][0]["article"]["path"] = str(root / "old-body.md")
            with self.assertRaisesRegex(WorkflowConflict, "article_checkpoint_identity_conflict"):
                topic_packet(
                    RUN_ID, BUSINESS_DATE, topics[0], 0, 2, 0, contract,
                    checkpoint=forged, artifact_root=root,
                )

    def test_transaction_rollback_removes_new_article_when_checkpoint_commit_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, checkpoint, topics = self.workflow(root)
            contract = load_writer_contract()
            packet = topic_packet(
                RUN_ID, BUSINESS_DATE, topics[0], 0, 2, 0, contract,
                checkpoint=checkpoint,
            )
            with mock.patch.object(workflow, "commit_stage", side_effect=WorkflowConflict("db_conflict")):
                with self.assertRaisesRegex(WorkflowConflict, "db_conflict"):
                    submit_article(
                        workflow, RUN_ID, BUSINESS_DATE, topics, checkpoint, contract,
                        self.article_submission(packet), artifact_root=root,
                    )
            self.assertFalse(article_artifact_path(root, RUN_ID, "topic:one").exists())
            self.assertEqual(workflow.stage(RUN_ID, "scripts")["payload"]["completed_items"], [])

    def test_retired_context_is_not_restored_and_authority_manifest_is_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, checkpoint, topics = self.workflow(root)
            checkpoint["private_authority"] = "should-not-pass"
            with self.assertRaisesRegex(WorkflowConflict, "scripts_checkpoint_private_context_retired"):
                validate_checkpoint(
                    checkpoint, RUN_ID, BUSINESS_DATE, topics, load_writer_contract(),
                )
        manifest = writer_authority_manifest()
        self.assertTrue(manifest["active_skill"]["sha256"])
        self.assertEqual(len(manifest["required_managed_references"]), 7)
        self.assertTrue(all(row["sha256"] for row in manifest["required_managed_references"]))


if __name__ == "__main__":
    unittest.main()
