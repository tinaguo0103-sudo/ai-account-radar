# AR-034B Legacy Douyin Lineage Attestation

This migration-only path binds a preserved pre-RC6 Douyin probe and resolver manual to its original daily run. It does not modify those artifacts, fabricate a native envelope, recollect an account, or relax the native RC6 contract.

## Trust boundary

Legacy mode is enabled only when both `--legacy-daily-log` and `--expected-source-run-id` are supplied. Without them, `ar034_recovery_check.py` uses the strict native validator and rejects a probe missing `run_id` or `manual_artifact`. A probe containing either native identity field cannot be downgraded to legacy mode.

The attestation independently reopens the canonical daily log, probe, and manual. It verifies file ownership and link identity, the exact scheduled command, run window, resolver identity, terminal coverage, failed-account isolation, ordered item fingerprints, and account counts. Truncated step stdout is recorded only as corroboration and is never an identity anchor.

## Check-only command

```bash
PYTHONPATH=scripts python3 scripts/ar034_recovery_check.py \
  --probe-result /absolute/production/output/spikes/douyin_cdp_source_watch_probe/cdp_probe_results.json \
  --douyin-manual /absolute/production/output/spikes/douyin_cdp_source_watch_probe/content_items_manual.jsonl \
  --incident-content-items /absolute/production/output/runs/run_YYYYMMDD_HHMMSS/content_items.csv \
  --incident-today-candidates /absolute/production/output/runs/run_YYYYMMDD_HHMMSS/today_10_topics.csv \
  --legacy-daily-log /absolute/production/output/logs/daily_pipeline_YYYY-MM-DD.json \
  --expected-source-run-id run_YYYYMMDD_HHMMSS \
  --check-only
```

Save the complete machine output as audit evidence. Immediately before the first external write, call the same command with `--locked-legacy-attestation /path/to/initial-check.json`. The command reopens all three originals and requires hashes, sizes, mtimes, ordered fingerprints, account mapping, run window, and command identity to equal the locked report. The report alone is never trusted.

The source run remains the original collection run. A later recovery run has a separate run ID and must build a new comparison universe; it must not reuse the incident candidate CSV or replace failed Douyin accounts.

## Residual risk

This is a local legacy migration trust path based on canonical paths, current-UID file identity, daily command and time evidence, content hashes, and account/item lineage. It is not a cryptographic receipt created by the original collection process. New artifacts must continue to use the native RC6 identity envelope.
