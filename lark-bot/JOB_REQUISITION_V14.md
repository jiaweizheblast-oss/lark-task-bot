# Recruiting foundation v14

## One business concept per layer

`Talent Discovery Search Profile` describes how public evidence is searched and
scored. It may contain many related titles, graduate paths, transferable roles,
metro/country expansion and iGaming relevance. It is read-only in Nexus and
never appears as an operational vacancy merely because it exists.

`Operational Job Requisition` describes a vacancy the company is actually
hiring now. It owns the HR owner, location, resume target, headcount target and
lifecycle. It may optionally link to one Talent Discovery Search Profile.

`Candidate` is one person. `Candidate Application` is that person in one job.
Stage, Entry Date, Stage Started On, source and HR owner belong to the
application. This permits one person to be considered for several jobs without
overwriting another job's history.

## Job lifecycle

- Draft: manager can edit; absent from HR selectors.
- Open: accepts new candidates and appears in Channel Analytics/Excel/Lark.
- Paused: existing applications can progress; new candidates are rejected.
- Closing: existing applications can finish; new candidates are rejected.
- Closed: immutable terminal state; history remains; cannot reopen or delete.

There is no hard delete. A title rename keeps aliases and a stable `job_ref`.

## Revision rules

- `definition_revision`: title, country, location, department or linked search
  profile changed.
- `operations_revision`: owner or operating targets changed.
- `catalog_revision`: a change that affects HR job choices.
- `record_version`: optimistic concurrency version.

The Contact Ready quota is a Talent Discovery search setting. It is not a Job
Req operating target and is intentionally absent from the Job Reqs page.

## Workbook safety

Every website XLSX contains:

- immutable artifact ID, schema version and Asia/Kolkata generation date;
- signed snapshot of job refs, displayed titles and catalog revisions;
- a signed opaque identity token for every usable row;
- hidden system columns and protected cells;
- editable HR command cells only.

Adding a job does not invalidate an older workbook. A rename resolves through
stable `job_ref`. Owner/target edits do not invalidate it. A new row for a job
that became Paused/Closing/Closed is rejected at submit time, while an existing
application may still advance. A modified token, stale date, wrong artifact or
unknown job fails closed.

## Lark safety

The persistent Lark Base uses the same active operational job catalog. Search
profiles never enter its dropdown. Before import the service reads the Base
twice and compares record IDs, modification times and canonical field content.
If HR edits during submission, no import starts and retry is safe. Old title
aliases remain resolvable for existing rows; server-side status checks prevent
new rows from entering inactive jobs.

## Migration guarantees

The SQL migration is additive and idempotent:

- existing Core-managed rows become `search_profile` records;
- existing local jobs become operational requisitions with stable refs;
- every legacy candidate workflow is copied into one Candidate Application;
- every legacy stage event is copied into application history;
- original tables and rows are retained for compatibility;
- foreign keys use `RESTRICT` for identity/history and never cascade-delete
  applications.

No production database or Lark Base is modified by building this package.
