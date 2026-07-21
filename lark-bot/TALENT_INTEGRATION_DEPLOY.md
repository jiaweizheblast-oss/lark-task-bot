# Talent Discovery integration deployment

This folder is the complete `lark-bot` application based on GitHub `main`
commit `aa898d8`, with the Talent Discovery manager-only mirror added.

## What this release changes

- Adds the signed `POST /api/integration/v1/talent/snapshot` endpoint.
- Adds the manager-authenticated `GET /api/talent/snapshot` endpoint.
- Adds a read-only Talent Discovery view to the existing Recruiting panel.
- Synchronises core Job Reqs by immutable `core_job_ref`; it never matches jobs
  by title or local numeric ID.
- Keeps scores manager-only and keeps mirrored candidates out of Nexus' mutable
  candidate table.

## Files changed from GitHub `aa898d8`

- `.env.example`
- `.gitignore`
- `bot.py`
- `db.py`
- `panel.html`
- `schema.sql`
- `talent_integration.py`
- `test_talent_integration.py`
- `test_talent_routes.py`

`TALENT_INTEGRATION_DEPLOY.md` is deployment guidance only.

## GitHub and Railway

1. Upload the contents of this folder into the repository's existing
   `lark-bot/` directory. Do not upload a second nested `lark-bot` folder.
2. Do not upload `.env`, databases, backups, logs, candidate exports, or real
   credentials.
3. In Railway Variables, add `NEXUS_INTEGRATION_SIGNING_KEY` with a random
   value of at least 32 UTF-8 bytes. Keep the real value out of GitHub.
4. Deploy and confirm `GET /` returns `ok`.
5. Confirm an unauthenticated `GET /api/talent/snapshot` returns HTTP 401.
6. Log in to `/panel`, open Recruiting -> Talent Discovery, and confirm it
   initially says that no signed snapshot has been received.

The existing startup runs `schema.sql` idempotently, so the new snapshot table
and `core_job_ref` columns are created during deployment.

## First signed sync from AI Talent Discovery

Configure the same signing key locally in AI Talent Discovery together with:

```text
NEXUS_RECRUITING_ENDPOINT=https://lark-task-bot-production.up.railway.app
NEXUS_PANEL_URL=https://lark-task-bot-production.up.railway.app/panel
```

Dry-run first:

```powershell
.venv\Scripts\python.exe scripts\sync_nexus_recruiting.py
```

Only after the deployment checks pass, perform the first signed write:

```powershell
.venv\Scripts\python.exe scripts\sync_nexus_recruiting.py `
  --push `
  --endpoint "https://lark-task-bot-production.up.railway.app" `
  --confirm-endpoint-host "lark-task-bot-production.up.railway.app"
```

The snapshot contains manager-only public recruiting fields. It does not update
AI Talent Discovery candidates, matches, HR tasks, reviews, or activities.

## Single Recruitment Bot boundary

Keep only one active Lark event consumer for the visible Recruitment Bot.
AI Talent Discovery remains the candidate workflow owner. During this phase,
`/channel_download` and `/channel_upload` open the authenticated Railway Channel
Analytics page; they do not copy Nexus business logic into the core Bot.
