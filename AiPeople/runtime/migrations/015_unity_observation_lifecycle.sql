-- Additive: old worlds and dialogue history remain readable.
CREATE TABLE unity_observations (
    save_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
) STRICT;
