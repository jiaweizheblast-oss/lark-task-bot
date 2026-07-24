# Safe reconfiguration of today's recruiting workbook

This release adds a `重新配置今日表` action to Talent Discovery.

## Manager workflow

1. Keep using the currently published workbook while deciding whether to
   change the selected jobs, HR roster, or candidate counts.
2. Click `重新配置今日表`.
3. Adjust the open Job Requisitions and HR allocations.
4. Click `开始搜索并生成今日表`.
5. The current workbook remains available while the Windows Worker searches.
6. The system switches to a new immutable publication only after every new
   cohort is frozen. The replacement publication is revision 2 or later and
   applies those newly frozen cohorts without searching again.

## Failure behavior

- A failed or short search does not replace the current publication.
- The existing Lark workbook is never deleted or modified.
- If a replacement has already been queued and its publication later fails,
  Talent Discovery shows a link to the most recently archived working Lark
  workbook.
- A submitted workbook cannot be replaced through this flow.
- `取消重新配置` leaves the current workbook unchanged.

## Deployment

Upload every file in this directory to the existing GitHub `lark-bot`
application directory, without uploading `__pycache__`, local `.env`, database,
logs, or candidate artifacts. Railway will redeploy from GitHub.

The local Windows Worker source in the AI-Talent-Discovery workspace must
include the matching publication-task contract from this release.
