CREATE TABLE world_state_transitions (
    transition_id TEXT PRIMARY KEY,
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    protagonist_payload_json TEXT NOT NULL CHECK (json_valid(protagonist_payload_json)),
    scene_payload_json TEXT NOT NULL CHECK (json_valid(scene_payload_json)),
    changed_game_time TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('bootstrap_current', 'program_update')),
    recorded_at TEXT NOT NULL,
    UNIQUE (save_id, version)
) STRICT;

CREATE TABLE heroine_runtime_seeds (
    seed_id TEXT PRIMARY KEY,
    save_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    source TEXT NOT NULL CHECK (source IN ('bootstrap_current', 'character_package')),
    recorded_at TEXT NOT NULL,
    UNIQUE (save_id, character_id, version)
) STRICT;

INSERT OR IGNORE INTO world_state_transitions(
    transition_id, save_id, world_id, protagonist_id, version,
    protagonist_payload_json, scene_payload_json, changed_game_time,
    source, recorded_at
)
SELECT
    'bootstrap_world:' || protagonist.save_id || ':' || protagonist.version,
    protagonist.save_id,
    protagonist.world_id,
    protagonist.protagonist_id,
    protagonist.version,
    protagonist.payload_json,
    scene.payload_json,
    protagonist.changed_game_time,
    'bootstrap_current',
    protagonist.changed_game_time
FROM protagonist_live_states AS protagonist
JOIN active_scene_states AS scene
  ON scene.save_id = protagonist.save_id
 AND scene.world_id = protagonist.world_id
 AND scene.version = protagonist.version;

INSERT OR IGNORE INTO heroine_runtime_seeds(
    seed_id, save_id, world_id, protagonist_id, character_id,
    version, payload_json, source, recorded_at
)
SELECT
    'bootstrap_heroine:' || save_id || ':' || character_id || ':' || version,
    save_id,
    world_id,
    protagonist_id,
    character_id,
    version,
    payload_json,
    'bootstrap_current',
    'migration-012'
FROM heroine_runtime_current;

CREATE TRIGGER world_state_transitions_reject_update
BEFORE UPDATE ON world_state_transitions
BEGIN
    SELECT RAISE(ABORT, 'world_state_transitions are append-only');
END;

CREATE TRIGGER world_state_transitions_reject_delete
BEFORE DELETE ON world_state_transitions
BEGIN
    SELECT RAISE(ABORT, 'world_state_transitions are append-only');
END;

CREATE TRIGGER heroine_runtime_seeds_reject_update
BEFORE UPDATE ON heroine_runtime_seeds
BEGIN
    SELECT RAISE(ABORT, 'heroine_runtime_seeds are append-only');
END;

CREATE TRIGGER heroine_runtime_seeds_reject_delete
BEFORE DELETE ON heroine_runtime_seeds
BEGIN
    SELECT RAISE(ABORT, 'heroine_runtime_seeds are append-only');
END;
