ALTER TABLE world_mind_reconcile_jobs
ADD COLUMN available_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00.000000+00:00';

UPDATE world_mind_reconcile_jobs
SET available_at = updated_at;

DROP INDEX ix_world_mind_reconcile_jobs_ready;

CREATE INDEX ix_world_mind_reconcile_jobs_ready
ON world_mind_reconcile_jobs(
    save_id, character_id, state, required_before_next_turn,
    available_at, created_at, job_id
);

ALTER TABLE world_mind_r1_memory_jobs
ADD COLUMN available_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00.000000+00:00';

UPDATE world_mind_r1_memory_jobs
SET available_at = updated_at;

DROP INDEX ix_world_mind_r1_memory_jobs_ready;

CREATE INDEX ix_world_mind_r1_memory_jobs_ready
ON world_mind_r1_memory_jobs(
    save_id, character_id, state, available_at, created_at, job_id
);
