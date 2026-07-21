# AR-040 Scheduled Flow Business Closure

## Runtime Contract

- Douyin collection reuses one existing page target from the canonical Chrome listener on port 9333. It never creates, minimizes, starts, stops, or replaces the browser/profile.
- Each account navigation waits for the expected account URL and works-grid DOM. A permanently blank shared target is one source-local runtime failure with zero Douyin artifacts.
- Trusted collection artifacts are authoritative for ordinary editorial candidates. Ordinary candidates do not open their display URL. Only high-risk hard facts require source-open and external research.
- Core output paths, environment, and Feishu DNS are checked before scheduled writes/sends. Optional telemetry path failure is a warning and cannot reverse a green exact write/read-back.
- Staging requires explicit table IDs and explicit test card targets. Table-name fallback into production is forbidden.

## Automation Audit

The definitions inspected on 2026-07-21 are `ai-rebuild`, `ai-04-rebuild`, and `ai-rebuild-2`. Their Prompt commands and business semantics remain correct:

- 08:00 invokes only `run_daily_collection_job.py --defer-editorial --no-notify`.
- 09:15 already states artifact-first ordinary candidates, high-risk-only research, candidate-local failure, exact 04 read-back, and optional console/telemetry behavior.
- 10:00 invokes only `run_topic_card_if_fresh.py` and forbids guard bypass.

Prompt changes are not required. Official configuration changes are required before production authorization because all three definitions currently use `cwds=["~"]`, and the observed 09:15 execution surface was read-only. Release operations must set the exact production repo as cwd and select a supported execution surface with project output write access and Feishu DNS/API access. Prompt `cd` cannot grant these capabilities. Development must not edit live automation TOMLs.

## Acceptance Matrix

| ID | Dev evidence | Independent QA staging evidence |
|---|---|---|
| R1 | Node public module regression proves existing fixed target only and zero target/window mutation calls. | Fixed 9333 window, profile, and one production-shaped account read-back. |
| R2 | Delayed navigation reaches works grid; permanent blank becomes one source runtime failure and zero artifacts. | Public collection path against the dedicated test account/window. |
| R3 | Exact-input public state-machine test proves unsupported non-empty URL and empty URL both survive with `source_open_calls=0`. | Test run enters editorial state and staging 04. |
| R4 | High-risk unsupported source fails one candidate while the ordinary survivor remains. | High-risk research failure fixture plus survivor finalization. |
| R5 | Finalizer is preflight-gated; staging IDs cannot fall back by table name. | Real staging 04 write and exact run/record read-back. |
| R6 | Card entrypoint retains freshness/idempotency and blocks DNS failure before sender subprocess. | One guarded personal test send, ledger proof, second-run no-send. |
| R7 | Preflight and telemetry tests distinguish optional unwritable telemetry from blocking DNS/external-write failure. | Inject unwritable telemetry path during a green staging write/send. |
| R8 | Read-only automation audit records current Prompt/cwd and required official execution surface correction. | Official automation configuration read-back and equivalent cwd/write/DNS probe. |

## Independent QA Command Manifest

Use `config/ar040_staging_qa_manifest.json`. QA must use `.env.staging.local`, explicit staging table IDs, a test app, and a personal test receive target. Dev fixture success is not Staging Verified.

## Release And Rollback

Release only after independent staging validation. Apply the product commit, then update official automation cwd/execution surface through the supported automation configuration path. Do not edit TOMLs directly. Roll back the product commit and restore the prior official automation configuration if fixed-window collection, staging 04 read-back, or guarded card idempotency regresses.
