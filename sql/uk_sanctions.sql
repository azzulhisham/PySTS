-- UK FCDO Sanctions List (UKSL) tables + uk_vessel view.
-- Approved DDL (reports/cursor-report.md section 5, UK block only).

CREATE TABLE IF NOT EXISTS uk_ingest_run (
    id               BIGSERIAL PRIMARY KEY,
    source_url       TEXT NOT NULL,
    list_name        TEXT NOT NULL DEFAULT 'UK',
    publish_date     TEXT,
    xml_record_count INTEGER,
    rows_loaded      INTEGER,
    vessel_count     INTEGER,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS uk_entry (
    uid              TEXT PRIMARY KEY,  -- Unique ID (e.g. RUS1234)
    entry_type       TEXT NOT NULL,     -- Individual | Entity | Ship
    reference_number TEXT,              -- Unique ID / OFSI Group ID
    name             TEXT,
    comments         TEXT,              -- Other Information / statement of reasons
    regime           TEXT,              -- Regime Name
    listed_on        TEXT,              -- Date Designated
    imo              TEXT,
    mmsi             TEXT,
    vessel_flag      TEXT,
    vessel_type      TEXT,
    vessel_owner     TEXT,
    list_name        TEXT NOT NULL DEFAULT 'UK',
    publish_date     TEXT,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS uk_aka (
    uid         TEXT NOT NULL REFERENCES uk_entry(uid) ON DELETE CASCADE,
    aka_type    TEXT,                   -- Name type / Alias strength
    quality     TEXT,
    alias_name  TEXT
);

CREATE TABLE IF NOT EXISTS uk_identifier (
    uid         TEXT NOT NULL REFERENCES uk_entry(uid) ON DELETE CASCADE,
    id_type     TEXT,
    id_number   TEXT,
    id_country  TEXT
);

CREATE INDEX IF NOT EXISTS idx_uk_entry_type ON uk_entry (entry_type);
CREATE INDEX IF NOT EXISTS idx_uk_entry_name ON uk_entry (name);
CREATE INDEX IF NOT EXISTS idx_uk_entry_imo ON uk_entry (imo);
CREATE INDEX IF NOT EXISTS idx_uk_entry_mmsi ON uk_entry (mmsi);
CREATE INDEX IF NOT EXISTS idx_uk_entry_ref ON uk_entry (reference_number);
CREATE INDEX IF NOT EXISTS idx_uk_aka_alias ON uk_aka (alias_name);
CREATE INDEX IF NOT EXISTS idx_uk_id_type_number ON uk_identifier (id_type, id_number);
CREATE INDEX IF NOT EXISTS idx_uk_id_number ON uk_identifier (id_number);

CREATE OR REPLACE VIEW uk_vessel AS
SELECT
    e.uid,
    e.list_name,
    e.name AS vessel_name,
    e.imo,
    e.mmsi,
    e.regime,
    e.listed_on,
    e.publish_date,
    e.reference_number,
    e.comments,
    e.vessel_flag,
    e.vessel_type,
    e.vessel_owner
FROM uk_entry e
WHERE e.entry_type = 'Ship'
  AND (e.imo IS NOT NULL OR e.mmsi IS NOT NULL);
