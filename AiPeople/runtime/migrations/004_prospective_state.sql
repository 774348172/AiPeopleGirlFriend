CREATE TABLE plan_transitions (
    transition_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    from_state TEXT CHECK (
        from_state IS NULL OR from_state IN (
            'proposed', 'confirmed', 'due', 'completed', 'cancelled', 'missed'
        )
    ),
    to_state TEXT NOT NULL CHECK (
        to_state IN (
            'proposed', 'confirmed', 'due', 'completed', 'cancelled', 'missed'
        )
    ),
    kind TEXT NOT NULL CHECK (kind IN ('promise', 'reminder', 'shared_plan')),
    description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 500),
    due_at TEXT,
    timezone TEXT NOT NULL,
    source_event_id TEXT NOT NULL REFERENCES events(event_id),
    transitioned_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    UNIQUE (plan_id, ordinal),
    CHECK (
        (ordinal = 1 AND from_state IS NULL)
        OR (ordinal > 1 AND from_state IS NOT NULL)
    ),
    CHECK (kind <> 'reminder' OR to_state = 'proposed' OR due_at IS NOT NULL)
) STRICT;

CREATE INDEX ix_plan_transitions_plan
ON plan_transitions(plan_id, ordinal);

CREATE INDEX ix_plan_transitions_conversation
ON plan_transitions(conversation_id, transitioned_at);

CREATE TRIGGER plan_transitions_same_conversation
BEFORE INSERT ON plan_transitions
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM events
        WHERE event_id = NEW.source_event_id
          AND conversation_id = NEW.conversation_id
    ) THEN RAISE(ABORT, 'plan evidence belongs to another conversation') END;
END;

CREATE TRIGGER plan_transitions_reject_update
BEFORE UPDATE ON plan_transitions
BEGIN
    SELECT RAISE(ABORT, 'plan transitions are append-only');
END;

CREATE TRIGGER plan_transitions_reject_delete
BEFORE DELETE ON plan_transitions
BEGIN
    SELECT RAISE(ABORT, 'plan transitions are append-only');
END;

CREATE TABLE plans (
    plan_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('promise', 'reminder', 'shared_plan')),
    description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 500),
    due_at TEXT,
    timezone TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('proposed', 'confirmed', 'due', 'completed', 'cancelled', 'missed')
    ),
    created_from_event_id TEXT NOT NULL REFERENCES events(event_id),
    updated_from_event_id TEXT NOT NULL REFERENCES events(event_id),
    last_transition_at TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    CHECK (kind <> 'reminder' OR state = 'proposed' OR due_at IS NOT NULL)
) STRICT;

CREATE INDEX ix_plans_conversation_state
ON plans(conversation_id, state, due_at);

