# wewe-rss fixed runtime and reauthentication

Scheduled collection uses one provider at `127.0.0.1:4000` and one worktree-independent data directory:

`~/.codex/ai-account-radar-runtime/providers/wewe-rss/data`

The scheduled path first invokes `python3 scripts/wewe_provider_refresh.py` and then runs `wewe_provider_health.py` with the resulting current-run receipt. It never starts Docker, discovers a browser, opens a login page, switches data directories, or accepts cached feed readability as login/freshness proof.

## Refresh ownership

The project signed adapter is the only refresh owner. Upstream `cooderl/wewe-rss-sqlite:2.6.1` registers `handleUpdateFeedsCron` unconditionally with Nest Schedule; `CRON_EXPRESSION` changes timing but has no supported disable value, and a missing value restores `35 5,17 * * *` in `Asia/Shanghai`. Invalid or impossible cron expressions are not an accepted disable mechanism.

Production therefore uses the project-built image `ai-account-radar/wewe-rss-sqlite:2.6.1-ar039-no-cron`, built from upstream commit `f88b023961804b986f3f1225c52d5066928df3c1` using `providers/wewe-rss-no-internal-cron/Dockerfile`. Its only source change removes the `@Cron` registration and handler. QR login, bearer token storage, feed refresh API, SQLite schema and data directory are unchanged. The image and container labels are mandatory read-back evidence; run `python3 scripts/wewe_runtime_contract.py` after deployment. `CRON_EXPRESSION` must not be present.

Official `latest` and `v2.6.1` resolve to the same archived release line; pulling `latest` or changing tags is not this repair. Do not switch to an unknown third-party image. `we-mp-rss` remains a separate future provider evaluation and is not part of this ownership change.

Do not add keepalive refreshes or provider probes that mutate feed/account state. Each normal business run may invoke the existing bounded signed adapter once. A provider `401` remains a typed WeChat source-local failure; it contributes zero WeChat rows while safe Douyin/AIHOT and editorial work continue under the Business Continuity contract.

Build and release are separate authorized production actions. Build the pinned Dockerfile, stop the old container normally, back up the stopped canonical data directory, recreate the same canonical container name/port/data bind with the project image, and read back the runtime contract before reauthentication. Never patch a running container or use an invalid/future cron expression. Rollback recreates the prior pinned image with the untouched stopped-data backup and keeps WeChat isolated until its ownership state is known.

Typed states are `updated_with_new_items`, `updated_no_new_items`, `stale_cache`, `login_required`, and `provider_failed`. Only the first two permit WeChat to participate; no-new is valid and contributes zero new rows.

The owned watermark is `~/.codex/ai-account-radar-runtime/providers/wewe-rss/health/last_success.json`. It records refresh revision, provider refresh timestamp, accepted attempt/run identity, and latest accepted article publish time. A missing/malformed watermark is `stale_cache`; the scheduled path cannot bootstrap itself from historical cache. The watermark advances atomically only after downstream write/read-back succeeds.

The public `GET /feeds/:id?update=true` controller returns without awaiting `updateFeed()` and is not used by automation. The fixed adapter uses the protected local tRPC mutation `POST /trpc/feed.refreshArticles`, whose router awaits `refreshMpArticlesAndUpdateFeed(mpId)`. That implementation commits the article transaction and then updates the feed's `sync_time`; errors do not advance it. `wewe_provider_refresh.py` holds an exclusive canonical lease, requests every exact active feed through this synchronous endpoint, then reads canonical SQLite and requires every feed's `sync_time` to advance beyond both its before value and the request start. It writes an atomic hashed receipt only after the complete feed set advances. A recent cache, stable value, partial feed set, asynchronous GET response, or HTTP status alone is never accepted.

The scheduled runtime must provide `WEWE_RSS_AUTH_CODE` through its existing private environment. The adapter uses it only as the local tRPC authorization header and never writes it to receipts, telemetry, stdout, or Git. Missing authorization fails before a refresh receipt can exist. The fixed scheduled path has no provider URL, data directory, browser profile, or port override.

## Refresh attestation trust boundary

The installed provider cannot persist a caller nonce in its refresh transaction without a custom image and data migration. AR-034 therefore uses a dedicated runtime HMAC attestation key rather than pretending mutually hashed JSON proves who created it. The key lives outside provider data and health artifacts at `~/.codex/ai-account-radar-runtime/providers/wewe-rss/secrets/wewe-refresh-attestation.key`, must be a regular single-link owner-only file, and is provisioned only by an authorized release task. It is never generated by the scheduled task, stored in Git, or emitted to receipts/logs.

The adapter signs the immutable lease record before reading/requesting, signs the request-bound attempt lineage before provider calls, and signs the final receipt after all-feed completion. The verifier independently reads the runtime key and validates all three signatures plus their canonical path, hash, identity and relational contracts. Hand-constructed canonical JSON without the key fails. Arbitrary code executing as the same Unix runtime identity and able to read the key remains the explicit final trust boundary; OS account and key-file permissions are therefore part of production provisioning and rollback.

Provisioning must create the canonical secrets parent as mode `0700` and the key as mode `0600`, then read back that both are owned by the runtime UID without printing key bytes or a key hash. The non-secret key ID is only a version identifier. The loader opens the parent with no-follow directory semantics, validates owner/mode on that fd, opens the key relative to the directory fd with `O_NOFOLLOW`, then performs `fstat` and every read on that same key fd.

Machine evidence uses `secret_material_read` to state whether protected material was actually loaded and `secrets_exposed=false` to state that no value was emitted. Normal refresh and receipt verification report material read; check-only planning reports no material read. The ambiguous `secrets_read` field is not emitted by this adapter or verifier.

## Authorized migration

1. Keep automations paused and stop the provider normally.
2. Back up the complete current provider data directory and record its tree hash. Never copy a live/locked SQLite directory.
3. Copy the stopped data directory to the canonical location, start the same pinned provider with that bind mount, and read back database/account/feed metadata without exposing tokens.
4. Before the first authorized refresh, initialize the watermark from the stopped historical database in the production release thread. Record the baseline hashes. Daily automation cannot perform this authorization step.
5. If state is `login_required`, run `python3 scripts/start_wewe_rss_admin_chrome.py --foreground`. This uses only port `9334`, the canonical `wewe-rss-admin-chrome-profile`, and the local `/dash` URL.
6. The user completes any QR/login interaction locally. Do not capture QR, cookies, tokens, localStorage, or account identity.
7. Trigger the provider refresh through its local admin surface, then re-run provider health. Resume only after the refresh revision advances and yields `updated_with_new_items` or `updated_no_new_items`.

Rollback: stop provider and the exact 9334 listener, restore the complete backup while stopped, restore the prior bind mount, verify hashes, and keep automations paused if any identity or health check fails.
