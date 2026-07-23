ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS search_run_id UUID;
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS search_run_order INTEGER;
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS search_run_size INTEGER;
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS progress_phase TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS progress_percent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS progress_message TEXT NOT NULL DEFAULT '';
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS progress_counts JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE talent_search_task
    ADD COLUMN IF NOT EXISTS last_progress_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_talent_search_task_run
    ON talent_search_task (search_run_id, search_run_order);

ALTER TABLE talent_search_task
    DROP CONSTRAINT IF EXISTS ck_talent_search_run_position;
ALTER TABLE talent_search_task
    ADD CONSTRAINT ck_talent_search_run_position CHECK (
        (search_run_id IS NULL AND search_run_order IS NULL AND search_run_size IS NULL)
        OR (
            search_run_id IS NOT NULL
            AND search_run_order BETWEEN 1 AND search_run_size
            AND search_run_size BETWEEN 1 AND 30
        )
    );

ALTER TABLE talent_search_task
    DROP CONSTRAINT IF EXISTS ck_talent_search_progress_percent;
ALTER TABLE talent_search_task
    ADD CONSTRAINT ck_talent_search_progress_percent
    CHECK (progress_percent BETWEEN 0 AND 100);
