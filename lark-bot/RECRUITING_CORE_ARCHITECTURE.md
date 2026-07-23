# Recruiting Core Architecture

Ratified: 2026-07-22
Reporting timezone: `Asia/Kolkata`

This document is the design authority for Talent Discovery, Job
Requisitions, the unified daily recruiting table, Channel Analytics, and the
Recruitment Bot. A convenient UI must never weaken these invariants.

## 1. Ownership boundaries

- **AI Talent Discovery / Recruiting Core** owns candidate identity, public
  evidence, sourcing quality, candidate-to-job matching, contact workflow,
  do-not-contact rules, immutable activity history, and Preview -> Freeze ->
  Apply.
- **Nexus recruiting operations** owns operational job requisitions, manager
  analytics projections, HR assignment, and delivery of signed HR artifacts.
- **Lark and Excel are command transports and projections, not databases of
  record.** They may collect HR input but cannot invent identity, infer a job
  from a title, overwrite history, or bypass the application service.
- The two products may use different physical databases. Integration is
  through versioned contracts and stable references, never by importing each
  other's internal Python modules or granting a second direct writer.

## 2. Stable identity

The logical unit of recruiting operations is one **candidate x hiring-job
application**. The following references have distinct purposes:

- `candidate_id`: stable person identity.
- `application_ref`: stable candidate x hiring-job application identity.
- `job_ref`: immutable requisition identity; a displayed title may be renamed.
- `artifact_id`: one generated Excel or Lark command artifact.
- `row_ref`: one signed row inside an artifact.
- `submission_event_id`: one user intent. Retrying the same intent with the
  same payload is an idempotent no-op; reusing it with different content is a
  conflict.
- `record_version`: optimistic concurrency version. An old artifact cannot
  overwrite a newer application.

Names, profile URLs, displayed job titles, table row numbers, and free text are
never identity keys.

## 3. Non-negotiable write rules

1. Every accepted HR row is applied in one database transaction: receipt,
   application, stage event, dates, and version either all commit or all roll
   back.
2. First-touch source and `job_ref` are immutable after the application is
   created. Ordinary HR uploads cannot rewrite them.
3. Stage history is append-only. Current state is a projection of that history.
   Terminal states (`Hired`, `Rejected`, `Withdrawn`, `Resigned`) cannot be
   silently reopened, and accidental backward transitions fail closed.
4. Closing a job prevents new applications while allowing existing applicants
   to finish their workflow. Closed jobs and their history are never deleted.
5. Work already in progress at go-live is a **baseline import**. It establishes
   the current stage without pretending earlier events occurred today.
6. Duplicate signed row references, copied system tokens, signature mismatch,
   version conflict, unknown job, or changed immutable fields are rejected.
   There is no fallback to fuzzy matching.
7. Candidate, application, activity, and completed review history are never
   deleted or overwritten to make an import succeed.

## 4. Unified daily recruiting table

Each operational day has one table named `RecruitingYYYYMMDD`. Talent
Discovery candidates and HR-added candidates use the same nine-column schema:

1. Date (system-created time)
2. Candidate Name
3. Candidate URL
4. Source Channel
5. Other Source Details (required only for `Other`)
6. Hiring Job
7. Assigned HR
8. Status
9. CV

Talent Discovery pre-fills identity, source, job, and HR assignment. HR may add
external candidates using the same schema. `Hiring Job` must come from an open
Job Requisition. The daily artifact retains the frozen job, channel, and HR
catalog with which it was created; later catalog changes never reinterpret an
old row.

The system assigns work across the configured HR roster before publication.
HR members work in the same table and use Assigned HR filters or views. A
manager submission imports the table idempotently. Repeating an unchanged
submission is a no-op.

Excel is a dated, frozen, signed fallback artifact. It must mirror the same
schema and validation but is not a free-form alternative workflow.

## 5. Current snapshot versus historical activity

The manager dashboard deliberately separates two questions:

- **Current Recruiting Portfolio** answers "where is every application now?"
  It includes baseline rows and shows total applications, in-progress, hired,
  rejected, status distribution, source mix, HR workload, and hiring-job mix.
- **Recruiting Activity** answers "what actually happened in the selected date
  window?" It is derived only from accepted intake and immutable stage-change
  events.

Historical stages that happened before go-live are unknown and are never
reconstructed. Baseline applications therefore appear in the current snapshot
but not as historical intake, interview, offer, or rejection events. All
changes after go-live are recorded forward-only.

