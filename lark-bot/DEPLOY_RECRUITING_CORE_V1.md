# Deploy Recruiting Core V1

This release upgrades the existing Railway service in place. It does not delete
jobs, candidates, applications, channel history, or bot data.

## Before deployment

1. Create a PostgreSQL backup/snapshot and verify that it is readable.
2. Keep the current successful Railway deployment available for code rollback.
3. Upload the **complete release package**. Do not mix selected files from an
   older V14/V16/V19 package with this one.
4. Preserve all existing Railway variables. In particular do not expose or
   replace `DATABASE_URL`, Lark secrets, signing keys, worker tokens, or admin
   credentials.

## Expected startup

The deployment log must include:

```text
[db] schema ready: 20260722_recruiting_core_v1
```

The service root must return `ok`. A checksum conflict or migration exception is
a stop condition: do not delete tables and do not retry manual SQL. Retain the
log and restore the previous code deployment while investigating.

## Manager verification

1. Open the panel and confirm existing jobs and Channel Analytics history are
   still present.
2. Confirm each Open requisition that should use candidate search is linked to
   the intended read-only Talent Discovery Search Profile. Closed, paused, and
   unlinked requisitions must not appear in the search form.
3. In Talent Discovery, select the real hiring requisition, enter the exact
   formal-candidate count, and add HR names and allocation counts. The allocation
   total must equal the requested count; Review candidates never count toward it.
4. Confirm the page displays the linked search conditions as read-only. The
   manager must not have to re-enter country, metro, role expansion, or quality
   rules for each run.
5. Start a small authorised test search only after the Windows worker is online.
   A shortfall must remain unpublished. A fulfilled frozen cohort may create or
   append to the single `RecruitingYYYYMMDD` Lark table and assign exactly the
   configured number of rows to each HR.
6. Retry the same completed task. It must not create a duplicate Lark table or
   duplicate candidate rows. A simultaneous retry must be rejected while the
   first publication claim is active.
7. In Lark, confirm the daily table has exactly the canonical nine English
   fields: Date, Candidate Name, Candidate URL, Source Channel, Other Source
   Details, Hiring Job, Assigned HR, Status, and CV. The empty default `Table`
   must not remain when it is safe to remove.
8. Submit one valid test change through `/submit_channel_sheet`, refresh the
   website, and confirm Channel Analytics is derived from the same table. Repeat
   the identical submission and confirm it is an idempotent no-op.

## Safe rollback

The migration is additive and recorded once. If application code must be rolled
back, roll back the Railway deployment only. Do not remove migration rows or
new columns. A future fix must use a new migration version rather than editing
the SQL under `20260722_recruiting_core_v1`.

## Artifact policy

- The Lark `RecruitingYYYYMMDD` table is the only HR exchange artifact. The
  retired Channel Excel generation/upload endpoints remain fail-closed.
- Railway queues signed search tasks; the local Windows worker performs the
  bounded public search. Railway never receives browser sessions or Lark/HMAC
  secrets from the worker.
- Search publication requires an immutable fulfilled result, an unchanged Open
  hiring requisition/search-profile link, exact HR allocation, and an atomic
  publication claim. Any mismatch stops safely.
- Candidate URL plus hiring job is the publication identity. Retries never
  duplicate rows and never replace a different candidate by name matching.
