from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import provision_ar040_staging_tables as provision


class FakeFeishu:
    def __init__(self) -> None:
        self.tables: dict[str, dict] = {}
        self.next_id = 1
        self.table_create_calls = 0
        self.mutation_calls = 0
        self.raise_after_create_for: set[str] = set()

    def add_table(self, spec: provision.TableSpec, *, table_id: str | None = None, complete: bool = True) -> str:
        value = table_id or f"tbl-test-{self.next_id}"
        self.next_id += 1
        fields = list(spec.fields) if complete else list(spec.fields[:-1])
        self.tables[value] = {
            "table_id": value, "name": spec.name,
            "fields": {name: {"field_name": name, "field_id": f"fld-{index}", "type": 1} for index, name in enumerate(fields)},
            "views": {name: {"view_name": name, "view_id": f"view-{index}"} for index, name in enumerate(spec.views)},
        }
        return value

    def request(self, method: str, path: str, *, token: str = "", body=None, **_kwargs):
        parts = path.split("?")[0].strip("/").split("/")
        if method == "GET" and parts[-1] == "tables":
            return {"data": {"items": [{"table_id": row["table_id"], "name": row["name"]} for row in self.tables.values()]}}
        table_id = parts[parts.index("tables") + 1] if "tables" in parts and parts[-1] != "tables" else ""
        if method == "GET" and parts[-1] == "fields":
            return {"data": {"items": list(self.tables[table_id]["fields"].values())}}
        if method == "GET" and parts[-1] == "views":
            return {"data": {"items": list(self.tables[table_id]["views"].values())}}
        if method == "POST" and parts[-1] == "tables":
            self.table_create_calls += 1
            self.mutation_calls += 1
            table = body["table"]
            value = f"tbl-created-{self.next_id}"
            self.next_id += 1
            self.tables[value] = {
                "table_id": value, "name": table["name"],
                "fields": {item["field_name"]: {"field_name": item["field_name"], "field_id": f"fld-{index}", "type": item["type"]} for index, item in enumerate(table["fields"])},
                "views": {table["default_view_name"]: {"view_name": table["default_view_name"], "view_id": "view-default"}},
            }
            if table["name"] in self.raise_after_create_for:
                raise TimeoutError("response lost after commit")
            return {"data": {"table": {"table_id": value}}}
        if method == "POST" and parts[-1] == "views":
            self.mutation_calls += 1
            name = body["view_name"]
            view = {"view_name": name, "view_id": f"view-{len(self.tables[table_id]['views'])}"}
            self.tables[table_id]["views"][name] = view
            return {"data": {"view": view}}
        if method == "PATCH" and "views" in parts:
            self.mutation_calls += 1
            return {"data": {}}
        raise AssertionError(f"unexpected request {method} {path}")


