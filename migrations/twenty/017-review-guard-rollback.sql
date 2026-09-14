-- Disable behavior; retain the audit table so no user feedback is lost.
BEGIN;
SET LOCAL lock_timeout = '5s';
DROP TRIGGER IF EXISTS radar_review_decision_guard ON workspace_radar."_projectInboxItem";
DROP FUNCTION IF EXISTS radar_review.validate_and_record();
COMMIT;