Metric rules:

- `source_received_on`: first accepted intake time. It counts as a new
  application only when the row is not a baseline import.
- `stage_started_at`: time of the most recent accepted stage transition. It is
  system-managed and supports Days in Stage.
- `system_imported_at`: system audit time, not a recruiting event.
- Entered Interview: the first real transition that reaches `Interview` or a
  later successful stage in the selected period.
- Reached Offer: the first real transition that reaches `Offer` or `Hired` in
  the selected period.
- Rejected: the first real transition to `Rejected` in the selected period.
- Source attribution is first-touch and immutable.

## 6. Manager analytics hierarchy

### Website

The website is the full manager analytics surface:

- Top: four current-portfolio KPIs.
- Middle: four current-state charts (status, source, HR workload, hiring-job
  mix).
- Lower: activity KPIs plus intake/stage trend, source comparison, hiring-job
  results, date/job/source/HR filters, Days in Stage, and the detailed
  application list.

Snapshot cards are not affected by a historical date filter. Activity charts
are. A selected hiring job may narrow both when explicitly requested.

### Lark

The Lark Base is the operational HR surface. The supported production contract
creates the daily table, fields, dropdown options, frozen assignments, and
submission link. The public Base API does not provide a sufficiently stable
contract for programmatically constructing and maintaining the complete chart
dashboard used by the website.

A future Lark Overview may be distributed only by cloning a manager-approved,
versioned Base template and validating its schema and chart bindings after the
clone. Until that end-to-end path is implemented, the website remains the
authoritative chart surface and the application must not claim that a Lark
dashboard was generated.

## 7. Database evolution

Production schema changes use immutable, checksummed migration versions under
a PostgreSQL advisory lock. A migration is applied once and atomically.
Reusing a version with different SQL is refused. Recoverable legacy rows get
stable recovery references; unresolved anomalies are recorded for manager
review instead of crashing every future deployment.

## 8. Delivery phases

### Internal-trial foundation implemented now

- Unified daily recruiting table and one manager submission path.
- Stable job/application/artifact/row/event references.
- Row-atomic writes, immutable submission ledger, optimistic concurrency, and
  state-transition validation.
- Baseline imports and event-based funnel metrics.
- Current snapshot plus forward-only recruiting activity dashboard.
- Frozen Excel catalogs, formula-injection protection, XLSX macro/external-link
  rejection, and duplicate-token rejection.
- Closed-job compatibility for existing applications.
- Bounded Lark reads, revision barrier, and fail-closed partial-sync status.
- Immutable database migration ledger and legacy anomaly capture.

### Next without disrupting internal use

- `Next Action On`, per-stage SLA, overdue views, and reminders.
- Incremental Lark synchronization using modification checkpoints with periodic
  full reconciliation as a guard.
- Projection outbox, retry ledger, and reconciliation health page.
- Append-only owner/note history and controlled HR roster identity.
- Manager-approved Lark Overview template cloning and post-clone conformance
  verification.
- Expand the deterministic role-family ontology beyond exact titles. Sales,
  customer service, operations, marketing, technology, product, design,
  compliance, and finance profiles may include evidence-backed transferable
  roles, graduates, trainees, and entry-level candidates where the hiring
  policy allows it.
- Rank explicit iGaming experience as positive evidence, never as a universal
  hard requirement. Country, configured location policy, role evidence,
  executive exclusions, and do-not-contact rules remain authoritative.
- Add primary and alternate Job Requisition recommendations after one
  cross-family deduplication pass. A candidate is counted once toward the
  overall manager target even when several Open requisitions are plausible.
- Add optional family minimums and maximums only when a manager needs a
  deliberate mix. The normal daily flow uses one overall quota and one final
  frozen publication rather than forcing a separate quota per title.

### Long-term scale and compliance

- SSO/RBAC, retention and archive partitions, HMAC key rotation, backup/restore
  drills, and audit export.
- Global do-not-contact enforcement owned by Recruiting Core.
- Recruiting-cycle attribution, hire/headcount/cost metrics, and explicit
  reconciliation of unidentified batch counts without double counting.
- Optional AI provider for ranking or explanation. Deterministic validation,
  identity, policy, and writes remain authoritative even when AI is enabled.

Items in the latter two sections are intentionally not exposed as extra
buttons until their end-to-end behavior is implemented and tested.
