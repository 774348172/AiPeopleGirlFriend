CREATE TABLE world_mind_reconcile_jobs (
    job_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN (
        'POST_REPLY_WORLD_MIND_RECONCILE',
        'FIVE_MINUTE_WORLD_MIND_RECONCILE'
    )),
    required_before_next_turn INTEGER NOT NULL CHECK (required_before_next_turn IN (0, 1)),
    source_request_id TEXT,
    source_turn_json TEXT CHECK (source_turn_json IS NULL OR json_valid(source_turn_json)),
    elapsed_runtime_seconds REAL NOT NULL CHECK (elapsed_runtime_seconds >= 0),
    missed_intervals INTEGER NOT NULL CHECK (missed_intervals >= 0),
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'completed', 'failed')),
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    failure_code TEXT,
    created_game_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX ix_world_mind_reconcile_jobs_ready
ON world_mind_reconcile_jobs(save_id, character_id, state, required_before_next_turn, created_at);

CREATE TABLE world_mind_reconcile_checkpoints (
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    last_world_version INTEGER NOT NULL CHECK (last_world_version >= 0),
    last_game_time TEXT NOT NULL,
    last_job_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (save_id, character_id)
) STRICT;

CREATE TABLE world_mind_reconcile_decisions (
    decision_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES world_mind_reconcile_jobs(job_id),
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    from_mind_version INTEGER NOT NULL CHECK (from_mind_version > 0),
    to_mind_version INTEGER NOT NULL CHECK (to_mind_version >= from_mind_version),
    operation TEXT NOT NULL CHECK (operation IN ('keep', 'update')),
    reconcile_result_json TEXT NOT NULL CHECK (json_valid(reconcile_result_json)),
    continuity_review_json TEXT NOT NULL CHECK (json_valid(continuity_review_json)),
    model_identity_json TEXT NOT NULL CHECK (json_valid(model_identity_json)),
    committed_game_time TEXT NOT NULL,
    committed_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER world_mind_reconcile_decisions_reject_update
BEFORE UPDATE ON world_mind_reconcile_decisions
BEGIN
    SELECT RAISE(ABORT, 'world_mind_reconcile_decisions are append-only');
END;
