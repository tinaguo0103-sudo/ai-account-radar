# AR-020E Production Release Gate

The ordinary `python3 scripts/pre_merge_check.py` command remains the development/RC pre-merge gate. It must run from `feature/next-production-flow` or an AR-020E RC branch and confirms that Topic Card sending is blocked outside production.

After an RC has passed release-gate QA and production main has been updated by an separately authorized release, run this explicit production check from the configured production worktree:

```bash
python3 scripts/pre_merge_check.py --production-release-check --expected-head <exact-origin-main-sha>
```

The production mode fails unless the root is the configured production worktree, the branch is `main`, the worktree is clean, and local HEAD, `origin/main`, and `--expected-head` are identical. It runs syntax, semantic-owner, failure-QA, and receiver tests, then invokes only:

```bash
python3 scripts/run_topic_card_if_fresh.py --check-only --no-notify
```

The output must be one parseable JSON object with `check_only=true` and `sent=false`, and Topic Card artifacts must remain byte-for-byte unchanged. Stale or no-candidate outcomes are safe passes. The mode has no skip/allow/fallback option and does not send notifications, cards, or write Feishu.

This gate does not deploy the SCF package, sync the global Skill, resume automations, or authorize production smoke. Those remain explicit release steps with their own backup/read-back/rollback evidence.
