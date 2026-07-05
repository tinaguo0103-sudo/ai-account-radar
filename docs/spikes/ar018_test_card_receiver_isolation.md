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

## 2026-07-04 Callback Write Failure Diagnosis

AR-018 Test Card Smoke Round 2 proved that the test card can be sent to the personal test target and rendered in Feishu Web, but clicking `本批都不选` did not update the staging/test 04 record.

The callback path was narrowed with a safe synthetic event against the isolated test receiver URL and the staging/test record:

- the request reached the isolated test SCF receiver;
- the receiver attempted to update staging/test table `tblWAH8Ba3wh5jdo`;
- Feishu rejected the update because `选择原因标签` was sent as an array while the actual staging/test field is `type=1 / Text`;
- production 04/06 and watcher paths were not touched.

The receiver and local sender now format `选择原因标签` by field shape:

- type `4` multi-select fields keep an array of option names;
- text fields receive a readable `、`-joined string;
- empty no-selection tags become an empty string for text fields.

This keeps the staging/test table compatible without assuming every environment has already migrated the reason-tag field to multi-select.

Deployment note: the local package was rebuilt with `npm run package:tencent-scf`, but this code still must be uploaded to the isolated test SCF `feishu-topic-card-receiver-ar018-test` before the next real test-card smoke. The available isolated Chrome profile is not logged in to Tencent Cloud, and no local `tccli`/deployment credential was available in the dev worktree.

## 2026-07-05 Real Button Callback Diagnosis

AR-018 Test Card Smoke Round 3 proved that the deployed test receiver can write staging/test `04` when called directly with a synthetic `submit_no_selection` event, but a real Feishu Web button click still did not update the staging/test record.

The diagnosis narrowed the failure to the Feishu app callback binding, not the receiver write logic:

- local staging health check still passed and pointed at `tblWAH8Ba3wh5jdo`;
- sender table-id probe and health check used the same staging/test table;
- Tencent SCF log query for `feishu-topic-card-receiver-ar018-test` could not confirm real clicks because the test function has not enabled log delivery;
- Feishu Open Platform callback config subscribed `card.action.trigger`, but the configured callback URL hash matched the production receiver URL from production `.env.local`;
- the configured callback URL hash did not match `.env.staging.local` `FEISHU_TENCENT_SCF_URL`;
- Feishu event log query for the real click window returned no `card.action.trigger` delivery result for the inspected app.

This means the real Web button is not proven to hit the isolated test SCF. The current app-level callback binding is production-shaped, while local staging sends cards with the same app credentials. Changing that callback URL to the test receiver would affect the app globally and could break production card callbacks, so it must not be done as an AR-018 test fix.

Next safe paths:

1. Create or use a separate Feishu test app/robot whose callback URL can point to `feishu-topic-card-receiver-ar018-test`.
2. Configure `.env.staging.local` to use that test app id/secret and personal test receive targets.
3. Keep the production app callback bound to the production receiver.
4. Enable log delivery on the test SCF or add a test-only redacted callback marker before the next real Web button smoke.
5. Rerun `check_feishu_card_cloud_receiver.py --require-test-card-config --table-key topic_decision`, then run a real test card smoke against the separate test app.

Until a separate test app or explicitly safe test callback binding exists, AR-018 should remain blocked for real button Flow QA even though synthetic receiver writes pass.

## 2026-07-05 AR-013 Direction Card / 06 Pre-Release Boundary

AR-013 pre-release testing needs to cover the path after selecting `生成脚本包`, without letting test clicks leak into the production `06` watcher.

Current isolated resources:

- test app / robot: independent AR-018 test app;
- test receiver: isolated Tencent SCF test receiver;
- test `04`: dedicated AR-018 test Base table, configured through `FEISHU_TOPIC_TABLE_ID`;
- test `06`: `06 完整脚本与制作包__测试`, configured through `FEISHU_SCRIPT_PACKAGE_TABLE_ID`;
- receive targets: personal/test targets in `.env.staging.local`.

Safe behavior verified with a synthetic test-only event:

- first card selection writes only the dedicated test `04`;
- selected records become `状态=生成脚本包`;
- selected records are queued with `制作方向卡状态=待发送`;
- the explicit queue action sends the production-direction card only to the test target and marks `制作方向卡状态=已发送`;
- submitting the production-direction card writes `我的制作补充` and `制作方向卡状态=已提交` on test `04`;
- no `06` record is created by the receiver path.

The `06` runner must use explicit staging table ids. `script_package_shared.feishu_ready_topics()` now prefers `FEISHU_TOPIC_TABLE_ID` / `FEISHU_TOPIC_DECISION_TABLE_ID` before falling back to the production table name. This keeps the runner, sender path, and health check on the same test table semantics.

Recommended pre-release QA path:

1. Send a clearly marked AR-013 test Topic Card from the staging/test app to the personal/test target.
2. In that card, select one test candidate under `生成脚本包` and submit.
3. Verify the dedicated test `04` record becomes `状态=生成脚本包` and `制作方向卡状态=待发送`.
4. Trigger the isolated test receiver queue action for `send_pending_production_direction_cards`.
5. Verify the test production-direction card arrives at the personal/test target and the same test `04` record becomes `制作方向卡状态=已发送`.
6. Submit the production-direction card with a short test-only note.
7. Verify the same test `04` record becomes `制作方向卡状态=已提交` and `我的制作补充` is populated.
8. For the `06` boundary, run the dev/staging runner with `--skip-codex --include-test-records --record-id <test_record_id>` and confirm it lists exactly the selected test record with `write_feishu=false`.

Do not run real `06` generation as part of this pre-release smoke unless PM/user explicitly authorizes it. Real generation would call Codex and may create test `06` records/documents; that is a separate L4 test, not required to prove AR-013 compensation-card selection and direction-card handoff.

Production safety checks for this boundary:

- production `04` / `06` read-back should have no AR-013 precheck marker;
- production output paths should have no AR-013 precheck marker;
- production launchd watcher should remain pointed at the production runtime and should not receive `.env.staging.local` or `--include-test-records`.
