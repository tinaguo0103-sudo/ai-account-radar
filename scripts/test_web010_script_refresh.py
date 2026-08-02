import copy
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from daily_workflow import DailyWorkflow
from publish_website_projection import ProjectionError, build_workflow_projection
from refresh_website_scripts import authority_snapshot, run_refresh, validate_override


RUN_ID = "run_20260802_104213"
DATE = "2026-08-02"
AUTHORITY = "qa-private-owner"
CONFIG = {
    "website_url": "http://qa-private.invalid",
    "authority_identity": AUTHORITY,
    "app_bearer": "fixture-app",
    "sites_bearer": "fixture-sites",
}


def script(topic_id, suffix="old"):
    return {"topic_id": topic_id, "title": f"Title {suffix} {topic_id}",
            "hook": f"Hook {suffix}", "structure": f"Structure {suffix}",
            "body": f"Body {suffix} for {topic_id}"}


def make_authority(root: Path, *, terminal=True):
    path = root / "workflow.sqlite3"
    workflow = DailyWorkflow(path)
    workflow.begin(RUN_ID, DATE)
    identities = [f"topic:{index}" for index in range(5)]
    collection = {
        "content_items": [{"item_id": identity, "title": identity,
                           "source_url": f"https://example.test/{index}", "source": "aihot"}
                          for index, identity in enumerate(identities)],
        "candidates": [{"candidate_id": identity, "item_id": identity} for identity in identities],
        "understanding_results": [], "source_ledger": [], "item_failures": [],
    }
    editorial = {"run_id": RUN_ID, "topics": [
        {"candidate_id": identity, "decision": "select", "title": identity,
         "hook": "old hook", "structure": "old structure", "selection_reason": "reason"}
        for identity in identities
    ]}
    old = {"run_id": RUN_ID, "scripts": [script(identity) for identity in identities], "failures": []}
    workflow.commit_stage(RUN_ID, "collection_enrichment", collection, "completed")
    workflow.commit_stage(RUN_ID, "editorial", editorial, "completed")
    workflow.commit_stage(RUN_ID, "scripts", old, "completed")
    if terminal:
        workflow.complete(RUN_ID, "completed", f"terminal:{RUN_ID}")
    workflow.db.close()
    return path, identities, old


def api_rows(payload):
    content = [{key: row.get(key) for key in (
        "id", "run_id", "source", "account", "title", "summary", "source_url",
        "published_at", "collected_at",
    )} | {"selected": 1 if any(topic["content_id"] == row["id"] and topic["status"] == "select"
                                for topic in payload["topics"]) else 0,
         "topic_id": next((topic["id"] for topic in payload["topics"] if topic["content_id"] == row["id"]), None),
         "script_id": next((item["id"] for item in payload["scripts"] if item["topic_id"] ==
                            next((topic["id"] for topic in payload["topics"] if topic["content_id"] == row["id"]), "")), None)}
        for row in payload["collected_items"]]
    topics = [copy.deepcopy(row) | {"business_date": payload["business_date"],
              "script_id": next((item["id"] for item in payload["scripts"] if item["topic_id"] == row["id"]), None)}
              for row in payload["topics"]]
    scripts = [{key: row.get(key) for key in (
        "id", "run_id", "topic_id", "script_version", "title", "hook", "content_structure",
        "body", "current_revision_number", "saved_at",
    )} | {"source": "aihot", "source_summary": "", "selection_reason": "reason",
         "source_url": "https://example.test"} for row in payload["scripts"]]
    return {"content": content, "topics": topics, "scripts": scripts}


