CREATE INDEX ix_events_conversation_time
ON events(conversation_id, occurred_at, sequence_no);
