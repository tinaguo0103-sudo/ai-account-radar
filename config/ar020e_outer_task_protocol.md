# AR-020E Production Outer Task Protocol

This is the Git-managed source for the 09:15 outer Codex automation prompt. The
outer task is the editorial engine. It must not start a nested model CLI, an API,
subagent, deterministic editorial logic, or a second model session.

1. Read today's deferred collection log and resolve exactly one same-day
   `run_id` and non-empty `output/runs/<run_id>/today_10_topics.csv`. Fail closed
   instead of selecting an older run.
2. Run `python3 scripts/ar020e_daily_editorial_entrypoint.py --check-only
   --run-id <run_id> --input <today_10_topics.csv>`. Stop if it is not ready.
   This owned gate verifies the persistent Git-managed Skill release manifest
   against both the repo mirror and the deployed global Skill. The outer task
   must not manually search chats, `/private/tmp` artifacts, or ad-hoc release
   notes as a substitute for `manifest_verified=true`.
3. Validate and lock the exact same-day candidate universe before source-open:
   `topic_editorial_state_machine.py check-exact-input --run-id <run_id>
   --exact-input-csv <today_10_topics.csv> --exact-input-sha256 <sha256>`.
   Then run `prepare-source-open` with the same three exact-input arguments.
   This mode consumes every CSV row in order and must not invoke the
   `content_items.csv` shortlist/resampling path or add replacement candidates.
4. Run the current-task state machine in this order:
   `prepare-source-open -> current task exact-source capture ->
   validate-source-open -> prepare-research -> current task web research ->
   validate-research -> prepare-stage1 -> current task editorial decisions ->
   validate-stage1 -> prepare-ranking -> current task global 0..N ranking ->
   validate-ranking -> prepare-stage2 -> current task operational mapping ->
   validate-stage2 -> finalize`.
5. Every exact source and opened research result must retain raw evidence,
   hashes and literal excerpts. Candidate failures remain excluded and visible;
   partial runs remain non-green.
6. Use the global `ai-account-editorial-director` only after
   `ar020e_daily_editorial_entrypoint.py --check-only` reports
   `manifest_verified=true`. Persona material is style and judgment reference
   only, never source evidence.
7. Ranking orders all eligible rows and never caps or truncates them. Stage 2
   cannot rewrite decision, title, angle, rationale, recommendation or rank.
   For an actionable AIHOT row, Stage 1 must also author
   `aihot_significance_rationale` and its current research evidence IDs. Stage 2
   may only copy that locked value to `AIHOT重大性说明`; deterministic candidate
   logic and cross-field substitution are forbidden.
8. Only after finalize succeeds, run
   `python3 scripts/finalize_daily_pipeline_after_editorial.py --run-id <run_id>
   --write-feishu`. This write command is forbidden in
   check-only, RC preparation and dry-run regression.

Any missing source/research evidence, stale hash, ownership drift, semantic
fallback, missing page identity or incomplete review fails closed. No legacy
editorial path is permitted.
