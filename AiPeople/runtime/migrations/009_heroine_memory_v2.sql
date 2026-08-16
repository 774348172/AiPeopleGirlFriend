CREATE TABLE memory_v2_transitions (
    transition_id TEXT PRIMARY KEY,
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    owner_character_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    from_version INTEGER,
    to_version INTEGER NOT NULL CHECK (to_version > 0),
    transition_kind TEXT NOT NULL CHECK (transition_kind IN ('commit', 'status')),
    representation_json TEXT NOT NULL CHECK (json_valid(representation_json)),
    source_event_ids_json TEXT NOT NULL CHECK (json_valid(source_event_ids_json)),
    idempotency_key TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (save_id, owner_character_id, memory_id, to_version),
    UNIQUE (save_id, owner_character_id, idempotency_key)
) STRICT;

CREATE TABLE memory_v2_current (
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    owner_character_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'disputed', 'superseded', 'rejected')),
    game_time TEXT NOT NULL,
    representation_json TEXT NOT NULL CHECK (json_valid(representation_json)),
    source_revision TEXT NOT NULL CHECK (length(source_revision) = 64),
    last_transition_id TEXT NOT NULL REFERENCES memory_v2_transitions(transition_id),
    PRIMARY KEY (save_id, owner_character_id, memory_id)
) STRICT;

CREATE INDEX idx_memory_v2_current_owner_status
ON memory_v2_current(save_id, owner_character_id, status, game_time, memory_id);

CREATE TABLE memory_v2_vector_generations (
    generation_id TEXT PRIMARY KEY,
    save_id TEXT NOT NULL,
    owner_character_id TEXT NOT NULL,
    view_profile_id TEXT NOT NULL,
    encoder_model_id TEXT NOT NULL,
    encoder_revision TEXT NOT NULL,
    encoder_artifact_sha256 TEXT NOT NULL CHECK (length(encoder_artifact_sha256) = 64),
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE (save_id, owner_character_id, generation_id)
) STRICT;

CREATE TABLE memory_v2_active_generations (
    save_id TEXT NOT NULL,
    owner_character_id TEXT NOT NULL,
    generation_id TEXT NOT NULL REFERENCES memory_v2_vector_generations(generation_id),
    PRIMARY KEY (save_id, owner_character_id)
) STRICT;

CREATE TABLE memory_v2_selector_views (
    save_id TEXT NOT NULL,
    owner_character_id TEXT NOT NULL,
    generation_id TEXT NOT NULL REFERENCES memory_v2_vector_generations(generation_id),
    memory_id TEXT NOT NULL,
    memory_version INTEGER NOT NULL CHECK (memory_version > 0),
    view_ordinal INTEGER NOT NULL CHECK (view_ordinal >= 0),
    view_kind TEXT NOT NULL,
    view_text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL CHECK (length(text_sha256) = 64),
    source_revision TEXT NOT NULL CHECK (length(source_revision) = 64),
    PRIMARY KEY (
        save_id, owner_character_id, generation_id,
        memory_id, memory_version, view_ordinal
    )
) STRICT;

CREATE TABLE memory_v2_embeddings (
    save_id TEXT NOT NULL,
    owner_character_id TEXT NOT NULL,
    generation_id TEXT NOT NULL REFERENCES memory_v2_vector_generations(generation_id),
    memory_id TEXT NOT NULL,
    memory_version INTEGER NOT NULL CHECK (memory_version > 0),
    view_ordinal INTEGER NOT NULL CHECK (view_ordinal >= 0),
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    dtype TEXT NOT NULL CHECK (dtype = 'float32le'),
    normalized INTEGER NOT NULL CHECK (normalized = 1),
    vector_blob BLOB NOT NULL,
    vector_sha256 TEXT NOT NULL CHECK (length(vector_sha256) = 64),
    PRIMARY KEY (
        save_id, owner_character_id, generation_id,
        memory_id, memory_version, view_ordinal
    )
) STRICT;

CREATE TABLE memory_v2_working_activation (
    save_id TEXT NOT NULL,
    owner_character_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    memory_version INTEGER NOT NULL CHECK (memory_version > 0),
    activation_score REAL NOT NULL,
    activated_game_time TEXT NOT NULL,
    PRIMARY KEY (save_id, owner_character_id, memory_id)
) STRICT;

CREATE TABLE memory_v2_self_timeline (
    save_id TEXT NOT NULL,
    owner_character_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    memory_version INTEGER NOT NULL CHECK (memory_version > 0),
    game_time TEXT NOT NULL,
    statement TEXT NOT NULL,
    PRIMARY KEY (save_id, owner_character_id, memory_id)
) STRICT;

CREATE TRIGGER memory_v2_transitions_reject_update
BEFORE UPDATE ON memory_v2_transitions
BEGIN
    SELECT RAISE(ABORT, 'memory_v2_transitions are append-only');
END;
