# AR-011 06 Feishu Clickable Links

## Conclusion

The current production `06 完整脚本与制作包 / 飞书文档` field is a text field (`type=1`), so writing a raw URL produces plain text in Feishu Base. Feishu rejects rich URL segment payloads for this text field with `TextFieldConvFail`; the field must remain a string unless the schema is changed.

The dev implementation now supports clickable URL-field payloads without changing production schema:

- If `飞书文档` or `飞书文件夹` is a URL field (`type=15`), the runner writes `{text, link}`.
- If the existing text field remains `type=1`, the runner keeps the raw URL string to avoid breaking 06 writes.
- If optional URL fields `飞书文档链接` / `飞书文件夹链接` exist, the runner fills them with clickable URL payloads while preserving the legacy text fields.

## Audit

- Generation path: `scripts/codex_script_package_runner.py`
- 06 record creation: `create_script_package_record()`
- Current row source: `package_row()` writes `doc_sync.url` into `飞书文档` and `doc_sync.folder_url` into `飞书文件夹`.
- Field setup: `SCRIPT_PACKAGE_FIELDS` and `setup_script_package_test_env.py` historically created all 06 fields as text fields.

## Verification

### Function tests

`scripts/test_codex_script_package_clickable_links.py` covers:

- text fields keep plain URL and do not receive Markdown;
- URL fields receive `{text, link}`;
- optional mirror URL fields are populated when present;
- empty links do not change script status or doc sync fields;
- learning feedback reads rich URL payloads back as the real URL.

### Staging/test Feishu

Environment: `.env.staging.local`

- Test table: `06 完整脚本与制作包__测试`
- Test table id: `tbl5PQjZhajZtxsP`
- Test record id: `recvooZdjGmiP5`
- Added/reused test-only URL fields:
  - `飞书文档链接` (`type=15`)
  - `飞书文件夹链接` (`type=15`)

Read-back confirmed:

```json
{
  "doc_text_field_type": 1,
  "doc_url_field_type": 15,
  "doc_text_field": "https://my.feishu.cn/docx/AR011ClickableLinkTest",
  "doc_url_field": {
    "link": "https://my.feishu.cn/docx/AR011ClickableLinkTest",
    "text": "打开飞书文档"
  },
  "folder_url_field": {
    "link": "https://my.feishu.cn/drive/folder/AR011ClickableFolderTest",
    "text": "打开飞书文件夹"
  }
}
```

### L3 UI click rework

Initial L3 test failed in the record detail panel: the URL fields were visible and the DOM exposed hrefs, but automated click attempts in the detail panel did not produce an observable navigation or new tab. This means API read-back alone was not enough, and the detail-panel path should not be the only acceptance path.

Rework changed the staging/test setup:

- Added a dedicated grid view: `AR-011 L3 链接验证`
- Test view id: `vewN1u2jdL`
- Test record id: `recvop4Ypg2yjh`
- Test screenshot: `/private/tmp/ar011_l3_grid_links_visible.png`
- The URL fields are visible as table columns:
  - `飞书文档链接`
  - `飞书文件夹链接`

The test record uses an existing real staging/test Feishu document URL and the staging/test folder URL:

```json
{
  "doc_url_field": {
    "link": "https://my.feishu.cn/docx/FZuPdGDlmobf6lxk2wmcquksn2c",
    "text": "打开飞书文档"
  },
  "folder_url_field": {
    "link": "https://my.feishu.cn/drive/folder/X79kfZ274lcpy4dtjypcEBUmn2b",
    "text": "打开飞书文件夹"
  }
}
```

Observed UI behavior:

- In the grid view, hovering the `飞书文档链接` cell underlines `打开飞书文档`.
- Clicking the underlined grid-cell link opens a Chrome tab at the target `docx` URL.
- Clicking the `飞书文件夹链接` grid-cell link opens a Chrome tab at the target test folder URL.
- In this browser tooling, opened target pages appeared in `browser.user.openTabs()` rather than the session-local `browser.tabs.list()`, which explains why a narrower automation check could incorrectly report "no new tab".

Current L3 conclusion: the grid-view URL-field path is clickable; the record-detail click path remains unreliable for automation and should not be the release proof. Acceptance should require URL fields visible in the main script-package grid view and a user-visible click from that grid.

