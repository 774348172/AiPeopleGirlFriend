CREATE TABLE memory_background_jobs (
    job_id TEXT PRIMARY KEY,
    queue_ordinal INTEGER NOT NULL UNIQUE CHECK (queue_ordinal > 0),
    mode TEXT NOT NULL CHECK (mode = 'MEMORY_PROPOSE'),
    proposal_run_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_event_id TEXT NOT NULL REFERENCES events(event_id),
    assistant_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
    from_sequence_no INTEGER NOT NULL CHECK (from_sequence_no > 0),
    through_sequence_no INTEGER NOT NULL CHECK (through_sequence_no >= from_sequence_no),
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'running', 'failed_retryable', 'failed_terminal', 'completed')
    ),
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    failure_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (state LIKE 'failed_%' AND failure_code IS NOT NULL)
        OR (state NOT LIKE 'failed_%' AND failure_code IS NULL)
    )
) STRICT;

CREATE INDEX ix_memory_background_jobs_ready
ON memory_background_jobs(state, queue_ordinal);
