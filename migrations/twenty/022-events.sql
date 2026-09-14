-- EVENT only: atomic event upsert, provenance and Inbox decision in one transaction.
BEGIN;
SET LOCAL lock_timeout='5s';
CREATE UNIQUE INDEX IF NOT EXISTS radar_event_key_unique ON workspace_radar."_radarEvent" ("eventKey");
CREATE OR REPLACE FUNCTION radar_review.transfer_event()
RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE d jsonb; k text; target_id uuid; source_entry jsonb; sources jsonb; prose text;
BEGIN
 IF NEW."candidateType"::text IS DISTINCT FROM 'EVENT' OR NEW."candidateId" IS NULL OR NEW."candidateId" NOT LIKE 'radar-%' THEN RETURN NEW; END IF;
 IF NEW."deletedAt" IS NOT NULL THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD."candidateType"::text='EVENT' AND OLD."eventId" IS NOT NULL AND NEW."eventId" IS DISTINCT FROM OLD."eventId" THEN
   RAISE EXCEPTION 'RADAR_EVENT_LINK_IMMUTABLE';
 END IF;
 IF NEW."reviewStatus"::text <> 'APPROVE' THEN RETURN NEW; END IF;
 d=NEW."eventData";
 IF d IS NULL OR d->>'schema' IS DISTINCT FROM 'radar-event-v1' OR NULLIF(d->>'name','') IS NULL OR NULLIF(d->>'eventKey','') IS NULL OR NULLIF(NEW."summaryMarkdown",'') IS NULL THEN
   RAISE EXCEPTION 'RADAR_EVENT_DATA_REQUIRED: Сначала сохраните сведения и полный исходник события';
 END IF;
 k=d->>'eventKey';
 IF k !~ '^event-[a-f0-9]{64}$' THEN RAISE EXCEPTION 'RADAR_EVENT_KEY_INVALID'; END IF;
 -- Always serialize all sources for the same occurrence, including concurrent clicks.
 PERFORM pg_advisory_xact_lock(hashtextextended(k,0));
 IF NEW."eventId" IS NOT NULL THEN
   SELECT id INTO target_id FROM workspace_radar."_radarEvent" WHERE id=NEW."eventId" AND "deletedAt" IS NULL;
   IF target_id IS NULL THEN RAISE EXCEPTION 'RADAR_EVENT_UNAVAILABLE: Сохранённое событие в корзине или недоступно'; END IF;
   SELECT "eventKey" INTO k FROM workspace_radar."_radarEvent" WHERE id=target_id;
 END IF;
 IF EXISTS(SELECT 1 FROM workspace_radar."_radarEvent" WHERE "eventKey"=k AND "deletedAt" IS NOT NULL) THEN RAISE EXCEPTION 'RADAR_EVENT_IN_TRASH: Восстановите прежнее событие'; END IF;
 INSERT INTO workspace_radar."_radarEvent" AS e
 (id,"eventKey","name", "startTime", "endTime", "timeZone", "city", "venue", "eventFormat", "language", "cost", "eventCurrency", "priceStatus", "registrationDeadline", "organizer", "officialUrl", "whyAttend", "audience", "announcedParticipants", "unknowns","startDate","endDate","checkedAt","attendanceStatus","eventCancellation","userComment","originalText","sourceUrl")
 VALUES (COALESCE(target_id,gen_random_uuid()),k,NULLIF(d->>'name',''), NULLIF(d->>'startTime',''), NULLIF(d->>'endTime',''), NULLIF(d->>'timeZone',''), NULLIF(d->>'city',''), NULLIF(d->>'venue',''), NULLIF(d->>'eventFormat',''), NULLIF(d->>'language',''), NULLIF(d->>'cost',''), NULLIF(d->>'currency',''), NULLIF(d->>'priceStatus',''), NULLIF(d->>'registrationDeadline',''), NULLIF(d->>'organizer',''), NULLIF(d->>'officialUrl',''), NULLIF(d->>'whyAttend',''), NULLIF(d->>'audience',''), NULLIF(d->>'announcedParticipants',''), NULLIF(d->>'unknowns',''),NULLIF(d->>'startDate','')::date,NULLIF(d->>'endDate','')::date,NULLIF(d->>'checkedAt','')::timestamptz,'INTERESTED','UNKNOWN',NEW."feedbackNoteMarkdown",NEW."summaryMarkdown",NEW."sourceLinkPrimaryLinkUrl")
 ON CONFLICT ("eventKey") DO UPDATE SET
 "name" = COALESCE(NULLIF(e."name",''), EXCLUDED."name"),
