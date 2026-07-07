# AR-020C Editorial Thinking Chain Implementation

Date: 2026-07-07

## Goal

AR-020C moves AR-020B from "field contract is correct" to "the editorial Skill first explains why Austin would choose or not choose a source, then maps that judgment into 04 / Topic Card / 06 fields."

This implementation does not write production Feishu, send Topic Cards, run collection, trigger 06/Codex generation, deploy SCF, or sync the global private Skill.

## Implementation Boundary

- `skills/ai-account-editorial-director/SKILL.md` mirrors the two-stage contract for repo QA and future global Skill sync.
- `scripts/editorial_skill_runner.py` passes source facts and non-authoritative hints separately, and asks the real Skill for:
  - `editorial_thinking_json`
  - `field_mapping_json`
  - `主编判断摘要`
  - `标题思路`
- `scripts/topic_flow_rework.py` no longer lets theme clusters or translation hints author visible titles, Briefs, or experiment fields.
- `scripts/topic_field_contract.py` validates:
  - field-direction consistency;
  - AI Hot major-news/Austin-angle requirements;
  - generate-script readiness;
  - public editorial trace quality;
  - pre-Skill hint leakage;
  - batch-level title skeleton/template collisions.
- `scripts/topic_decision_fields.py`, `scripts/push_today10_to_feishu.py`, and `scripts/feishu_topic_decision_card.py` expose `主编判断摘要` and `标题思路` in 04 / Topic Card visible surfaces.
- `scripts/topic_skill_replay_evaluation.py` outputs the AR-020C PM sample package:
  - candidate universe;
  - actionable rows;
  - observe rows;
  - near-miss/high-fit unselected rows;
  - title body-check table;
  - one-page sample summary.

## Replay Evidence

Command used:

```bash
PYTHONPATH=scripts PYTHONPYCACHEPREFIX=/private/tmp/ai_account_radar_pycache python3 scripts/topic_skill_replay_evaluation.py --since 2026-07-01 --out-dir /private/tmp/ar020c_skill_replay_20260707_dev --content-csv /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar/output/runs/run_20260702_084335/content_items.csv --content-csv /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar/output/runs/run_20260703_083948/content_items.csv --content-csv /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar/output/runs/run_20260704_080730/content_items.csv --content-csv /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar/output/runs/run_20260705_102318/content_items.csv --content-csv /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar/output/runs/run_20260706_080330/content_items.csv --content-csv /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar/output/runs/run_20260706_085249/content_items.csv --content-csv /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar/output/runs/run_20260706_092517/content_items.csv --content-csv /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar/output/runs/run_20260707_082257/content_items.csv
```

Summary:

- content_items: 273
- candidate_count: 34
- pre_skill_pool_count: 16
- skill_rows: 16
- actionable_count: 3
- observe_count: 13
- rejected_count: 0
- contract_failure_count: 0
- fallback_row_count: 0
- reverse_flags: 0
- near_miss_count: 0
- title_quality_failure_count: 0
- title_quality_warning_count: 0
- writes_feishu: false

Output package:

- `/private/tmp/ar020c_skill_replay_20260707_dev/ar020c_user_sample_summary.md`
- `/private/tmp/ar020c_skill_replay_20260707_dev/skill_actionable.csv`
- `/private/tmp/ar020c_skill_replay_20260707_dev/skill_observe.csv`
- `/private/tmp/ar020c_skill_replay_20260707_dev/near_miss_high_fit_unselected.csv`
- `/private/tmp/ar020c_skill_replay_20260707_dev/title_body_check.csv`
- `/private/tmp/ar020c_skill_replay_20260707_dev/skill_replay_summary.json`

## QA Focus

QA should verify the actual downstream fields, not only the summary report:

- `主编判断摘要` mentions source evidence, Austin scene, action/experiment, and a tradeoff.
- `标题思路` explains the title/angle choice or why no publishable title is allowed.
- `生成脚本包` rows do not share one title skeleton.
- observe/supplement rows are visibly not equal to generated rows.
- deterministic fallback rows are not accepted as editorial quality.
- non-authoritative hints do not leak into visible title / proposition / Brief / experiment fields without Skill judgment.

## Remaining Release Note

The global private `ai-account-editorial-director` Skill was not modified in this dev task. Before production release, PM/production should decide whether to sync the repo mirror contract into the global private Skill or keep the runner prompt as the active contract owner.
