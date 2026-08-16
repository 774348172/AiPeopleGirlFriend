CREATE TABLE memory_run_transitions (
    transition_id TEXT PRIMARY KEY,
    proposal_run_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    from_state TEXT CHECK (
        from_state IS NULL OR from_state IN (
            'pending', 'failed_retryable', 'failed_terminal', 'committed'
        )
    ),
    to_state TEXT NOT NULL CHECK (
        to_state IN ('pending', 'failed_retryable', 'failed_terminal', 'committed')
    ),
    from_sequence_no INTEGER NOT NULL CHECK (from_sequence_no > 0),
    through_sequence_no INTEGER NOT NULL CHECK (through_sequence_no >= from_sequence_no),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    failure_code TEXT,
    batch_sha256 TEXT,
    transitioned_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    UNIQUE (proposal_run_id, ordinal),
    CHECK ((ordinal = 1 AND from_state IS NULL) OR (ordinal > 1 AND from_state IS NOT NULL)),
    CHECK ((to_state LIKE 'failed_%' AND failure_code IS NOT NULL) OR (to_state NOT LIKE 'failed_%' AND failure_code IS NULL)),
    CHECK ((to_state = 'committed' AND batch_sha256 IS NOT NULL) OR (to_state <> 'committed' AND batch_sha256 IS NULL))
) STRICT;

CREATE INDEX ix_memory_run_transitions_run
ON memory_run_transitions(proposal_run_id, ordinal);

CREATE TRIGGER memory_run_transitions_reject_update
BEFORE UPDATE ON memory_run_transitions
BEGIN
    SELECT RAISE(ABORT, 'memory run transitions are append-only');
END;

CREATE TRIGGER memory_run_transitions_reject_delete
BEFORE DELETE ON memory_run_transitions
BEGIN
    SELECT RAISE(ABORT, 'memory run transitions are append-only');
END;

CREATE TABLE memory_proposal_runs (
    proposal_run_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    from_sequence_no INTEGER NOT NULL CHECK (from_sequence_no > 0),
    through_sequence_no INTEGER NOT NULL CHECK (through_sequence_no >= from_sequence_no),
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'failed_retryable', 'failed_terminal', 'committed')
    ),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    failure_code TEXT,
    batch_sha256 TEXT,
    latest_transition_id TEXT NOT NULL REFERENCES memory_run_transitions(transition_id),
    updated_at TEXT NOT NULL,
    CHECK ((state LIKE 'failed_%' AND failure_code IS NOT NULL) OR (state NOT LIKE 'failed_%' AND failure_code IS NULL)),
    CHECK ((state = 'committed' AND batch_sha256 IS NOT NULL) OR (state <> 'committed' AND batch_sha256 IS NULL))
) STRICT;

CREATE INDEX ix_memory_proposal_runs_state
ON memory_proposal_runs(state, updated_at);

CREATE TABLE memory_transitions (
    transition_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    proposal_run_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    from_status TEXT CHECK (
        from_status IS NULL OR from_status IN (
            'proposed', 'active', 'disputed', 'superseded', 'rejected'
        )
    ),
    to_status TEXT NOT NULL CHECK (
        to_status IN ('proposed', 'active', 'disputed', 'superseded', 'rejected')
    ),
    representation_json TEXT NOT NULL CHECK (json_valid(representation_json)),
    reason_code TEXT NOT NULL,
    source_event_id TEXT REFERENCES events(event_id),
    transitioned_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    UNIQUE (memory_id, ordinal),
    CHECK ((ordinal = 1 AND from_status IS NULL AND to_status = 'proposed') OR (ordinal > 1 AND from_status IS NOT NULL))
) STRICT;

CREATE INDEX ix_memory_transitions_memory
ON memory_transitions(memory_id, ordinal);

CREATE INDEX ix_memory_transitions_run
ON memory_transitions(proposal_run_id, memory_id);

CREATE TRIGGER memory_transitions_source_conversation
BEFORE INSERT ON memory_transitions
WHEN NEW.source_event_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM events
        WHERE event_id = NEW.source_event_id
          AND conversation_id = NEW.conversation_id
    ) THEN RAISE(ABORT, 'memory decision source belongs to another conversation') END;
END;

CREATE TRIGGER memory_transitions_reject_update
BEFORE UPDATE ON memory_transitions
BEGIN
    SELECT RAISE(ABORT, 'memory transitions are append-only');
END;

CREATE TRIGGER memory_transitions_reject_delete
BEFORE DELETE ON memory_transitions
BEGIN
    SELECT RAISE(ABORT, 'memory transitions are append-only');
END;

CREATE TABLE memories (
    memory_id TEXT PRIMARY KEY,
    proposal_run_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('proposed', 'active', 'disputed', 'superseded', 'rejected')
    ),
    version INTEGER NOT NULL CHECK (version > 0),
    representation_json TEXT NOT NULL CHECK (json_valid(representation_json)),
    latest_transition_id TEXT NOT NULL REFERENCES memory_transitions(transition_id),
    updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX ix_memories_conversation_status
ON memories(conversation_id, status, memory_id);

