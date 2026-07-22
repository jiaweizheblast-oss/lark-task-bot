# TG Automation connection

NEXUS keeps the browser-facing panel and proxies Telegram operations to the dedicated TG
Automation service. The Telegram Bot Token must never be stored in `panel.html` or returned to the
browser.

Required NEXUS environment variables:

```text
TG_AUTOMATION_API_URL=http://${{TG-Automation.RAILWAY_PRIVATE_DOMAIN}}:${{TG-Automation.PORT}}
TG_AUTOMATION_API_KEY=the same NEXUS operator key configured by the TG service
```

The first website version intentionally supports test publishing only:

- select an enabled test channel or test group;
- upload one JPEG, PNG, or WebP image up to 10 MB;
- enter a caption and one optional URL button;
- send and display the Telegram `chat_id` and `message_id` result.

The TG service must keep `GLOBAL_SENDING_ENABLED=false`. Its independent
`TELEGRAM_TEST_SENDING_ENABLED` switch controls whether test publishing is available.

Railway service settings for `TG-Automation`:

```text
Root Directory: /tg-automation
Config file: /tg-automation/railway.toml
Volume mount: /data
DATABASE_URL: sqlite:////data/tg_automation.db
```
