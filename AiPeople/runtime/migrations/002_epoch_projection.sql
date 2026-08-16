CREATE TABLE conversation_epochs (
    epoch_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    state TEXT NOT NULL CHECK (state IN ('open', 'closed')),
    opened_sequence_no INTEGER NOT NULL CHECK (opened_sequence_no > 0),
    closed_sequence_no INTEGER,
    close_reason TEXT CHECK (close_reason IN ('context_budget', 'rebuild')),
    budget_version INTEGER NOT NULL CHECK (budget_version > 0),
    created_at TEXT NOT NULL,
    closed_at TEXT,
    UNIQUE (conversation_id, ordinal),
    CHECK (
        (state = 'open' AND closed_sequence_no IS NULL
            AND close_reason IS NULL AND closed_at IS NULL)
        OR
        (state = 'closed' AND closed_sequence_no IS NOT NULL
            AND close_reason IS NOT NULL AND closed_at IS NOT NULL)
    )
) STRICT;

CREATE UNIQUE INDEX uq_conversation_open_epoch
ON conversation_epochs(conversation_id)
WHERE state = 'open';

CREATE INDEX ix_conversation_epochs_order
ON conversation_epochs(conversation_id, ordinal);

CREATE TABLE event_epochs (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id),
    epoch_id TEXT NOT NULL REFERENCES conversation_epochs(epoch_id) ON DELETE CASCADE,
    ordinal_in_epoch INTEGER NOT NULL CHECK (ordinal_in_epoch > 0),
    estimated_tokens INTEGER NOT NULL DEFAULT 0 CHECK (estimated_tokens >= 0),
    UNIQUE (epoch_id, ordinal_in_epoch)
) STRICT;

CREATE INDEX ix_event_epochs_epoch
ON event_epochs(epoch_id, ordinal_in_epoch);

CREATE TRIGGER event_epochs_same_conversation
BEFORE INSERT ON event_epochs
BEGIN
    SELECT CASE WHEN (
        SELECT e.conversation_id
        FROM events AS e
        WHERE e.event_id = NEW.event_id
    ) <> (
        SELECT ce.conversation_id
        FROM conversation_epochs AS ce
        WHERE ce.epoch_id = NEW.epoch_id
    ) THEN RAISE(ABORT, 'event and epoch conversations differ') END;
END;
