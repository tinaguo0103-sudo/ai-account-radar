#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from feishu_user_oauth_store import (
    latest_token_values,
    preserve_latest_user_tokens,
    read_env_file,
    related_env_files,
    sync_user_tokens,
    update_env_file,
)


TOKEN_VALUES_OLD = {
    "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN": "old_access",
    "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN": "old_refresh",
    "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN_EXPIRES_AT": "100",
    "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN_EXPIRES_AT": "200",
}
TOKEN_VALUES_NEW = {
    "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN": "new_access",
    "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN": "new_refresh",
    "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN_EXPIRES_AT": "1000",
    "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN_EXPIRES_AT": "2000",
}


class FeishuUserOauthStoreTest(unittest.TestCase):
    def test_update_env_file_preserves_unrelated_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env.local"
            env_file.write_text('KEEP_ME="yes"\nFEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN="old"\n', encoding="utf-8")
            update_env_file(env_file, {"FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN": 'new"value'})
            values = read_env_file(env_file)
            self.assertEqual(values["KEEP_ME"], "yes")
            self.assertEqual(values["FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN"], 'new"value')

    def test_related_env_files_only_pairs_declared_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            runtime = Path(tmp) / "runtime"
            other_runtime = Path(tmp) / "other_runtime"
            root.mkdir()
            runtime.mkdir()
            other_runtime.mkdir()
            (runtime / "RUNTIME_SOURCE.txt").write_text(f"Synced from: {root}\n", encoding="utf-8")
            (other_runtime / "RUNTIME_SOURCE.txt").write_text("Synced from: /tmp/other\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    related_env_files(root, runtime),
                    [(root / ".env.local").resolve(), (runtime / ".env.local").resolve()],
                )
                self.assertEqual(related_env_files(root, other_runtime), [(root / ".env.local").resolve()])

    def test_preserve_latest_user_tokens_chooses_newest_refresh_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            runtime = Path(tmp) / "runtime"
            root.mkdir()
            runtime.mkdir()
            (runtime / "RUNTIME_SOURCE.txt").write_text(f"Synced from: {root}\n", encoding="utf-8")
            update_env_file(root / ".env.local", TOKEN_VALUES_OLD)
            update_env_file(runtime / ".env.local", TOKEN_VALUES_NEW)
            with patch.dict(os.environ, {}, clear=True):
                latest, env_files = preserve_latest_user_tokens(root=root, runtime_dir=runtime)
            self.assertEqual([(root / ".env.local").resolve(), (runtime / ".env.local").resolve()], env_files)
            self.assertEqual(latest["FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN"], "new_refresh")

    def test_sync_user_tokens_writes_source_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            runtime = Path(tmp) / "runtime"
            root.mkdir()
            runtime.mkdir()
            (runtime / "RUNTIME_SOURCE.txt").write_text(f"Synced from: {root}\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                saved_to = sync_user_tokens(TOKEN_VALUES_NEW, root=root, runtime_dir=runtime)
            self.assertEqual(saved_to, [(root / ".env.local").resolve(), (runtime / ".env.local").resolve()])
            self.assertEqual(read_env_file(root / ".env.local")["FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN"], "new_refresh")
            self.assertEqual(read_env_file(runtime / ".env.local")["FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN"], "new_refresh")

    def test_latest_token_values_ignores_empty_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_a = Path(tmp) / "a.env"
            env_b = Path(tmp) / "b.env"
            env_a.write_text("OTHER=1\n", encoding="utf-8")
            update_env_file(env_b, TOKEN_VALUES_NEW)
            self.assertEqual(latest_token_values([env_a, env_b])["FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN"], "new_access")


if __name__ == "__main__":
    unittest.main()
