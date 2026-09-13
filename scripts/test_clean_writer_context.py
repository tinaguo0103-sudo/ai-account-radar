from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import clean_writer_context as clean
import spoken_script_runtime as script_runtime
from daily_workflow import WorkflowConflict


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "run_20260913_120000"
BUSINESS_DATE = "2026-09-13"


class CleanWriterContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="clean-writer-context-test-")
        self.root = Path(self.temp.name)
        self.skill = self.root / "skill-source"
        shutil.copytree(ROOT / "skills" / "austin-voice-scriptwriter", self.skill)
        self.authority = script_runtime.writer_authority_manifest(
            active_root=self.skill,
            source_root=self.skill,
            require_source_parity=True,
        )
        self.topics = [
            {
                "topic_id": "synthetic:one",
                "trend_event_id": "event:one",
                "source_evidence": {
                    "source": {"title": "合成题一", "summary": "同 run 事实"},
                    "source_facts": {"details": "一条足够完成写作的事实。"},
                },
            },
            {
                "topic_id": "synthetic:two",
                "trend_event_id": "event:two",
                "source_evidence": {
                    "source": {"title": "合成题二", "summary": "另一条同 run 事实"},
                    "source_facts": {"details": "第二条足够完成写作的事实。"},
                },
            },
        ]

    def tearDown(self):
        self.temp.cleanup()

    def fake_runner(self, calls):
        def run(command, prompt, input_root, output_path):
            calls.append({"command": command, "prompt": prompt, "input_root": input_root})
            output = {
                "run_id": RUN_ID,
                "business_date": BUSINESS_DATE,
                "topics": [],
            }
            for topic in self.topics:
                topic_id = topic["topic_id"]
                output["topics"].append({
                    "topic_id": topic_id,
                    "article": {
                        "topic_id": topic_id,
                        "title": f"{topic_id} article",
                        "body": f"{topic_id} article body",
                    },
                    "article_failure": None,
                    "script": {
                        "topic_id": topic_id,
                        "title": f"{topic_id} script",
                        "hook": f"{topic_id} hook",
                        "structure": "开头；展开；收束",
                        "body": f"{topic_id} spoken body",
                    },
                    "spoken_failure": None,
                })
            output_path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '{"type":"thread.started","thread_id":"thread-synthetic-1"}\n'
                    '{"type":"turn.started"}\n'
                    '{"type":"item.completed","item":{"type":"agent_message"}}\n'
                    '{"type":"turn.completed"}\n'
                ),
                stderr="",
            )
        return run

    def test_clean_contract_changes_only_topology(self):
        contract = clean.clean_writer_contract()
        self.assertEqual(contract["topology"], "one_fresh_codex_writer_context_per_exact_run")
        self.assertTrue(contract["fresh_context"])
        self.assertTrue(contract["non_user_visible"])
        self.assertTrue(contract["one_context_per_exact_run"])
        self.assertEqual(contract["raw_text_persistence"], "typed_output_only")
        self.assertEqual(contract["skills"], ["austin-voice-scriptwriter"])

    def test_fresh_identity_and_one_context_resume(self):
        calls = []
        first = clean.run_clean_writer_context(
            artifact_root=self.root / "artifacts",
            run_id=RUN_ID,
            business_date=BUSINESS_DATE,
            selected_topics=self.topics,
            writer_authority=self.authority,
            codex_bin="/bin/echo",
            runner=self.fake_runner(calls),
        )
        self.assertFalse(first["cached"])
        self.assertEqual(len(calls), 1)
        command = calls[0]["command"]
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--sandbox", command)
        self.assertIn("read-only", command)
        self.assertIn("gpt-5.6-luna", command)
        self.assertIn('model_reasoning_effort="max"', command)
        self.assertEqual(first["identity"]["thread_id"], "thread-synthetic-1")

        def should_not_run(*_args):
            raise AssertionError("a second Writer context was started")

        second = clean.run_clean_writer_context(
            artifact_root=self.root / "artifacts",
            run_id=RUN_ID,
            business_date=BUSINESS_DATE,
            selected_topics=self.topics,
            writer_authority=self.authority,
            codex_bin="/bin/echo",
            runner=should_not_run,
        )
        self.assertTrue(second["cached"])
        self.assertEqual(second["output"], first["output"])
        receipt = json.loads(
            (self.root / "artifacts" / RUN_ID / "clean_writer_context" / "receipt.json").read_text()
        )
        self.assertEqual(receipt["invocation_count"], 1)
        self.assertEqual(receipt["status"], "completed")

        output_path = self.root / "artifacts" / RUN_ID / "clean_writer_context" / "output.json"
        output_path.write_text(output_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowConflict, "output_hash_conflict"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "artifacts",
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=self.authority,
                codex_bin="/bin/echo",
                runner=should_not_run,
            )

    def test_clean_root_allowlist_and_forbidden_scope(self):
        calls = []
        clean.run_clean_writer_context(
            artifact_root=self.root / "artifacts",
            run_id=RUN_ID,
            business_date=BUSINESS_DATE,
            selected_topics=self.topics,
            writer_authority=self.authority,
            codex_bin="/bin/echo",
            runner=self.fake_runner(calls),
        )
        clean_root = self.root / "artifacts" / RUN_ID / "clean_writer_context" / "clean_root"
        visible = {
            str(path.relative_to(clean_root))
            for path in clean_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            visible,
            {
                "manifest.json",
                "topics.json",
                "writer_prompt.md",
                "skill/SKILL.md",
                *(f"skill/{name}" for name in script_runtime.ARTICLE_REQUIRED_REFERENCES),
            },
        )
        manifest = json.loads((clean_root / "manifest.json").read_text())
        self.assertIn("AGENTS.md", manifest["forbidden_paths"])
        self.assertNotIn("daily_workflow.sqlite3", visible)
        with self.assertRaisesRegex(WorkflowConflict, "topic_scope_invalid"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "forbidden",
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=[{**self.topics[0], "body": "old body"}],
                writer_authority=self.authority,
                codex_bin="/bin/echo",
                runner=self.fake_runner([]),
            )

    def test_model_and_command_scope_are_fail_closed(self):
        calls = []
        clean.run_clean_writer_context(
            artifact_root=self.root / "model",
            run_id=RUN_ID,
            business_date=BUSINESS_DATE,
            selected_topics=self.topics,
            writer_authority=self.authority,
            codex_bin="/bin/echo",
            runner=self.fake_runner(calls),
        )
        receipt_path = self.root / "model" / RUN_ID / "clean_writer_context" / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["model"] = "wrong-model"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(WorkflowConflict, "model_conflict"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "model",
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=self.authority,
                codex_bin="/bin/echo",
                runner=lambda *_args: (_ for _ in ()).throw(AssertionError("no retry")),
            )

        def outside_read(command, prompt, input_root, output_path):
            result = self.fake_runner([])(command, prompt, input_root, output_path)
            result.stdout = (
                '{"type":"thread.started","thread_id":"thread-outside"}\n'
                '{"type":"item.completed","item":{"type":"command_execution",'
                '"command":"/bin/zsh -lc \'cat /Users/other/secret\'"}}\n'
            )
            return result

        with self.assertRaisesRegex(WorkflowConflict, "absolute_path_read"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "outside",
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=self.authority,
                codex_bin="/bin/echo",
                runner=outside_read,
            )

        mismatched = json.loads(json.dumps(self.authority))
        mismatched["active_skill"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(WorkflowConflict, "skill_manifest_conflict"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "mismatched-skill",
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=mismatched,
                codex_bin="/bin/echo",
                runner=self.fake_runner([]),
            )

    def test_missing_skill_wrong_output_and_failed_child_are_typed_no_fallback(self):
        broken = json.loads(json.dumps(self.authority))
        broken["active_skill"]["path"] = str(self.root / "missing-skill" / "SKILL.md")
        with self.assertRaisesRegex(WorkflowConflict, "skill_missing"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "missing",
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=broken,
                codex_bin="/bin/echo",
                runner=self.fake_runner([]),
            )

        calls = []

        def wrong_output(command, prompt, input_root, output_path):
            output_path.write_text(json.dumps({
                "run_id": RUN_ID,
                "business_date": BUSINESS_DATE,
                "topics": [{"topic_id": "wrong", "article": None, "article_failure": None,
                             "script": None, "spoken_failure": None}],
            }), encoding="utf-8")
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout='{"type":"thread.started","thread_id":"wrong"}\n', stderr="")

        with self.assertRaisesRegex(WorkflowConflict, "coverage_invalid"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "wrong-output",
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=self.authority,
                codex_bin="/bin/echo",
                runner=wrong_output,
            )
        with self.assertRaisesRegex(WorkflowConflict, "already_attempted"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "wrong-output",
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=self.authority,
                codex_bin="/bin/echo",
                runner=self.fake_runner([]),
            )

        def child_failure(*_args):
            return SimpleNamespace(returncode=9, stdout="", stderr="child failed")

        failed_root = self.root / "failed-child"
        with self.assertRaisesRegex(WorkflowConflict, "cli_failed"):
            clean.run_clean_writer_context(
                artifact_root=failed_root,
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=self.authority,
                codex_bin="/bin/echo",
                runner=child_failure,
            )
        with self.assertRaisesRegex(WorkflowConflict, "already_attempted"):
            clean.run_clean_writer_context(
                artifact_root=failed_root,
                run_id=RUN_ID,
                business_date=BUSINESS_DATE,
                selected_topics=self.topics,
                writer_authority=self.authority,
                codex_bin="/bin/echo",
                runner=self.fake_runner([]),
            )

    def test_article_insufficiency_stays_item_local(self):
        def insufficient(command, prompt, input_root, output_path):
            output_path.write_text(json.dumps({
                "run_id": RUN_ID,
                "business_date": BUSINESS_DATE,
                "topics": [
                    {
                        "topic_id": topic["topic_id"],
                        "article": None,
                        "article_failure": {
                            "topic_id": topic["topic_id"],
                            "reason": "material_or_angle_insufficiency",
                            "detail": "仅有元数据，不能安全成文。",
                        },
                        "script": None,
                        "spoken_failure": {
                            "topic_id": topic["topic_id"],
                            "reason": "material_or_angle_insufficiency",
                            "detail": "没有冻结文章可供改编。",
                        },
                    }
                    for topic in self.topics
                ],
            }, ensure_ascii=False), encoding="utf-8")
            return SimpleNamespace(
                returncode=0,
                stdout='{"type":"thread.started","thread_id":"thread-insufficient"}\n',
                stderr="",
            )

        result = clean.run_clean_writer_context(
            artifact_root=self.root / "insufficient",
            run_id=RUN_ID,
            business_date=BUSINESS_DATE,
            selected_topics=self.topics,
            writer_authority=self.authority,
            codex_bin="/bin/echo",
            runner=insufficient,
        )
        self.assertTrue(all(row["article"] is None for row in result["output"]["topics"]))


if __name__ == "__main__":
    unittest.main()
