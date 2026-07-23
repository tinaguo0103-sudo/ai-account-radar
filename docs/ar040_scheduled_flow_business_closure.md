# AR-040 Scheduled Flow Business Closure

## Runtime Contract

- Douyin collection reuses one existing page target from the canonical Chrome listener on port 9333. It never creates, minimizes, starts, stops, or replaces the browser/profile.
- Each account navigation enables `Network` before navigation, waits for navigation-history commit and the expected account URL, then uses the works DOM only to click/scroll the existing page. Business extraction accepts only page-owned `GET` XHR responses at `/aweme/v1/web/aweme/post/` whose `sec_user_id` exactly matches the configured account. It reads the body through `Network.getResponseBody`; it never replays the API outside the page.
- `works_root_count > 0` is diagnostic only. Trusted works come from `aweme_list` public fields (`aweme_id`, `desc`, pinned state, `create_time`, author binding), scan at least 10 items, exclude pinned/seen/contaminated items, and select at most 3 unseen works. A valid exact-account list with no unseen item returns `updated_no_new_items`.
- If Chrome rotates the renderer attachment, the probe reopens only the DevTools WebSocket for the same fixed `targetId`. Recovery is bounded per consecutive transition and total reattachments remain auditable across a full account plan. It never closes or replaces the user's page. A permanently blank/lost shared target is one source-runtime failure; page-response failures remain typed and account-local while other configured accounts continue with zero failed-account artifact leakage.
- Trusted collection artifacts are authoritative for ordinary editorial candidates. Ordinary candidates do not open their display URL. Only high-risk hard facts require source-open and external research.
- Core output paths, environment, and Feishu DNS are checked before scheduled writes/sends. Optional telemetry path failure is a warning and cannot reverse a green exact write/read-back.
- Staging requires explicit table IDs and explicit test card targets. Table-name fallback into production is forbidden.
- The formal staging collection proof uses `run_daily_collection_job.py` with an explicit run ID and a current-run Douyin artifact owned by `daily_pipeline.py`. The wrapper writes and reads back staging 03 before its normal daily/downstream ledgers can report `downstream_usable=true`; callers cannot supply or patch that state.
- Staging uses the public sampler/finalizer entrypoints with explicit staging
  environment and exact-run inputs. The scheduled collection wrapper has no
  owned-input bypass or source substitution mode.

## Automation Audit

The definitions inspected on 2026-07-21 are `ai-rebuild`, `ai-04-rebuild`, and `ai-rebuild-2`. Their Prompt commands and business semantics remain correct:

- 08:00 invokes only `run_daily_collection_job.py --defer-editorial --no-notify`.
- 09:15 already states artifact-first ordinary candidates, high-risk-only research, candidate-local failure, exact 04 read-back, and optional console/telemetry behavior.
- 10:00 invokes only `run_topic_card_if_fresh.py` and forbids guard bypass.

Prompt changes are not required. Official configuration changes are required before production authorization because all three definitions currently use `cwds=["~"]`, and the observed 09:15 execution surface was read-only. Release operations must set the exact production repo as cwd and select a supported execution surface with project output write access and Feishu DNS/API access. Prompt `cd` cannot grant these capabilities. Development must not edit live automation TOMLs.

## Acceptance Matrix

