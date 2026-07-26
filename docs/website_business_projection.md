# Website business projection

`scripts/publish_website_projection.py` is the only supported Radar-to-Website
business projection entrypoint. It reads one explicit `run_id`; it never
discovers `latest`, combines runs, or changes the Radar result.

Required runtime values:

```bash
WEBSITE_PROJECTION_BEARER=<runtime-only-app-bearer> \
WEBSITE_PROJECTION_SIWC_BYPASS_BEARER=<runtime-only-sites-machine-bearer> \
python3 scripts/publish_website_projection.py \
  --run-id run_YYYYMMDD_HHMMSS \
  --revision 1 \
  --authority-identity radar-production:<released-commit> \
  --website-url https://<owner-only-site>
```

The optional outer Sites bearer is required only when the owner-only access
layer needs it. Neither credential may be written to source, arguments,
artifacts, or logs.

The Website commits one exact run and performs an exact read-back before
changing `active_run_id`. Identical run/revision/hash is a no-op; an identical
run/revision with a different hash is a conflict. A later stage uses a higher
revision. Publisher transport or read-back failure is typed and does not change
the Radar run result. No live automation change is part of this release.
