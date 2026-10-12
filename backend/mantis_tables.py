"""Create the two new pnav tables. Existing tables are not altered."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS public.ais_positionanomaly (
    id bigserial PRIMARY KEY,
    mmsi bigint NOT NULL,
    event_day date NOT NULL,
    ts timestamptz NOT NULL,
    prev_ts timestamptz NOT NULL,
    latitude double precision NOT NULL,
    longitude double precision NOT NULL,
    prev_latitude double precision NOT NULL,
    prev_longitude double precision NOT NULL,
    dist_m double precision NOT NULL,
    dt_s integer NOT NULL,
    implied_kn double precision NOT NULL,
    reason varchar(20) NOT NULL,
    rule_version varchar(80) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mmsi, event_day)
);

CREATE INDEX IF NOT EXISTS idx_positionanomaly_ts
    ON public.ais_positionanomaly (ts);

CREATE TABLE IF NOT EXISTS public.ais_stsreviewcase (
    id bigserial PRIMARY KEY,
    observation_id bigint NOT NULL UNIQUE,
    cluster_signature varchar(255),
    status varchar(20) NOT NULL DEFAULT 'unreviewed',
    review_class varchar(30),
    labeled_at timestamptz,
    labeled_by varchar(120),
    review_notes text,
    first_detected_at timestamptz,
    last_detected_at timestamptz,
    duration_seconds double precision,
    suspicion_score double precision,
    is_open boolean,
    centroid_latitude double precision,
    centroid_longitude double precision,
    vessel_count integer,
    cargo_count integer,
    tanker_count integer,
    max_internal_distance_m double precision,
    in_anchorage boolean,
    anchorage_name text,
    outside_anchorage boolean,
    sanctions_match boolean,
    dredge_licence_match boolean,
    bunker_list_match boolean NOT NULL DEFAULT false,
    dark_overlap boolean,
    illegal_anchor_overlap boolean,
    loiter_overlap boolean,
    anchor_swing_overlap boolean,
    spoof_overlap boolean,
    members jsonb,
    refreshed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stsreviewcase_status
    ON public.ais_stsreviewcase (status);
"""


def ensure_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(ENSURE_SQL))
