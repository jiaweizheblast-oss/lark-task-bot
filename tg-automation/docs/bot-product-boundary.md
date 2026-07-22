# Admin Bot and Website Product Boundary

The current Telegram Bot is an internal administration and publishing Bot. It is not a
player-facing rewards, support, or subscription Bot. Only Telegram user IDs listed in
`TELEGRAM_ADMIN_USER_IDS` may use it.

Its private-chat menu is intentionally small:

1. Create / Send — opens Campaign management in NEXUS;
2. Scheduled — shows the three IST Gift Code slots and other upcoming Campaigns;
3. Groups — shows connected channels/groups and permission health;
4. Sending Status — shows queue and delivery results;
5. Open NEXUS — opens the full administration website.

NEXUS is the primary operating interface. It owns content editing, media selection, Campaign
approval, schedules, destinations, detailed delivery records, analytics, audit history, and
integration configuration. The Bot provides quick operational checks and links; it must not
become a second copy of the website.

The worker, not the interactive Bot process, executes approved delivery jobs through the
official Telegram Bot API. Expired worker leases can be reclaimed, and operators can manually
retry failed public deliveries through the protected Operations API.

Player one-to-one subscriptions and private scheduled broadcasts are deferred. Their dormant
code and schema are kept only for future compatibility, are disabled by default with
`PRIVATE_SUBSCRIBER_FEATURE_ENABLED=false`, and are not exposed by the current API or Bot menu.
