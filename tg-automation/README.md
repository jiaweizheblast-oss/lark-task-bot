# NEXUS TG Automation Centre

Formal working repository for the Telegram automation MVP. This codebase is under active development and is not an installation package.

Current scope:

- FastAPI application foundation and health checks;
- SQLAlchemy domain model with reversible Alembic migrations;
- Telegram gateway boundary for official Bot API calls;
- content, a simple shared image library, Campaign approval, scheduling, and delivery queues;
- editable NEXUS drafts plus immutable approved Campaign snapshots;
- send-time validation for content windows and destination posting permission;
- destination lifecycle, 24-hour permission freshness, and independently gated test sends;
- structured Campaign preflight results for the future NEXUS UI;
- safe content-template catalog and an operations-focused NEXUS Dashboard API;
- richer website-event review records without automatic publication;
- automatic least-recently-used image selection from one shared enabled pool;
- public channel/group publishing with destination-specific buttons and tracking links;
- internal Telegram administration Bot protected by a numeric user-ID allowlist;
- intentionally minimal admin navigation for Campaigns, schedules, destinations, delivery status,
  and NEXUS access;
- production-only analytics by default, with explicit test-data visibility;
- idempotent NEXUS website-event import with Telegram-specific review drafts;
- one-action conversion from a reviewed website event into a simple TG Campaign draft;
- two simple NEXUS roles (`Operator` and `Admin`) plus durable audit records;
- one-shot and persistent workers with per-task fault isolation and retry backoff;
- isolated SQLite tests and a PostgreSQL-ready schema design.

Not included yet: production credentials, NEXUS UI, live website webhook registration,
game/APK conversion events, analytics UI, deployment infrastructure, or WhatsApp.

## Local development

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn apps.api.main:app --reload
```

Run the internal admin Bot in another terminal after a real test token and administrator IDs are
configured:

```powershell
tg-bot
```

Process one due-delivery batch:

```powershell
tg-worker-once
```

Run the persistent worker after configuration is validated:

```powershell
tg-worker
```

Run tests:

```powershell
pytest
```

No real Telegram token is required for unit and integration tests.

## Current API groups

- `/api/v1/tg/contents` — content lifecycle and approval;
- `/api/v1/tg/media` — one shared image pool with simple least-recently-used rotation;
- `/api/v1/tg/destinations` — channels, groups, test targets, and permission checks;
- `/api/v1/tg/campaigns` — preview, approval, scheduling, and delivery creation;
- `/api/v1/tg/bot-control` — internal Bot and publishing overview;
- `/api/v1/tg/operations` — queue status, delivery records, and protected manual retry;
- `/api/v1/tg/analytics` — Campaign, image, destination, and click metrics;
- `/api/v1/integrations/nexus/content-events` — idempotent website-content inbox;
- `/r/{tracking_code}` — allowlisted, expiring campaign redirects.

The redirect host allowlist must contain only company-approved domains. Test destinations are
excluded from normal analytics unless `include_test=true` is requested.
Staging and production refuse to start without API authentication, a sufficiently long admin
key, and HTTPS tracking links.

Gift Code automation and player private subscriptions are deliberately outside the current scope.
