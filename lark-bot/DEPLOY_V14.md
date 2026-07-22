# Deploy v14 safely

1. Back up the Railway PostgreSQL database and verify the backup can be read.
2. Upload the complete `lark-bot` folder from the integration ZIP to the same
   GitHub folder used by Railway. Do not upload `.env` or local data.
3. Wait for Railway to show `Deployment successful`. Startup executes
   `schema.sql`; all v14 migrations are idempotent.
4. Open `/panel`, then **Job Reqs**. Existing Core-managed rows must no longer
   appear there. They remain visible as Talent Discovery Search Profiles.
5. Create the four current operational requisitions as separate records:
   - Customer Service Representative — headcount 20
   - Telesales Executive — headcount 20
   - Sales Team Lead — headcount 1
   - Customer Support Specialist — headcount 2
6. Keep each new record in Draft until owner, country/location and targets are
   reviewed. Move it to Open only when HR may use it.
7. Open Channel Analytics once. Its Job filters and Lark schema repair will use
   only Open requisitions. Download a fresh XLSX after any catalog change.
8. Run these checks before HR use:
   - download → fill one fake new row → submit;
   - resubmit the same file (must be idempotent);
   - tamper Row Ref (must be rejected);
   - pause a job after download, then submit a new row for it (must be rejected);
   - update an existing application in that paused job (must remain allowed);
   - `/channel_sheet` and website submit must report the same last-sync state.

Rollback: restore the database backup and deploy the previous Git commit. The
v14 migration does not delete legacy rows, so code rollback remains possible.