class AR040StagingProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env_file = self.root / ".env.staging.local"
        self.production_env = self.root / ".env.production.local"
        self.base_env = (
            "AI_ACCOUNT_RADAR_ENV=staging\n"
            "FEISHU_APP_ID=staging-app\n"
            "FEISHU_APP_SECRET=staging-secret\n"
            "FEISHU_BASE_APP_TOKEN=staging-base\n"
            "UNCHANGED_VALUE=keep-exactly\n"
        )
        self.env_file.write_text(self.base_env, encoding="utf-8")
        self.env_file.chmod(0o600)
        self.production_env.write_text("FEISHU_APP_ID=production-app\nFEISHU_BASE_APP_TOKEN=production-base\n", encoding="utf-8")
        self.production_env.chmod(0o600)
        self.fake = FakeFeishu()
        self.request_patch = mock.patch.object(provision.feishu, "request_json", side_effect=self.fake.request)
        self.token_patch = mock.patch.object(provision.feishu, "tenant_token", return_value="tenant-test")
        self.sleep1 = mock.patch.object(provision.sync_source_sampling.time, "sleep")
        self.sleep2 = mock.patch.object(provision.content_sampler.time, "sleep")
        self.request_patch.start(); self.token_patch.start(); self.sleep1.start(); self.sleep2.start()

    def tearDown(self) -> None:
        mock.patch.stopall()
        self.temp.cleanup()

    def execute(self, *, write: bool):
        return provision.run(
            self.env_file, write=write, environ={"AI_ACCOUNT_RADAR_ENV": "staging"},
            production_env_file=self.production_env,
        )

    def invoke_main(self, *, write: bool) -> tuple[int, dict]:
        argv = ["provision", "--env-file", str(self.env_file)] + (["--write"] if write else [])
        stdout = io.StringIO()
        with mock.patch.object(provision, "PRODUCTION_ENV_FILE", self.production_env), \
             mock.patch.dict(os.environ, {"AI_ACCOUNT_RADAR_ENV": "staging"}, clear=False), \
             mock.patch("sys.argv", argv), contextlib.redirect_stdout(stdout):
            code = provision.main()
        return code, json.loads(stdout.getvalue())

    def test_check_only_missing_tables_has_zero_writes(self) -> None:
        before = self.env_file.read_bytes()
        result = self.execute(write=False)
        self.assertEqual([row["action"] for row in result["resources"]], ["would_create", "would_create"])
        self.assertEqual(self.fake.mutation_calls, 0)
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_public_cli_check_then_write(self) -> None:
        check_code, check = self.invoke_main(write=False)
        self.assertEqual(check_code, 0)
        self.assertEqual([row["action"] for row in check["resources"]], ["would_create", "would_create"])
        self.assertEqual(self.fake.mutation_calls, 0)
        write_code, written = self.invoke_main(write=True)
        self.assertEqual(write_code, 0)
        self.assertEqual(written["env_keys_bound"], ["FEISHU_CONTENT_TABLE_ID", "FEISHU_SOURCE_TABLE_ID"])
        self.assertEqual(self.fake.table_create_calls, 2)

    def test_write_creates_exact_two_then_second_run_is_noop(self) -> None:
        result = self.execute(write=True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.fake.table_create_calls, 2)
        self.assertEqual(len(self.fake.tables), 2)
        updated = provision.parse_env_file(self.env_file)
        self.assertEqual(updated["UNCHANGED_VALUE"], "keep-exactly")
        self.assertTrue(updated["FEISHU_SOURCE_TABLE_ID"].startswith("tbl-created-"))
        self.assertTrue(updated["FEISHU_CONTENT_TABLE_ID"].startswith("tbl-created-"))
        first_create_count = self.fake.table_create_calls
        second = self.execute(write=True)
        self.assertTrue(second["ok"])
        self.assertEqual(self.fake.table_create_calls, first_create_count)
        self.assertEqual(len(self.fake.tables), 2)

    def test_existing_exact_tables_bind_without_create(self) -> None:
        for spec in provision.TABLE_SPECS:
            self.fake.add_table(spec)
        result = self.execute(write=True)
        self.assertEqual([row["action"] for row in result["resources"]], ["bound_existing", "bound_existing"])
        self.assertEqual(self.fake.table_create_calls, 0)
        values = provision.parse_env_file(self.env_file)
        self.assertIn("FEISHU_SOURCE_TABLE_ID", values)
        self.assertIn("FEISHU_CONTENT_TABLE_ID", values)

    def test_incompatible_and_duplicate_tables_stop(self) -> None:
        self.fake.add_table(provision.TABLE_SPECS[0], complete=False)
        with self.assertRaisesRegex(provision.ProvisionError, "source_sampling_schema_incompatible"):
            self.execute(write=False)
        self.fake = FakeFeishu()
        self.request_patch.stop()
        self.request_patch = mock.patch.object(provision.feishu, "request_json", side_effect=self.fake.request)
        self.request_patch.start()
        self.fake.add_table(provision.TABLE_SPECS[0])
        self.fake.add_table(provision.TABLE_SPECS[0])
        with self.assertRaisesRegex(provision.ProvisionError, "source_sampling_table_duplicate"):
            self.execute(write=False)

    def test_unknown_create_response_reconciles_exact_committed_table(self) -> None:
        self.fake.raise_after_create_for.add(provision.TABLE_SPECS[0].name)
        result = self.execute(write=True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.fake.table_create_calls, 2)
        self.assertEqual(len(self.fake.tables), 2)

    def test_production_identity_rejected_before_http(self) -> None:
        self.env_file.write_text(self.base_env.replace("staging-base", "production-base"), encoding="utf-8")
        with self.assertRaisesRegex(provision.ProvisionError, "staging_identity_matches_production"):
            self.execute(write=False)
        self.assertEqual(self.fake.mutation_calls, 0)
        self.assertEqual(self.fake.table_create_calls, 0)

    def test_public_cli_returns_typed_failure_without_secret_output(self) -> None:
        self.env_file.write_text(self.base_env.replace("staging-app", "production-app"), encoding="utf-8")
        with mock.patch.object(provision, "PRODUCTION_ENV_FILE", self.production_env), \
             mock.patch.object(os, "environ", {"AI_ACCOUNT_RADAR_ENV": "staging"}), \
             mock.patch("sys.argv", ["provision", "--env-file", str(self.env_file)]), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(provision.main(), 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["reason"], "staging_identity_matches_production")
        self.assertNotIn("production-app", stdout.getvalue())
        self.assertNotIn("staging-secret", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
