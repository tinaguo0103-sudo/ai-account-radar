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

No live automation change is part of this candidate.
