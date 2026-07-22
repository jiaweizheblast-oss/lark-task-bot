# AI Talent Discovery + Nexus Recruiting Daily Publication v2

This archive contains two deployable components and one contract:

- `lark-bot/`: complete Railway/Nexus application, including UI, PostgreSQL
  migrations, manager approval queue, Bot routes and tests.
- `AI-Talent-Discovery/`: complete local core source (runtime data, database,
  plans, backups, logs, browser state and `.env` are excluded).
- `manifests/`: hashes and verification results for the exact archive content.

## Deployment ownership

Railway owns the operations UI, task/channel reporting, Recruitment Bot webhook,
manager approval command and persistent PostgreSQL read model. AI Talent
Discovery remains the only owner of candidate discovery, public-evidence
scoring, deduplication, Contact Ready quotas, frozen plans, controlled apply,
native Lark Sheet publication and signed HR candidate commands.

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

Read `lark-bot/DEPLOY_DAILY_RECRUITING_V2.md` before deployment and perform its
post-deploy checks in order.

## What daily publication v2 changes globally

- search profiles and real job requisitions are separate everywhere;
- the 15 existing search profiles remain intact and disappear from Job Reqs;
- actual jobs have stable refs, lifecycle and definition/operations/catalog
  revisions;
- Candidate and Candidate Application are separate, including stage history;
- Railway never applies a frozen plan and never publishes Lark;
- a manager approval creates one immutable command for all completed searches
  for the same business date;
- the authorised Windows worker validates every frozen plan and shared database
  baseline, creates one verified backup, then applies every selected job in one
  database transaction;
- a failure in any job rolls the whole daily cohort back;
- publication creates exactly one native Lark Sheet named
  `RecruitingYYYYMMDD`, with one sheet per HR and `Recruiting Overview` last;
- only controlled-applied Candidate/Match rows can enter the workbook;
- system rows keep signed opaque identity; only Status and CV are editable;
- replay and crash recovery cannot silently publish a second workbook;
- closed jobs and candidate history cannot be hard-deleted;
- Contact Ready quota remains manager-only in Talent Discovery.

This package build itself performed no production database write, no Lark write,
no candidate search and no Bot submission.
