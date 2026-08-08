from __future__ import annotations

import json
import unittest
from pathlib import Path

import run_daily_workflow as workflow


class EditorialContinuationTest(unittest.TestCase):
    def candidate(self, identity: str = "trend:one") -> dict:
        return {
            "candidate_id": identity,
            "sources": [{"source_id": "https://example.com/source"}],
        }

    def decision(self, decision: str, reason: str) -> dict:
        return {
            "candidate_id": "trend:one",
            "decision": decision,
            "selection_reason": reason,
            "evidence_source_ids": ["https://example.com/source"],
            "decision_basis": {
                "traffic": "same-cohort signal",
                "content": "topic-specific content judgment",
                "persona": "Austin workflow fit judgment",
                "differentiation": "distinct experiment angle",
            },
            "unique_judgment": "A creator changes one workflow action and sees a concrete delivery consequence.",
            "title": "Title" if decision == "select" else "",
            "hook": "Hook" if decision == "select" else "",
            "structure": ["Scene", "Action", "Consequence"] if decision == "select" else [],
        }

    def test_research_failure_does_not_make_select_invalid(self) -> None:
        row = self.decision("select", "The topic is timely, useful and fits Austin's workflow judgment.")
        row["research"] = {"status": "failed", "reason": "public_page_unavailable"}
        workflow.validate_editorial("run_20260803_110453", {
            "run_id": "run_20260803_110453", "topics": [row],
        }, [self.candidate()])

    def test_nonselect_requires_topic_basis_not_evidence_shortage_only(self) -> None:
        row = self.decision("observe", "Independent corroboration is missing.")
        row["decision_basis"]["content"] = ""
        with self.assertRaisesRegex(workflow.WorkflowConflict, "editorial_nonselect_topic_basis_missing"):
            workflow.validate_editorial("run_20260803_110453", {
                "run_id": "run_20260803_110453", "topics": [row],
            }, [self.candidate()])

        legacy_shape = self.decision("observe", "Evidence insufficient")
        legacy_shape.pop("decision_basis")
        with self.assertRaisesRegex(workflow.WorkflowConflict, "editorial_nonselect_evidence_gate_forbidden"):
            workflow.validate_editorial("run_20260803_110453", {
                "run_id": "run_20260803_110453", "topics": [legacy_shape],
            }, [self.candidate()])

    def test_every_selected_topic_requires_complete_script(self) -> None:
        with self.assertRaisesRegex(workflow.WorkflowConflict, "script_result_incomplete"):
            workflow.validate_scripts("run_20260803_110453", {
                "run_id": "run_20260803_110453", "scripts": [],
                "failures": [{"topic_id": "trend:one", "reason": "generation_failed"}],
            }, {"trend:one"})
        workflow.validate_scripts("run_20260803_110453", {
            "run_id": "run_20260803_110453",
            "scripts": [{
                "topic_id": "trend:one", "title": "Title", "hook": "Hook",
                "structure": ["Scene", "Action"], "body": "Complete spoken body.",
            }],
            "failures": [],
        }, {"trend:one"})

    def test_release_protocol_supersedes_evidence_gate(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / "config/web010_single_daily_workflow_release.json").read_text())
        prompt = "\n".join(contract["externalSchedule"]["outerAgentProtocol"]).lower()
        self.assertIn("evidence quantity is not recommendation eligibility", prompt)
        self.assertIn("research failure never automatically changes select to observe", prompt)
        self.assertIn("exposes exactly one topic packet containing exact identity", prompt)
        self.assertIn("submit before the next topic is exposed", prompt)
        self.assertNotIn("evidence shortage forces observe", prompt)

    def test_public_editorial_handoff_uses_completed_deep_read_candidates(self) -> None:
        complete = [{"candidate_id": "trend:complete"}]
        all_candidates = complete + [{"candidate_id": "trend:metadata-only"}]
        self.assertEqual(
            workflow.editorial_handoff_candidates({
                "editorial_candidates": complete,
                "candidates": all_candidates,
            }),
            complete,
        )
        self.assertEqual(
            workflow.editorial_handoff_candidates({"candidates": all_candidates}),
            all_candidates,
        )


if __name__ == "__main__":
    unittest.main()
