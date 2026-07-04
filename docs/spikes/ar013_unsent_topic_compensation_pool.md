# AR-013 Unsent Topic Compensation Pool V1

## Conclusion

AR-013 V1 keeps the existing Topic Card global limit unchanged and changes only the candidate pool behind that limit.

When today's guarded card sender has a fresh collection run, the card now selects from one unified pool:

- candidates from the current fresh run;
- pending, unsent candidates from the last 3 calendar days;
- all candidates keep their original `推荐日期` and original `运行批次`.

There is no separate compensation section and no fixed compensation quota.

## Limit Audit

Current limit sources:

- `scripts/run_topic_card_if_fresh.py --limit`, default `7`;
- `scripts/run_topic_decision_card_session.py --limit`, default `7`;
- `scripts/feishu_topic_decision_card.py DEFAULT_LIMIT`, value `7`;
- `fetch_candidates(run_id, limit, ...)` applies the final slice with `selected[:limit]`.

This is a card-level global limit. AR-013 V1 does not change it and does not add any compensation-area limit.

## Candidate Pool Rules

The production trigger remains unchanged:

- `scripts/run_topic_card_if_fresh.py` must first pass the fresh collection guard;
- `output/latest_write` is not overwritten;
- old candidates are read from Feishu 04 only, not from local run artifacts;
- if AR-015 idempotency unknown guard blocks the run, Topic Card sending still skips.

Candidate inclusion:

- today's run candidates are included by `运行批次 == current run_id`;
- historical candidates are included when `推荐日期` is within the last 3 calendar days;
- eligible statuses remain `待判断` or empty;
- already generated/processed candidates are excluded;
- candidates already recorded as sent/submitted in local Topic Card candidate ledger or callback receipts are excluded;
- duplicate historical candidates are deduped against today's candidate by source/title key, preferring today's run.

Sorting still starts from the existing `今日排名`. When ranks tie, newer dates come first.

## Card Changes

The card header now includes the coverage date range:

```text
本次候选覆盖：2026-07-02、2026-07-03、2026-07-04
```

Each visible candidate includes:

- original `推荐日期`;
- original `运行批次`;
- the existing direction/risk/brief/experiment/evidence fields.

The card callback payload keeps candidate snapshots with original date/run_id. This allows the callback to update historical candidates that were explicitly included in the card while still rejecting arbitrary cross-run records.

The Feishu cloud callback receiver and the Tencent SCF bundled entry both accept historical candidates only when the submitted card carries a matching `candidate_snapshots[record_id].run_id`. Without that snapshot, cross-run records are still blocked with `card_run_mismatch`.

The follow-up production direction card also carries the same candidate snapshots, so a historical candidate selected from the unified card can still receive its production direction supplement without rewriting its original `运行批次`.

## Sent Candidate Ledger

V1 adds:

```text
output/decision_cards/topic_card_candidate_ledger.jsonl
```

It is written only after a real card send succeeds, not during dry-run preview. It records:

- candidate `record_id`;
- current card `run_id`;
- original candidate `run_id`;
- original date;
- title hash;
- preview path.

It does not record card body, receive_id, token, or full candidate text.

Callback receipts are also treated as evidence that a candidate has already entered a Topic Card.

## Validation

Unit coverage in `scripts/test_ar013_compensation_pool.py` verifies:

- last-3-day inclusion;
- older-than-3-day exclusion;
- processed/generated/sent candidate exclusion;
- duplicate historical/current candidates prefer today's candidate;
- existing global limit remains `7`;
- card header includes coverage dates;
- visible candidate text includes original date/run_id;
- callback can update historical candidates only when candidate snapshot run_id matches;
- candidate ledger stores ids and hashes, not card body.

Cloud callback coverage in `cloud_functions/feishu-card-receiver/test/receiver.test.mjs` verifies:

- historical compensation candidates can be selected when card snapshots preserve the original run;
- cross-run candidates without snapshots are rejected and not written;
- production direction feedback for historical candidates is accepted only through the same snapshot-safe path.

Regression coverage:

```bash
PYTHONPATH=scripts PYTHONPYCACHEPREFIX=/private/tmp/ai_account_radar_pycache \
  python3 -m unittest scripts/test_ar013_compensation_pool.py scripts/test_feishu_idempotency_phase1.py
```

No production table writes, real card sends, production collection, or scheduler changes were used for this validation.
