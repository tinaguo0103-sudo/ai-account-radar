# AR-026 Full-account Collection Release

AR-026 changes the scheduled collection contract from a bounded sample to all
eligible competitor accounts. `0` is the only scheduled default and means no
account cap. Any positive, negative, malformed, duplicate, or alias-form cap is
rejected as `limited_plan_rejected` by the outer job, daily pipeline, and Node
probe before environment loading, cache access, Chrome/CDP contact, output
creation, Feishu access, collection, or notification.

## Read-only release check

```bash
python3 scripts/source_pool_governance.py --from-feishu --out-dir /private/tmp/ar026-source-governance
python3 scripts/run_daily_collection_job.py --check-only \
  --source-config /private/tmp/ar026-source-governance/post_migration_source_config.json \
  --douyin-account-limit 0
node scripts/douyin_cdp_source_watch_probe.mjs \
  --config /private/tmp/ar026-source-governance/post_migration_source_config.json \
  --out-dir /private/tmp/ar026-douyin-plan --check-only
```

The first command is GET-only. The other commands do not contact Chrome,
collect content, or write Feishu. The expected plan is 33 eligible competitor
accounts: 31 Douyin accounts plus 2 accounts handled by other source paths.

## Feishu 01 migration

Release authorization must cover exactly the eight records in
`production_01_planned_mutations.json`. Update only `来源角色`, `默认启用`,
`是否参与主采样`, and `优先级`. Read back all eight records and verify that the
other 43-record snapshot hash is unchanged. Keep the generated rollback payload.

Feishu 03 is immutable for this migration. Do not delete or relabel historical
rows that mention quarantined account names.

## First scheduled run acceptance

- canonical port 9333 profile identity and visible login preflight pass;
- scheduled plan reports 33 total accounts with no positive cap;
- Douyin coverage reports planned, attempted, succeeded, failed, per-account
  failure reason, and artifact count;
- `attempted == planned` and `succeeded + failed == attempted`;
- any account failure produces `completed_with_failures` and a non-success outer
  collection result while successful source artifacts remain recoverable;
- failed accounts produce no content item and no cross-account substitution;
- no current-day cache, alternate browser/profile/port, or historical 03 data is
  used to fill a failed account.

## Rollback

Keep scheduled automations paused during release. Back up production Git and the
eight Feishu 01 field values. If code or read-back verification fails, revert the
release commit and restore only the eight rows from the rollback payload. Do not
touch Feishu 03, the canonical Chrome profile, global Skills, SCF, or Topic Card
state.
