# Rebuild an unused published recruiting workbook

This release adds one manager-only recovery action to Talent Discovery:

`重建今日表（旧表未提交）`

Use it only when today's workbook was already published but has not been
submitted and should be replaced because its HR roster, blank-row capacity,
job catalog, source catalog, or workbook layout is obsolete.

## Safety behavior

- The existing Lark workbook is retained and is never deleted or edited.
- Frozen candidates, HR allocation, open jobs, and source channels are copied
  exactly into revision 2.
- No new search is started.
- No candidate, source, match, task, batch, or activity history is deleted.
- The local Worker checks the AI Talent Discovery database before publishing.
- If the old artifact has a submission timestamp or any accepted submission
  event, replacement fails closed.
- After a successful rebuild, only the newest workbook should be submitted.

## Deployment

Upload every file in this package to the existing `lark-bot` GitHub directory,
excluding no source files. Do not upload local `.env`, database, backup, log,
or cache files. Railway will deploy the new migration automatically.

Wait until Railway shows `Deployment successful`, then refresh the manager
panel. The new button appears inside the green completed-publication panel.

## Manager workflow

1. Confirm the old workbook has never been submitted.
2. Confirm the local Talent Worker is online.
3. Click `重建今日表（旧表未提交）`.
4. Read the confirmation carefully and approve once.
5. Wait until the status becomes completed.
6. Open the new workbook and verify its revision-2 layout.
7. Stop using the old workbook. Submit only the new workbook.

If the old workbook was already submitted, do not use this action. Its
accepted history is immutable.
