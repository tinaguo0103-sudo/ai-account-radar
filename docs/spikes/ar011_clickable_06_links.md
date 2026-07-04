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

### Production read-only audit

Environment: `../ai_account_radar/.env.local`

- Production 06 table id: `tblFjYFFH9nfekeK`
- `飞书文档`: `type=1`
- `飞书文档链接`: missing
- `飞书文件夹链接`: missing

No production write was performed.

## Release Requirement

This AR cannot be marked Ready for production until PM/user authorizes a production schema change. Minimal schema option:

1. Add URL fields to production `06 完整脚本与制作包`:
   - `飞书文档链接`
   - `飞书文件夹链接`
2. Add these fields to the main script package views near the existing `飞书文档` / `飞书文件夹` fields.
3. Keep legacy text fields for compatibility and historical records.
4. After merge/pull, run one minimal production smoke on a real newly generated 06 record and read back the URL fields.

Changing the existing `飞书文档` field from text to URL may be cleaner visually but is riskier because historical text values and view behavior may be affected.
