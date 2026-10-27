-- STS shadow episodes (rule C). Shadow only: not used by MANTIS API.

CREATE TABLE IF NOT EXISTS public.ais_sts_shadow_episode (
    id                  BIGSERIAL PRIMARY KEY,
    mmsi_a              BIGINT NOT NULL,
    mmsi_b              BIGINT NOT NULL,
    ship_name_a         TEXT,
    ship_name_b         TEXT,
    ship_type_a         INTEGER,
    ship_type_b         INTEGER,
    length_m_a          DOUBLE PRECISION,
    length_m_b          DOUBLE PRECISION,
    ts_start            TIMESTAMPTZ NOT NULL,
    ts_last             TIMESTAMPTZ NOT NULL,
    duration_h          DOUBLE PRECISION NOT NULL,
    min_distance_m      DOUBLE PRECISION,
    median_distance_m   DOUBLE PRECISION,
    max_distance_m      DOUBLE PRECISION,
    median_sog_kn       DOUBLE PRECISION,
    rule_band           TEXT NOT NULL,
    possible_bunker     BOOLEAN NOT NULL DEFAULT FALSE,
    bunker_hub_a        BOOLEAN NOT NULL DEFAULT FALSE,
    bunker_hub_b        BOOLEAN NOT NULL DEFAULT FALSE,
    centroid_latitude   DOUBLE PRECISION,
    centroid_longitude  DOUBLE PRECISION,
    zone_name           TEXT,
    in_excl             BOOLEAN NOT NULL DEFAULT FALSE,
    harbour_excl        BOOLEAN NOT NULL DEFAULT FALSE,
    in_mantis           BOOLEAN NOT NULL DEFAULT FALSE,
    is_open             BOOLEAN NOT NULL DEFAULT TRUE,
    detection_version   TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_sts_shadow_episode UNIQUE (mmsi_a, mmsi_b, ts_start),
    CONSTRAINT chk_sts_shadow_mmsi_order CHECK (mmsi_a < mmsi_b),
    CONSTRAINT chk_sts_shadow_rule_band CHECK (rule_band IN ('100m', 'tanker200m'))
);

CREATE INDEX IF NOT EXISTS idx_sts_shadow_open
    ON public.ais_sts_shadow_episode (ts_last DESC)
    WHERE is_open;

CREATE INDEX IF NOT EXISTS idx_sts_shadow_open_pair
    ON public.ais_sts_shadow_episode (mmsi_a, mmsi_b, ts_last DESC)
    WHERE is_open;

CREATE INDEX IF NOT EXISTS idx_sts_shadow_mmsi_a_ts
    ON public.ais_sts_shadow_episode (mmsi_a, ts_start DESC);

CREATE INDEX IF NOT EXISTS idx_sts_shadow_mmsi_b_ts
    ON public.ais_sts_shadow_episode (mmsi_b, ts_start DESC);

COMMENT ON TABLE public.ais_sts_shadow_episode IS
    'Shadow STS pair episodes (rule C). Not consumed by MANTIS API.';
