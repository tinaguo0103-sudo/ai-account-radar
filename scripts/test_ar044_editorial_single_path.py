#!/usr/bin/env python3
from __future__ import annotations

import argparse
import inspect
import json
import tempfile
import unittest
from pathlib import Path

import topic_editorial_state_machine as machine
import topic_research_contract as research


class Ar044EditorialSinglePathTests(unittest.TestCase):
    def candidate(self, index: int, *, high_risk_raw: bool) -> dict:
        raw = "医疗法律数据显示 42% 的变化" if high_risk_raw else "一个具体的 AI 工作流判断"
        return {
            "index": index,
            "candidate_id": f"candidate_{index:03d}",
            "exact_url": "",
            "content_fingerprint": f"fp-{index:03d}",
            "candidate_fingerprint": f"candidate-fp-{index:03d}",
            "csv_title": f"AIHOT 候选 {index}",
            "original_publication_copy": raw,
            "source_account": "AIHOT",
            "source_type": "AIHOT",
            "platform": "AIHOT",
            "artifact_text": raw,
            "local_trace_hash": f"trace-{index:03d}",
            "source_open_required": False,
            "primary_adapter": "",
            "expected_page_identity": {},
        }

    def test_production_shaped_31_10_21_all_enter_stage1_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp)
            candidates = [
                self.candidate(index, high_risk_raw=index < 10)
                for index in range(31)
            ]
            state = {
                "out_dir": str(out_dir),
                "stages": {
                    "source_open": machine.stage_record(
                        "completed",
                        candidates={
                            item["candidate_id"]: machine.stage_record("completed")
                            for item in candidates
                        },
                    ),
                    "research": machine.stage_record(),
                },
            }
            machine.write_json(out_dir / "editorial_state_machine.json", state)
            machine.write_json(out_dir / "shortlist_candidates.json", candidates)
            for item in candidates:
                source = {
                    "open_status": "artifact_only",
                    "eligible": True,
                    "link_unavailable": True,
                    "content_evidence": [{
                        "evidence_id": f"artifact:{item['content_fingerprint']}",
                        "text": item["artifact_text"],
                    }],
                }
                machine.write_json(
                    out_dir / "source_open" / item["candidate_id"] / "validated.json",
                    source,
                )

            result = machine.prepare_research(argparse.Namespace(out_dir=str(out_dir)))
            prepared = machine.read_json(out_dir / "editorial_state_machine.json")

        self.assertEqual(result["researchable"], 0)
        self.assertEqual(result["stage1_eligible"], 31)
        self.assertEqual(
            len(machine.completed_candidate_ids(prepared, "research")),
            31,
        )

    def test_raw_text_has_no_research_prequalification_api(self) -> None:
        self.assertFalse(hasattr(research, "requires_external_research"))
        source = Path(inspect.getsourcefile(research) or "").read_text(encoding="utf-8")
        self.assertNotIn("HIGH_RISK_FACT_PATTERNS", source)

    def test_final_visible_hard_claim_requires_opened_evidence(self) -> None:
        dossier = {
            "source": {"content_evidence": [{"evidence_id": "artifact:1"}]},
            "results": [],
        }
        research.validate_recommendation_research_eligibility({
            "decision": "select",
            "recommendation_status": "生成脚本包",
            "hard_fact_usage": "none",
        }, dossier)
        with self.assertRaisesRegex(research.ContractError, "no freshly opened"):
            research.validate_recommendation_research_eligibility({
                "decision": "select",
                "recommendation_status": "生成脚本包",
                "hard_fact_usage": "42% 医疗效果",
                "research_evidence_ids": "artifact:1",
            }, dossier)

    def test_removed_gate_and_second_path_symbols_are_physically_absent(self) -> None:
        machine_source = Path(inspect.getsourcefile(machine) or "").read_text(encoding="utf-8")
        research_source = Path(inspect.getsourcefile(research) or "").read_text(encoding="utf-8")
        combined = machine_source + research_source
        for symbol in (
            "research_required_for_high_risk_facts",
            "requires_external_research",
            "HIGH_RISK_FACT_PATTERNS",
            '"quality_gate_ok": False',
            "minimum_recommendation",
            "fallback_recommendation",
            "uniform_observe_override",
            "degraded_editorial",
        ):
            self.assertNotIn(symbol, combined)


if __name__ == "__main__":
    unittest.main()