Note: creating a fresh staging test document was blocked by revoked user OAuth (`invalid_grant`, code `20064`), so this round reused an existing real staging/test document URL. No production document, production table, or production schema was touched.

### Flow QA and backfill rework

User feedback clarified that clickable-field QA is not complete unless the actual 06 write flow and old-record backfill path are covered. This round adds three narrow, production-safe entrypoints:

- `scripts/setup_script_package_clickable_links.py`
  - Default mode is dry-run.
  - Only checks/creates `飞书文档链接` and `飞书文件夹链接` as URL fields (`type=15`).
  - Only patches explicitly selected grid views, preserving existing hidden-field settings and removing the two URL fields from the hidden list when needed.
  - Does not rename title fields, delete deprecated fields, delete old views, backfill records, or run the broad 06 workspace setup.
- `scripts/backfill_script_package_clickable_links.py`
  - Default mode is dry-run.
  - Reads legacy text URL fields `飞书文档` / `飞书文件夹`.
  - Writes only mirror URL fields `飞书文档链接` / `飞书文件夹链接`.
  - Produces a JSON report in `output/logs/`, supports read-back validation, detects invalid URLs, and is idempotent when mirror fields already match.
- `scripts/script_package_clickable_link_flow_qa.py`
  - Uses a fixture `FeishuDocSyncResult` instead of calling `codex exec` or creating a real doc.
  - Exercises the real `package_row -> create_script_package_record -> read-back` path.
  - Verifies both legacy text fields and new URL mirror fields are written from `doc_sync.url` / `folder_url`.

Staging/test validation used `.env.staging.local` and table `06 完整脚本与制作包__测试` (`tbl5PQjZhajZtxsP`). No production table, production schema, production document, collection job, or Topic Card was touched.

Narrow schema/view setup:

```json
{
  "environment": "staging",
  "table_id": "tbl5PQjZhajZtxsP",
  "field_plan": {
    "already_ok": ["飞书文档链接", "飞书文件夹链接"],
    "create": [],
    "conflicts": []
  },
  "view": "AR-011 L3 链接验证",
  "view_id": "vewN1u2jdL",
  "write": true
}
```

06 flow QA fixture:

```json
{
  "created_record_id": "recvopbwen6A9r",
  "checks": {
    "legacy_doc_text": true,
    "legacy_folder_text": true,
    "doc_url_field": true,
    "folder_url_field": true
  }
}
```

Backfill staging/test fixture:

```json
{
  "created_fixture_record_ids": [
    "recvopbAcl2SDf",
    "recvopbAZnYhOX",
    "recvopbBNkS8DG"
  ],
  "dry_run_counts": {
    "to_update": 2,
    "invalid_source": 1
  },
  "write_read_back_ok": true,
  "idempotent_rerun_counts": {
    "already_ok": 2,
    "invalid_source": 1
  }
}
```

### Production read-only audit

Environment: `../ai_account_radar/.env.local`

- Production 06 table id: `tblFjYFFH9nfekeK`
- `飞书文档`: `type=1`
- `飞书文档链接`: missing
- `飞书文件夹链接`: missing

No production write was performed.

## Release Requirement

This AR cannot be marked Ready for production until PM/user authorizes a production schema change. Minimal schema option:

1. Run `scripts/setup_script_package_clickable_links.py` in production dry-run mode and confirm the side-effect list only contains URL-field creation and selected grid-view patching.
2. Add URL fields to production `06 完整脚本与制作包` after authorization:
   - `飞书文档链接`
   - `飞书文件夹链接`
3. Add these fields to the main script package grid views near the existing `飞书文档` / `飞书文件夹` fields. The grid view is the verified clickable path.
4. Run `scripts/backfill_script_package_clickable_links.py` production dry-run, review total records, parseable URLs, invalid URLs, and planned record ids.
5. After authorization, run backfill write and read-back. Keep legacy text fields unchanged.
6. After merge/pull, run one minimal production smoke on a real newly generated 06 record and read back the old text fields plus new URL fields.

Changing the existing `飞书文档` field from text to URL may be cleaner visually but is riskier because historical text values and view behavior may be affected.
