from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from daily_workflow import DailyWorkflow, WorkflowConflict
from run_daily_workflow import build_scripts_handoff, enrich
from spoken_script_runtime import (
    load_writer_contract,
    new_checkpoint,
    sanitize_handoff,
    topic_packet,
    validate_checkpoint,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "run_20260808_120000"
BUSINESS_DATE = "2026-08-08"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def last_json(output: str) -> dict:
    return json.loads(output.strip().splitlines()[-1])


class Publisher(BaseHTTPRequestHandler):
    posts = 0
    gets = 0
    payloads: dict[str, dict] = {}

    def log_message(self, *_args):
        return

    def respond(self, value: dict, status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def readback(payload: dict, status: str) -> dict:
        return {
            "ok": True,
            "status": status,
            "run_id": payload["run_id"],
            "business_date": payload["business_date"],
            "run_status": payload["run"]["status"],
            "revision": payload["revision"],
            "authority_identity": payload["authority_identity"],
            "counts": {
                "content": len(payload["collected_items"]),
                "topics": len(payload["topics"]),
                "scripts": len(payload["scripts"]),
            },
        }

    def do_POST(self):
        self.__class__.posts += 1
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.__class__.payloads[payload["run_id"]] = payload
        self.respond(self.readback(payload, "applied"))

    def do_GET(self):
        self.__class__.gets += 1
        run_id = self.path.split("run_id=", 1)[-1]
        payload = self.__class__.payloads.get(run_id)
        if payload is None:
            self.respond({"error": "business_projection_missing"}, 404)
            return
        self.respond(self.readback(payload, "readback"))


class PerTopicVoiceRuntimeTest(unittest.TestCase):
    def setUp(self):
        Publisher.posts = 0
        Publisher.gets = 0
        Publisher.payloads = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Publisher)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def fixture(self, root: Path, run_id: str = RUN_ID, count: int = 3) -> Path:
        content = []
        candidates = []
        for index in range(count):
            identity = f"douyin:{9000 + index}"
            title = [
                "一段采访如何暴露产品承诺",
                "一个工具功能为什么改变不了交付",
                "一条产品公告里被忽略的限制",
            ][index % 3]
            content.append({
                "item_id": identity,
                "source_url": f"https://www.douyin.com/video/{9000 + index}",
                "source_title": title,
                "source_summary": "QA-private exact same-run source summary",
                "title": title,
                "summary": "QA-private exact same-run content",
            })
            candidates.append({
                "candidate_id": identity,
                "item_id": identity,
                "source_url": f"https://www.douyin.com/video/{9000 + index}",
                "title": title,
                "summary": "QA-private exact same-run candidate",
            })
        path = root / f"collection-{run_id}.json"
        write_json(path, {
            "run_id": run_id,
            "business_date": BUSINESS_DATE,
            "status": "completed",
            "content_items": content,
            "candidates": candidates,
            "source_runs": [{"source": "QA-private", "status": "completed", "item_count": count}],
        })
        return path

    def command(self, root: Path, fixture: Path, run_id: str = RUN_ID) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "run_daily_workflow.py"),
            "--run-id", run_id,
            "--business-date", BUSINESS_DATE,
            "--workflow-db", str(root / f"{run_id}.sqlite3"),
            "--artifact-root", str(root / "runs"),
            "--collection-fixture", str(fixture),
            "--video-mode", "disabled",
        ]

    def execute(
        self,
        command: list[str],
        config: Path,
        extra_env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["WEBSITE_PUBLISHER_CONFIG"] = str(config)
        if extra_env:
            environment.update(extra_env)
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            env=environment,
            cwd=str(cwd) if cwd is not None else None,
        )

    def publisher_config(self, root: Path) -> Path:
        path = root / "publisher.json"
        write_json(path, {
            "website_url": f"http://127.0.0.1:{self.server.server_port}",
            "authority_identity": "qa-private:content-driven-reset",
            "app_bearer": "runtime-only-test",
            "sites_bearer": "runtime-only-test",
        })
        return path

    def test_writer_packet_preserves_source_owned_facts(self):
        collection = {
            "candidates": [{
                "candidate_id": "topic:source-facts",
                "item_id": "item:source-facts",
                "source_url": "https://example.test/source-facts",
                "source_title": "Source-owned title",
                "source_summary": "Source-owned summary with the concrete event detail.",
                "不能照搬": "Do not copy the source wording.",
            }],
            "content_items": [{
                "item_id": "item:source-facts",
                "source_url": "https://example.test/source-facts",
                "正文/字幕/简介片段": "Source-owned caption and fact excerpt.",
                "解析说明": "Metadata-only boundary.",
            }],
            "understanding_results": [],
        }
        editorial = {"topics": [{
            "candidate_id": "topic:source-facts",
            "decision": "select",
            "selection_reason": "The card has a concrete source-owned event.",
            "editorial_thesis": {
                "thesis": "The source changes the cost of a real decision.",
                "audience_conflict": "The audience has a concrete choice to make.",
                "why_now": "This same-run source supplies the decisive fact.",
                "evidence_boundary": {
                    "source_facts": "The source-owned event is recorded in this run.",
                    "interpretation": "The event changes how the audience should judge the choice.",
                    "proposed_test": "A bounded follow-up can test the interpretation.",
                },
            },
        }]}
        handoff = build_scripts_handoff(RUN_ID, BUSINESS_DATE, collection, editorial)
        selected = handoff["selected_topics"][0]
        self.assertEqual(
            selected["source_evidence"]["source_facts"]["details"],
            "Source-owned summary with the concrete event detail.",
        )
        self.assertEqual(
            selected["source_evidence"]["source_facts"]["transcript"],
            "Source-owned caption and fact excerpt.",
        )
        self.assertNotIn("cannot_claim", selected["source_evidence"])

    def editorial_file(self, root: Path, topic_ids: list[str], run_id: str = RUN_ID) -> Path:
        path = root / f"editorial-{run_id}.json"
        write_json(path, {
            "run_id": run_id,
            "topics": [{
                "candidate_id": topic_id,
                "decision": "select",
                "title": f"题目 {index} 的真实判断",
                "hook": f"先看题目 {index} 自己的冲突。",
                "structure": "按这条材料自然推进",
                "selection_reason": "该题拥有独立事实和用户价值。",
                "editorial_thesis": {
                    "thesis": f"题目 {index} 的事实指向一个不能被替代的判断。",
                    "audience_conflict": f"受众在题目 {index} 上面临具体取舍。",
                    "why_now": "同 run 来源现在给出了可讲的事实。",
                    "evidence_boundary": {
                        "source_facts": f"题目 {index} 的同 run 来源事实。",
                        "interpretation": f"题目 {index} 的判断来自这条事实，而不是标题推断。",
                        "proposed_test": f"可以用题目 {index} 的边界设计一个有界验证。",
                    },
                },
                "unique_judgment": f"题目 {index} 不能被另一题替代。",
                "standalone_eligibility": {
                    "decision": "select",
                    "reason": "隐藏其他候选后仍值得完整表达。",
                },
            } for index, topic_id in enumerate(topic_ids)] + [{
                "candidate_id": topic_id,
                "decision": "observe",
                "selection_reason": "QA fixture non-selected row",
            } for topic_id in topic_ids[len(topic_ids):]],
        })
        return path

    def submission_file(self, root: Path, handoff: dict, index: int, failure: bool = False) -> Path:
        topic = handoff["selected_topics"][0]
        path = root / f"submission-{index}.json"
        if failure:
            payload = {
                "packet_id": handoff["topic_input"]["packet_id"],
                "failure": {
                    "topic_id": topic["topic_id"],
                    "reason": "material_insufficiency",
                    "detail": "该题的同一 run 材料不足以支撑独特的公开判断。",
                },
            }
        else:
            payload = {
                "packet_id": handoff["topic_input"]["packet_id"],
                "script": {
                    "topic_id": topic["topic_id"],
                    "title": f"题目 {index} 的判断",
                    "hook": f"先看题目 {index} 里真正发生了什么。",
                    "structure": "从来源事实推进到题目自己的判断。",
                    "body": (
                        f"这是题目 {index} 自己的连续口播正文。先把这条材料里的变化讲清楚，"
                        "再给出只属于它的判断和下一步，不借用另一题的现场。"
                    ),
                },
            }
        write_json(path, payload)
        return path

    def test_direct_writer_stage_contract_has_no_default_reference_authority(self):
        contract = load_writer_contract()
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(
            contract["topology"],
            "one_automation_codex_direct_writer_stage_per_selected_topic",
        )
        self.assertEqual(
            contract["skills"],
            ["austin-voice-scriptwriter"],
        )
        self.assertEqual(
            contract["input_scope"],
            [
                "one_same_run_rich_topic_card",
                "current_topic_raw_source_and_video_evidence",
                "simple_truthfulness_requirement",
                "simple_spoken_script_output",
            ],
        )
        self.assertNotIn("private_authority", contract)
        self.assertEqual(contract["previous_topic_body"], "forbidden")
        self.assertEqual(contract["other_topic_identity"], "forbidden")
        self.assertEqual(contract["editorial_batch_deliberation"], "forbidden")
        self.assertEqual(contract["recursive_model_execution"], "forbidden")
        self.assertEqual(contract["raw_text_persistence"], "forbidden")
        packet = {
            "action": "scripts_required",
            "topic_input": {
                "writing_contract": contract,
                "current_topic_only": True,
            },
        }
        sanitized = sanitize_handoff({
            **packet,
            "reference_selection": {"private_case_id": "should-be-removed"},
            "private_case_catalog": ["should-be-removed"],
        })
        encoded = json.dumps(sanitized, ensure_ascii=False)
        self.assertNotIn("austin-no-overtime-scripting", encoded)
        self.assertNotIn("reference_selection", encoded)
        self.assertNotIn("private_case_catalog", encoded)
        self.assertNotIn("voice_pack", encoded)
        self.assertNotIn("editing_reference", encoded)
        self.assertNotIn("semantic_reread", encoded)

        self.assertEqual(
            json.loads(
                (ROOT / "config" / "web010_single_daily_workflow_release.json")
                .read_text(encoding="utf-8")
            )["runtimeTopology"],
            ["collection_enrichment", "editorial", "scripts"],
        )
        release_protocol = "\n".join(
            json.loads(
                (ROOT / "config" / "web010_single_daily_workflow_release.json")
                .read_text(encoding="utf-8")
            )["externalSchedule"]["outerAgentProtocol"]
        )
        self.assertIn("directly applies austin-voice-scriptwriter", release_protocol)
        self.assertIn("raw source/video material", release_protocol)
        self.assertIn("only the current rich Topic Card", release_protocol)
        self.assertNotIn("full original user materials", release_protocol)
        self.assertNotIn("web010_austin_private_context_allowlist", release_protocol)
        self.assertNotIn("legacy private Skill files", release_protocol)
        self.assertIn("stay truthful about Austin/client/team tests and results", release_protocol)
        self.assertIn("compose the complete body before filling title/hook/structure", release_protocol)
        self.assertIn("The controller owns order, checkpoint, validation and publisher", release_protocol)
        self.assertNotIn("Seedance", release_protocol)
        self.assertNotIn("candidate-specific reason", release_protocol)
        self.assertNotIn("silent fact limits", release_protocol)
        self.assertNotIn("selection reason", release_protocol)
        self.assertNotIn("source verification", release_protocol)
        self.assertNotIn("evidence level", release_protocol)
        self.assertNotIn("claim provenance", release_protocol)
        self.assertNotIn("austin-no-overtime-scripting", release_protocol)

        source = (ROOT / "scripts" / "run_daily_workflow.py").read_text(encoding="utf-8")
        self.assertNotIn("--script-reference-selection-file", source)
        self.assertNotIn("set_reference_selection", source)
        self.assertNotIn("austin-no-overtime-scripting", source)
        runtime = (ROOT / "scripts" / "spoken_script_runtime.py").read_text(encoding="utf-8")
        for forbidden in (
            "load_austin_authority",
            "_context_allowlist",
            "AUSTIN_PRIVATE_CONTEXT_ALLOWLIST",
            "AUSTIN_PRIVATE_REFERENCE_ROOT",
            "AUSTIN_CASE_REFERENCE_FILE",
            "AUSTIN_APPROVED_SCRIPT_MVP",
            "AUSTIN_APPROVED_SCRIPT_DIRECTOR",
        ):
            self.assertNotIn(forbidden, runtime)
        self.assertFalse((ROOT / "config" / "web010_austin_private_context_allowlist.json").exists())

    def test_default_writer_does_not_read_private_sources_or_environment_paths(self):
        topic = {
            "topic_id": "topic-default-only",
            "source_evidence": {
                "source": {"title": "same-run source"},
                "video": {
                    "asr": "spoken evidence",
                    "ocr": ["visible evidence"],
                    "keyframes": [{"timestamp_ms": 1200, "observation": "visual evidence"}],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "production_context.md"
            sentinel.write_text("PRIVATE_TEMPLATE_SENTINEL", encoding="utf-8")
            env = {
                "AUSTIN_PRIVATE_CONTEXT_ALLOWLIST": str(root / "allowlist.json"),
                "AUSTIN_PRIVATE_REFERENCE_ROOT": str(root),
                "AUSTIN_CASE_REFERENCE_FILE": str(root / "full_topic_cards.json"),
                "AUSTIN_APPROVED_SCRIPT_MVP": str(root / "mvp.md"),
                "AUSTIN_APPROVED_SCRIPT_DIRECTOR": str(root / "director.md"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch("builtins.open", side_effect=AssertionError("private source read")):
                    contract = load_writer_contract()
                    packet = topic_packet(
                        RUN_ID, BUSINESS_DATE, topic, 0, 1, 0, contract,
                    )
            self.assertEqual(
                contract["input_scope"],
                [
                    "one_same_run_rich_topic_card",
                    "current_topic_raw_source_and_video_evidence",
                    "simple_truthfulness_requirement",
                    "simple_spoken_script_output",
                ],
            )
            encoded = json.dumps(packet, ensure_ascii=False)
            self.assertNotIn("PRIVATE_TEMPLATE_SENTINEL", encoded)
            self.assertNotIn("allowlist", encoded)
            self.assertNotIn("private_context", encoded)
            self.assertEqual(packet["topic_input"]["topic_id"], "topic-default-only")
            self.assertEqual(
                packet["selected_topics"][0]["source_evidence"],
                topic["source_evidence"],
            )

    def test_retired_checkpoint_contract_cannot_restore_legacy_writer_context(self):
        topic = {
            "topic_id": "topic-legacy",
            "source": {"url": "https://example.test/topic-legacy"},
            "source_facts": {"details": "same-run fact"},
            "fact_boundary": "do not invent results",
            "cannot_claim": None,
        }
        current = load_writer_contract()
        checkpoint = new_checkpoint(
            RUN_ID, BUSINESS_DATE, [topic], current,
        )
        checkpoint["writing_contract"] = {
            "schema_version": 1,
            "topology": "one_fresh_bounded_writer_child_per_selected_topic",
            "skills": ["austin-no-overtime-scripting", "austin-voice-scriptwriter"],
        }
        with self.assertRaisesRegex(WorkflowConflict, "scripts_checkpoint_writing_contract_conflict"):
            validate_checkpoint(checkpoint, RUN_ID, BUSINESS_DATE, [topic], current)
        checkpoint = new_checkpoint(
            RUN_ID, BUSINESS_DATE, [topic], current,
        )
        checkpoint["private_authority"] = "legacy/private_runtime.json"
        with self.assertRaisesRegex(WorkflowConflict, "scripts_checkpoint_private_context_retired"):
            validate_checkpoint(checkpoint, RUN_ID, BUSINESS_DATE, [topic], current)
        packet = topic_packet(
            RUN_ID, BUSINESS_DATE, topic, 0, 1, 0, current,
        )
        self.assertNotIn("writing_contract", packet)

    def test_public_per_topic_checkpoint_resume_and_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self.fixture(root)
            config = self.publisher_config(root)
            command = self.command(root, fixture)
            normalized = enrich(
                SimpleNamespace(
                    run_id=RUN_ID,
                    business_date=BUSINESS_DATE,
                    video_mode="disabled",
                    qa_frozen_packages=None,
                ),
                json.loads(fixture.read_text(encoding="utf-8")),
            )
            topic_ids = [row["candidate_id"] for row in normalized["candidates"]]
            editorial = self.editorial_file(root, topic_ids)

            first = self.execute(command, config)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertEqual(last_json(first.stdout)["action"], "editorial_required")

            editorial_stage_call = self.execute(
                command + ["--editorial-result-file", str(editorial)],
                config,
            )
            self.assertEqual(editorial_stage_call.returncode, 0, editorial_stage_call.stderr + editorial_stage_call.stdout)
            self.assertEqual(last_json(editorial_stage_call.stdout)["action"], "scripts_required")

            workflow = DailyWorkflow(root / f"{RUN_ID}.sqlite3")
            collection_stage = workflow.stage(RUN_ID, "collection_enrichment")
            editorial_stage = workflow.stage(RUN_ID, "editorial")
            scripts_stage = workflow.stage(RUN_ID, "scripts")
            self.assertIsNotNone(collection_stage)
            self.assertIsNotNone(editorial_stage)
            self.assertEqual(scripts_stage["status"], "in_progress")
            all_handoff = build_scripts_handoff(
                RUN_ID,
                BUSINESS_DATE,
                collection_stage["payload"],
                editorial_stage["payload"],
            )
            selected_topics = all_handoff["selected_topics"]
            contract = load_writer_contract()
            first_packet = topic_packet(
                RUN_ID,
                BUSINESS_DATE,
                selected_topics[0],
                0,
                len(selected_topics),
                len(scripts_stage["payload"]["completed_items"]),
                contract,
            )
            first_submission = self.submission_file(root, first_packet, 0)

            second = self.execute(
                command + [
                    "--editorial-result-file", str(editorial),
                    "--script-item-file", str(first_submission),
                ],
                config,
            )
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            handoff_path = root / "runs" / RUN_ID / "workflow_handoff.json"
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertEqual(handoff["action"], "scripts_required")
            self.assertEqual(len(handoff["selected_topics"]), 1)
            self.assertEqual(handoff["topic_index"], 1)
            self.assertNotIn(topic_ids[2], json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("writing_contract", handoff)
            self.assertNotIn("writing_phases", handoff["topic_input"])
            self.assertEqual(
                handoff["topic_input"]["writer_owns_final_fields"],
                ["body", "title", "hook", "structure"],
            )
            self.assertNotIn("PRIVATE_PERSONA", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("reference_selection", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("full_body_injection", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("private_case_routing", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("editing_reference", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn('"austin_authority_read":', json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn('"austin_private_context":', json.dumps(handoff, ensure_ascii=False))

            for index, topic_id in enumerate(topic_ids[1:], start=1):
                handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                self.assertEqual(handoff["action"], "scripts_required")
                self.assertEqual(handoff["selected_topics"][0]["topic_id"], topic_id)
                submission = self.submission_file(root, handoff, index)
                result = self.execute(
                    command + ["--editorial-result-file", str(editorial), "--script-item-file", str(submission)],
                    config,
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                if index < len(topic_ids) - 1:
                    next_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                    self.assertEqual(next_handoff["action"], "scripts_required")
                    self.assertEqual(next_handoff["topic_index"], index + 1)
                    self.assertNotIn(topic_id, json.dumps(next_handoff, ensure_ascii=False))

            terminal = last_json(result.stdout)
            self.assertEqual(terminal["action"], "completed")
            self.assertEqual(terminal["script_count"], 3)
            self.assertEqual(Publisher.posts, 1)
            stage = DailyWorkflow(root / f"{RUN_ID}.sqlite3").stage(RUN_ID, "scripts")
            self.assertEqual(stage["status"], "completed")
            self.assertEqual(
                [row["topic_id"] for row in stage["payload"]["scripts"]], topic_ids,
            )
            before = (root / f"{RUN_ID}.sqlite3").read_bytes()
            posts = Publisher.posts
            replay = self.execute(command, config)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            self.assertEqual(last_json(replay.stdout)["action"], "noop")
            self.assertEqual(Publisher.posts, posts)
            self.assertEqual((root / f"{RUN_ID}.sqlite3").read_bytes(), before)

    def test_sanitize_removes_transient_private_text_if_a_caller_supplies_it(self):
        sanitized = sanitize_handoff({
            "action": "scripts_required",
            "topic_input": {
                "austin_private_context_raw": "PRIVATE_CONTEXT_SENTINEL",
            },
        })
        self.assertNotIn("PRIVATE_CONTEXT_SENTINEL", json.dumps(sanitized, ensure_ascii=False))
        self.assertNotIn("austin_private_context_raw", sanitized["topic_input"])

    def test_material_insufficiency_is_item_local_and_terminal(self):
        run_id = "run_20260808_120001"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            production_shaped_cwd = root / "production-shaped-checkout"
            production_default = production_shaped_cwd / "output" / "state" / "website_publisher.json"
            write_json(production_default, {
                "website_url": f"http://127.0.0.1:{self.server.server_port}",
                "authority_identity": "owner-only:production-shaped-default",
                "app_bearer": "production-shaped-test-bearer",
                "sites_bearer": "production-shaped-test-bypass-bearer",
            })
            qa_private_missing = root / "qa-private" / "publisher.json"
            self.assertTrue(production_default.is_file())
            self.assertFalse(qa_private_missing.exists())
            fixture = self.fixture(root, run_id=run_id, count=1)
            command = self.command(root, fixture, run_id=run_id)
            normalized = enrich(
                SimpleNamespace(
                    run_id=run_id,
                    business_date=BUSINESS_DATE,
                    video_mode="disabled",
                    qa_frozen_packages=None,
                ),
                json.loads(fixture.read_text(encoding="utf-8")),
            )
            ids = [row["candidate_id"] for row in normalized["candidates"]]
            editorial = self.editorial_file(root, ids, run_id=run_id)
            first = self.execute(command, qa_private_missing, cwd=production_shaped_cwd)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertEqual(last_json(first.stdout)["action"], "editorial_required")
            editorial_stage_call = self.execute(
                command + ["--editorial-result-file", str(editorial)],
                qa_private_missing,
                cwd=production_shaped_cwd,
            )
            self.assertEqual(editorial_stage_call.returncode, 0, editorial_stage_call.stderr + editorial_stage_call.stdout)
            self.assertEqual(last_json(editorial_stage_call.stdout)["action"], "scripts_required")
            workflow = DailyWorkflow(root / f"{run_id}.sqlite3")
            collection_stage = workflow.stage(run_id, "collection_enrichment")
            editorial_stage = workflow.stage(run_id, "editorial")
            scripts_stage = workflow.stage(run_id, "scripts")
            all_handoff = build_scripts_handoff(
                run_id,
                BUSINESS_DATE,
                collection_stage["payload"],
                editorial_stage["payload"],
            )
            contract = load_writer_contract()
            handoff = topic_packet(
                run_id,
                BUSINESS_DATE,
                all_handoff["selected_topics"][0],
                0,
                1,
                len(scripts_stage["payload"]["completed_items"]),
                contract,
            )
            submission = self.submission_file(root, handoff, 0, failure=True)
            terminal = self.execute(
                command + ["--editorial-result-file", str(editorial), "--script-item-file", str(submission)],
                qa_private_missing,
                cwd=production_shaped_cwd,
            )
            self.assertEqual(terminal.returncode, 0, terminal.stderr + terminal.stdout)
            self.assertEqual(last_json(terminal.stdout)["action"], "completed_publish_pending")
            self.assertEqual(Publisher.posts, 0)
            self.assertEqual(Publisher.gets, 0)
            stage = DailyWorkflow(root / f"{run_id}.sqlite3").stage(run_id, "scripts")
            self.assertEqual(stage["status"], "completed_with_failures")
            self.assertEqual(
                stage["payload"]["failures"][0]["reason"],
                "material_or_angle_insufficiency",
            )

    def test_selector_cli_is_removed_from_normal_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [
                sys.executable,
                str(ROOT / "scripts" / "run_daily_workflow.py"),
                "--business-date", BUSINESS_DATE,
                "--workflow-db", str(root / "workflow.sqlite3"),
                "--artifact-root", str(root / "runs"),
                "--script-reference-selection-file", str(root / "selection.json"),
            ]
            result = self.execute(command, root / "qa-private" / "publisher.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
