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
            active_root=self.skill, source_root=self.skill, require_source_parity=True,
        )
        self.topics = [
            {"topic_id": "synthetic:one", "trend_event_id": "event:one", "source_evidence": {"source": {"title": "合成题一", "summary": "同 run 事实"}, "source_facts": {"details": "足够完成第一题写作的事实。"}}},
            {"topic_id": "synthetic:two", "trend_event_id": "event:two", "source_evidence": {"source": {"title": "合成题二", "summary": "另一条同 run 事实"}, "source_facts": {"details": "足够完成第二题写作的事实。"}}},
        ]

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def fake_runner(calls, *, fail_on=None, batch=False, read_plans=None):
        def run(command, prompt, input_root, output_path):
            call_index = len(calls)
            calls.append({"command": list(command), "prompt": prompt})
            topic_id = next(line.split(": ", 1)[1] for line in prompt.splitlines() if line.startswith("Current topic:"))
            phase = next(line.split(": ", 1)[1] for line in prompt.splitlines() if line.startswith("Current phase:"))
            if fail_on == (topic_id, phase):
                return SimpleNamespace(returncode=9, stdout="", stderr="synthetic child failure")
            if batch:
                value = {"run_id": RUN_ID, "business_date": BUSINESS_DATE, "topics": []}
            else:
                value = {"run_id": RUN_ID, "business_date": BUSINESS_DATE, "topic_id": topic_id, "phase": phase, "article": None, "script": None, "failure": None}
                if phase == "article_required":
                    value["article"] = {"topic_id": topic_id, "title": f"{topic_id} article", "body": f"{topic_id} article body"}
                else:
                    value["script"] = {"topic_id": topic_id, "title": f"{topic_id} script", "hook": f"{topic_id} hook", "structure": "事实；判断；收束", "body": f"{topic_id} spoken body"}
            output_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            started = "" if any("thread-synthetic-1" in str(row["command"]) for row in calls[:-1]) else '{"type":"thread.started","thread_id":"thread-synthetic-1"}\n'
            reads = ["skill/SKILL.md"] + (list(script_runtime.ARTICLE_REQUIRED_REFERENCES) if phase == "article_required" else ["references/spoken-adaptation.md"])
            reads.append("current_topic.json" if phase == "article_required" else "frozen_article.json")
            if read_plans is not None and call_index < len(read_plans):
                reads = list(read_plans[call_index])
            commands = "".join(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": f"sed -n '1,1p' {input_root / ('skill/' + relative if relative.startswith('references/') else relative)}"}}) + "\n" for relative in reads)
            return SimpleNamespace(returncode=0, stdout=started + '{"type":"turn.started"}\n' + commands + '{"type":"turn.completed"}\n', stderr="")
        return run

    def _run(self, root_name="artifacts", topics=None, current_index=0, phase="article_required", calls=None, frozen_article=None, **kwargs):
        calls = calls if calls is not None else []
        topics = topics or self.topics
        return clean.run_clean_writer_context(
            artifact_root=self.root / root_name, run_id=RUN_ID, business_date=BUSINESS_DATE,
            selected_topics=topics, writer_authority=self.authority,
            current_topic=topics[current_index], phase=phase,
            frozen_article=frozen_article,
            codex_bin="/bin/echo", runner=self.fake_runner(calls, **kwargs),
        )

    def test_contract_declares_one_context_controlled_turns(self):
        contract = clean.clean_writer_contract()
        self.assertEqual(contract["topology"], "one_fresh_codex_writer_context_per_exact_run_controlled_turns")
        self.assertTrue(contract["fresh_context"])
        self.assertTrue(contract["non_user_visible"])
        self.assertTrue(contract["one_context_per_exact_run"])
        self.assertTrue(contract["one_app_server_process_per_exact_run"])
        self.assertTrue(contract["controlled_turns"])
        self.assertTrue(contract["batch_output_forbidden"])
        self.assertIn("necessary_public_first_party_read_only_research_when_capable", contract["input_scope"])

    def test_one_context_multiple_turns_and_article_before_spoken(self):
        calls = []
        first = self._run(calls=calls)
        article_meta = script_runtime.write_article_artifact(self.root / "artifacts", RUN_ID, BUSINESS_DATE, first["output"]["article"])
        article_meta.pop("created", None)
        second = self._run(calls=calls, phase="spoken_adaptation_required", frozen_article=article_meta)
        third = self._run(calls=calls, current_index=1)
        article_two = script_runtime.write_article_artifact(self.root / "artifacts", RUN_ID, BUSINESS_DATE, third["output"]["article"])
        article_two.pop("created", None)
        fourth = self._run(calls=calls, current_index=1, phase="spoken_adaptation_required", frozen_article=article_two)
        self.assertEqual([row["output"]["phase"] for row in (first, second, third, fourth)], ["article_required", "spoken_adaptation_required", "article_required", "spoken_adaptation_required"])
        self.assertEqual(len(calls), 4)
        self.assertIn("exec", calls[0]["command"])
        self.assertNotIn("resume", calls[0]["command"])
        for call in calls[1:]:
            self.assertEqual(call["command"][1:3], ["exec", "resume"])
            self.assertIn("thread-synthetic-1", call["command"])
        self.assertNotIn("topics", first["output"])
        receipt = json.loads((self.root / "artifacts" / RUN_ID / "clean_writer_context" / "receipt.json").read_text())
        self.assertEqual(receipt["invocation_count"], 1)
        self.assertEqual(receipt["context_start_count"], 1)
        self.assertEqual(receipt["turn_count"], 4)
        self.assertEqual(receipt["completed_turn_count"], 4)

    def test_article_is_not_regenerated_when_spoken_retries(self):
        calls = []
        first = self._run(root_name="resume", calls=calls)
        article_meta = script_runtime.write_article_artifact(self.root / "resume", RUN_ID, BUSINESS_DATE, first["output"]["article"])
        article_meta.pop("created", None)
        with self.assertRaisesRegex(WorkflowConflict, "cli_failed"):
            clean.run_clean_writer_context(
                artifact_root=self.root / "resume", run_id=RUN_ID, business_date=BUSINESS_DATE,
                selected_topics=self.topics, writer_authority=self.authority, current_topic=self.topics[0],
                phase="spoken_adaptation_required", frozen_article=article_meta, codex_bin="/bin/echo",
                runner=self.fake_runner(calls, fail_on=("synthetic:one", "spoken_adaptation_required")),
            )
        retry = self._run(root_name="resume", calls=calls, phase="spoken_adaptation_required", frozen_article=article_meta)
        self.assertTrue(retry["output"]["script"])
        self.assertEqual(sum("article_required" in row["prompt"] for row in calls), 1)
        self.assertEqual(calls[1]["command"][1:3], ["exec", "resume"])
        receipt = json.loads((self.root / "resume" / RUN_ID / "clean_writer_context" / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "active")
        self.assertEqual(receipt["invocation_count"], 1)
        turn = json.loads((self.root / "resume" / RUN_ID / "clean_writer_context" / "turns" / "turn-0002.json").read_text())
        self.assertNotIn("error", turn)
        ledger = (self.root / "resume" / RUN_ID / "clean_writer_context" / "events.jsonl").read_text().splitlines()
        self.assertEqual(receipt["event_count"], len(ledger))

    def test_first_article_requires_complete_common_reference_trace(self):
        calls = []
        reads = ["skill/SKILL.md", *list(script_runtime.ARTICLE_REQUIRED_REFERENCES)[:-1], "current_topic.json"]
        with self.assertRaisesRegex(WorkflowConflict, "skill_read_trace_incomplete"):
            self._run(root_name="trace-first-missing", calls=calls, read_plans=[reads])
        receipt = json.loads((self.root / "trace-first-missing" / RUN_ID / "clean_writer_context" / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertNotIn("read_trace", receipt)

    def test_later_article_uses_cumulative_common_trace_and_reads_current_topic(self):
        calls = []
        first = self._run(root_name="trace-cumulative", calls=calls)
        self.assertEqual(first["output"]["phase"], "article_required")
        second = self._run(
            root_name="trace-cumulative",
            calls=calls,
            current_index=1,
            read_plans=[[], ["current_topic.json"]],
        )
        self.assertEqual(second["output"]["phase"], "article_required")
        self.assertIn("Do not mechanically reread", calls[1]["prompt"])
        self.assertNotIn("Read every required managed reference", calls[1]["prompt"])
        receipt = json.loads((self.root / "trace-cumulative" / RUN_ID / "clean_writer_context" / "receipt.json").read_text())
        self.assertTrue(receipt["read_trace"]["common_references_read"])
        self.assertEqual(receipt["read_trace"]["article_current_topic_reads"], 2)

    def test_later_article_missing_current_topic_fails_after_cumulative_trace(self):
        calls = []
        self._run(root_name="trace-current-required", calls=calls)
        with self.assertRaisesRegex(WorkflowConflict, "skill_read_trace_incomplete"):
            self._run(
                root_name="trace-current-required",
                calls=calls,
                current_index=1,
                read_plans=[[], ["skill/SKILL.md"]],
            )
        receipt = json.loads((self.root / "trace-current-required" / RUN_ID / "clean_writer_context" / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(receipt["read_trace"]["common_references_read"])
        self.assertEqual(receipt["read_trace"]["article_current_topic_reads"], 1)

    def test_spoken_requires_frozen_article_and_spoken_reference(self):
        for root_name, reads in (
            ("trace-spoken-reference", ["frozen_article.json"]),
            ("trace-spoken-frozen", ["skill/references/spoken-adaptation.md"]),
        ):
            calls = []
            first = self._run(root_name=root_name, calls=calls)
            article_meta = script_runtime.write_article_artifact(self.root / root_name, RUN_ID, BUSINESS_DATE, first["output"]["article"])
            article_meta.pop("created", None)
            with self.assertRaisesRegex(WorkflowConflict, "skill_read_trace_incomplete"):
                self._run(
                    root_name=root_name,
                    calls=calls,
                    phase="spoken_adaptation_required",
                    frozen_article=article_meta,
                    read_plans=[[], reads],
                )
            receipt = json.loads((self.root / root_name / RUN_ID / "clean_writer_context" / "receipt.json").read_text())
            self.assertTrue(receipt["read_trace"]["common_references_read"])
            self.assertEqual(receipt["read_trace"]["spoken_frozen_article_reads"], 0)

    def test_completed_turn_is_cached_without_requiring_codex_binary(self):
        calls = []
        first = self._run(root_name="cached", calls=calls)
        cached = clean.run_clean_writer_context(
            artifact_root=self.root / "cached", run_id=RUN_ID, business_date=BUSINESS_DATE,
            selected_topics=self.topics, writer_authority=self.authority,
            current_topic=self.topics[0], phase="article_required", codex_bin="/missing/codex",
        )
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["output"], first["output"])
        self.assertEqual(len(calls), 1)

    def test_controlled_case_and_voice_paths_are_real_and_reachable(self):
        calls = []
        self._run(root_name="authority", calls=calls)
        clean_root = self.root / "authority" / RUN_ID / "clean_writer_context" / "clean_root"
        manifest = json.loads((clean_root / "manifest.json").read_text())
        self.assertGreaterEqual(len(manifest["controlled_case_files"]), 1)
        self.assertGreaterEqual(len(manifest["voice_sample_files"]), 1)
        for relative in manifest["controlled_case_files"] + manifest["voice_sample_files"]:
            self.assertTrue((clean_root / "skill" / "references" / relative).is_file(), relative)
        for relative in manifest["controlled_case_files"]:
            self.assertIn(f"skill/references/{relative}", manifest["static_allowed_files"])

    def test_prompt_preserves_public_research_capability_boundary(self):
        calls = []
        self._run(root_name="research", calls=calls)
        prompt = calls[0]["prompt"]
        self.assertIn("public first-party read-only research", prompt)
        self.assertIn("research_unavailable", prompt)
        self.assertNotIn("do not use the network", prompt.lower())

    def test_app_server_agent_message_content_is_read_as_output(self):
        session = clean._AppServerSession("/bin/echo", self.root, self.root / "stderr.log")
        pieces = []
        session._consume_notification({"method": "item/agentMessage/delta", "params": {"delta": "{"}}, pieces)
        session._consume_notification({
            "method": "item/completed",
            "params": {"item": {"type": "agent_message", "content": [{"type": "output_text", "text": "{}"}]}},
        }, pieces)
        self.assertEqual(pieces, ["{}"])

    def test_app_server_acceptance_and_event_ledger_callbacks_are_incremental(self):
        session = clean._AppServerSession("/bin/echo", self.root, self.root / "stderr.log")
        session.process = object()
        session.thread_id = "thread-incremental"
        messages = iter([
            {"id": "clean-turn-1", "result": {"turn": {"id": "turn-incremental", "threadId": "thread-incremental", "status": "inProgress"}}},
            {"method": "turn/started", "params": {"threadId": "thread-incremental", "turn": {"id": "turn-incremental", "status": "inProgress"}}},
            {"method": "item/completed", "params": {"threadId": "thread-incremental", "turnId": "turn-incremental", "item": {"type": "command_execution", "command": "sed -n '1,1p' current_topic.json"}}},
            {"method": "item/agentMessage/delta", "params": {"threadId": "thread-incremental", "turnId": "turn-incremental", "delta": "{}"}},
            {"method": "turn/completed", "params": {"threadId": "thread-incremental", "turn": {"id": "turn-incremental", "status": "completed", "items": [{"type": "agent_message", "content": [{"type": "output_text", "text": "{}"}]}]}}},
        ])
        session._send = lambda value: None
        session._read = lambda timeout=clean.DEFAULT_TIMEOUT_SECONDS, **kwargs: next(messages)
        timeline, accepted, snapshots = [], [], []
        session.configure_persistence(
            on_event=lambda event: timeline.append(event),
            on_turn_accepted=lambda thread_id, turn_id: (timeline.append({"accepted": True}), accepted.append((thread_id, turn_id))),
            on_output_snapshot=lambda value: snapshots.append(value),
        )
        self.assertEqual(session.turn("return {}", {}), "{}")
        self.assertEqual(accepted, [("thread-incremental", "turn-incremental")])
        self.assertTrue(timeline[0].get("accepted"))
        self.assertEqual(timeline[1].get("type"), "turn/started")
        self.assertEqual(timeline[-1].get("status"), "completed")
        self.assertEqual(snapshots[-1], "{}")
        self.assertNotIn("delta", timeline[-1])
        self.assertEqual(session.last_turn_state, "completed")

    def test_app_server_same_turn_readback_states_are_typed(self):
        session = clean._AppServerSession("/bin/echo", self.root, self.root / "stderr.log")
        session.process = object()
        session.thread_id = "thread-readback"
        session.turn_statuses["turn-completed"] = "completed"
        completed_readback = session.reconcile_turn("turn-completed", ["{}"])
        self.assertEqual(completed_readback["state"], "completed")
        session.turn_statuses["turn-failed"] = "failed"
        self.assertEqual(session.reconcile_turn("turn-failed")["state"], "failed")
        session.turn_statuses.clear()
        session._rpc = lambda method, params, **kwargs: {"thread": {"status": {"type": "active"}}}
        old_wait = clean.RECONCILE_WAIT_SECONDS
        try:
            clean.RECONCILE_WAIT_SECONDS = 0
            self.assertEqual(session.reconcile_turn("turn-running")["state"], "running")
        finally:
            clean.RECONCILE_WAIT_SECONDS = old_wait
        session.process = None
        self.assertEqual(session.reconcile_turn("turn-unknown")["state"], "unknown")

    def test_silent_turn_fixture_recovers_same_identity_without_new_turn(self):
        session = clean._AppServerSession("/bin/echo", self.root, self.root / "stderr.log")
        session.process = object()
        session.thread_id = "thread-silent"
        sent = []
        session._send = lambda value: sent.append(value)
        reads = [
            {"id": "clean-turn-1", "result": {"turn": {"id": "turn-silent", "threadId": "thread-silent", "status": "inProgress"}}},
            WorkflowConflict("clean_writer_context_turn_timeout"),
            {"id": "clean-2", "result": {"thread": {"status": {"type": "active"}}}},
            {"method": "turn/completed", "params": {"threadId": "thread-silent", "turn": {"id": "turn-silent", "status": "completed", "items": [{"type": "agent_message", "content": [{"type": "output_text", "text": "{\"ok\":true}"}]}]}}},
        ]

        def read(timeout=clean.DEFAULT_TIMEOUT_SECONDS, **kwargs):
            value = reads.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        session._read = read
        accepted = []
        session.configure_persistence(on_turn_accepted=lambda thread_id, turn_id: accepted.append((thread_id, turn_id)))
        old_wait = clean.RECONCILE_WAIT_SECONDS
        old_interrupt_wait = clean.RECONCILE_INTERRUPT_WAIT_SECONDS
        try:
            clean.RECONCILE_WAIT_SECONDS = 0.2
            clean.RECONCILE_INTERRUPT_WAIT_SECONDS = 0.2
            self.assertEqual(session.turn("return {}", {}), "{\"ok\":true}")
        finally:
            clean.RECONCILE_WAIT_SECONDS = old_wait
            clean.RECONCILE_INTERRUPT_WAIT_SECONDS = old_interrupt_wait
        self.assertEqual(accepted, [("thread-silent", "turn-silent")])
        self.assertEqual([item["method"] for item in sent], ["turn/start", "thread/read"])
        self.assertEqual(session.last_turn_id, "turn-silent")
        self.assertEqual(session.last_turn_state, "completed")

    def test_process_exit_marks_unknown_and_does_not_start_duplicate_turn(self):
        calls = []
        original = clean._AppServerSession

        class SilentSession:
            instances = []

            def __init__(self, binary, input_root, stderr_path):
                self.thread_id = "thread-process-exit"
                self.last_turn_id = "turn-process-exit"
                self.last_turn_state = "unknown"
                self.last_turn_status = ""
                self.last_turn_output = ""
                self.last_events = []
                self.callbacks = {}
                self.turn_calls = 0
                SilentSession.instances.append(self)

            def start(self):
                return None

            def configure_persistence(self, **callbacks):
                self.callbacks = callbacks

            def turn(self, prompt, schema):
                self.turn_calls += 1
                self.callbacks["on_turn_accepted"](self.thread_id, self.last_turn_id)
                raise WorkflowConflict("clean_writer_context_session_ended")

            def reconcile_turn(self, turn_id):
                calls.append(("reconcile", turn_id))
                return {"state": "unknown", "turn_id": turn_id, "thread_id": self.thread_id, "status": "", "output": "", "detail": "fixture"}

            def continue_turn(self, turn_id):
                calls.append(("continue", turn_id))
                return {"state": "unknown", "turn_id": turn_id, "thread_id": self.thread_id, "status": "", "output": "", "detail": "fixture"}

        clean._SESSIONS.clear()
        clean._AppServerSession = SilentSession
        try:
            def run_direct():
                return clean.run_clean_writer_context(
                    artifact_root=self.root / "process-exit",
                    run_id=RUN_ID,
                    business_date=BUSINESS_DATE,
                    selected_topics=self.topics,
                    writer_authority=self.authority,
                    current_topic=self.topics[0],
                    phase="article_required",
                    codex_bin="/bin/echo",
                    runner=None,
                )

            with self.assertRaisesRegex(WorkflowConflict, "session_ended"):
                run_direct()
            receipt_path = self.root / "process-exit" / RUN_ID / "clean_writer_context" / "receipt.json"
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["status"], "unknown")
            self.assertEqual(receipt["active_turn_id"], "turn-process-exit")
            self.assertEqual(SilentSession.instances[0].turn_calls, 1)
            with self.assertRaisesRegex(WorkflowConflict, "status_unknown"):
                run_direct()
            self.assertEqual(SilentSession.instances[0].turn_calls, 1)
            self.assertEqual(calls, [("reconcile", "turn-process-exit")])
        finally:
            clean._SESSIONS.clear()
            clean._AppServerSession = original

    def test_embedded_absolute_command_path_is_rejected(self):
        with self.assertRaisesRegex(WorkflowConflict, "absolute_path_read"):
            clean._event_summaries(
                json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "python -c 'open(\"/etc/passwd\")'"}}),
                self.root,
            )

    def test_batch_output_is_rejected_and_receipt_remains_recoverable_on_same_context(self):
        calls = []
        with self.assertRaisesRegex(WorkflowConflict, "output_schema_invalid"):
            self._run(root_name="batch", calls=calls, batch=True)
        receipt = json.loads((self.root / "batch" / RUN_ID / "clean_writer_context" / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["invocation_count"], 1)
        retry_calls = []
        resumed = self._run(root_name="batch", calls=retry_calls, topics=self.topics, current_index=0)
        self.assertEqual(resumed["turn"]["attempt_count"], 2)
        # The resumed command reuses the same persisted thread; no second
        # thread.started event is emitted.
        self.assertEqual(retry_calls[0]["command"][1:3], ["exec", "resume"])

    def test_initial_child_failure_without_thread_can_retry_before_context_exists(self):
        failed_calls = []
        with self.assertRaisesRegex(WorkflowConflict, "cli_failed"):
            self._run(root_name="no-thread", calls=failed_calls, fail_on=("synthetic:one", "article_required"))
        retry_calls = []
        retried = self._run(root_name="no-thread", calls=retry_calls)
        self.assertEqual(retried["output"]["phase"], "article_required")
        self.assertEqual(retry_calls[0]["command"][1:2], ["exec"])
        receipt = json.loads((self.root / "no-thread" / RUN_ID / "clean_writer_context" / "receipt.json").read_text())
        self.assertEqual(receipt["invocation_count"], 1)

    def test_forbidden_topic_body_and_wrong_model_fail_closed(self):
        with self.assertRaisesRegex(WorkflowConflict, "topic_scope_invalid"):
            self._run(root_name="forbidden", topics=[{**self.topics[0], "body": "old"}])
        calls = []
        self._run(root_name="model", calls=calls)
        receipt_path = self.root / "model" / RUN_ID / "clean_writer_context" / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["model"] = "wrong-model"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(WorkflowConflict, "model_conflict"):
            self._run(root_name="model", calls=[])

    def test_natural_topic_boundaries_do_not_create_batch_output(self):
        for count in (1, 6, 10):
            topics = self.topics[:1] * 0 + [
                {"topic_id": f"synthetic:{index}", "trend_event_id": f"event:{index}", "source_evidence": {"source": {"title": "题", "summary": "事实"}}}
                for index in range(count)
            ]
            calls = []
            result = self._run(root_name=f"boundary-{count}", topics=topics, calls=calls)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("topics", result["output"])
        source = (ROOT / "scripts" / "run_daily_workflow.py").read_text(encoding="utf-8")
        self.assertIn('"scripts": [], "failures": []', source)


if __name__ == "__main__":
    unittest.main()
