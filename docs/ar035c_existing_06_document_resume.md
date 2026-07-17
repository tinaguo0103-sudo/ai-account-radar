# AR-035C Existing 06 Document Resume

Use this entrypoint only when a script package and exactly one Feishu 06 record already exist, but Feishu document synchronization failed. It never generates content, creates a second 06 record, changes the source 04 marker, or processes the watcher queue.

## Check only

```bash
python3 scripts/resume_existing_script_package_document.py \
  --existing-06-record-id <record_id> \
  --source-04-record-id <record_id> \
  --markdown-path <absolute_markdown_path> \
  --expected-sha256 <sha256> \
  --check-only
```

Require `ok=true`, `would_create_document=true`, `would_update_existing_06=true`, and all side-effect counters zero.

## Authorized write

Keep the watcher stopped. First renew user OAuth with the project OAuth command and browser confirmation. Never record access or refresh tokens in evidence.

```bash
python3 scripts/resume_existing_script_package_document.py \
  --existing-06-record-id <record_id> \
  --source-04-record-id <record_id> \
  --markdown-path <absolute_markdown_path> \
  --expected-sha256 <sha256> \
  --write
```

The command validates OAuth before document creation, creates at most one document, and updates only the specified existing 06 record. If the document exists but the record update fails, a minimal local state under `output/script_package_doc_resume` binds the document URL to the exact source, 06 record, run, path, and Markdown SHA. A retry consumes that state and must not create another document.

After success, read back the same 06 record and require a clickable document Link, green sync status, and unchanged record count. Only then reinstall and start the watcher. Never search for or delete an orphan document without a known safe document ID.
