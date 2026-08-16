CREATE TABLE game_clock_anchors (
    save_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    anchor_game_time TEXT NOT NULL,
    time_scale REAL NOT NULL CHECK (time_scale > 0),
    running INTEGER NOT NULL CHECK (running IN (0, 1)),
    state_version INTEGER NOT NULL CHECK (state_version > 0),
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE save_runtime_versions (
    save_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    live_world_version INTEGER NOT NULL CHECK (live_world_version >= 0),
    mind_commit_version INTEGER NOT NULL CHECK (mind_commit_version >= 0),
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE protagonist_live_states (
    save_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    changed_game_time TEXT NOT NULL
) STRICT;

CREATE TABLE active_scene_states (
    save_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    changed_game_time TEXT NOT NULL
) STRICT;

CREATE TABLE world_event_deltas (
    event_id TEXT PRIMARY KEY,
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    from_version INTEGER NOT NULL CHECK (from_version >= 0),
    to_version INTEGER NOT NULL CHECK (to_version = from_version + 1),
    changed_fields_json TEXT NOT NULL CHECK (json_valid(changed_fields_json)),
    game_time TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (save_id, to_version)
) STRICT;

CREATE TABLE heroine_runtime_current (
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    last_transition_id TEXT,
    PRIMARY KEY (save_id, character_id)
) STRICT;

CREATE TABLE heroine_mind_transitions (
    transition_id TEXT PRIMARY KEY,
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    from_version INTEGER NOT NULL CHECK (from_version > 0),
    to_version INTEGER NOT NULL CHECK (to_version = from_version + 1),
    evidence_refs_json TEXT NOT NULL CHECK (json_valid(evidence_refs_json)),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    game_time TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (save_id, character_id, to_version)
) STRICT;

CREATE TABLE world_mind_events (
    event_id TEXT PRIMARY KEY,
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    game_time TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('protagonist', 'heroine')),
    event_type TEXT NOT NULL CHECK (event_type IN ('utterance', 'reply')),
    text TEXT NOT NULL CHECK (length(text) > 0),
    snapshot_id TEXT NOT NULL,
    causation_event_id TEXT REFERENCES world_mind_events(event_id),
    recorded_at TEXT NOT NULL,
    UNIQUE (save_id, sequence_no)
) STRICT;

CREATE UNIQUE INDEX uq_world_mind_protagonist_request
ON world_mind_events(save_id, request_id)
WHERE actor = 'protagonist';

CREATE UNIQUE INDEX uq_world_mind_heroine_request
ON world_mind_events(save_id, request_id)
WHERE actor = 'heroine';

CREATE TABLE turn_transactions (
    save_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    transaction_id TEXT NOT NULL UNIQUE,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    approved_runtime_json TEXT NOT NULL CHECK (json_valid(approved_runtime_json)),
    user_event_id TEXT NOT NULL UNIQUE REFERENCES world_mind_events(event_id),
    assistant_event_id TEXT NOT NULL UNIQUE REFERENCES world_mind_events(event_id),
    reply_text TEXT NOT NULL CHECK (length(reply_text) > 0),
    committed_game_time TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    PRIMARY KEY (save_id, request_id)
) STRICT;

CREATE TRIGGER world_event_deltas_reject_update
BEFORE UPDATE ON world_event_deltas
BEGIN
    SELECT RAISE(ABORT, 'world_event_deltas are append-only');
END;

CREATE TRIGGER heroine_mind_transitions_reject_update
BEFORE UPDATE ON heroine_mind_transitions
BEGIN
    SELECT RAISE(ABORT, 'heroine_mind_transitions are append-only');
END;

CREATE TRIGGER world_mind_events_reject_update
BEFORE UPDATE ON world_mind_events
BEGIN
    SELECT RAISE(ABORT, 'world_mind_events are append-only');
END;

CREATE TRIGGER turn_transactions_reject_update
BEFORE UPDATE ON turn_transactions
BEGIN
    SELECT RAISE(ABORT, 'turn_transactions are append-only');
END;
