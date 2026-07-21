from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import scheduled_flow_preflight as preflight
import topic_editorial_state_machine as machine


FIELDS = [
    "来源链接", "内容指纹", "来源类型", "来源内容", "原始来源标题",
    "原始发布文案", "原始来源账号", "平台",
]


class AR040ScheduledFlowTests(unittest.TestCase):
    def prepare(self, rows: list[dict[str, str]], *, assert_no_open: bool = True):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        run_id = "run_20260721_080000"
        path = root / "output" / "runs" / run_id / "today_10_topics.csv"
        path.parent.mkdir(parents=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        args = Namespace(
            out_dir=str(root / "state"), content_csv=[], since="2026-07-21", batch_size=3,
            max_skill_candidates=20, task_id="ar040-public-path", persona_docx=str(root / "persona.docx"),
            exact_input_csv=str(path), exact_input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), run_id=run_id,
        )
        adapter_patch = mock.patch.object(machine.source_adapter, "primary_adapter_for_url", side_effect=AssertionError("ordinary candidate opened network")) if assert_no_open else mock.patch.object(machine.source_adapter, "primary_adapter_for_url", side_effect=machine.source_adapter.AdapterContractError("unsupported_high_risk_source"))
        identity_patch = mock.patch.object(machine.source_adapter, "expected_identity", side_effect=AssertionError("ordinary candidate resolved URL"))
        with mock.patch.object(machine.persona_builder, "build_bundle", return_value={"authority_sha256": "a" * 64, "manifest_hash": "b" * 64}), adapter_patch, identity_patch:
            result = machine.prepare_source_open(args)
        return temp, root, result, machine.load_state(root / "state")

    def test_trusted_ordinary_artifacts_skip_source_open_for_nonempty_and_empty_urls(self) -> None:
        rows = [
            {"来源链接": "https://claude.com/blog/article/unsupported-slug", "内容指纹": "fp-nonempty", "来源类型": "AI热点", "来源内容": "A practical workflow opinion without hard facts", "原始来源标题": "Workflow opinion", "原始发布文案": "", "原始来源账号": "AIHOT", "平台": "Web"},
            {"来源链接": "", "内容指纹": "fp-empty", "来源类型": "对标视频", "来源内容": "A useful content structure and personal judgment", "原始来源标题": "Structure", "原始发布文案": "", "原始来源账号": "creator", "平台": "抖音"},
        ]
        temp, root, result, state = self.prepare(rows)
        self.addCleanup(temp.cleanup)
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["source_open_calls"], 0)
        self.assertEqual(state["stages"]["source_open"]["status"], "completed")
        for candidate in machine.candidate_rows_from_state(state):
            validated = json.loads((root / "state" / "source_open" / candidate["candidate_id"] / "validated.json").read_text())
            self.assertEqual(validated["open_status"], "artifact_only")
            self.assertTrue(validated["eligible"])

    def test_high_risk_unsupported_url_is_candidate_local(self) -> None:
        rows = [
            {"来源链接": "https://claude.com/blog/article/unsupported-slug", "内容指纹": "fp-risk", "来源类型": "AI热点", "来源内容": "官方宣布融资10亿元", "原始来源标题": "Funding", "原始发布文案": "", "原始来源账号": "AIHOT", "平台": "Web"},
            {"来源链接": "https://example.com/opinion", "内容指纹": "fp-safe", "来源类型": "AI热点", "来源内容": "An ordinary workflow opinion", "原始来源标题": "Opinion", "原始发布文案": "", "原始来源账号": "AIHOT", "平台": "Web"},
        ]
        temp, root, result, state = self.prepare(rows, assert_no_open=False)
        self.addCleanup(temp.cleanup)
        self.assertEqual(result["source_open_calls"], 0)
        self.assertEqual(state["stages"]["source_open"]["status"], "completed_with_failures")
        failures = [record for record in state["stages"]["source_open"]["candidates"].values() if record["status"] == "failed"]
        successes = [record for record in state["stages"]["source_open"]["candidates"].values() if record["status"] == "completed"]
        self.assertEqual((len(failures), len(successes)), (1, 1))

    def test_preflight_classifies_blockers_and_optional_telemetry(self) -> None:
        env = {
            "AI_ACCOUNT_RADAR_ENV": "staging",
            "FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "secret", "FEISHU_BASE_APP_TOKEN": "base",
            "FEISHU_TOPIC_TABLE_ID": "topic", "FEISHU_CARD_RECEIVE_TARGETS": "open_id:test",
        }
        result = preflight.evaluate_preflight(
            "card", environ=env, check_network=True,
            resolver=lambda *_args, **_kwargs: [(None, None, None, None, None)],
            path_probe=lambda path: "logs" not in str(path),
        )
        self.assertTrue(result["ok"])
        self.assertIn("optional_telemetry_unavailable", result["classifications"])
        blocked = preflight.evaluate_preflight(
            "card", environ=env, check_network=True,
            resolver=mock.Mock(side_effect=OSError("dns down")), path_probe=lambda _path: True,
        )
        self.assertFalse(blocked["ok"])
        self.assertIn("core_external_write_unavailable", blocked["blocking_reasons"])
        self.assertEqual(blocked["external_calls"], 0)

    def test_staging_requires_explicit_resources(self) -> None:
        result = preflight.evaluate_preflight(
            "collection",
            environ={"AI_ACCOUNT_RADAR_ENV": "staging", "FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "secret", "FEISHU_BASE_APP_TOKEN": "base"},
            path_probe=lambda _path: True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("FEISHU_SOURCE_TABLE_ID", result["missing_environment_keys"])
        self.assertIn("FEISHU_CONTENT_TABLE_ID", result["missing_environment_keys"])


if __name__ == "__main__":
    unittest.main()
