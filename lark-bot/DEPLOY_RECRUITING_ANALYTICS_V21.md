# Nexus Recruiting Analytics V21

This is a complete replacement package for the Railway service. It keeps the
existing PostgreSQL database and Railway variables; do not delete either.

## What changed

- One daily `RecruitingYYYYMMDD` table is the HR workflow for both discovered
  and HR-added candidates.
- Manager analytics now separates the current recruiting portfolio from
  immutable activity in the selected time window.
- Existing go-live rows are visible in the current snapshot but are excluded
  from historical funnel activity unless a real post-go-live event exists.
- Website hierarchy: four portfolio KPIs, four portfolio charts, activity KPIs,
  three result/trend charts, filters, and detailed pipeline.
- Source-channel and hiring-job catalogs remain controlled and old daily tables
  retain their frozen catalog.
- Channel Excel generation/upload remains retired; Excel is only a validated
  fallback for the unified daily table.

## Safe deployment

1. Keep the current Railway PostgreSQL volume and service variables.
2. Replace the full application directory with this package.
3. Deploy and wait for `Deployment successful`.
4. Open `/panel` and confirm Job Requisitions and Channel Analytics load.
5. Send `/channel_sheet` to the Recruitment Bot. It must open today's unified
   recruiting table.
6. Add one clearly labelled test row against an open requisition, submit once,
   then submit the unchanged table again. The second submission must create no
   duplicate application or event.
7. Remove the labelled test row only through the manager test-reset control.

## Expected analytics behavior

- Current Recruiting Portfolio includes baseline applications.
- Date-window activity excludes baseline imports and counts only real intake
  and stage transitions.
- Repeating an unchanged table submission is an idempotent no-op.
- Old cards are snapshots; send `/channel_sheet` again to obtain current status.
- Rich charts are on the website. This release does not claim to auto-create a
  Lark chart dashboard; Lark remains the daily operational table.

## Rollback

Redeploy the previous successful Railway deployment. Do not roll back or delete
the PostgreSQL volume. Migration versions are immutable and safe to leave in
place.
