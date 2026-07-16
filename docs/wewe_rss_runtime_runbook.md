# wewe-rss fixed runtime and reauthentication

Scheduled collection uses one provider at `127.0.0.1:4000` and one worktree-independent data directory:

`~/.codex/ai-account-radar-runtime/providers/wewe-rss/data`

The scheduled path first invokes `python3 scripts/wewe_provider_refresh.py` and then runs `wewe_provider_health.py` with the resulting current-run receipt. It never starts Docker, discovers a browser, opens a login page, switches data directories, or accepts cached feed readability as login/freshness proof.

Typed states are `updated_with_new_items`, `updated_no_new_items`, `stale_cache`, `login_required`, and `provider_failed`. Only the first two permit WeChat to participate; no-new is valid and contributes zero new rows.

The owned watermark is `~/.codex/ai-account-radar-runtime/providers/wewe-rss/health/last_success.json`. It records refresh revision, provider refresh timestamp, accepted attempt/run identity, and latest accepted article publish time. A missing/malformed watermark is `stale_cache`; the scheduled path cannot bootstrap itself from historical cache. The watermark advances atomically only after downstream write/read-back succeeds.

The currently installed `cooderl/wewe-rss-sqlite` image exposes an asynchronous `GET /feeds/:id?update=true` path but no completion receipt bound to a caller attempt. That surface cannot prove a refresh completed in the current collection window. `wewe_provider_refresh.py` therefore returns typed `refresh_surface_unverifiable` until a receipt-capable fixed-provider adapter is released. A recent cache, unchanged revision, or an unbound provider timestamp is never accepted as today's refresh.

## Authorized migration

1. Keep automations paused and stop the provider normally.
2. Back up the complete current provider data directory and record its tree hash. Never copy a live/locked SQLite directory.
3. Copy the stopped data directory to the canonical location, start the same pinned provider with that bind mount, and read back database/account/feed metadata without exposing tokens.
4. Before the first authorized refresh, initialize the watermark from the stopped historical database in the production release thread. Record the baseline hashes. Daily automation cannot perform this authorization step.
5. If state is `login_required`, run `python3 scripts/start_wewe_rss_admin_chrome.py --foreground`. This uses only port `9334`, the canonical `wewe-rss-admin-chrome-profile`, and the local `/dash` URL.
6. The user completes any QR/login interaction locally. Do not capture QR, cookies, tokens, localStorage, or account identity.
7. Trigger the provider refresh through its local admin surface, then re-run provider health. Resume only after the refresh revision advances and yields `updated_with_new_items` or `updated_no_new_items`.

Rollback: stop provider and the exact 9334 listener, restore the complete backup while stopped, restore the prior bind mount, verify hashes, and keep automations paused if any identity or health check fails.