class FakeWebsite:
    def __init__(self, target_payload, historical_payload):
        self.payloads = {target_payload["run_id"]: copy.deepcopy(target_payload),
                         historical_payload["run_id"]: copy.deepcopy(historical_payload)}
        self.projected_at = {target_payload["run_id"]: "old-target",
                             historical_payload["run_id"]: "old-history"}
        self.posts = 0
        self.fail_post = None
        self.mutate_readback = None

    def request(self, method, url, payload=None):
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/business-projection":
            run_id = query.get("run_id", [""])[0]
            if method == "POST":
                self.posts += 1
                if self.fail_post:
                    raise ProjectionError(self.fail_post)
                run_id = payload["run_id"]
                existing = self.payloads[run_id]
                pre = payload.get("refresh_precondition") or {}
                if pre.get("projected_at") != self.projected_at[run_id]:
                    raise ProjectionError("business_projection_precondition_stale")
                self.payloads[run_id] = copy.deepcopy(payload)
                self.projected_at[run_id] = "new-target"
                return self.metadata(run_id, "refreshed")
            return self.metadata(run_id, "readback")
        resource = parsed.path.rsplit("/", 1)[-1]
        rows = []
        exact = query.get("run_id", [None])[0]
        for run_id, value in self.payloads.items():
            if exact and run_id != exact:
                continue
            rows.extend(api_rows(value)[resource])
        if self.mutate_readback and self.posts:
            rows = self.mutate_readback(resource, rows)
        page = int(query.get("page", ["1"])[0])
        page_size = 20
        batch = rows[(page - 1) * page_size:page * page_size]
        key = "items" if resource == "content" else resource
        pages = max(1, (len(rows) + page_size - 1) // page_size)
        return {key: batch, "page": {"page": page, "page_size": page_size,
                                      "total": len(rows), "total_pages": pages}}

    def metadata(self, run_id, status):
        value = self.payloads[run_id]
        return {"run_id": run_id, "business_date": value["business_date"],
                "run_status": value["run"]["status"], "authority_identity": AUTHORITY,
                "projected_at": self.projected_at[run_id], "status": status,
                "counts": {"content": len(value["collected_items"]),
                           "topics": len(value["topics"]), "scripts": len(value["scripts"])}}


class ScriptRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db, self.identities, self.old = make_authority(self.root)
        self.accepted = {"run_id": RUN_ID,
                         "scripts": [script(identity, "accepted") for identity in self.identities],
                         "failures": []}
        self.artifact = self.root / "accepted.json"
        self.artifact.write_text(json.dumps(self.accepted), encoding="utf-8")
        self.old_payload = build_workflow_projection(self.db, RUN_ID, AUTHORITY)
        historical = copy.deepcopy(self.old_payload)
        historical["run_id"] = "run_20260801_080000"
        historical["business_date"] = "2026-08-01"
        historical["run"] = copy.deepcopy(historical["run"])
        for group in ("collected_items", "topics", "scripts"):
            historical[group] = copy.deepcopy(historical[group])
            for row in historical[group]:
                row["run_id"] = historical["run_id"]
                row["id"] = "history-" + row["id"]
        topic_ids = {row["id"].removeprefix("history-"): row["id"] for row in historical["topics"]}
        for row in historical["scripts"]:
            row["topic_id"] = "history-" + row["topic_id"]
        for row in historical["topics"]:
            row["content_id"] = "history-" + row["content_id"]
        self.website = FakeWebsite(self.old_payload, historical)

    def tearDown(self):
        self.temp.cleanup()

    def test_refresh_then_independent_noop(self):
        first = run_refresh(self.db, RUN_ID, DATE, self.artifact,
                            config=CONFIG, request_fn=self.website.request)
        self.assertEqual(first["action"], "refreshed")
        self.assertEqual(first["request_ledger"]["terminal_post"], 1)
        second = run_refresh(self.db, RUN_ID, DATE, self.artifact,
                             config=CONFIG, request_fn=self.website.request)
        self.assertEqual(second["action"], "noop")
        self.assertEqual(second["request_ledger"]["terminal_post"], 0)
        self.assertEqual(self.website.posts, 1)

    def test_strict_artifact_matrix(self):
        snapshot = authority_snapshot(self.db, RUN_ID, DATE)
        cases = []
        wrong_run = copy.deepcopy(self.accepted); wrong_run["run_id"] = "run_20260801_080000"
        cases.append((wrong_run, "script_refresh_artifact_run_conflict"))
        missing = copy.deepcopy(self.accepted); missing["scripts"].pop()
        cases.append((missing, "script_refresh_selected_coverage_conflict"))
        extra = copy.deepcopy(self.accepted); extra["scripts"].append(script("topic:extra"))
        cases.append((extra, "script_refresh_selected_coverage_conflict"))
        duplicate = copy.deepcopy(self.accepted); duplicate["scripts"].append(copy.deepcopy(duplicate["scripts"][0]))
        cases.append((duplicate, "script_refresh_script_identity_conflict"))
        schema = copy.deepcopy(self.accepted); schema["scripts"][0]["extra"] = True
        cases.append((schema, "script_refresh_artifact_schema_invalid"))
        for value, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ProjectionError, reason):
                validate_override(value, RUN_ID, snapshot["selected"])

    def test_wrong_date_and_non_terminal_fail_before_requests(self):
        with self.assertRaisesRegex(ValueError, "wrong_business_date"):
            authority_snapshot(self.db, RUN_ID, "2026-08-01")
        pending_db, _, _ = make_authority(self.root / "pending", terminal=False)
        with self.assertRaisesRegex(ProjectionError, "script_refresh_run_not_terminal"):
            run_refresh(pending_db, RUN_ID, DATE, self.artifact,
                        config=CONFIG, request_fn=self.website.request)
        self.assertEqual(self.website.posts, 0)

    def test_stale_and_concurrent_claim_fail_closed(self):
        for reason in ("business_projection_precondition_stale", "business_projection_refresh_in_progress"):
            website = FakeWebsite(self.old_payload, self.website.payloads["run_20260801_080000"])
            website.fail_post = reason
            with self.subTest(reason=reason), self.assertRaisesRegex(ProjectionError, reason):
                run_refresh(self.db, RUN_ID, DATE, self.artifact,
                            config=CONFIG, request_fn=website.request)
            self.assertEqual(website.posts, 1)

    def test_unknown_projection_lineage_fails_before_post(self):
        original = self.website.request

        def request(method, url, payload=None):
            value = original(method, url, payload)
            if method == "GET" and "/api/business-projection?" in url:
                value["authority_identity"] = "other-authority"
            return value

        with self.assertRaisesRegex(ProjectionError, "script_refresh_projection_precondition_mismatch"):
            run_refresh(self.db, RUN_ID, DATE, self.artifact,
                        config=CONFIG, request_fn=request)
        self.assertEqual(self.website.posts, 0)

    def test_content_and_topic_precondition_drift_fail_before_post(self):
        for resource, expected in (
            ("content", "script_refresh_content_precondition_drift"),
            ("topics", "script_refresh_topic_precondition_drift"),
        ):
            website = FakeWebsite(self.old_payload, self.website.payloads["run_20260801_080000"])
            original = website.request

            def request(method, url, payload=None, *, resource=resource):
                value = original(method, url, payload)
                if method == "GET" and f"/api/{resource}?" in url:
                    key = "items" if resource == "content" else resource
                    value[key] = value[key][1:]
                    value["page"]["total"] -= 1
                return value

            with self.subTest(resource=resource), self.assertRaisesRegex(ProjectionError, expected):
                run_refresh(self.db, RUN_ID, DATE, self.artifact,
                            config=CONFIG, request_fn=request)
            self.assertEqual(website.posts, 0)

    def test_readback_content_topic_and_history_drift_are_typed(self):
        mutations = {
            "script_refresh_content_drift": lambda resource, rows: [
                ({**row, "title": "drift"} if resource == "content" and row.get("run_id") == RUN_ID else row)
                for row in rows],
            "script_refresh_topic_drift": lambda resource, rows: [
                ({**row, "title": "drift"} if resource == "topics" and row.get("run_id") == RUN_ID else row)
                for row in rows],
            "script_refresh_historical_run_drift": lambda resource, rows: [
                ({**row, "title": "drift"} if row.get("run_id") != RUN_ID else row) for row in rows],
            "script_refresh_script_readback_mismatch": lambda resource, rows: [
                ({**row, "body": "drift"} if resource == "scripts" and row.get("run_id") == RUN_ID else row)
                for row in rows],
        }
        for reason, mutation in mutations.items():
            website = FakeWebsite(self.old_payload, self.website.payloads["run_20260801_080000"])
            website.mutate_readback = mutation
            with self.subTest(reason=reason), self.assertRaisesRegex(ProjectionError, reason):
                run_refresh(self.db, RUN_ID, DATE, self.artifact,
                            config=CONFIG, request_fn=website.request)


if __name__ == "__main__":
    unittest.main()
