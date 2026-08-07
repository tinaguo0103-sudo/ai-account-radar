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

from daily_workflow import DailyWorkflow
from spoken_script_runtime import load_private_style_context, load_voice_pack, topic_packet


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

    def test_voice_authority_is_the_two_approved_bodies_and_private_context_is_loaded(self):
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
        packet = topic_packet(
            RUN_ID,
            BUSINESS_DATE,
            {"topic_id": "trend:voice-authority", "title": "QA-private"},
            0,
            1,
            0,
            pack,
        )
        self.assertEqual(packet["private_style_context"], context)
        self.assertEqual(packet["voice_pack"], pack["exemplars"])
        self.assertEqual(packet["voice_pack_contract"]["positive_authority"], "two_user_approved_full_bodies")
        self.assertFalse(packet["voice_pack_contract"]["rejected_system_scripts_included"])

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
            self.assertEqual(handoff["action"], "scripts_required")
            self.assertEqual(len(handoff["selected_topics"]), 1)
            self.assertEqual(handoff["topic_index"], 0)
            self.assertEqual(handoff["voice_pack"], load_voice_pack()["exemplars"])
            self.assertTrue(handoff["voice_pack_contract"]["embedded_content"])
            self.assertEqual(
                handoff["topic_input"]["voice_pack_content_bytes"],
                load_voice_pack()["content_bytes"],
            )
            first_id = handoff["selected_topics"][0]["topic_id"]
            self.assertNotIn(topic_ids[1], json.dumps(handoff, ensure_ascii=False))

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
            self.assertEqual(len(handoff["selected_topics"]), 1)
            self.assertEqual(handoff["selected_topics"][0]["topic_id"], first_id)

            completed_ids = []
            for index in range(3):
                handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                self.assertEqual(len(handoff["selected_topics"]), 1)
                current_id = handoff["selected_topics"][0]["topic_id"]
                self.assertEqual(current_id, topic_ids[index])
                self.assertEqual(handoff["voice_pack"], load_voice_pack()["exemplars"])
                completed_ids.append(current_id)
                submission = self.submit_file(root, handoff, index)
                result = self.execute(command + ["--script-item-file", str(submission)], config)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                if index < 2:
                    next_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                    self.assertEqual(next_handoff["topic_index"], index + 1)
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
