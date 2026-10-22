-- Rollback for sql/uk_sanctions.sql (do not run unless reverting UK sanctions).
-- Indexes drop with their tables.

DROP VIEW IF EXISTS uk_vessel;
DROP TABLE IF EXISTS uk_identifier;
DROP TABLE IF EXISTS uk_aka;
DROP TABLE IF EXISTS uk_entry;
DROP TABLE IF EXISTS uk_ingest_run;
