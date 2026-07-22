# Daily Recruiting Publication v2

## Scope

This release separates the trust boundary deliberately:

- Railway queues searches, shows manager results and records one publication
  command for the business date.
- Railway does not write the AI Talent Discovery SQLite database and does not
  publish Lark.
- The authorised Windows worker performs frozen-plan verification, backup,
  one-transaction controlled apply, idempotency verification and native Lark
  Sheet publication.

The published artifact is one editable Lark Sheet named
`RecruitingYYYYMMDD`. It contains one visible sheet for each configured HR and
one final visible `Recruiting Overview` sheet with four KPIs and seven charts.

## Railway deployment

1. Replace the contents of the repository's existing `lark-bot/` directory
   with this directory. Do not create a second nested directory.
2. Never upload a local `.env`, candidate export, SQLite database, frozen plan,
   backup, log or real credential.
3. Keep the existing Railway variables. Confirm that
   `NEXUS_TALENT_WORKER_TOKEN` is a random value of at least 32 bytes and is the
   same value used only by the authorised Windows worker.
4. Deploy. Startup applies the two additive, restartable migrations:
   `schema_20260722_talent_publication_queue_v1.sql` and
   `schema_20260722_talent_daily_publication_v2.sql`.
5. Confirm the service is Active and `/panel` loads.

## Authorised Windows worker

The local worker must retain the real Lark credentials, row-signing key and AI
Talent Discovery database. They do not belong in Railway or GitHub.

Copy `config/nexus.worker.local.ps1.example` to the Git-ignored
`config/nexus.worker.local.ps1`, set the endpoint and the same worker token, and
start read-only search processing with:

```powershell
.\scripts\start_nexus_search_worker.ps1
```

Only when manager-approved publication should be enabled on this host, use:

```powershell
.\scripts\start_nexus_search_worker.ps1 -EnablePublicationWrites
```

That switch does not publish by itself. It only allows the worker to claim a
manager-approved, hash-bound daily command.

## Required acceptance checks

- A preview completion creates no database, Lark, DailyBatch, Review Task or
  ContactActivity write.
- Manager approval is blocked while any search created on the same business
  date is Pending or Running.
- The queued command lists every included job cohort and contains no candidate
  names, URLs or profile data.
- All frozen plans share the exact database baseline.
- Controlled apply creates one backup and uses one transaction across jobs.
- A forced failure in the second job rolls back the first job.
- Workbook verification succeeds before Lark publication.
- Lark publication returns a native `/sheets/` URL, never a `/base/` URL.
- Replaying the same command creates no second workbook or database rows.

## Rollback

If Railway deployment fails, redeploy the previous successful GitHub commit.
The migrations are additive and keep historical rows. Do not drop PostgreSQL
tables. If a local controlled apply fails before commit, it rolls back. If a
failure occurs after the SQLite commit but before a verified Lark receipt, stop
and inspect the local publication journal and verified backup; do not click
publish again blindly.
