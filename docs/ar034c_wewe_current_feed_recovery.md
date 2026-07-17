# AR-034C WeWe Current-Feed Recovery

## Supported read surface

The installed `cooderl/wewe-rss-sqlite:latest` implementation applies `limit` and `page` in its Prisma article query before rendering full text. `mode=fulltext` then resolves content only for that bounded page. AR-034C therefore does not read `/feeds/all.json` or a complete per-feed JSON document.

The reader first validates the signed refresh receipt and canonical live SQLite snapshot. It selects only article rows inside each receipt feed's `(before.max_publish_time, after.max_publish_time]` interval and requires their count to equal the receipt article-count delta. It then reads one exact article per provider request with `limit=1`, verifies the returned article ID and title, and revalidates the signed receipt and database plan after all reads.

## Check-only recovery command

This command validates the existing signed receipt and plans the exact current article identities. It makes no provider request and writes no output:

```bash
PYTHONPATH=scripts python3 scripts/wewe_current_feed_reader.py \
  --check-only \
  --run-id run_20260717_093104 \
  --run-started-at-ms 1784251865025 \
  --refresh-result output/provider_health/run_20260717_093104/wewe_refresh_attempt.json
```

After a release is separately authorized, omit `--check-only` and provide `--out` and `--csv` to continue the same run. That read does not refresh the provider. A failed page, missing full text, count drift, revision drift, receipt mismatch, duplicate, or reordered identity stops the recovery before downstream writes.

## Rollback

Revert the AR-034C code commit. Existing signed receipt, database, watermark, provider runtime, and failed read evidence remain unchanged. Keep automations paused until the prior giant-feed path is no longer reachable from the same-run recovery.
