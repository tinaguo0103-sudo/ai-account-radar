from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from daily_workflow import DailyWorkflow, WorkflowConflict
from spoken_script_runtime import (
    load_private_reference_library,
    load_private_style_context,
    load_voice_pack,
    reference_selector_handoff,
    sanitize_handoff,
    select_topic_references,
    topic_packet,
    validate_reference_selection,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "run_20260807_120000"
BUSINESS_DATE = "2026-08-07"


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

    def respond(self, value: dict) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def readback(self, payload: dict) -> dict:
        return {
            "ok": True,
            "status": "applied",
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
        self.respond(self.readback(payload))

    def do_GET(self):
        self.__class__.gets += 1
        run_id = self.path.split("run_id=", 1)[-1]
        payload = self.__class__.payloads.get(run_id)
        if payload is None:
            self.send_response(404)
            body = json.dumps({"error": "business_projection_missing"}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.respond({**self.readback(payload), "status": "readback"})


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

    def test_voice_authority_is_topic_matched_and_private_context_stays_transient(self):
        pack = load_voice_pack()
        self.assertEqual(
            [row["exemplar_id"] for row in pack["exemplars"]],
            [
                "run_20260805_080110:script:0f51191cbf21c6b2677f",
                "run_20260803_110453:trend:f6f8f499268476dcc550",
            ],
        )
        self.assertEqual(
            [hashlib.sha256(row["body"].encode("utf-8")).hexdigest() for row in pack["exemplars"]],
            [
                "61e7884e8dbba991310c7537f79f6816e7b4ab3d3bc3fe3b0ba55b71382be570",
                "e1b6b5246c7ef9cb833d71a3eed5a2406d840944ab9a0e1abf5d3c7e095f38be",
            ],
        )
        joined = json.dumps(pack["exemplars"], ensure_ascii=False)
        self.assertNotIn("一个人做账号，最该自动化的不是写文案", joined)

        context = load_private_style_context()
        self.assertTrue(context["loaded"])
        self.assertEqual(context["loaded_source_count"], 5)
        self.assertFalse(context["raw_content_embedded"])
        self.assertNotIn("derived_style_cues", json.dumps(context, ensure_ascii=False))
        library = load_private_reference_library()
        self.assertEqual(len(library["private_case_catalog"]), 7)
        self.assertEqual(
            [row["reference_id"] for row in library["references"] if row["role"] == "private_case"],
            [f"private_anchor:{index:02d}" for index in range(1, 8)],
        )
        self.assertTrue(all(
            row.get("text") and row.get("excerpt_sha256")
            for row in library["references"]
            if row["role"] == "private_case"
        ))
        self.assertTrue(all(
            row.get("source_roles") and row.get("source_hashes")
            for row in library["references"]
            if row["role"] == "private_case"
        ))
        self.assertNotIn("match_terms", json.dumps(library, ensure_ascii=False))
        with self.assertRaises(WorkflowConflict):
            validate_reference_selection(
                {"topic_id": "trend:voice-authority"},
                pack,
                {
                    "topic_id": "trend:voice-authority",
                    "approved_exemplar_id": None,
                    "private_case_id": "private_anchor:99",
                    "persona_id": None,
                    "reason": "not a catalog identity",
                },
                library,
            )
        selector = reference_selector_handoff(
            RUN_ID,
            BUSINESS_DATE,
            {
                "topic_id": "trend:voice-authority",
                "title": "AI视频导演交付验收",
                "summary": "镜头、成片、返修与交付",
            },
            0,
            1,
            0,
            pack,
            library,
        )
        selector_text = json.dumps(selector, ensure_ascii=False)
        self.assertEqual(selector["action"], "script_reference_selection_required")
        self.assertNotIn("match_terms", selector_text)
        self.assertNotIn("一个人做账号，最该自动化的不是写文案", selector_text)
        self.assertNotIn(pack["exemplars"][0]["body"], selector_text)
        selection = {
            "topic_id": "trend:voice-authority",
            "approved_exemplar_id": pack["exemplars"][1]["exemplar_id"],
            "private_case_id": "private_anchor:04",
            "persona_id": None,
            "reason": "同样面对交付失控，但这里的中心冲突是镜头责任如何回到创作者，适合借鉴验收动作而不是复用题目事实。",
        }
        packet = topic_packet(
            RUN_ID,
            BUSINESS_DATE,
            {
                "topic_id": "trend:voice-authority",
                "title": "AI视频导演交付验收",
                "summary": "镜头、成片、返修与交付",
            },
            0,
            1,
            0,
            pack,
            selection,
            private_library=library,
        )
        self.assertEqual(packet["private_style_context"], context)
        self.assertNotIn("voice_pack", packet)
        self.assertFalse(packet["voice_pack_contract"]["embedded_content"])
        self.assertFalse(packet["voice_pack_contract"]["shared_full_text_pack"])
        self.assertLessEqual(packet["voice_pack_contract"]["approved_full_script_count"], 1)
        self.assertLessEqual(packet["voice_pack_contract"]["private_excerpt_count"], 2)
        self.assertFalse(packet["voice_pack_contract"]["rejected_system_scripts_included"])
        transient = packet["topic_input"]["reference_input"]
        self.assertTrue(transient["approved_full_scripts"] or transient["private_excerpts"])
        persisted = sanitize_handoff(packet)
        self.assertNotIn("voice_pack", persisted)
        self.assertFalse(persisted["topic_input"]["reference_input"]["raw_text_persisted"])
        self.assertEqual(
            len(persisted["reference_selection"]["private_excerpt_hashes"]),
            len(persisted["reference_selection"]["private_excerpt_ids"]),
        )
        persisted_text = json.dumps(persisted, ensure_ascii=False)
        self.assertEqual(
            set(persisted["topic_input"]["reference_input"]["approved_full_scripts"][0]),
            {"exemplar_id", "body_sha256", "selection_reason"},
        )
        self.assertEqual(
            set(persisted["topic_input"]["reference_input"]["private_excerpts"][0]),
            {"reference_id", "excerpt_sha256", "selection_reason"},
        )
        for row in transient["approved_full_scripts"]:
            self.assertNotIn(row["body"], persisted_text)
        for row in transient["private_excerpts"]:
            self.assertNotIn(row["text"], persisted_text)

        no_body = select_topic_references(
            {
                "topic_id": "trend:cover-automation",
                "title": "封面自动化最难的不是出图，是视觉规则",
            },
            pack,
            {
                "topic_id": "trend:cover-automation",
                "approved_exemplar_id": None,
                "private_case_id": None,
                "persona_id": None,
                "reason": "本题没有需要借用的已批准完整稿或私案例子，保持事实和判断由当前 Topic Card 驱动。",
            },
            library,
        )
        self.assertEqual(no_body["approved_full_scripts"], [])
        self.assertEqual(no_body["private_excerpts"], [])
        self.assertEqual(no_body["ledger"]["private_excerpt_hashes"], [])

    def fixture(self, root: Path) -> Path:
        content = []
        candidates = []
        for index in range(3):
            identity = f"douyin:{9000 + index}"
            content.append({
                "item_id": identity,
                "source_url": f"https://www.douyin.com/video/{9000 + index}",
                "title": f"同一批资料的第 {index} 个现场",
                "summary": "QA-private same-run content",
            })
            candidates.append({
                "candidate_id": identity,
                "item_id": identity,
                "source_url": f"https://www.douyin.com/video/{9000 + index}",
                "title": f"同一批资料的第 {index} 个现场",
                "summary": "QA-private same-run candidate",
            })
        path = root / "collection.json"
        write_json(path, {
            "run_id": RUN_ID,
            "business_date": BUSINESS_DATE,
            "status": "completed",
            "content_items": content,
            "candidates": candidates,
            "source_runs": [{"source": "QA-private", "status": "completed", "item_count": 3}],
        })
        self.editorial = root / "editorial.json"
        return path

    def command(self, root: Path, fixture: Path) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "run_daily_workflow.py"),
            "--run-id", RUN_ID,
            "--business-date", BUSINESS_DATE,
            "--workflow-db", str(root / "workflow.sqlite3"),
            "--artifact-root", str(root / "runs"),
            "--collection-fixture", str(fixture),
            "--video-mode", "disabled",
        ]

    def execute(self, command: list[str], config: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["WEBSITE_PUBLISHER_CONFIG"] = str(config)
        return subprocess.run(command, text=True, capture_output=True, env=environment)

    def submit_file(self, root: Path, handoff: dict, index: int) -> Path:
        topic = handoff["selected_topics"][0]
        path = root / f"submission-{index}.json"
        write_json(path, {
            "packet_id": handoff["topic_input"]["packet_id"],
            "voice_pack_sha256": handoff["topic_input"]["voice_pack_sha256"],
            "script": {
                "topic_id": topic["topic_id"],
                "title": topic["title"],
                "hook": topic["hook"],
                "structure": topic["structure"],
                "body": f"题目 {index} 的完整连续口播正文，包含现场、动作、判断和自然收束。",
            },
        })
        return path

    def selection_file(self, root: Path, handoff: dict, index: int) -> Path:
        topic = handoff["selected_topics"][0]
        path = root / f"selection-{index}.json"
        write_json(path, {
            "topic_id": topic["topic_id"],
            "approved_exemplar_id": None,
            "private_case_id": f"private_anchor:{index + 1:02d}",
            "persona_id": None,
            "reason": "当前题的中心冲突、责任后果和判断动作与该案例可对照，但案例正文不作为当前事实。",
        })
        return path

    def test_public_runtime_exposes_one_topic_and_resumes_without_regeneration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self.fixture(root)
            config = root / "publisher.json"
            write_json(config, {
                "website_url": f"http://127.0.0.1:{self.server.server_port}",
                "authority_identity": "qa-private:per-topic",
                "app_bearer": "runtime-only-test",
                "sites_bearer": "runtime-only-test",
            })
            command = self.command(root, fixture)
            first = self.execute(command, config)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            collection_handoff = json.loads(
                (root / "runs" / RUN_ID / "workflow_handoff.json").read_text(encoding="utf-8")
            )
            topic_ids = [row["candidate_id"] for row in collection_handoff["candidates"]]
            write_json(self.editorial, {
                "run_id": RUN_ID,
                "topics": [{
                    "candidate_id": identity,
                    "decision": "select",
                    "title": f"题目 {index}",
                    "hook": f"先看题目 {index} 的真实冲突。",
                    "structure": "现场 -> 动作 -> 判断",
                    "selection_reason": "该题自己的工作流冲突足以独立制作。",
                    "standalone_eligibility": {
                        "decision": "select",
                        "reason": "即使单独出现也有明确用户价值和独立判断。",
                    },
                    "decision_basis": {
                        "content": "有同一批资料的真实工作流冲突。",
                        "persona": "适合 Austin 的工作流复盘。",
                        "differentiation": "每题动作和后果不同。",
                    },
                    "unique_judgment": f"题目 {index} 的判断。",
                } for index, identity in enumerate(topic_ids)],
            })
            editorial = self.execute(command + ["--editorial-result-file", str(self.editorial)], config)
            self.assertEqual(editorial.returncode, 0, editorial.stderr + editorial.stdout)
            handoff_path = root / "runs" / RUN_ID / "workflow_handoff.json"
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertEqual(handoff["action"], "script_reference_selection_required")
            self.assertEqual(len(handoff["selected_topics"]), 1)
            self.assertEqual(handoff["topic_index"], 0)
            first_id = handoff["selected_topics"][0]["topic_id"]
            self.assertNotIn(topic_ids[1], json.dumps(handoff, ensure_ascii=False))
            self.assertEqual(len(handoff["topic_selector_input"]["private_case_catalog"]), 7)
            self.assertNotIn("match_terms", json.dumps(handoff, ensure_ascii=False))

            whole_batch = root / "whole-batch.json"
            write_json(whole_batch, {
                "run_id": RUN_ID,
                "scripts": [],
                "failures": [],
            })
            blocked_batch = self.execute(
                command + ["--scripts-result-file", str(whole_batch)], config,
            )
            self.assertEqual(blocked_batch.returncode, 2)
            self.assertIn(
                "whole_batch_scripts_submission_forbidden",
                json.loads(handoff_path.read_text(encoding="utf-8"))["error"],
            )

            selection = self.selection_file(root, handoff, 0)
            selected = self.execute(
                command + ["--script-reference-selection-file", str(selection)], config,
            )
            self.assertEqual(selected.returncode, 0, selected.stderr + selected.stdout)
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertEqual(handoff["action"], "scripts_required")
            self.assertNotIn("voice_pack", handoff)
            self.assertFalse(handoff["voice_pack_contract"]["embedded_content"])
            self.assertFalse(handoff["voice_pack_contract"]["shared_full_text_pack"])
            self.assertFalse(handoff["topic_input"]["reference_input"]["raw_text_persisted"])
            for row in handoff["topic_input"]["reference_input"]["approved_full_scripts"]:
                self.assertNotIn("body", row)
            for row in handoff["topic_input"]["reference_input"]["private_excerpts"]:
                self.assertNotIn("text", row)
            self.assertEqual(
                handoff["topic_input"]["voice_pack_content_bytes"],
                load_voice_pack()["content_bytes"],
            )

            wrong_topic = self.submit_file(root, handoff, 99)
            wrong_payload = json.loads(wrong_topic.read_text(encoding="utf-8"))
            wrong_payload["script"]["topic_id"] = topic_ids[1]
            write_json(wrong_topic, wrong_payload)
            blocked_topic = self.execute(
                command + ["--script-item-file", str(wrong_topic)], config,
            )
            self.assertEqual(blocked_topic.returncode, 2)
            self.assertIn(
                "script_topic_not_current",
                json.loads(handoff_path.read_text(encoding="utf-8"))["error"],
            )
            checkpoint_after_reject = DailyWorkflow(root / "workflow.sqlite3").stage(
                RUN_ID, "scripts",
            )
            self.assertEqual(checkpoint_after_reject["status"], "in_progress")
            self.assertEqual(checkpoint_after_reject["payload"]["completed_scripts"], [])
            resumed = self.execute(command, config)
            self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertEqual(handoff["action"], "scripts_required")
            self.assertEqual(len(handoff["selected_topics"]), 1)
            self.assertEqual(handoff["selected_topics"][0]["topic_id"], first_id)

            completed_ids = []
            for index in range(3):
                handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                if handoff["action"] == "script_reference_selection_required":
                    selection = self.selection_file(root, handoff, index)
                    selected = self.execute(
                        command + ["--script-reference-selection-file", str(selection)], config,
                    )
                    self.assertEqual(selected.returncode, 0, selected.stderr + selected.stdout)
                    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                self.assertEqual(handoff["action"], "scripts_required")
                self.assertEqual(len(handoff["selected_topics"]), 1)
                current_id = handoff["selected_topics"][0]["topic_id"]
                self.assertEqual(current_id, topic_ids[index])
                self.assertNotIn("voice_pack", handoff)
                self.assertFalse(handoff["voice_pack_contract"]["embedded_content"])
                completed_ids.append(current_id)
                submission = self.submit_file(root, handoff, index)
                result = self.execute(command + ["--script-item-file", str(submission)], config)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                if index < 2:
                    next_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                    self.assertEqual(next_handoff["topic_index"], index + 1)
                    self.assertEqual(next_handoff["action"], "script_reference_selection_required")
                    self.assertNotIn(current_id, json.dumps(next_handoff, ensure_ascii=False))
                    self.assertNotEqual(next_handoff["selected_topics"][0]["topic_id"], first_id if index == 0 else current_id)

            terminal = last_json(result.stdout)
            self.assertEqual(terminal["action"], "completed")
            self.assertEqual(terminal["script_count"], 3)
            self.assertEqual(
                Publisher.posts,
                1,
                DailyWorkflow(root / "workflow.sqlite3").read_run(RUN_ID),
            )
            stage = DailyWorkflow(root / "workflow.sqlite3").stage(RUN_ID, "scripts")
            self.assertEqual(stage["status"], "completed")
            self.assertEqual(
                [row["topic_id"] for row in stage["payload"]["scripts"]], completed_ids,
            )
            before = (root / "workflow.sqlite3").read_bytes()
            posts = Publisher.posts
            replay = self.execute(command, config)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            self.assertEqual(last_json(replay.stdout)["action"], "noop")
            self.assertEqual(Publisher.posts, posts)
            self.assertEqual((root / "workflow.sqlite3").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
