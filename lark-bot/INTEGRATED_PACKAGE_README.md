# AI Talent Discovery + Nexus Recruiting v14

This archive contains two deployable components and one contract:

- `lark-bot/`: complete Railway/Nexus application, including UI, PostgreSQL
  migration, Excel/Lark transport, Bot routes and tests.
- `AI-Talent-Discovery/`: complete local core source (runtime data, database,
  plans, backups, logs, browser state and `.env` are excluded).
- `manifests/`: hashes and verification results for the exact archive content.

## Deployment ownership

Railway owns operations UI, task/channel reporting, the Recruitment Bot webhook
and the persistent PostgreSQL read model. AI Talent Discovery remains the only
owner of candidate discovery, public-evidence scoring, deduplication, Contact
Ready quotas, frozen plans and signed HR candidate commands.

The two databases are not merged. Integration uses signed snapshots and the
authorized local worker. No production HMAC key is contained in this archive.

## Upload

For the existing `jiaweizheblast-oss/lark-task-bot` repository, replace the
contents of its `lark-bot` directory with this archive's `lark-bot` directory.
Keep Railway's existing environment variables. Do not upload either project's
local `.env`.

The `AI-Talent-Discovery` directory is the local/core source for VS Code and the
Windows search worker. It is not deployed into the Railway web service unless a
separate worker service is deliberately provisioned later.

Read `lark-bot/DEPLOY_V14.md` before deployment and perform the backup and
post-deploy checks in order.

## What v14 changes globally

- search profiles and real job requisitions are separate everywhere;
- the 15 existing search profiles remain intact and disappear from Job Reqs;
- actual jobs have stable refs, lifecycle and definition/operations/catalog
  revisions;
- Candidate and Candidate Application are separate, including stage history;
- Channel Analytics, website XLSX and Lark use Open operational jobs only;
- frozen XLSX job catalogs survive rename and fail safely on inactive jobs;
- every XLSX row has signed opaque system identity;
- Lark submission uses a double-read revision barrier;
- closed jobs and candidate history cannot be hard-deleted;
- Contact Ready quota remains manager-only in Talent Discovery.

This package build itself performed no production database write, no Lark write,
no candidate search and no Bot submission.
