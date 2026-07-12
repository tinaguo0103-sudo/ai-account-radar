#!/usr/bin/env python3
from __future__ import annotations

import argparse
import inspect
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import editorial_skill_runner as runner
import topic_editorial_state_machine as machine
import topic_skill_replay_evaluation as replay


class TopicEditorialStateMachineTests(unittest.TestCase):
    def test_stage2_decisions_follow_candidate_index_not_rank_order(self) -> None:
        source = Path(inspect.getsourcefile(machine) or "")
        text = source.read_text(encoding="utf-8")
        self.assertIn('ranked_by_index = {int(item["index"]): item for item in ranked}', text)
        self.assertIn('decisions = [ranked_by_index[start + offset] for offset in range(len(rows))]', text)

    def test_finalize_marks_upstream_candidate_failures_visible(self) -> None:
        source = Path(inspect.getsourcefile(machine) or "")
        text = source.read_text(encoding="utf-8")
        self.assertIn('"stage": "completed_with_failures"', text)
        self.assertIn('"failure_semantics": "failed candidates were excluded before editorial decision and cards"', text)

    def test_stage1_payload_is_minimized_allowlist(self) -> None:
        row = {
            "来源类型": "对标视频",
            "平台": "抖音",
            "原始来源账号": "AIGC自修室",
            "原始来源标题": "Codex联动Obsidian，搭建知识库",
            "来源内容": "Codex 联动 Obsidian，留下长期判断。",
            "来源链接": "https://example.invalid/private",
            "内容指纹": "secret-fingerprint",
            "Austin转译角度": "forbidden deterministic angle",
            "我的工作流痛点": "forbidden old 04 field",
            "关联母场景": "forbidden mother scene",
            "来源权重类型": "有效对标账号核心源",
            "市场验证依据": "标题有明确工具组合和结果承诺",
        }

        payload = machine.minimized_stage1_row(
            {"candidate_id": "candidate-1"},
            {
                "source": {"exact_url": "https://example.invalid/article/1", "exact_title": row["原始来源标题"]},
                "research_summary": "已打开来源并完成研究。",
                "results": [{"evidence_id": "web-1", "url": "https://example.invalid/evidence"}],
                "conflicts": [], "confidence": "high", "dossier_hash": "a" * 64,
                "hook_analysis": {"audience_hook": "明确结果承诺", "hook_evidence_ids": ["web-1"]},
            },
            {"account_role": "AI业务系统导演"},
            [{"text": "先看冲突，再决定是否选择。", "reference_only": True}],
        )
        text = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(set(payload), machine.STAGE1_ALLOWLIST)
        self.assertIn("https://example.invalid/article/1", text)
        self.assertNotIn("secret-fingerprint", text)
        self.assertNotIn("forbidden deterministic angle", text)
        self.assertNotIn("forbidden old 04 field", text)
        self.assertNotIn("forbidden mother scene", text)

    def test_active_path_has_no_nested_model_execution(self) -> None:
        machine_source = inspect.getsource(machine)
        runner_source = inspect.getsource(runner)

        self.assertNotIn("subprocess", machine_source)
        self.assertNotIn("codex exec", machine_source.lower())
        self.assertFalse(hasattr(runner, "run_codex_prompt"))
        self.assertNotIn("run_codex_prompt", runner_source)
        self.assertNotIn("subprocess", runner_source)

    def test_provenance_declares_current_task_and_no_fallback(self) -> None:
        manifest = machine.provenance("test-task")

        self.assertEqual(manifest["execution_surface"], "current_codex_task")
        self.assertEqual(manifest["task_provenance"], "test-task")
        self.assertFalse(manifest["nested_model_execution"])
        self.assertTrue(manifest["strict_fail_closed"])
        self.assertEqual(manifest["prohibited_path_count"], 0)
        self.assertTrue(manifest["persona_style_reference_only"])

    def test_legacy_editorial_codex_cli_fails_before_business_io(self) -> None:
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "must-not-exist.csv"
            argv = [
                "editorial_skill_runner.py", "--engine", "codex",
                "--input", str(Path(tmp) / "missing.csv"), "--output", str(output),
            ]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as caught:
                runner.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(output.exists())

    def test_legacy_replay_codex_cli_fails_before_output_creation(self) -> None:
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "must-not-exist"
            argv = ["topic_skill_replay_evaluation.py", "--engine", "codex", "--out-dir", str(output)]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as caught:
                replay.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(output.exists())

    def test_later_stage_cannot_run_before_dependency(self) -> None:
        state = {"stages": {"stage1": machine.stage_record("prepared")}}

        with self.assertRaisesRegex(RuntimeError, "not completed"):
            machine.require_completed(state, "stage1")

    def test_stage_failure_can_retry_but_cannot_look_completed(self) -> None:
        record = machine.stage_record("prepared")
        machine.start_stage(record, "input-v1")
        machine.fail_stage(record, RuntimeError("bad output"))
        self.assertEqual(record["status"], "failed")
        self.assertIn("bad output", record["error"])

        machine.start_stage(record, "input-v1")
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["error"], "")

    def test_changed_stage1_output_invalidates_all_downstream_stages(self) -> None:
        state = {"stages": {
            "stage1": machine.stage_record("completed"),
            "global_ranking": machine.stage_record("completed"),
            "stage2": machine.stage_record("completed"),
            "finalize": machine.stage_record("completed"),
        }}

        machine.invalidate_downstream(state, "stage1", "changed")

        self.assertEqual(state["stages"]["global_ranking"]["status"], "stale")
        self.assertEqual(state["stages"]["stage2"]["status"], "stale")
        self.assertEqual(state["stages"]["finalize"]["status"], "stale")

    def test_validate_stage1_rejects_stale_input_hash(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            batch_dir = out / "stage1" / "batch_000"
            batch_dir.mkdir(parents=True)
            machine.write_json(batch_dir / "input.json", {"changed": True})
            machine.write_json(batch_dir / "output.pending.json", {"editorial_decisions": []})
            state = {
                "stages": {
                    "prepare_stage1": machine.stage_record("completed"),
                    "stage1": machine.stage_record("prepared", batches={
                        "batch_000": machine.stage_record("prepared", input_hash="stale", start_index=0, row_count=1),
                    }),
                }
            }
            machine.save_state(out, state)

            with self.assertRaisesRegex(RuntimeError, "input hash mismatch"):
                machine.validate_stage1(argparse.Namespace(out_dir=str(out), batch_id="batch_000"))

    def test_completed_stage_resume_uses_output_hash(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            batch_dir = out / "stage1" / "batch_000"
            batch_dir.mkdir(parents=True)
            input_payload = {"rows": []}
            output_payload = {"editorial_decisions": []}
            machine.write_json(batch_dir / "input.json", input_payload)
            machine.write_json(batch_dir / "output.pending.json", output_payload)
            record = machine.stage_record(
                "completed",
                input_hash=machine.hash_json(input_payload),
                raw_output_hash=machine.file_hash(batch_dir / "output.pending.json"),
                start_index=0,
                row_count=0,
            )
            state = {
                "stages": {
                    "prepare_stage1": machine.stage_record("completed"),
                    "stage1": machine.stage_record("completed", batches={"batch_000": record}),
                }
            }
            machine.save_state(out, state)

            result = machine.validate_stage1(argparse.Namespace(out_dir=str(out), batch_id="batch_000"))

            self.assertTrue(result["resumed"])
            self.assertEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
