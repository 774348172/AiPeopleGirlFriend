CREATE TABLE turn_model_decisions (
    save_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    protagonist_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    mind_result_json TEXT NOT NULL CHECK (json_valid(mind_result_json)),
    continuity_review_json TEXT NOT NULL CHECK (json_valid(continuity_review_json)),
    game_reply_result_json TEXT NOT NULL CHECK (json_valid(game_reply_result_json)),
    model_identity_json TEXT NOT NULL CHECK (json_valid(model_identity_json)),
    committed_at TEXT NOT NULL,
    PRIMARY KEY (save_id, request_id),
    FOREIGN KEY (save_id, request_id)
        REFERENCES turn_transactions(save_id, request_id)
) STRICT;

CREATE TRIGGER turn_model_decisions_reject_update
BEFORE UPDATE ON turn_model_decisions
BEGIN
    SELECT RAISE(ABORT, 'turn_model_decisions are append-only');
END;
