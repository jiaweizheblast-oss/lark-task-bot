-- Preserve every superseded published daily command before a manager requests
-- a replacement. The external Lark workbook remains untouched.
CREATE TABLE IF NOT EXISTS talent_daily_publication_archive (
    publication_id              UUID PRIMARY KEY,
    business_date               DATE NOT NULL,
    revision                    INTEGER NOT NULL,
    status                      TEXT NOT NULL,
    payload                     JSONB NOT NULL,
    payload_sha256              TEXT NOT NULL,
    receipt                     JSONB NOT NULL,
    task_ids                    JSONB NOT NULL,
    replacement_publication_id  UUID NOT NULL,
    archived_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_talent_daily_publication_archive_date
    ON talent_daily_publication_archive (business_date, archived_at);