"startTime" = COALESCE(NULLIF(e."startTime",''), EXCLUDED."startTime"),
"endTime" = COALESCE(NULLIF(e."endTime",''), EXCLUDED."endTime"),
"timeZone" = COALESCE(NULLIF(e."timeZone",''), EXCLUDED."timeZone"),
"city" = COALESCE(NULLIF(e."city",''), EXCLUDED."city"),
"venue" = COALESCE(NULLIF(e."venue",''), EXCLUDED."venue"),
"eventFormat" = COALESCE(NULLIF(e."eventFormat",''), EXCLUDED."eventFormat"),
"language" = COALESCE(NULLIF(e."language",''), EXCLUDED."language"),
"cost" = COALESCE(NULLIF(e."cost",''), EXCLUDED."cost"),
"eventCurrency" = COALESCE(NULLIF(e."eventCurrency",''), EXCLUDED."eventCurrency"),
"priceStatus" = COALESCE(NULLIF(e."priceStatus",''), EXCLUDED."priceStatus"),
"registrationDeadline" = COALESCE(NULLIF(e."registrationDeadline",''), EXCLUDED."registrationDeadline"),
"organizer" = COALESCE(NULLIF(e."organizer",''), EXCLUDED."organizer"),
"officialUrl" = COALESCE(NULLIF(e."officialUrl",''), EXCLUDED."officialUrl"),
"whyAttend" = COALESCE(NULLIF(e."whyAttend",''), EXCLUDED."whyAttend"),
"audience" = COALESCE(NULLIF(e."audience",''), EXCLUDED."audience"),
"announcedParticipants" = COALESCE(NULLIF(e."announcedParticipants",''), EXCLUDED."announcedParticipants"),
"unknowns" = COALESCE(NULLIF(e."unknowns",''), EXCLUDED."unknowns"),
 "startDate"=COALESCE(e."startDate",EXCLUDED."startDate"),"endDate"=COALESCE(e."endDate",EXCLUDED."endDate"),
 "checkedAt"=GREATEST(e."checkedAt",EXCLUDED."checkedAt"), "userComment"=COALESCE(NULLIF(e."userComment",''),EXCLUDED."userComment")
 RETURNING id INTO target_id;
 IF d->>'eventCancellation' IN ('SCHEDULED','CANCELLED','POSTPONED') THEN
   UPDATE workspace_radar."_radarEvent" e SET "eventCancellation"=(jsonb_populate_record(NULL::workspace_radar."_radarEvent",jsonb_build_object('eventCancellation',d->>'eventCancellation')))."eventCancellation" WHERE id=target_id AND ("eventCancellation" IS NULL OR "eventCancellation"::text='UNKNOWN');
 END IF;
 source_entry=jsonb_build_object('inboxId',NEW.id,'candidateId',NEW."candidateId",'sourceUrl',NEW."sourceLinkPrimaryLinkUrl",'originalText',NEW."summaryMarkdown",'facts',d,'decision','APPROVE','comment',NEW."feedbackNoteMarkdown",'decisionProvenance',d->'decisionProvenance');
 SELECT COALESCE("sourceRecords",'[]'::jsonb) INTO sources FROM workspace_radar."_radarEvent" WHERE id=target_id;
 -- One entry per original; older decisions remain in radar_review.feedback.
 SELECT COALESCE(jsonb_agg(x),'[]'::jsonb) INTO sources FROM jsonb_array_elements(sources) x WHERE x->>'inboxId'<>NEW.id::text;
 sources=sources||jsonb_build_array(source_entry);
 SELECT string_agg('Источник: '||COALESCE(x->>'sourceUrl','не указан')||E'\nИстория / Inbox: https://twenty.example.invalid/object/projectInboxItem/'||(x->>'inboxId')||E'\nРешение: Approve — Интересно\nКомментарий: '||COALESCE(x->>'comment','')||E'\nПроисхождение решения: '||COALESCE((x->'decisionProvenance')::text,'решение в CRM; полная история сохранена')||E'\n\nОригинал:\n'||COALESCE(x->>'originalText',''),E'\n\n---\n\n') INTO prose FROM jsonb_array_elements(sources) x;
 UPDATE workspace_radar."_radarEvent" SET "sourceRecords"=sources,"provenanceMarkdown"=prose,"provenanceBlocknote"=jsonb_build_array(jsonb_build_object('id','radar-event-sources','type','paragraph','props','{}'::jsonb,'content',jsonb_build_array(jsonb_build_object('type','text','text',prose,'styles','{}'::jsonb)),'children','[]'::jsonb))::text,"updatedAt"=now() WHERE id=target_id;
 NEW."eventId"=target_id;
 RETURN NEW;
END;
$fn$;
CREATE OR REPLACE TRIGGER radar_event_transfer_guard BEFORE INSERT OR UPDATE ON workspace_radar."_projectInboxItem" FOR EACH ROW EXECUTE FUNCTION radar_review.transfer_event();
ALTER TABLE radar_review.feedback ALTER COLUMN app_version SET DEFAULT '0.22.0';
COMMIT;
