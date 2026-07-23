# Multi-job observable sourcing

This release keeps the existing Telegram/Task Bot code and adds the Talent
Discovery search workflow below.

## What changed

- Managers may select multiple Open Job Requisitions in one run.
- Selected jobs that share one Talent Discovery search profile are searched as
  one job family, avoiding duplicate searches.
- HR candidate counts remain one overall target. The server divides that target
  across the selected search families while preserving the exact total.
- All families belong to one `search_run_id`. The final publication is created
  once, only after every family in that run has finished successfully.
- The Talent Discovery page reports the current phase, percentage, observed
  candidates, selected candidates, completed families, and total families.
- Expired task leases are safely re-queued. Do not click Search again when the
  page says a lease is being recovered.
- iGaming evidence improves relevance but is not a universal hard requirement.
  Related and transferable Sales / Customer Service roles remain eligible.

## Deployment

1. Replace the Railway repository's `lark-bot` folder with this folder's files.
   Do not upload `__pycache__`, `.pyc`, or a local `.env`.
2. Wait until the Railway deployment reports `Deployment successful`.
3. Restart the local Worker so it loads the progress-reporting code:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\Wyn\Projects\AI-Talent-Discovery\scripts\manage_nexus_worker_startup.ps1" -Action Restart
```

4. Refresh Talent Discovery and confirm the top-right indicator says the Worker
   is online and the search browser is ready.

No new Railway environment variables are required.

## Normal daily operation

1. Open the Job Requisitions page and keep the real hiring positions Open.
2. In Talent Discovery, select one or more positions.
3. Enter HR names and the number each HR should receive. This is the overall
   target for the entire run, not a separate quota for each selected position.
4. Click the search button once.
5. Watch the progress on the same page. The Worker searches each unique job
   family, preserves completed family results, and creates one final daily
   recruiting workbook after all families finish.
6. Open the completed workbook from the page or request today's recruiting
   workbook from Recruitment Bot.

## Current matching rule

The Open Job Requisition dropdown in the generated workbook contains every
currently Open hiring job. Automated search is grouped by linked search
profile; this release does not yet perform a second AI pass that assigns every
candidate a primary and alternate requisition. That deterministic
primary/alternate assignment is recorded as a later quality enhancement, not a
requirement for this release.

The run has one manager-visible total and one final publication, but the total
is currently divided into balanced family coverage targets before scanning.
If one family cannot fill its share, the run stops with an explicit shortfall
instead of silently replacing that family with unrelated candidates. Adaptive
cross-family top-up requires the existing portfolio selector to be connected to
the Worker publication contract; it must not be simulated by weakening location
or professional-evidence rules.
