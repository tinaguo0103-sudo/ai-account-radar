from __future__ import annotations

import csv
import hashlib
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


class ProjectionHandler(BaseHTTPRequestHandler):
    projections: dict[str, dict] = {}
    requests: list[tuple[str, str]] = []

    def log_message(self, *_args):
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.__class__.requests.append(("POST", self.path))
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length))
        existing = self.__class__.projections.get(payload["run_id"])
        if existing and existing["revision"] >= payload["revision"]:
            self._send(409, {"error": "business_projection_conflict"})
            return
        self.__class__.projections[payload["run_id"]] = payload
        self._send(200, self._readback(payload, "applied"))

    def do_GET(self):
        self.__class__.requests.append(("GET", self.path))
        run_id = self.path.split("run_id=", 1)[-1]
        payload = self.__class__.projections.get(run_id)
        if not payload:
            self._send(404, {"error": "business_projection_missing"})
            return
        self._send(200, self._readback(payload, "readback"))

    @staticmethod
    def _readback(payload: dict, status: str) -> dict:
        return {
            "ok": True,
            "status": status,
            "run_id": payload["run_id"],
            "revision": payload["revision"],
            "payload_sha256": payload["payload_sha256"],
            "authority_identity": payload["authority_identity"],
            "counts": {
                "content": len(payload["collected_items"]),
                "topics": len(payload["topics"]),
                "scripts": len(payload["scripts"]),
            },
        }


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class CompatibilityPublicFlowTest(unittest.TestCase):
    def setUp(self):
        ProjectionHandler.projections = {}
        ProjectionHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ProjectionHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def fake_codex(self, root: Path) -> Path:
        target = root / "fake-codex"
        target.write_text(
            """#!/usr/bin/env python3
import json, sys
from pathlib import Path
prompt = sys.stdin.read()
out = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
topic = "douyin:7001" if "douyin:7001" in prompt else "c1"
if "editorial_selection" in prompt:
    value = {"run_id": "exact", "topics": [{"candidate_id": topic, "decision": "select",
      "title": "选题", "hook": "钩子", "structure": "结构", "selection_reason": "理由"}]}
else:
    value = {"run_id": "exact", "topic_id": topic, "title": "脚本",
      "hook": "钩子", "structure": "结构", "body": "完整口播稿"}
out.write_text(json.dumps(value, ensure_ascii=False))
""",
            encoding="utf-8",
        )
        target.chmod(0o755)
        return target

    def environment(self, root: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "CODEX_BIN": str(self.fake_codex(root)),
            "WEBSITE_PROJECTION_BEARER": "qa-app",
            "WEBSITE_PROJECTION_SIWC_BYPASS_BEARER": "qa-machine",
            "PYTHONPYCACHEPREFIX": str(root / "pycache"),
        })
        return env

    def base_command(self, root: Path, run_id: str, date: str) -> list[str]:
        return [
            sys.executable, str(ROOT / "scripts/run_daily_workflow.py"),
            "--run-id", run_id, "--business-date", date,
            "--source-revision", "3", "--source-db", str(root / "source.sqlite3"),
            "--workflow-db", str(root / "workflow.sqlite3"),
            "--artifact-root", str(root / "runs"),
            "--publisher-url", f"http://127.0.0.1:{self.server.server_port}",
            "--publisher-identity", "qa-private:compat",
        ]

    def test_new_four_stage_public_flow_and_replay_are_exact(self):
        with tempfile.TemporaryDirectory(prefix="rel_ar050_web010_new_") as tmp:
            root = Path(tmp)
            run_id = "run_20260728_080000"
            candidate = {
                "run_id": run_id, "aweme_id": "7001",
                "source_url": "https://www.douyin.com/video/7001",
                "author": "qa", "title": "AI workflow", "published_at": "2026-07-28",
                "duration_seconds": 60, "discovery_source": "dynamic_search",
                "likes": 10, "comments": 1, "favorites": 2, "shares": 1,
                "raw_identity": "raw-7001",
            }
            collection = {
                "run_id": run_id, "business_date": "2026-07-28", "status": "completed",
                "content_items": [{
                    "id": "douyin:7001", "content_fingerprint": "douyin:7001",
                    "source": "Douyin", "title": "AI workflow",
                    "source_url": candidate["source_url"],
                }],
                "candidates": [{"candidate_id": "douyin:7001", "title": "AI workflow"}],
                "source_runs": [{"source": "Douyin", "status": "completed", "item_count": 1}],
            }
            package = {
                "run_id": run_id, "aweme_id": "7001",
                "source_url": candidate["source_url"], "status": "completed",
                "caption_timeline": [{"start": 0, "end": 1, "text": "字幕", "frame_sha256": "f"}],
                "asr": {"primary_model": "SenseVoiceSmall+FSMN-VAD", "fills": []},
                "screen_text": [{"kind": "prompt", "text": "提示", "start": 0, "verified": True}],
                "keyframes": [{"start": 0, "sha256": "k", "path": "keyframes/k.jpg"}],
                "unresolved_terms": [], "failures": [], "temporary_media_remaining": 0,
            }
            for name, value in {
                "collection.json": collection,
                "candidates.json": [[candidate]],
                "decisions.json": [{"candidate_id": "douyin:7001", "selected": True,
                                    "reasons": ["title_value"]}],
                "packages.json": [package],
            }.items():
                write_json(root / name, value)
            command = self.base_command(root, run_id, "2026-07-28") + [
                "--collection-fixture", str(root / "collection.json"),
                "--video-mode", "qa-fixture",
                "--video-candidates", str(root / "candidates.json"),
                "--video-decisions", str(root / "decisions.json"),
                "--video-packages", str(root / "packages.json"),
            ]
            first = subprocess.run(
                command, text=True, capture_output=True, env=self.environment(root),
            )
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            flow = DailyWorkflow(root / "workflow.sqlite3")
            readback = flow.read_run(run_id)
            self.assertEqual(
                [row["stage"] for row in readback["stages"]],
                ["collection", "video_understanding", "editorial", "scripts"],
            )
            self.assertEqual(len(readback["projection_receipts"]), 4)
            before = (root / "workflow.sqlite3").read_bytes()
            request_count = len(ProjectionHandler.requests)
            second = subprocess.run(
                command, text=True, capture_output=True, env=self.environment(root),
            )
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            self.assertEqual((root / "workflow.sqlite3").read_bytes(), before)
            self.assertEqual(len(ProjectionHandler.requests), request_count)

    def test_v1_exact_artifact_recovery_has_no_video_or_collection_calls(self):
        with tempfile.TemporaryDirectory(prefix="rel_ar050_web010_v1_") as tmp:
            root = Path(tmp)
            run_id = "run_20260727_080141"
            run_dir = root / "runs" / run_id
            write_csv(run_dir / "content_items.csv", [{
                "id": "c1", "content_fingerprint": "c1", "source": "AIHOT",
                "title": "历史精确内容", "source_url": "https://example.test/c1",
            }])
            write_csv(run_dir / "today_10_topics.csv", [{
                "candidate_id": "c1", "title": "历史精确内容",
            }])
            daily_log = root / "daily.json"
            write_json(daily_log, {
                "run_id": run_id, "collection_status": "completed_with_failures",
                "downstream_usable": True, "run_output_dir": str(run_dir),
                "source_outcomes": [{"source": "AIHOT", "status": "completed", "item_count": 1}],
            })
            marker = root / "forbidden-video-runtime.json"
            command = self.base_command(root, run_id, "2026-07-27") + [
                "--recover-daily-log", str(daily_log),
            ]
            legacy = DailyWorkflow(root / "workflow.sqlite3")
            legacy.begin(
                run_id, "2026-07-27", 3, "released-v1-contract",
                stage_plan=("collection", "editorial", "scripts"),
            )
            legacy.db.close()
            result = subprocess.run(
                command, text=True, capture_output=True, env=self.environment(root),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            readback = DailyWorkflow(root / "workflow.sqlite3").read_run(run_id)
            self.assertEqual(
                [row["stage"] for row in readback["stages"]],
                ["collection", "editorial", "scripts"],
            )
            self.assertFalse(marker.exists())
            self.assertEqual(len(readback["projection_receipts"]), 3)
            self.assertEqual(
                ProjectionHandler.projections[run_id]["revision"], 3,
            )


if __name__ == "__main__":
    unittest.main()
