# WeWe RSS Runtime Runbook

The scheduled WeWe source has one business path:

1. acquire `output/state/wewe-refresh/refresh.lock`;
2. send exactly one refresh request to the canonical local provider;
3. read the live canonical SQLite database immediately;
4. emit current-run rows or a typed source failure;
5. release the lock.

The lock is the only scheduled mutable coordination file. It is project-owned
and ignored by Git. A dead, expired owner may be removed so a stale mutex cannot
permanently stop collection. No recovery journal is written.

Provider SQLite data, credentials and container identity remain in the
owner-only canonical runtime and are read only. The scheduled adapter does not
read a signing key, prior success marker, cached result, historical receipt or
legacy health state.

`wewe_provider_refresh.py --check-only` acquires and releases the project lock,
reads the configured SQLite database, and performs zero provider requests. It is
the public filesystem preflight for the scheduled execution surface.

A refresh failure contributes zero WeChat rows. It never substitutes prior
rows. Safe Douyin and AIHOT rows continue through the shared owner/candidate
plan.

The current-run result may record the exact run, status, request count/timing
and before/after database counts. It is operational output, not proof or an
authorization mechanism.

Rollback restores the previous released source code. It does not modify
provider data or replay a refresh.
