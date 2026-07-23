# Talent Discovery Live Search Console

This package is a complete Railway application replacement built from the
existing Nexus deployment package. It preserves the Telegram and Recruitment
Bot code and adds a clear Talent Discovery search console to `panel.html`.

## What the manager sees

- Worker online/offline state and the age of its latest 15-second heartbeat.
- Browser readiness before a search starts.
- Current step, next system step, total progress, candidates discovered,
  public results scanned, completed queries, and search-engine availability.
- Progress refreshes from the existing website polling loop about every five
  seconds while a task is active.
- A clear recovery card when the Worker is offline, its heartbeat becomes
  stale, the browser is not ready, all public search engines are unavailable,
  or a task fails.
- Exact PowerShell commands for checking and restarting the local Worker, with
  copy buttons.

The search console intentionally does not display internal Review Pool
terminology. Candidate counts are preliminary discoveries until the existing
freeze, deduplication, and publication safety checks finish.

## Safe recovery behavior

- One unavailable engine does not end the run; the next configured public
  engine is tried.
- Candidates already discovered are retained if a later engine is blocked.
- If every engine is unavailable, the task stops safely and tells the manager
  to wait, verify ordinary browser access, and retry later. It never bypasses
  CAPTCHA, login, consent, or access restrictions.
- A lost Worker lease is re-queued by the existing server workflow.

## Deployment

Upload all files from this directory to the existing `lark-bot` repository
folder, excluding no files from this package. Railway can then deploy the
resulting GitHub commit normally. No database migration or new Railway
environment variable is required for this console.

The local Windows Worker must use the matching AI-Talent-Discovery source
changes that emit structured progress. The website remains backward compatible:
older Workers simply show fewer counters.

## Validation completed

- Python progress contract smoke tests.
- Scanner fallback and all-engines-unavailable tests.
- Worker task contract test.
- Search-console HTML contract test.
- JavaScript syntax validation.
- Python compile check.
- `git diff --check`.

No real search, database write, Lark publication, ContactOut call, or Railway
write was performed during validation.
