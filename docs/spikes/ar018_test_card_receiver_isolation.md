# AR-018 Feishu Test Card Receiver Isolation

## Boundary

AR-018 is a test infrastructure gate. It does not release AR-013, does not send a production Topic Card, and does not change production SCF or production Feishu app configuration.

The goal is to make card Flow QA safe and provable:

- local sender uses staging/test env;
- health check reads the explicit staging/test 04 table id;
- real test card sends only to a personal/test receive target;
- receiver callback writes only staging/test 04;
- no real 06 watcher path is triggered.

Worst failure: a test card uses production receiver or production table lookup and changes real `04 分析与选题` records. AR-018 treats that as blocked until proven otherwise.

## Findings

`check_feishu_card_cloud_receiver.py` used table-name lookup for read checks. In staging/test env this could still resolve the production table named `04 分析与选题` instead of the explicit staging table id.

The Feishu cloud receiver code itself already prefers explicit topic table env vars:

- `FEISHU_TOPIC_TABLE_ID`
- `FEISHU_TOPIC_DECISION_TABLE_ID`

The local health check did not mirror that priority before this change.

After the receiver health check was fixed and a test SCF URL was configured, AR-018 Test Card Smoke found one more isolation gap: the real Topic Card sender path still used table-name lookup in `feishu_topic_decision_card.get_topic_table()`. Under the same `.env.staging.local`, health check used `tblWAH8Ba3wh5jdo`, but sender/build resolved the production table named `04 分析与选题`. Sender and health check must share the same table-id priority before any real test card is sent.

The current `.env.staging.local` has the staging 04 table id configured. For real Flow QA it must also have the isolated test receiver and personal/test receive targets:

- `FEISHU_TENCENT_SCF_URL`
- `FEISHU_CARD_RECEIVE_TARGETS`
- `FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS`

## Implemented Gate

`scripts/check_feishu_card_cloud_receiver.py` now:

- uses explicit table id env vars before table-name lookup;
- reports `table_id_source` so QA can see whether the read used an env var or name lookup;
- adds `--require-test-card-config` for card Flow QA;
- fails the test-card config gate when receiver URL, test receive target, production-direction test target, or explicit topic table id is missing;
- rejects `AI_ACCOUNT_RADAR_ENV=prod/production` for test-card config.

`scripts/feishu_topic_decision_card.py` now uses the same explicit topic-table priority:

- `FEISHU_TOPIC_TABLE_ID`;
- `FEISHU_TOPIC_DECISION_TABLE_ID`;
- table-name lookup only when no explicit topic table id is configured.

This applies to build/send candidate reads, apply, local callback serving, and long-connection callback serving because all of those paths resolve through `get_topic_table()`.

Normal read-only checks can still use:

```bash
AI_ACCOUNT_RADAR_ENV_FILE=.env.staging.local \
  python3 scripts/check_feishu_card_cloud_receiver.py \
  --skip-receiver \
  --table-key topic_decision
```

AR-013/AR-018 Flow QA should use:

```bash
AI_ACCOUNT_RADAR_ENV_FILE=.env.staging.local \
  python3 scripts/check_feishu_card_cloud_receiver.py \
  --require-test-card-config \
  --table-key topic_decision
```

If receiver URL and test targets are missing, this command must fail. That is the correct state: Flow QA is waiting for test receiver configuration, not passed.

## Current Local Verification

Read-only staging health now reports the explicit staging table id source for `topic_decision`:

```json
{
  "table_id": "tblWAH8Ba3wh5jdo",
  "table_id_source": "FEISHU_TOPIC_TABLE_ID"
}
```

The real sender path now matches that table id under the same staging env:

```text
health_table_id=tblWAH8Ba3wh5jdo
health_source=FEISHU_TOPIC_TABLE_ID
sender_table_id=tblWAH8Ba3wh5jdo
same=true
```

`feishu_topic_decision_card.py build --run-id ar018-test --limit 1` can run without sending a card and uses the staging/test table path.

No production table writes, production card sends, production collection, or production SCF changes were performed.

## Required External Configuration

Before AR-013 Flow QA can send/click a real test card, PM/user must ensure:

- the test Tencent SCF receiver URL is configured in local staging env;
- cloud env vars for that receiver explicitly include staging/test `FEISHU_TOPIC_TABLE_ID=tblWAH8Ba3wh5jdo`;
- personal/test `FEISHU_CARD_RECEIVE_TARGETS` is configured;
- personal/test `FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS` is configured;
- sender/build table id and health check table id both point to `tblWAH8Ba3wh5jdo`;
- the test receiver app does not trigger production 06 watcher or production follow-up automation.

After those are configured, run the full gate command above and only then resume AR-013 Flow + Regression QA.
