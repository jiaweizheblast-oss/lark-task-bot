-- One business date owns one immutable multi-job recruiting publication.
-- Search rows remain preview receipts; the local worker owns controlled apply
-- and native Lark Sheet publication.
CREATE TABLE IF NOT EXISTS talent_daily_publication (
    publication_id      UUID PRIMARY KEY,
    business_date       DATE NOT NULL UNIQUE,
    revision            INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'queued',
    payload             JSONB NOT NULL,
    payload_sha256      TEXT NOT NULL,
    receipt             JSONB NOT NULL DEFAULT '{}'::jsonb,
    worker_id           TEXT,
    lease_token_sha256  TEXT,
    claimed_at          TIMESTAMPTZ,
    lease_expires_at    TIMESTAMPTZ,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    last_error_code     TEXT,
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_talent_daily_publication_status CHECK (
      status IN ('queued','publishing','published','failed')
    )
);

CREATE TABLE IF NOT EXISTS talent_daily_publication_item (
    publication_id  UUID NOT NULL REFERENCES talent_daily_publication(publication_id)
                    ON DELETE RESTRICT,
    task_id         UUID NOT NULL REFERENCES talent_search_task(task_id)
                    ON DELETE RESTRICT,
    cohort_order    INTEGER NOT NULL,
    PRIMARY KEY (publication_id, task_id),
    UNIQUE (task_id),
    UNIQUE (publication_id, cohort_order)
);

CREATE INDEX IF NOT EXISTS idx_talent_daily_publication_claim
    ON talent_daily_publication (status, updated_at);

-- Commands from the superseded single-cohort queue cannot be claimed by the
-- v2 worker. Returning them to ready is safe because no candidate/database/Lark
-- write was authorised by a completed v2 receipt.
UPDATE talent_search_task
SET publication_status='ready', publication='{}'::jsonb,
    worker_id=NULL, lease_token_sha256=NULL, claimed_at=NULL,
    lease_expires_at=NULL, updated_at=now()
WHERE status='succeeded'
  AND publication_status IN ('queued','publishing','failed')
  AND COALESCE(publication->>'schema_version', '') <> 'talent-daily-publication-receipt-v2';