| ID | Dev evidence | Independent QA staging evidence |
|---|---|---|
| R1 | Node public module regression proves existing fixed target only, Network capture on that page, and zero target/window mutation calls. | Fixed 9333 window, profile, target and one production-shaped account read-back. |
| R2 | Production-shaped sanitized page-owned response fixture plus live public probe prove exact account binding, item filtering, partial continuation and zero failed-account artifact leakage. | Public collection path against the dedicated test account/window. |
| R3 | Two otherwise-equivalent ordinary `owned_staging_input` artifacts, one with an unsupported display URL and one with an empty URL, receive equivalent sampler eligibility. Both retain fetch lineage through `today_10_topics.csv`, the exact candidate manifest and state-machine input; the non-empty URL remains display metadata and ordinary-candidate `source_open_calls=0`. Formal staging collection retains and reads back both through 03. | Same-run public collection and editorial state prove both identities at every boundary before staging 04; a high-risk unsupported sibling may fail candidate-locally without removing unrelated survivors. |
| R4 | High-risk unsupported source fails one candidate while unrelated ordinary candidates continue. | Same-run high-risk research failure plus survivor finalization. |
| R5 | Formal wrapper owns the same-run daily/usability ledger; finalizer is preflight-gated and staging IDs cannot fall back by table name. Feishu 04 create/update planning, unknown-write reconciliation and exact verification share the persisted identity `running batch + original source title + topic title`; display URL is not identity. The writer builds a complete non-ambiguous plan before any PUT/POST and stops on duplicate local or remote identity. | Real staging 03 and 04 exact write/read-back; a second identical finalizer creates zero records, preserves every remote record ID and reports no duplicate, then exact-run cleanup. |
| R6 | Recommended cards require the Chinese research summary/hook, confidence, structure, Austin angle and display evidence. Source URL and evidence gap are optional metadata: present values render, absent values create no placeholder or fake link. Card entrypoints distinguish build/send failure, dry-run preview, first send receipt and same-run idempotent zero-send; freshness, DNS and target guards remain authoritative. | Dry-run renders the exact 3 candidates including an empty-URL/empty-gap candidate, then one guarded personal test send with receipt and `already_sent_for_run` zero-send. |
| R7 | Preflight and telemetry tests distinguish optional unwritable telemetry from blocking DNS/external-write failure. | Inject unwritable telemetry during a green exact 04 read-back; synthetic DNS/API failure invokes no writer/sender. |
| R8 | Read-only automation audit records current Prompt/cwd and required official execution surface correction. | Official automation configuration read-back and equivalent cwd/write/DNS probe. |

## Independent QA Command Manifest

Use `config/ar040_staging_qa_manifest.json`. QA must use `.env.staging.local`, explicit staging table IDs, a test app, and a personal test receive target. Dev fixture success is not Staging Verified.

QA models the normal business path: write 04 once, rerun the same finalizer against the intact records, send one card, and immediately verify the same-run zero-send before cleanup. Deleting and recreating those 04 records with new remote record IDs, then attempting to reconcile an old card receipt, is a test-only residual outside AR-040 acceptance and must not be manufactured in QA.

Before the business flow, QA runs the supported provisioner in check-only mode and then, under the staging authorization, once in write mode:

```bash
AI_ACCOUNT_RADAR_ENV=staging python3 scripts/provision_ar040_staging_tables.py \
  --env-file /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev/.env.staging.local

AI_ACCOUNT_RADAR_ENV=staging python3 scripts/provision_ar040_staging_tables.py \
  --env-file /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev/.env.staging.local --write
```

The provisioner owns exactly `01 来源与采样__AR040_TEST` and `03 内容收件箱__AR040_TEST`. It reuses the current source/content field and view contracts, compares staging app/Base identity with the fixed production env reference in memory, never prints identities or secrets, creates no records, and atomically changes only `FEISHU_SOURCE_TABLE_ID` and `FEISHU_CONTENT_TABLE_ID`. A second successful write run is a no-op. Unknown create state is reconciled by exact test table name once and is never blindly recreated.

Rollback may delete only a table whose ID was recorded as `created` by this QA run, after all QA-owned dependent records are removed. Never delete a table that was merely `bound_existing`, never delete the generic placeholder, 04, 06, or any production resource. Restore the two env lines from the QA before-snapshot atomically.

## Release And Rollback

Release only after independent staging validation. Apply the product commit, then update official automation cwd/execution surface through the supported automation configuration path. Do not edit TOMLs directly. Roll back the product commit and restore the prior official automation configuration if fixed-window collection, staging 04 read-back, or guarded card idempotency regresses.
