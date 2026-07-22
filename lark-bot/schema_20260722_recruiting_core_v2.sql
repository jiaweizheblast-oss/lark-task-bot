-- Immutable follow-up to 20260722_recruiting_core_v1.
-- Never add these statements back to schema.sql: Railway databases retain
-- the v1 checksum and must be able to validate it on every deployment.

ALTER TABLE candidate_application
    ADD COLUMN IF NOT EXISTS candidate_url TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_application
    ADD COLUMN IF NOT EXISTS cv_url TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_application
    ALTER COLUMN current_stage SET DEFAULT 'Pending';

ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS publication_status TEXT NOT NULL DEFAULT 'not_ready';
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS publication JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_talent_search_publication_status'
          AND conrelid = 'talent_search_task'::regclass
    ) THEN
        ALTER TABLE talent_search_task
            ADD CONSTRAINT ck_talent_search_publication_status
            CHECK (publication_status IN (
                'not_ready','ready','publishing','published','failed'
            ));
    END IF;
END $$;
