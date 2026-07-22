# Recruiting Core architecture — ratified 2026-07-22

This document is the design authority for Talent Discovery, Job Requisitions,
Channel Analytics, Excel exchange, and the Recruitment Bot.  A convenient UI
must never weaken these invariants.

## 1. Ownership boundaries

- **AI Talent Discovery / Recruiting Core** owns candidate identity, public
  evidence, sourcing quality, candidate-to-job matching, contact workflow,
  do-not-contact rules, immutable activity history, and Preview → Freeze →
  Apply.
- **Nexus recruiting operations** owns operational job requisitions, Channel
  Analytics projections, manager views, and delivery of signed HR artifacts.
- **Lark and Excel are command transports and projections, not databases of
  record.** They may collect HR input but cannot invent identity, infer a job
  from a title, overwrite history, or bypass the application service.
- The two products may use different physical databases. Integration is through
  versioned contracts and stable references, never by importing each other's
  internal Python modules or granting a second direct writer.

## 2. Stable identity

The logical unit of Channel Analytics is one **candidate × job application**.
The following references have distinct purposes and must not be substituted:

- `candidate_id`: stable person identity.
- `application_ref`: stable candidate × job application identity.
- `job_ref`: immutable requisition identity; a displayed title may be renamed.
- `artifact_id`: one generated Excel/Lark command artifact.
- `row_ref`: one signed row inside an artifact.
- `submission_event_id`: one user intent. Retrying the same intent with the
  same payload is an idempotent no-op; reusing it with different content is a
  conflict.
- `record_version`: optimistic concurrency version. An old artifact cannot
  overwrite a newer application.

Names, profile URLs, spreadsheet row numbers, displayed job titles, and free
text are never identity keys.

## 3. Non-negotiable write rules

1. Every HR row is applied in one database transaction: receipt, application,
   stage event, dates, and version either all commit or all roll back.
2. First-touch source and the application's `job_ref` are immutable after the
   row is created. Corrections require an explicit future manager correction
   command with an audit reason; ordinary HR uploads cannot rewrite them.
3. Stage history is append-only. Current state is a projection of that history.
   Terminal states (`Hired`, `Rejected`, `Withdrawn`) cannot be silently
   reopened, and accidental backward transitions fail closed.
4. Closing a job prevents new applications while allowing existing applicants
   to finish their workflow. Closed jobs and their history are never deleted.
5. Directly importing work already under way is a **baseline import**. It sets
   the current stage without pretending that earlier funnel events happened
   today.
6. Duplicate signed row references, copied system tokens, signature mismatch,
   version conflict, unknown job, or changed immutable fields are rejected.
   There is no fallback to fuzzy matching.
7. Candidate, application, activity, and completed review history are never
   deleted or overwritten to make an import succeed.

## 4. Operational flow

### Existing recruitment at go-live

Managers may add candidates already at screening, interview, rejected, or
hired. The system stores the initial state as a baseline and records only later
real transitions as new funnel events. This avoids false daily and weekly
metrics.

### New leads after go-live

Each new person is created once against one open `job_ref`, with first-touch
source and a stable application reference. Subsequent HR work updates that same
application through commands; it does not add another candidate row.

### Persistent Lark workspace

The Channel Analytics Lark Base is a persistent operational workspace, not a
new Base every day. This preserves stable Lark record IDs and avoids duplicate
copies. Daily usability should come from views, not duplicated data:

- New Intake
- Needs Action
- Active Pipeline
- Completed

The current release safely supports the persistent table and refuses to claim a
complete sync beyond the explicit 25,000-row full-read boundary. Incremental
sync and managed archive views are scheduled below before scale reaches that
boundary.

### Excel exchange

Excel is a dated, frozen, signed artifact for offline work. It is not a free-form
export. It carries a frozen job/channel catalog, artifact identity, row identity,
and record version. Lark and Excel feed the same application service and obey
the same source, job, stage, idempotency, and concurrency rules.

## 5. Analytics semantics

- `source_received_on`: first accepted intake time. New-resume metrics count it
  only when the row is not a baseline import.
- `stage_started_at`: most recent accepted stage transition time. It is system
  managed and supports future Days in Stage and SLA calculations.
- `system_imported_at`: audit time for the system write; it is not a recruiting
  performance event.
- Passed-screening, recommended/interview, and rejected metrics are derived
  from real stage-transition events in the selected time window, not from the
  candidate's current stage.
- Source attribution is first-touch and immutable. `Other Source Details` is
  required only when Source Channel is `Other`.

## 6. Database evolution

Production schema changes use immutable, checksummed migration versions under a
PostgreSQL advisory lock. A migration is applied once, atomically. Reusing a
version with different SQL is refused. Recoverable legacy rows are assigned
stable recovery references; unresolved anomalies are recorded for manager
review instead of crashing every future deployment.

## 7. Delivery phases

### Implemented now — internal-trial foundation

- Stable job/application/artifact/row/event references.
- Row-atomic writes, immutable submission ledger, optimistic concurrency, and
  state-transition validation.
- Baseline imports and event-based funnel metrics.
- Frozen Excel catalogs, HMAC row protection, formula-injection protection,
  XLSX macro/external-link/size rejection, and duplicate-token rejection.
- Closed-job compatibility for existing applications.
- Bounded Lark reads, revision barrier, and fail-closed partial sync status.
- Immutable database migration ledger and legacy anomaly capture.

### Next without disrupting internal use

- `Next Action On`, per-stage SLA, Days in Stage, overdue views and reminders.
- Explicit manager correction commands with reason, approver, and superseded
  event references.
- Incremental Lark synchronization using record modification checkpoints and,
  where reliable, Lark events; periodic full reconciliation remains as a guard.
- Projection outbox, retry ledger, and tombstone handling so database commits
  and external-table updates cannot drift silently.
- Manager health page for migration anomalies, last successful/attempted sync,
  backlog, stale versions, and reconciliation status.
- Append-only owner/note history and controlled HR roster identity.

### Long-term scale and compliance

- SSO/RBAC, retention and archive partitions, HMAC key rotation, backup/restore
  drills, and audit export.
- Global do-not-contact enforcement owned by Recruiting Core.
- Recruiting-cycle attribution, hire/headcount/cost metrics, and manual-batch
  reconciliation without double counting.
- Optional AI provider for ranking or explanation. Deterministic validation,
  identity, policy, and writes remain authoritative even when AI is enabled.

Items in the latter two sections are intentionally not exposed as extra buttons
until their end-to-end behavior is implemented and tested.
