# Website business projection

The daily workflow sends at most one terminal projection through
`scripts/website_publisher_client.py`. It reads one explicit `run_id`; it never
discovers `latest`, combines runs, or changes the Radar result.

The normal automation command contains only business workflow arguments:

```bash
python3 scripts/run_daily_workflow.py \
  --run-id run_YYYYMMDD_HHMMSS \
  --business-date YYYY-MM-DD
```

The publisher client reads `WEBSITE_PUBLISHER_CONFIG` or the ignored default
`output/state/website_publisher.json`. That file owns the endpoint, authority
identity, app bearer, and Sites machine bearer. These transport details never
appear in the automation Prompt or workflow arguments.

The Website commits one exact terminal run and performs an exact read-back.
An identical terminal payload is a no-op. Transport or read-back failure leaves
the local business run completed with `publish_status=pending`; the next daily
invocation retries only that terminal publish and never repeats collection,
enrichment, editorial, or scripting.

An accepted replacement for the scripts of one already-terminal exact run uses
the supported script-only refresh command:

```bash
WEBSITE_PUBLISHER_CONFIG=/owner-only/path/website_publisher.json \
python3 scripts/refresh_website_scripts.py \
  --workflow-db /owner-only/path/daily_workflow.sqlite3 \
  --run-id run_YYYYMMDD_HHMMSS \
  --business-date YYYY-MM-DD \
  --scripts-result /read-only/path/scripts_result.json
```

The command reads the terminal workflow database as the sole local authority,
rebuilds the canonical full-run projection in memory, and replaces only its
scripts before using the existing Website conditional refresh. It does not edit
the workflow database or checkpoints and does not run collection, enrichment,
editorial, or Skills. Exact scripts already visible on the Website return a
no-op without a POST.

No live automation change is part of this candidate.
