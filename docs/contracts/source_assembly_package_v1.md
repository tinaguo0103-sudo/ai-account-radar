# Source-only package v1

This contract carries only collected source material into the existing exact-run workflow. It does not create a run, fetch sources, make editorial decisions, or write to Feishu or the Website.

The package directory contains exactly these three control/data files plus a `raw/` directory:

- `manifest.json`: `schema_version: 1`, exact `run_id`, matching Asia/Shanghai `business_date`, current integer `config_revision`, and the exact five allowed `source_ids`.
- `receipts.jsonl`: exactly one record per allowed source ID. Each record carries the same `run_id`, the exact source-control `verified_identity`, `attempted`, `attempted_at`, `attempt_count`, `failed_attempt_count`, terminal `status`, raw `item_count`, optional `visible_list_complete`, and a bounded `error_summary`/`failure_class` when needed. An unattempted source is `blocked` with zero attempts/items and an explicit reason; it is never reported as a successful empty result.
- `items.jsonl`: one record per observed item with `run_id`, `source_id`, `item_id`, `title`, canonical HTTP(S) `url`, optional `author` and `published_at`, `raw_path` relative to the package root and beneath `raw/`, and the exact UTF-8 byte `raw_sha256`.
- `raw/`: body text files referenced by `raw_path`.

The five identities are OpenAI `feishu_recvl4uBak96UM`, Anthropic `feishu_recvl4uBakM7LI`, Product Hunt AI `feishu_recvl4uBakDz73`, Hacker News AI discussion `feishu_recvl4uBakYAzP`, and X `feishu_recvl4uBakTUDT`. Per-source item caps are 5, 5, 5, 5, and 3 respectively.

The importer validates the whole package, identities, exact run/date/revision, source set, receipt/item counts, attempt/status consistency, path containment, and raw-byte hashes before writing imported rows. Same-URL rows are combined into one content row while preserving each exact source ID in `source_provenance`; raw item counts, unique source URLs, and deduplicated content rows remain separate measures. A same-run import checkpoint can be reused without re-reading a remote source. A different package for an already checkpointed run is a conflict.
