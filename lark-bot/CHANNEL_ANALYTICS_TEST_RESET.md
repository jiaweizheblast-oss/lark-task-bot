# Channel Analytics test-data reset

This internal-testing release adds one manager-only **Reset Test Data** button
to the top of Channel Analytics.

After one explicit browser confirmation, the reset clears:

- Candidate Pipeline rows in the Lark Base
- Unidentified Batch Counts rows in the Lark Base
- website candidate applications
- candidate stage history
- Channel Analytics submission receipts
- manual batch-count records

The reset deliberately preserves:

- Job Requisitions and their status/history
- the Lark Base, tables, fields, views, and dropdown configuration
- the source-channel catalog and search profiles
- all Talent Discovery snapshots, candidates, matches, and activities
- all unrelated task, attendance, and reporting data

Safety behavior:

- the endpoint requires the authenticated manager panel password
- a fixed server-side confirmation token is required
- if Lark cannot be read or cleared, the website database is not changed
- all database deletions commit in one transaction or roll back together
- the response reports exact deletion counts

This control is intended only for the current internal test phase and should be
removed or feature-flagged before external production use.
