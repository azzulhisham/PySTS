-- Loitering motion evidence (SOG / heading) — informational columns only.
-- DO NOT auto-apply. Review and run on pnav when ready.
-- Code skips writing these columns until they exist (safe deploy order).

ALTER TABLE public.ais_vesselloiteractivity
    ADD COLUMN IF NOT EXISTS avg_sog_kn DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS max_sog_kn DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sog_std_kn DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sog_change_count INTEGER,
    ADD COLUMN IF NOT EXISTS heading_change_count INTEGER,
    ADD COLUMN IF NOT EXISTS loiter_ratio DOUBLE PRECISION;

COMMENT ON COLUMN public.ais_vesselloiteractivity.avg_sog_kn IS
    'Mean usable SOG (kn) over the detection window; AIS sog>=102 treated as missing.';
COMMENT ON COLUMN public.ais_vesselloiteractivity.max_sog_kn IS
    'Max usable SOG (kn) over the detection window.';
COMMENT ON COLUMN public.ais_vesselloiteractivity.sog_std_kn IS
    'Sample stddev of usable SOG (kn); 0 when fewer than 2 samples.';
COMMENT ON COLUMN public.ais_vesselloiteractivity.sog_change_count IS
    'Count of consecutive 5-min SOG steps with |delta| >= 0.5 kn.';
COMMENT ON COLUMN public.ais_vesselloiteractivity.heading_change_count IS
    'Count of consecutive heading steps with wrap-aware |delta| >= 45 deg (trueHeading, else COG; 511 skipped).';
COMMENT ON COLUMN public.ais_vesselloiteractivity.loiter_ratio IS
    'path_m / net_m; null when net_m <= 1 m.';
