CREATE TABLE IF NOT EXISTS talent_worker_presence (
    worker_id TEXT PRIMARY KEY,
    status TEXT NOT NULL
        CHECK (status IN ('starting', 'idle', 'working', 'stopping')),
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
    version TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT talent_worker_presence_worker_id_length
        CHECK (length(worker_id) BETWEEN 3 AND 100),
    CONSTRAINT talent_worker_presence_version_length
        CHECK (length(version) <= 80)
);

CREATE INDEX IF NOT EXISTS talent_worker_presence_last_seen_idx
    ON talent_worker_presence (last_seen_at DESC);
