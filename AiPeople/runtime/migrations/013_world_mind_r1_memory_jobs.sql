CREATE TABLE world_mind_r1_memory_jobs (
    job_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    source_request_id TEXT NOT NULL,
    user_event_id TEXT NOT NULL REFERENCES world_mind_events(event_id),
    assistant_event_id TEXT NOT NULL REFERENCES world_mind_events(event_id),
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'completed', 'failed')),
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    failure_code TEXT,
    created_game_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX ix_world_mind_r1_memory_jobs_ready
ON world_mind_r1_memory_jobs(save_id, character_id, state, created_at, job_id);
