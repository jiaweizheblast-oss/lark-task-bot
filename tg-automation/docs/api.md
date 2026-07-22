# API implemented in the current batch

All routes are prefixed with `/api/v1` except health checks.

## System

- `GET /health`
- `GET /health/ready`

## Content Centre

- `POST /tg/contents`
- `GET /tg/contents`
- `GET /tg/contents/{id}`
- `PATCH /tg/contents/{id}` — editable drafts and review items only;
- `POST /tg/contents/{id}/approve`
- `POST /tg/contents/{id}/archive`

## Content templates

- `GET /tg/content-templates`
- `GET /tg/content-templates/{preset_id}`

The read-only catalog supplies NEXUS with standard captions and recommended public buttons. Gift
Code is not part of the current version. Welcome Bonus, VIP Bonus, Deposit Bonus, Emergency Notice,
and Industry Content remain hidden from the first-version UI.

## Media Library

- `POST /tg/media`
- `GET /tg/media`
- `GET /tg/media/recommend`
- `POST /tg/media/{id}/enable`
- `POST /tg/media/{id}/disable`

## Destinations

- `POST /tg/destinations`
- `GET /tg/destinations`
- `PATCH /tg/destinations/{id}`
- `POST /tg/destinations/{id}/enable`
- `POST /tg/destinations/{id}/disable`
- `POST /tg/destinations/{id}/check-permissions`
- `POST /tg/destinations/{id}/send-test`
- `POST /tg/destinations/bulk-status`

Destination type and `is_test` must agree. Changing a chat ID or type clears previous Telegram
permission results. Real destinations require a successful posting-permission check within the
last 24 hours before scheduling. Test sends require both a test destination and the separate
`TELEGRAM_TEST_SENDING_ENABLED` safety switch.

## NEXUS dashboard

- `GET /tg/dashboard`

The dashboard aggregates review work, Campaign and delivery states, last-24-hour sends, failures,
known link clicks, destination permission health, upcoming Campaigns, and recent delivery errors.
It never exposes API keys or the Telegram Bot Token and does not claim unavailable APK, install,
registration, deposit, or ROI data.

## Campaigns

- `POST /tg/campaigns`
- `GET /tg/campaigns`
- `GET /tg/campaigns/{id}`
- `POST /tg/campaigns/{id}/preview`
- `POST /tg/campaigns/{id}/preflight`
- `POST /tg/campaigns/{id}/send-test-preview`
- `PATCH /tg/campaigns/{id}` — editable Campaign states only;
- `POST /tg/campaigns/{id}/approve`
- `POST /tg/campaigns/{id}/approve-and-schedule`
- `POST /tg/campaigns/{id}/schedule`
- `POST /tg/campaigns/{id}/send-now`
- `POST /tg/campaigns/{id}/cancel`

Tracked outbound URLs are
generated per public destination at delivery time.

Draft Campaign updates can replace content, media, target destinations, timing, and buttons.
Approval freezes a fresh rendered snapshot; approved or scheduled Campaigns cannot be edited.
For the normal NEXUS workflow, `approve-and-schedule` is the single final confirmation after
preview. It approves reviewable content and the Campaign only after every other preflight check
passes, then creates the delivery queue. A failure never enters the sending queue.
Scheduling rechecks content validity, image availability, destination enablement, and verified posting
permission for every non-test destination.

Preflight reports individual checks plus `configuration_ready` and `dispatch_ready`, allowing
NEXUS to show exactly what still blocks approval or delivery.

Campaign test preview renders the actual current Campaign into a test destination. It requires
the test-send switch, leaves Campaign state unchanged, creates no production delivery row, and
records only an audit event.

## Bot Control for the NEXUS website

- `GET /tg/bot-control/overview` — operator role;

The overview reports the internal admin Bot configuration, authorised-admin count, enabled
destinations, scheduled Campaigns, and delivery states.

## Publishing operations

- `GET /tg/operations/queue` — queue counts and stale leases;
- `GET /tg/operations/campaigns/{campaign_id}/deliveries` — per-destination delivery records;
- `POST /tg/operations/deliveries/{delivery_id}/retry` — operator role, failed public deliveries
  only.

Manual retry rejects disabled destinations and expired content, clears the previous transport
error, and records an audit event.

## Analytics

- `GET /tg/analytics/overview`
- `GET /tg/analytics/campaigns`
- `GET /tg/analytics/media`
- `GET /tg/analytics/destinations`

All analytics endpoints exclude test placements by default. Technical
verification can opt in with `?include_test=true`.

## Tracking redirects

- `GET /r/{tracking_code}` (not shown in OpenAPI)

Redirects accept only HTTPS destinations on `TRACKING_ALLOWED_HOSTS`, reject URL credentials,
expire with their campaign content, record anonymous click events, and return HTTP 302.

## NEXUS website integration

- `POST /integrations/nexus/content-events` — operator role;
- `GET /integrations/nexus/content-events` — operator role;
- `GET /integrations/nexus/content-events/{external_event_id}` — operator role;
- `POST /integrations/nexus/content-events/{external_event_id}/campaign-draft` — operator role.

Supported imports are announcements, new games, new features, bank-delay notices, daily events,
Lucky Spin, and emergency notices. Imports produce escaped, Telegram-sized captions in
`WAITING_REVIEW`; they do not bypass content or Campaign approval.
Event list responses include the current review status, generated Telegram caption, and title;
they can be filtered by `content_type`.

The Campaign-draft action carries the website `action_url` into one primary button appropriate
to the content type, such as `PLAY NOW`, `VIEW DETAILS`, or `SPIN NOW`. It is idempotent for the
same targets and schedule. The result remains a draft and still requires content review,
Campaign approval, media validation, and scheduling; website publication never sends directly.

When `API_AUTH_ENABLED=true`, the first version has only two roles: `Operator` for normal daily
work and read-only Analytics, and `Admin` for final approval, sending, and audit access.
`X-NEXUS-ACTOR` identifies the human or service in audit logs but cannot increase its role.

## Audit

- `GET /tg/audit-logs` — admin role.

Supports `resource_type`, `actor_id`, and bounded `limit` filters. API keys must be unique across
roles, so one credential cannot accidentally resolve to a weaker role.

`tg-worker-once` processes one batch. `tg-worker` runs the persistent loop with isolated task
failures, retry backoff, and expired-lease recovery.
