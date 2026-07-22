-- A read-only preview can never publish candidates directly.  A manager may
-- only queue the verified frozen-plan receipt for an authorised local worker.
ALTER TABLE talent_search_task
    DROP CONSTRAINT IF EXISTS ck_talent_search_publication_status;

ALTER TABLE talent_search_task
    ADD CONSTRAINT ck_talent_search_publication_status CHECK (
        publication_status IN (
            'not_ready', 'ready', 'queued', 'publishing', 'published', 'failed'
        )
    );

CREATE INDEX IF NOT EXISTS idx_talent_search_publication_claim
    ON talent_search_task (publication_status, updated_at)
    WHERE status = 'succeeded';
