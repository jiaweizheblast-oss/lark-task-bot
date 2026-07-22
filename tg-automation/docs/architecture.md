# Architecture

The MVP is a modular monolith with three process entry points sharing one domain package and database:

- `apps/api`: NEXUS-facing REST API and health checks;
- `apps/worker`: claims due delivery rows and calls the official Telegram Bot API;
- `apps/bot`: provides a small allowlisted internal administration interface.

The browser never receives the Telegram Bot Token. Campaign approval freezes a rendered message snapshot. Scheduling creates one `message_deliveries` row per destination, protected by a unique `(campaign_id, destination_id)` constraint. Workers lease delivery rows before network calls so abandoned work can be reclaimed.

All persisted timestamps are interpreted as UTC. `display_timezone` is presentation metadata only.

## Safety boundaries

- `GLOBAL_SENDING_ENABLED=false` is the default.
- The test-send API only accepts destinations marked `is_test=true`.
- Real Telegram calls require a secret-provided token.
- Content must be approved before Campaign approval.
- Campaigns must be approved before scheduling or sending.
- Website events create `WAITING_REVIEW` content and never publish directly.
- NEXUS event IDs are idempotent; reusing one with changed payload is rejected.
- NEXUS API access is role-based and fails closed in staging/production.
- Persistent worker cycles isolate individual failures and back off after infrastructure errors.
- Automated Gift Codes expire instead of sending after the configured lateness window.
- Automated Gift Code validity is checked again immediately before the Telegram API call.
- Manual retry cannot extend an automated Gift Code beyond its original lateness window.
- Cancelling an automated Campaign also cancels its schedule slot and pending deliveries.
- Re-running slot generation, Code assignment, or Campaign preparation does not create duplicates.
- Player private subscriptions remain disabled and outside the current product boundary.
