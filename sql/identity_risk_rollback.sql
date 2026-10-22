-- Rollback for sql/identity_risk.sql (do not run unless reverting identity risk).

DROP TABLE IF EXISTS public.ais_lloyds_mmsi_conflict_snap;
DROP TABLE IF EXISTS public.ais_ship_risk_rating;
DROP TABLE IF EXISTS public.ais_identity_event;
