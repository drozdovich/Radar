-- Only future feedback events get the new extension version.
BEGIN;
ALTER TABLE radar_review.feedback ALTER COLUMN app_version SET DEFAULT '0.21.0';
COMMIT;
