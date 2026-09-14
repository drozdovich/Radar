-- Executes in production only inside a rolled-back transaction. No lasting rows.
BEGIN;
SET LOCAL lock_timeout = '5s';
DO $$
DECLARE
    test_id uuid := gen_random_uuid();
    company_count bigint;
    history_count bigint;
BEGIN
    SELECT count(*) INTO company_count FROM workspace_radar.company;
    INSERT INTO workspace_radar."_projectInboxItem"(id,name,"candidateId","reviewStatus","createdByName","updatedByName")
    VALUES(test_id,'RADAR rollback-only validation','radar-validation-v017','NEW','Radar QA','Radar QA');
    BEGIN
        UPDATE workspace_radar."_projectInboxItem" SET "reviewStatus"='REJECT' WHERE id=test_id;
        RAISE EXCEPTION 'Test failed: reasonless rejection accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        UPDATE workspace_radar."_projectInboxItem" SET "reviewStatus"='REJECT',"rejectReason"='OTHER' WHERE id=test_id;
        RAISE EXCEPTION 'Test failed: scopeless rejection accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    UPDATE workspace_radar."_projectInboxItem"
    SET "reviewStatus"='REJECT',"rejectReason"='OTHER',"feedbackScope"='THIS_ITEM',"feedbackNoteMarkdown"='Проверка; откат'
    WHERE id=test_id;
    SELECT count(*) INTO history_count FROM radar_review.feedback WHERE record_id=test_id AND decision='REJECT' AND note='Проверка; откат';
    IF history_count <> 1 THEN RAISE EXCEPTION 'Test failed: audit missing'; END IF;
    UPDATE workspace_radar."_projectInboxItem" SET name='Unrelated edit' WHERE id=test_id;
    SELECT count(*) INTO history_count FROM radar_review.feedback WHERE record_id=test_id;
    IF history_count <> 1 THEN RAISE EXCEPTION 'Test failed: unrelated edit duplicated feedback'; END IF;
    UPDATE workspace_radar."_projectInboxItem" SET "reviewStatus"='APPROVE',"rejectReason"=null,"feedbackScope"=null WHERE id=test_id;
    IF (SELECT count(*) FROM radar_review.feedback WHERE record_id=test_id) <> 2 THEN RAISE EXCEPTION 'Test failed: approve audit missing'; END IF;
    IF (SELECT count(*) FROM workspace_radar.company) <> company_count THEN RAISE EXCEPTION 'Test failed: companies changed'; END IF;
    RAISE NOTICE 'PASS: reason required, scope required, valid reject/approve audited, unrelated edits ignored, no company created';
END;
$$;
ROLLBACK;
