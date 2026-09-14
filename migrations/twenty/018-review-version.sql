-- Version only for newly recorded decisions. Existing history stays unchanged.
BEGIN;
ALTER TABLE radar_review.feedback ALTER COLUMN app_version SET DEFAULT '0.18.3';
COMMIT;
