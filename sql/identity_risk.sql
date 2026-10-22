-- AIS identity risk rating (Lloyd's List-style MMSI misuse).
-- Approved DDL from reports/identity-research.md section 6.5 (exactly).

CREATE TABLE IF NOT EXISTS public.ais_identity_event (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    -- 'dual_track_mmsi' | 'lloyds_mmsi_imo_conflict' | 'mmsi_multi_imo_static'
    -- | 'mmsi_reused_static_evt' | 'reflag_dual_mmsi'
    confidence      TEXT NOT NULL DEFAULT 'medium',  -- high | medium | low
    mmsi            BIGINT,
    imo             TEXT,             -- primary ship key when known (AIS or Lloyd's)
    imo_other       TEXT,             -- conflicting IMO (other vessel / registry)
    ship_name       TEXT,
    ship_name_other TEXT,
    ts_start        TIMESTAMPTZ NOT NULL,
    ts_end          TIMESTAMPTZ,
    distance_m      DOUBLE PRECISION, -- dual-track separation when applicable
    evidence        JSONB,            -- raw prev/cur, cluster centres, list names, etc.
    source_table    TEXT,             -- ais_positionanomaly | ais_static_evt | ais_lloyds | live
    source_ref      TEXT,             -- e.g. anomaly id / event fingerprint
    sanctions_match BOOLEAN NOT NULL DEFAULT FALSE,
    sanctions_lists TEXT[],           -- e.g. {SDN,UK}
    rule_version    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_type, mmsi, imo, imo_other, ts_start)
);

CREATE INDEX IF NOT EXISTS idx_identity_event_mmsi_ts
    ON public.ais_identity_event (mmsi, ts_start DESC);
CREATE INDEX IF NOT EXISTS idx_identity_event_imo_ts
    ON public.ais_identity_event (imo, ts_start DESC);
CREATE INDEX IF NOT EXISTS idx_identity_event_type_ts
    ON public.ais_identity_event (event_type, ts_start DESC);
CREATE INDEX IF NOT EXISTS idx_identity_event_sanctions
    ON public.ais_identity_event (sanctions_match)
    WHERE sanctions_match;

CREATE TABLE IF NOT EXISTS public.ais_ship_risk_rating (
    id                 BIGSERIAL PRIMARY KEY,
    ship_key           TEXT NOT NULL,          -- 'imo:#######' or 'mmsi:#########'
    imo                TEXT,
    mmsi               BIGINT,
    ship_name          TEXT,
    rating             TEXT NOT NULL,          -- RED | AMBER | GREEN
    score              INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    lookback_months    INTEGER NOT NULL DEFAULT 24,
    mmsi_misuse        BOOLEAN NOT NULL DEFAULT FALSE,  -- LL rule inside lookback
    sanctions_match    BOOLEAN NOT NULL DEFAULT FALSE,
    sanctions_lists    TEXT[],
    event_count_window INTEGER NOT NULL DEFAULT 0,
    event_count_total  INTEGER NOT NULL DEFAULT 0,
    latest_event_at    TIMESTAMPTZ,
    factors            JSONB,                  -- breakdown for UI
    rule_version       TEXT NOT NULL,
    computed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ship_key, lookback_months)
);

CREATE INDEX IF NOT EXISTS idx_ship_risk_rating_rating
    ON public.ais_ship_risk_rating (rating, score DESC);
CREATE INDEX IF NOT EXISTS idx_ship_risk_imo
    ON public.ais_ship_risk_rating (imo);
CREATE INDEX IF NOT EXISTS idx_ship_risk_mmsi
    ON public.ais_ship_risk_rating (mmsi);

-- Optional: nightly Lloyd's mismatch snapshot so Class B becomes historical
CREATE TABLE IF NOT EXISTS public.ais_lloyds_mmsi_conflict_snap (
    snap_date     DATE NOT NULL,
    mmsi          BIGINT NOT NULL,
    ais_imo       TEXT NOT NULL,
    lloyds_imo    TEXT NOT NULL,
    ais_name      TEXT,
    lloyds_name   TEXT,
    name_agree    BOOLEAN,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (snap_date, mmsi, ais_imo, lloyds_imo)
);
