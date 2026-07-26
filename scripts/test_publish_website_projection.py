import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from publish_website_projection import ProjectionError, build_projection, main


class WebsiteProjectionTest(unittest.TestCase):
    def test_exact_production_shape_is_126_2_0(self):
        payload = build_projection(
            Path("/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar"),
            "run_20260726_080306", 1, "radar-production",
        )
        self.assertEqual(126, len(payload["collected_items"]))
        self.assertEqual(2, len(payload["topics"]))
        self.assertEqual(0, len(payload["scripts"]))
        self.assertEqual("editorial", payload["stage"])
        self.assertEqual(2, len({row["content_id"] for row in payload["topics"]}))

    def test_wrong_run_has_no_latest_fallback(self):
        with self.assertRaisesRegex(ProjectionError, "required_artifact_missing"):
            build_projection(Path(tempfile.mkdtemp()), "run_20260726_080306", 1, "fixture")

    @patch("publish_website_projection.request_json")
    def test_readback_mismatch_is_typed_failure(self, request_json):
        payload_file = Path(tempfile.mkdtemp()) / "payload.json"
        argv = [
            "publish_website_projection.py",
            "--repo", "/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar",
            "--run-id", "run_20260726_080306",
            "--revision", "1",
            "--authority-identity", "radar-production",
            "--website-url", "http://127.0.0.1:4290",
            "--payload-out", str(payload_file),
        ]
        request_json.side_effect = [
            {"ok": True, "status": "applied"},
            {"ok": True, "payload_sha256": "wrong"},
        ]
        with patch.object(sys, "argv", argv):
            self.assertEqual(2, main())
        self.assertEqual(2, request_json.call_count)
        self.assertEqual(126, len(json.loads(payload_file.read_text())["collected_items"]))

    @patch("publish_website_projection.request_json")
    def test_exact_409_is_reconciled_by_authoritative_readback(self, request_json):
        payload = build_projection(
            Path("/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar"),
            "run_20260726_080306", 1, "radar-production",
        )
        readback = {
            "ok": True, "revision": 1,
            "payload_sha256": payload["payload_sha256"],
            "authority_identity": "radar-production",
            "counts": {"content": 126, "topics": 2, "scripts": 0},
        }
        request_json.side_effect = [
            ProjectionError("business_projection_conflict"),
            readback,
            readback,
        ]
        argv = [
            "publish_website_projection.py",
            "--repo", "/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar",
            "--run-id", "run_20260726_080306",
            "--revision", "1",
            "--authority-identity", "radar-production",
            "--website-url", "http://127.0.0.1:4290",
        ]
        with patch.object(sys, "argv", argv):
            self.assertEqual(0, main())
        self.assertEqual(3, request_json.call_count)


if __name__ == "__main__":
    unittest.main()
