from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from daily_workflow import DailyWorkflow

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

    def execute(self, command: list[str], config: Path):
        env = os.environ.copy()
        env["WEBSITE_PUBLISHER_CONFIG"] = str(config)
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
            first = self.execute(command, config)
            first_value = last_json(first.stdout)
            self.assertEqual(first_value["action"], "editorial_required")
            handoff = json.loads(
                (root / "runs" / run_id / "workflow_handoff.json").read_text()
            )
            identities = [row["candidate_id"] for row in handoff["candidates"]]
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
            second = self.execute(command + ["--editorial-result-file", str(editorial)], config)
            second_value = last_json(second.stdout)
            self.assertEqual(second_value["action"], "scripts_required")
            handoff = json.loads(
                (root / "runs" / run_id / "workflow_handoff.json").read_text()
            )
            selected_id = handoff["selected_topics"][0]["topic_id"]
            scripts = root / "scripts.json"
            write(scripts, {
                "packet_id": handoff["topic_input"]["packet_id"],
                "script": {
                    "topic_id": selected_id, "title": "稿件", "hook": "钩子",
                    "structure": "结构", "body": "完整正文",
                },
            })
            third = self.execute(command + [
                "--editorial-result-file", str(editorial),
                "--script-item-file", str(scripts),
            ], config)
            self.assertEqual(third.returncode, 0, third.stderr + third.stdout)
            result = last_json(third.stdout)
            self.assertEqual(result["action"], "completed")
            self.assertEqual(TerminalProjectionHandler.posts, 1)
            self.assertEqual(result["candidate_count"], 6)
            self.assertEqual(result["selected_count"], 1)
            self.assertEqual(result["script_count"], 1)
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

    def test_historical_one_shot_maps_180_rows_without_normal_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run_20260727_080141"
            run_dir.mkdir()
            fields = ["内容指纹", "平台", "内容标题", "内容链接", "正文/字幕/简介片段"]
            with (run_dir / "content_items.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows([
                    {"内容指纹": f"fp{i}", "平台": "AIHOT", "内容标题": f"内容{i}",
                     "内容链接": f"https://example.test/{i}", "正文/字幕/简介片段": "正文"}
                    for i in range(180)
                ])
            with (run_dir / "today_10_topics.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["内容指纹", "内容标题"])
                writer.writeheader()
                writer.writerow({"内容指纹": "fp0", "内容标题": "内容0"})
            log = root / "daily.json"
            write(log, {
                "run_id": "run_20260727_080141",
                "collection_status": "completed_with_failures",
                "downstream_usable": True, "run_output_dir": str(run_dir),
            })
            editorial = root / "editorial.json"
            write(editorial, {"run_id": "run_20260727_080141", "topics": [{
                "candidate_id": "legacy:fp0", "decision": "select", "title": "选题",
                "hook": "钩子", "structure": "结构", "selection_reason": "理由",
            }]})
            scripts = root / "scripts.json"
            write(scripts, {"run_id": "run_20260727_080141", "scripts": [{
                "topic_id": "legacy:fp0", "title": "稿件", "hook": "钩子",
                "structure": "结构", "body": "完整正文",
            }], "failures": []})
            env = os.environ.copy()
            env["WEBSITE_PUBLISHER_CONFIG"] = str(self.config(root))
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/recover_web010_historical.py"),
                "--run-dir", str(run_dir), "--daily-log", str(log),
                "--workflow-db", str(root / "workflow.sqlite3"),
                "--artifact-root", str(root / "artifacts"),
                "--editorial-result-file", str(editorial),
                "--scripts-result-file", str(scripts),
            ], text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            flow = DailyWorkflow(root / "workflow.sqlite3").read_run("run_20260727_080141")
            self.assertEqual(len([row for row in flow["items"] if row["status"] == "completed"]), 180)
            payload = TerminalProjectionHandler.payloads["run_20260727_080141"]
            self.assertEqual(
                (len(payload["collected_items"]), len(payload["topics"]), len(payload["scripts"])),
                (180, 1, 1),
            )
            normal_source = (ROOT / "scripts/run_daily_workflow.py").read_text()
            self.assertNotIn("内容指纹", normal_source)
            self.assertNotIn("recover_web010_historical", normal_source)


if __name__ == "__main__":
    unittest.main()
