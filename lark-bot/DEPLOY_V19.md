# Nexus Recruiting final migration release (v19)

This release replaces the failed startup migration with a restart-safe,
serialized PostgreSQL migration. It is intended to be deployed as one complete
Railway source package. Do not run manual cleanup SQL before deployment.

## What is protected

- The full schema migration runs in one explicit transaction.
- A PostgreSQL transaction advisory lock serializes overlapping Railway starts.
- A restart after a partially completed older deployment is idempotent even
  when the legacy requisition is `NULL`.
- If a legacy candidate was reassigned after its first migration, the old
  application and history remain intact while the missing current workflow is
  added under a second stable reference.
- Every legacy candidate must resolve to a candidate application for its
  current legacy requisition; otherwise the migration rolls back.
- Candidate application creation uses a null-safe requisition comparison and a
  per-candidate transaction lock.
- Legacy stage events prefer the original deterministic application, preserving
  stable historical ownership when a legacy candidate later changes jobs.

## Expected first-start evidence

Railway deploy logs must show:

```text
[db] tables ready
[bot] starting in webhook mode, listening on port ...
```

The log must not contain `UniqueViolation` or
`candidate_application legacy migration incomplete`.

## Acceptance checks after deployment

1. The deployment is `Active` and the panel loads.
2. Existing Job Requisitions and Channel Analytics records remain present.
3. Send `/channel_sheet`; the Recruitment Bot returns the existing workspace.
4. Do not submit test rows until those read-only checks pass.
5. Restart/redeploy the same revision once. It must again reach
   `[db] tables ready` without creating duplicate application rows.

If startup still fails, retain the database and inspect the exact deployment
error. Do not delete candidates, applications, stage history, or requisitions.
