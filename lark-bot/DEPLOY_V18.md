# Nexus Recruiting final migration release (v18)

This release replaces the failed startup migration with a restart-safe,
serialized PostgreSQL migration. It is intended to be deployed as one complete
Railway source package. Do not run manual cleanup SQL before deployment.

## What is protected

- The full schema migration runs in one explicit transaction.
- A PostgreSQL transaction advisory lock serializes overlapping Railway starts.
- A restart after a partially completed older deployment is idempotent even
  when the legacy requisition is `NULL`.
- Every legacy candidate must still resolve to a candidate application; the
  migration fails and rolls back if that postcondition is not true.
- Candidate application creation uses a null-safe requisition comparison and a
  per-candidate transaction lock.
- Legacy stage events are attached through the candidate and null-safe
  requisition identity, not by assuming a generated application reference.

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
