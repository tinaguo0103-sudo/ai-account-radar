from __future__ import annotations

import csv
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

from daily_workflow import DailyWorkflow
from run_daily_workflow import build_scripts_handoff, enrich
from spoken_script_runtime import load_author_edit_contract, topic_packet

ROOT = Path(__file__).resolve().parents[1]


class TerminalProjectionHandler(BaseHTTPRequestHandler):
    posts = 0
    gets = 0
    payloads: dict[str, dict] = {}

    def log_message(self, *_args):
        return

    def reply(self, code: int, value: dict):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def readback(self, payload: dict, status: str):
        return {
            "ok": True, "status": status, "run_id": payload["run_id"],
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
        self.reply(200, self.readback(payload, "applied"))

    def do_GET(self):
        self.__class__.gets += 1
        run_id = self.path.split("run_id=", 1)[-1]
        payload = self.__class__.payloads.get(run_id)
        if not payload:
            self.reply(404, {"error": "business_projection_missing"})
        else:
            self.reply(200, self.readback(payload, "readback"))


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def last_json(output: str) -> dict:
    return json.loads(output.strip().splitlines()[-1])


class PublicV2FlowTest(unittest.TestCase):
    def setUp(self):
        TerminalProjectionHandler.posts = 0
        TerminalProjectionHandler.gets = 0
        TerminalProjectionHandler.payloads = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TerminalProjectionHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def config(self, root: Path, port: int | None = None) -> Path:
        path = root / "publisher.json"
        write(path, {
            "website_url": f"http://127.0.0.1:{port or self.server.server_port}",
            "authority_identity": "qa-private:v2",
            "app_bearer": "runtime-only-test",
            "sites_bearer": "runtime-only-test",
        })
        return path

    def command(self, root: Path, run_id: str, fixture: Path) -> list[str]:
        return [
            sys.executable, str(ROOT / "scripts/run_daily_workflow.py"),
            "--run-id", run_id, "--business-date", "2026-07-28",
            "--workflow-db", str(root / "workflow.sqlite3"),
            "--artifact-root", str(root / "runs"),
            "--collection-fixture", str(fixture), "--video-mode", "disabled",
        ]

    def execute(self, command: list[str], config: Path, extra_env: dict[str, str] | None = None):
        env = os.environ.copy()
        env["WEBSITE_PUBLISHER_CONFIG"] = str(config)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(command, text=True, capture_output=True, env=env)

    def test_public_three_stage_single_publish_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260728_080000"
            fixture = root / "collection.json"
            content = [
                {"aweme_id": str(7000 + i), "source": "Douyin",
                 "source_url": f"https://www.douyin.com/video/{7000+i}",
                 "title": f"AI {i}", "summary": "真实冻结理解包"}
                for i in range(6)
            ]
            write(fixture, {
                "run_id": run_id, "business_date": "2026-07-28",
                "content_items": content,
                "candidates": [
                    {"candidate_id": f"douyin:{7000+i}", "title": f"AI {i}"}
                    for i in range(6)
                ],
                "source_runs": [{"source": "Douyin", "status": "completed", "item_count": 6}],
            })
            command = self.command(root, run_id, fixture)
            config = self.config(root)
            normalized = enrich(
                SimpleNamespace(
                    run_id=run_id,
                    business_date="2026-07-28",
                    video_mode="disabled",
                    qa_frozen_packages=None,
                ),
                json.loads(fixture.read_text(encoding="utf-8")),
            )
            identities = [row["candidate_id"] for row in normalized["candidates"]]
            editorial = root / "editorial.json"
            write(editorial, {
                "run_id": run_id, "topics": [{
                    "candidate_id": identities[0], "decision": "select",
                    "title": "选题", "hook": "钩子", "structure": "结构",
                    "selection_reason": "理由",
                }] + [{
                    "candidate_id": identity, "decision": "observe",
                    "selection_reason": "未达到本轮选择标准",
                } for identity in identities[1:]],
            })
            first = self.execute(
                command + ["--editorial-result-file", str(editorial)],
                config,
                {"WEB010_INJECT_WRITER_FAILURE_TOPIC": identities[0]},
            )
            self.assertEqual(first.returncode, 2, first.stderr + first.stdout)
            self.assertEqual(last_json(first.stdout)["action"], "child_failed_recoverable")
            workflow = DailyWorkflow(root / "workflow.sqlite3")
            collection_stage = workflow.stage(run_id, "collection_enrichment")
            editorial_stage = workflow.stage(run_id, "editorial")
            scripts_stage = workflow.stage(run_id, "scripts")
            all_handoff = build_scripts_handoff(
                run_id, "2026-07-28", collection_stage["payload"], editorial_stage["payload"],
            )
            handoff = topic_packet(
                run_id, "2026-07-28", all_handoff["selected_topics"][0], 0, 1,
                len(scripts_stage["payload"]["completed_items"]), load_author_edit_contract(),
            )
            scripts = root / "scripts.json"
            write(scripts, {
                "packet_id": handoff["topic_input"]["packet_id"],
                "script": {
                    "topic_id": identities[0], "title": "稿件", "hook": "钩子",
                    "structure": "结构", "body": "完整正文",
                },
            })
            second = self.execute(command + [
                "--editorial-result-file", str(editorial),
                "--script-item-file", str(scripts),
            ], config)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            first_value = last_json(second.stdout)
            self.assertEqual(first_value["action"], "completed")
            self.assertEqual(first_value["selected_count"], 1)
            self.assertEqual(first_value["script_count"], 1)
            self.assertEqual(TerminalProjectionHandler.posts, 1)
            self.assertEqual(first_value["candidate_count"], 6)
            before = (root / "workflow.sqlite3").read_bytes()
            post_count = TerminalProjectionHandler.posts
            replay = self.execute(command, config)
            self.assertEqual(last_json(replay.stdout)["action"], "noop")
            self.assertEqual((root / "workflow.sqlite3").read_bytes(), before)
            self.assertEqual(TerminalProjectionHandler.posts, post_count)

    def test_offline_is_terminal_pending_and_replay_only_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260728_080000"
            fixture = root / "collection.json"
            write(fixture, {
                "run_id": run_id, "business_date": "2026-07-28",
                "content_items": [], "candidates": [], "source_runs": [],
            })
            offline = self.config(root, 1)
            command = self.command(root, run_id, fixture)
            first = self.execute(command, offline)
            self.assertEqual(first.returncode, 0)
            result = last_json(first.stdout)
            self.assertEqual(result["action"], "completed_publish_pending")
            self.assertEqual(result["status"], "completed_empty")
            self.assertEqual(result["publish_status"], "pending")
            before_stages = DailyWorkflow(root / "workflow.sqlite3").read_run(run_id)["stages"]
            recovered = self.execute(command, self.config(root))
            value = last_json(recovered.stdout)
            self.assertEqual(value["action"], "noop")
            self.assertEqual(value["publish_status"], "applied")
            self.assertEqual(
                DailyWorkflow(root / "workflow.sqlite3").read_run(run_id)["stages"],
                before_stages,
            )
            self.assertEqual(TerminalProjectionHandler.posts, 1)

    def test_historical_adapter_is_not_reconnected_to_normal_branch(self):
        normal_source = (ROOT / "scripts/run_daily_workflow.py").read_text()
        release = json.loads(
            (ROOT / "config/web010_single_daily_workflow_release.json").read_text()
        )
        protocol = "\n".join(release["externalSchedule"]["outerAgentProtocol"])
        self.assertNotIn("recover_web010_historical", normal_source)
        self.assertNotIn("historical/latest", protocol)
        self.assertIn("historical adapter", release["normalRuntimeForbiddenCalls"])


if __name__ == "__main__":
    unittest.main()
