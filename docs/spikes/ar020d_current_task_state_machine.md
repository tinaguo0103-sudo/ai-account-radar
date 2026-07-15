# AR-020D current-task editorial state machine

## Decision

AR-020D no longer starts a nested model process. The active editorial engine is the current Codex task that is already running the development, QA, or production outer automation. Python owns only deterministic input preparation, state transitions, hashes, validation, and artifact generation.

The legacy `topic_skill_replay_evaluation.py --engine codex` path is disabled and fails visibly. It is not a fallback or QA path.

## Protocol

```text
prepare-stage1
  -> current-task Stage 1 output x 7
  -> validate-stage1 x 7
  -> prepare-ranking
  -> current-task global ranking output x 1
  -> validate-ranking
  -> prepare-stage2
  -> current-task Stage 2 output x 7
  -> validate-stage2 x 7
  -> finalize
```

Official entrypoint:

```bash
PYTHONPATH=scripts python3 scripts/topic_editorial_state_machine.py <command> --out-dir <isolated-output-dir>
```

The current task writes only the expected `output.pending.json` files. It never edits validator output, ranked decisions, final rows, or quality artifacts.

## Ownership

- Stage 1 owns canonical selection intent, title, angle, title rationale, public summary, and recommendation intent.
- Global ranking owns final daily level, final action, produce state, rank, and public tradeoff across the whole day.
- Stage 2 owns operational fields only. Owner-field output is rejected even if normalization could later restore the locked value.
- Guards reject invalid output; they do not author editorial quality.
- Persona/cases are embedded as style and judgment reference only. They are not source evidence and cannot appear as a case anchor or citation.

## Data minimization

Stage 1 input allows source type, platform, account, clean original title, short excerpt, title hook, source weight/market validation, AI Hot major-news evidence, and account directions. Links, fingerprints, crawl state, failure reasons, local paths, deterministic angle/title hints, mother scenes, and old 04 fields remain only in the local source manifest.

## State and recovery

`editorial_state_machine.json` records each stage and batch as `prepared`, `started`, `completed`, or `failed`, plus input/output hashes and timestamps.

- A failed validator blocks every later stage.
- Replacing a failed `output.pending.json` and rerunning its validator is the retry path.
- Re-running validation with the same completed output hash is a no-op resume.
- Changed input hashes are stale and fail before output is consumed.
- `prepare-stage1 --resume` is allowed only when all source CSV hashes still match.

## Production release boundary

This development task does not modify production automation or the global private Skill. Release requires:

1. merge the repo mirror and state-machine code;
2. sync the repo Skill to the production global private Skill through the existing controlled sync path;
3. read back Skill/persona hashes;
4. update the outer Codex automation to follow the same state-machine commands;
5. run isolated staging/production smoke without Feishu writes or Topic Card sends until explicitly authorized.
