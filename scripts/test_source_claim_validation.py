from __future__ import annotations

import unittest

from daily_workflow import WorkflowConflict
from source_claim_validation import (
    claim_contract,
    digest,
    research_evidence_id,
    validate_claim_dependencies,
)


RUN_ID = "run_20261006_160002_web010"
BUSINESS_DATE = "2026-10-06"
TOPIC_ID = "topic:claim-validation"


def evidence_fixture() -> dict:
    evidence = {
        "source": {
            "url": "https://origin.example/article",
            "summary": "同 run 来源提供主题背景。",
        },
        "source_material": {
            "same_run": {"run_id": RUN_ID, "business_date": BUSINESS_DATE},
            "source_record": {"candidate_id": TOPIC_ID},
        },
    }
    evidence["claim_contract"] = claim_contract(evidence)
    return evidence


def material_fixture() -> dict[str, str]:
    return {
        "run_id": RUN_ID,
        "business_date": BUSINESS_DATE,
        "topic_id": TOPIC_ID,
        "source_url": "https://research.example/qa/synthetic",
        "title": "QA synthetic public research fixture",
        "excerpt": "Synthetic material for an isolated test; not a factual claim.",
    }


def review_for(content: dict, evidence: dict, claims: list[dict], materials: list[dict] | None = None) -> dict:
    bound = {key: str(content[key]) for key in ("title", "hook", "structure", "body") if key in content}
    contract = claim_contract(
        evidence,
        research_materials=materials or [],
        topic_id=content["topic_id"],
    )
    return {
        "evidence_sha256": contract["evidence_sha256"],
        "content_sha256": digest(bound),
        "excluded_warning_ids": [row["warning_id"] for row in contract["warnings"]],
        "research_materials": materials or [],
        "claims": claims,
    }


class SourceClaimValidationTest(unittest.TestCase):
    def setUp(self):
        self.evidence = evidence_fixture()
        self.content = {"topic_id": TOPIC_ID, "title": "独立判断", "body": "这是一段独立解读。"}

    def test_unanchored_interpretation_is_allowed(self):
        claim = {"field": "title", "text": "独立判断", "scope": "interpretation", "evidence_ids": []}
        claim_body = {"field": "body", "text": "这是一段独立解读。", "scope": "interpretation", "evidence_ids": []}
        review = review_for(self.content, self.evidence, [claim, claim_body])
        validate_claim_dependencies(self.content, self.evidence, review)

    def test_source_scoped_claim_requires_a_valid_anchor(self):
        claim = {"field": "body", "text": "这是一段独立解读。", "scope": "source_context", "evidence_ids": []}
        review = review_for(self.content, self.evidence, [
            {"field": "title", "text": "独立判断", "scope": "interpretation", "evidence_ids": []},
            claim,
        ])
        with self.assertRaisesRegex(WorkflowConflict, "script_claim_evidence_missing"):
            validate_claim_dependencies(self.content, self.evidence, review)

    def test_public_research_material_is_bound_and_traceable(self):
        material = material_fixture()
        reference = research_evidence_id(material)
        claims = [
            {"field": "title", "text": "独立判断", "scope": "interpretation", "evidence_ids": []},
            {"field": "body", "text": "这是一段独立解读。", "scope": "source_context", "evidence_ids": [reference]},
        ]
        review = review_for(self.content, self.evidence, claims, [material])
        validate_claim_dependencies(self.content, self.evidence, review, frozen_research_materials=[material])
        contract = claim_contract(
            self.evidence, research_materials=[material], topic_id=TOPIC_ID,
        )
        research_anchor = next(row for row in contract["anchors"] if row["kind"] == "public_research_material")
        self.assertEqual(research_anchor["evidence_id"], reference)
        self.assertFalse(contract["research_truth_verified"])

    def test_caller_supplied_ids_and_wrong_identity_are_rejected(self):
        invalid = material_fixture() | {"evidence_id": "caller-chosen"}
        with self.assertRaisesRegex(WorkflowConflict, "script_claim_research_material_schema_invalid"):
            claim_contract(self.evidence, research_materials=[invalid], topic_id=TOPIC_ID)

        for key, value in (("run_id", "other-run"), ("business_date", "2026-10-05"), ("topic_id", "other-topic")):
            invalid = material_fixture()
            invalid[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(
                WorkflowConflict, "script_claim_research_material_identity_conflict",
            ):
                claim_contract(self.evidence, research_materials=[invalid], topic_id=TOPIC_ID)

    def test_unknown_research_reference_and_disallowed_scope_are_rejected(self):
        material = material_fixture()
        claims = [
            {"field": "title", "text": "独立判断", "scope": "interpretation", "evidence_ids": []},
            {"field": "body", "text": "这是一段独立解读。", "scope": "source_context", "evidence_ids": ["public-research:arbitrary"]},
        ]
        review = review_for(self.content, self.evidence, claims, [material])
        with self.assertRaisesRegex(WorkflowConflict, "script_claim_evidence_identity_conflict"):
            validate_claim_dependencies(self.content, self.evidence, review)

        claims[1]["scope"] = "external_fact"
        claims[1]["evidence_ids"] = [research_evidence_id(material)]
        review = review_for(self.content, self.evidence, claims, [material])
        with self.assertRaisesRegex(WorkflowConflict, "script_claim_evidence_scope_conflict"):
            validate_claim_dependencies(self.content, self.evidence, review)


if __name__ == "__main__":
    unittest.main()
