-- Twenty 2.37.4 / Dro workspace only. Reversible; no Company/People writes.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE SCHEMA IF NOT EXISTS radar_review;
CREATE TABLE IF NOT EXISTS radar_review.feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id uuid NOT NULL,
    candidate_id text NOT NULL,
    previous_status text,
    decision text NOT NULL,
    reason text,
    scope text,
    note text,
    rules_version text,
    actor_workspace_member_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    app_version text NOT NULL DEFAULT '0.17.0'
);
CREATE INDEX IF NOT EXISTS feedback_candidate_created ON radar_review.feedback(candidate_id, created_at);

CREATE OR REPLACE FUNCTION radar_review.validate_and_record()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW."candidateId" IS NULL OR NEW."candidateId" NOT LIKE 'radar-%' THEN RETURN NEW; END IF;
    IF TG_OP = 'UPDATE' THEN
        IF (NEW."reviewStatus", NEW."rejectReason", NEW."feedbackScope", NEW."feedbackNoteMarkdown")
            IS NOT DISTINCT FROM
           (OLD."reviewStatus", OLD."rejectReason", OLD."feedbackScope", OLD."feedbackNoteMarkdown") THEN
            RETURN NEW;
        END IF;
    END IF;
    IF NEW."reviewStatus"::text = 'REJECT' AND
        (NEW."rejectReason" IS NULL OR NEW."feedbackScope" IS NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'RADAR_REJECTION_REASON_REQUIRED: Для отказа укажите причину и область применения в форме «Прочитать и решить».';
    END IF;
    IF NEW."reviewStatus"::text IN ('APPROVE', 'NEED_INFO') THEN
        NEW."rejectReason" := NULL;
        NEW."feedbackScope" := NULL;
    END IF;
    IF NEW."reviewStatus"::text IN ('APPROVE', 'REJECT', 'NEED_INFO') THEN
        INSERT INTO radar_review.feedback(record_id, candidate_id, previous_status, decision, reason, scope, note, rules_version, actor_workspace_member_id)
        VALUES (NEW.id, NEW."candidateId", CASE WHEN TG_OP = 'UPDATE' THEN OLD."reviewStatus"::text ELSE NULL END,
            NEW."reviewStatus"::text, NEW."rejectReason"::text, NEW."feedbackScope"::text,
            NEW."feedbackNoteMarkdown", NEW."rulesVersion", NEW."updatedByWorkspaceMemberId");
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER radar_review_decision_guard
    BEFORE INSERT OR UPDATE ON workspace_radar."_projectInboxItem"
    FOR EACH ROW EXECUTE FUNCTION radar_review.validate_and_record();
COMMIT;
