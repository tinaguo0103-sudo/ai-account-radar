# AR-020E Production Outer Task Protocol

This is the Git-managed source for the 09:15 outer Codex automation prompt. The
outer task is the editorial engine. It must not start a nested model CLI, an API,
subagent, deterministic editorial logic, or a second model session.

1. Read today's deferred collection log and resolve exactly one same-day
   `run_id` and non-empty `output/runs/<run_id>/today_10_topics.csv`. Fail closed
   instead of selecting an older run.
2. Run `python3 scripts/ar020e_daily_editorial_entrypoint.py --check-only
   --run-id <run_id> --input <today_10_topics.csv>`. Stop if it is not ready.
3. Run the current-task state machine in this order:
   `prepare-source-open -> current task exact-source capture ->
   validate-source-open -> prepare-research -> current task web research ->
   validate-research -> prepare-stage1 -> current task editorial decisions ->
   validate-stage1 -> prepare-ranking -> current task global 0..N ranking ->
   validate-ranking -> prepare-stage2 -> current task operational mapping ->
   validate-stage2 -> finalize`.
4. Every exact source and opened research result must retain raw evidence,
   hashes and literal excerpts. Candidate failures remain excluded and visible;
   partial runs remain non-green.
5. Use the global `ai-account-editorial-director` only after its deployed hash
   has been verified against the release manifest. Persona material is style
   and judgment reference only, never source evidence.
6. Ranking orders all eligible rows and never caps or truncates them. Stage 2
   cannot rewrite decision, title, angle, rationale, recommendation or rank.
7. Only after finalize succeeds, run
   `python3 scripts/finalize_daily_pipeline_after_editorial.py --run-id <run_id>
   --write-feishu --update-scheduled-log`. This write command is forbidden in
   check-only, RC preparation and dry-run regression.

Any missing source/research evidence, stale hash, ownership drift, semantic
fallback, missing page identity or incomplete review fails closed. No legacy
editorial path is permitted.
