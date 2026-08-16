CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    occurred_timezone TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('user', 'character', 'system')),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('message', 'turn_failed', 'generation_cancelled')
    ),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    source TEXT NOT NULL CHECK (source IN ('typed', 'stt', 'runtime')),
    causation_event_id TEXT REFERENCES events(event_id),
    supersedes_event_id TEXT REFERENCES events(event_id),
    schema_version INTEGER NOT NULL,
    UNIQUE (conversation_id, sequence_no),
    CHECK (event_type <> 'message' OR json_type(payload_json, '$.text') = 'text')
) STRICT;

CREATE UNIQUE INDEX uq_user_request
ON events(request_id)
WHERE actor = 'user' AND event_type = 'message';

CREATE UNIQUE INDEX uq_completed_reply
ON events(request_id)
WHERE actor = 'character' AND event_type = 'message';

CREATE INDEX ix_events_time ON events(occurred_at);
CREATE INDEX ix_events_actor ON events(actor);
CREATE INDEX ix_events_conversation ON events(conversation_id, sequence_no);
CREATE INDEX ix_events_causation ON events(causation_event_id);

CREATE VIRTUAL TABLE event_fts USING fts5(
    event_id UNINDEXED,
    text,
    tokenize = 'trigram'
);

CREATE TRIGGER events_to_fts
AFTER INSERT ON events
WHEN NEW.event_type = 'message'
BEGIN
    INSERT INTO event_fts(event_id, text)
    VALUES (NEW.event_id, CAST(json_extract(NEW.payload_json, '$.text') AS TEXT));
END;

CREATE TRIGGER events_reject_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER events_reject_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

