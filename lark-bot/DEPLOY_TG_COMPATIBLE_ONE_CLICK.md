# TG-Compatible Talent One-Click Daily Flow

This deployment is based on the latest local package:

`TG_composer_B_replace_2_files.zip`

It preserves the TG Automation panel, TG proxy routes, multi-button composer,
image checks, immediate test sending, and test scheduling.

It also restores the Talent Discovery daily workflow:

- one recruiting-table run per business date;
- repeated identical search clicks are idempotent;
- a conflicting second search is rejected;
- `GET /api/talent/publications/today` reports the current daily state;
- the Talent page polls the current run and shows one clear state card;
- published runs show a direct `Open today's recruiting table` link;
- blank daily workbooks remain supported without running a search.

## Deployment

Upload all files in this directory to the existing `lark-bot` directory in
GitHub. Do not upload the containing directory itself.

Keep the existing Railway variables, including the TG variables:

- `TG_AUTOMATION_API_URL`
- `TG_AUTOMATION_API_KEY`

No database reset or migration command is required for this compatibility
merge.

## Verification

1. Open the panel and confirm the `TG Automation` tab still loads.
2. Open `Talent Discovery` and confirm the daily state card is visible.
3. Do not start a second real search merely to test the UI.
4. If today's workbook is already published, the state card should show the
   existing workbook link.

