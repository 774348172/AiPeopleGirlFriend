CREATE TABLE memory_vector_generations (
    generation_id TEXT PRIMARY KEY,
    view_profile_id TEXT NOT NULL,
    encoder_model_id TEXT NOT NULL,
    encoder_revision TEXT NOT NULL,
    encoder_artifact_sha256 TEXT NOT NULL CHECK (length(encoder_artifact_sha256) = 64),
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    dtype TEXT NOT NULL CHECK (dtype = 'float32le'),
    normalized INTEGER NOT NULL CHECK (normalized = 1),
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
) STRICT;

CREATE TABLE memory_selector_views (
    generation_id TEXT NOT NULL REFERENCES memory_vector_generations(generation_id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL,
    memory_version INTEGER NOT NULL CHECK (memory_version > 0),
    view_ordinal INTEGER NOT NULL CHECK (view_ordinal >= 0),
    view_kind TEXT NOT NULL CHECK (view_kind IN ('statement', 'evidence', 'temporal')),
    view_text TEXT NOT NULL CHECK (length(view_text) > 0),
    text_sha256 TEXT NOT NULL CHECK (length(text_sha256) = 64),
    source_revision TEXT NOT NULL CHECK (length(source_revision) = 64),
    PRIMARY KEY (generation_id, memory_id, view_ordinal)
) STRICT;

CREATE INDEX ix_memory_selector_views_memory
ON memory_selector_views(generation_id, memory_id, memory_version);

CREATE TABLE memory_embeddings (
    generation_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    memory_version INTEGER NOT NULL CHECK (memory_version > 0),
    view_ordinal INTEGER NOT NULL CHECK (view_ordinal >= 0),
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    dtype TEXT NOT NULL CHECK (dtype = 'float32le'),
    normalized INTEGER NOT NULL CHECK (normalized = 1),
    vector_blob BLOB NOT NULL,
    vector_sha256 TEXT NOT NULL CHECK (length(vector_sha256) = 64),
    PRIMARY KEY (generation_id, memory_id, view_ordinal),
    FOREIGN KEY (generation_id, memory_id, view_ordinal)
        REFERENCES memory_selector_views(generation_id, memory_id, view_ordinal)
        ON DELETE CASCADE
) STRICT;

CREATE TABLE memory_vector_projection_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    active_generation_id TEXT REFERENCES memory_vector_generations(generation_id),
    updated_at TEXT NOT NULL
) STRICT;

