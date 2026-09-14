-- No permanent data. Inject a failure AFTER event + feedback have been written.
BEGIN;
DO $test$
DECLARE q uuid=gen_random_uuid(); ek text='event-'||repeat('c',64); n integer;
BEGIN
 INSERT INTO workspace_radar."_projectInboxItem" (id,name,"createdByName","updatedByName","candidateId","candidateType","reviewStatus","summaryMarkdown","eventData") VALUES(q,'QA transaction rollback','QA','QA','radar-qa-event-transaction','EVENT','NEW','Synthetic source',jsonb_build_object('schema','radar-event-v1','name','QA rollback','eventKey',ek,'startDate','2026-11-01'));
 BEGIN
  UPDATE workspace_radar."_projectInboxItem" SET "reviewStatus"='APPROVE' WHERE id=q;
  SELECT count(*) INTO n FROM workspace_radar."_radarEvent" WHERE "eventKey"=ek;
  IF n<>1 THEN RAISE EXCEPTION USING ERRCODE='22000',MESSAGE='Event did not materialize before failure'; END IF;
  RAISE EXCEPTION 'simulated late failure';
 EXCEPTION WHEN raise_exception THEN NULL;
 END;
 IF EXISTS(SELECT 1 FROM workspace_radar."_radarEvent" WHERE "eventKey"=ek) OR EXISTS(SELECT 1 FROM radar_review.feedback WHERE record_id=q) OR EXISTS(SELECT 1 FROM workspace_radar."_projectInboxItem" WHERE id=q AND "reviewStatus"::text<>'NEW') THEN RAISE EXCEPTION 'Rollback failed'; END IF;
 UPDATE workspace_radar."_projectInboxItem" SET "reviewStatus"='APPROVE' WHERE id=q;
 IF NOT EXISTS(SELECT 1 FROM radar_review.feedback WHERE record_id=q AND decision='APPROVE') THEN RAISE EXCEPTION 'Retry failed'; END IF;
 RAISE NOTICE 'PASS late failure rolls back event, link and decision; retry succeeds';
END;
$test$;
ROLLBACK;
