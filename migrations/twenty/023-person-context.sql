-- PERSON-only transaction. Notes, tasks and people have distinct IDs (Twenty caches by UUID).
BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS radar_review.person_identity (
 identity_key text PRIMARY KEY, person_id uuid NOT NULL REFERENCES workspace_radar.person(id));
CREATE TABLE IF NOT EXISTS radar_review.person_source (
 inbox_id uuid PRIMARY KEY, source_url text NOT NULL UNIQUE, original_text text NOT NULL,
 person_id uuid NOT NULL REFERENCES workspace_radar.person(id),
 note_id uuid NOT NULL REFERENCES workspace_radar.note(id),
 task_id uuid NOT NULL REFERENCES workspace_radar.task(id));
CREATE OR REPLACE FUNCTION radar_review.person_uuid(value text) RETURNS uuid LANGUAGE sql IMMUTABLE AS $$ SELECT (substr(md5(value),1,12)||'5'||substr(md5(value),14,3)||'8'||substr(md5(value),18,15))::uuid $$;
CREATE OR REPLACE FUNCTION radar_review.linked_blocks(prose text) RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE blocks jsonb='[]'; parts jsonb; line text; remain text; url text; before_url text; n integer=0;
BEGIN
 FOREACH line IN ARRAY string_to_array(prose,E'\n') LOOP
  n=n+1; parts='[]'; remain=CASE WHEN left(line,3)='## ' THEN substr(line,4) ELSE line END;
  LOOP
   url=substring(remain from 'https?://[^[:space:]<>]+'); EXIT WHEN url IS NULL;
   before_url=split_part(remain,url,1);
   IF before_url<>'' THEN parts=parts||jsonb_build_array(jsonb_build_object('type','text','text',before_url,'styles','{}'::jsonb)); END IF;
   parts=parts||jsonb_build_array(jsonb_build_object('type','link','href',url,'content',jsonb_build_array(jsonb_build_object('type','text','text',url,'styles','{}'::jsonb))));
   remain=substr(remain,length(before_url)+length(url)+1);
  END LOOP;
  IF remain<>'' THEN parts=parts||jsonb_build_array(jsonb_build_object('type','text','text',remain,'styles','{}'::jsonb)); END IF;
  blocks=blocks||jsonb_build_array(jsonb_build_object('id','radar-'||n,'type',CASE WHEN left(line,3)='## ' THEN 'heading' ELSE 'paragraph' END,'props',CASE WHEN left(line,3)='## ' THEN '{"level":2}'::jsonb ELSE '{}'::jsonb END,'content',parts,'children','[]'::jsonb));
 END LOOP;
 RETURN blocks::text;
END;$$;
CREATE OR REPLACE FUNCTION radar_review.transfer_person() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb; a jsonb; keys text[]='{}'; k text; pid uuid; matches uuid[]; nid uuid; tid uuid; cid uuid; s radar_review.person_source; prose text; task_prose text; tg text; company_claim jsonb; actual jsonb; conflicts text='';
BEGIN
 IF NEW."candidateType"::text IS DISTINCT FROM 'PERSON' OR NEW."candidateId" NOT LIKE 'radar-%' OR NEW."deletedAt" IS NOT NULL OR NEW."reviewStatus"::text IS DISTINCT FROM 'APPROVE' THEN RETURN NEW; END IF;
 p=NEW."personProfile"; a=p->'next_action';
 IF p->>'schema_version' IS DISTINCT FROM 'person-profile-v1' OR p->>'status' IS DISTINCT FROM 'ready' OR p->>'candidate_id' IS DISTINCT FROM NEW."candidateId" OR p->>'source_url' IS DISTINCT FROM NEW."sourceLinkPrimaryLinkUrl" OR nullif(trim(p->>'first_name'),'') IS NULL OR p->>'last_name' IS NULL THEN RAISE EXCEPTION 'RADAR_PERSON_IDENTITY_REQUIRED'; END IF;
 IF NULLIF(NEW."summaryMarkdown",'') IS NULL OR NEW."sourceLinkPrimaryLinkUrl" !~ '^https://t.me/' THEN RAISE EXCEPTION 'RADAR_PERSON_ORIGINAL_REQUIRED'; END IF;
 FOREACH k IN ARRAY ARRAY['title','evidence_quote','questions','useful_result','draft'] LOOP
  IF NULLIF(trim(a->>k),'') IS NULL THEN RAISE EXCEPTION 'RADAR_PERSON_ACTION_REQUIRED: %',k; END IF;
 END LOOP;
 IF position(a->>'evidence_quote' in NEW."summaryMarkdown")=0 THEN RAISE EXCEPTION 'RADAR_PERSON_QUOTE_MISMATCH'; END IF;
 IF NULLIF(p->>'email','') IS NOT NULL AND (p->>'email' !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' OR position(lower(p->>'email') in lower(NEW."summaryMarkdown"))=0) THEN RAISE EXCEPTION 'RADAR_PERSON_EMAIL_EVIDENCE_REQUIRED'; END IF;
 IF NULLIF(p->>'linkedin_url','') IS NOT NULL AND (p->>'linkedin_url' !~ '^https://(www\.)?linkedin.com/in/[A-Za-z0-9_-]+/?$' OR position(regexp_replace(p->>'linkedin_url','^https://(www\.)?','') in replace(NEW."summaryMarkdown",'www.',''))=0) THEN RAISE EXCEPTION 'RADAR_PERSON_LINKEDIN_EVIDENCE_REQUIRED'; END IF;
 -- Small personal CRM: serialize identity resolution and all source writes together.
 PERFORM pg_advisory_xact_lock(hashtextextended('radar-person-transfer-v1',0));
 IF p->>'telegram_user_id' ~ '^[0-9]+$' THEN keys=array_append(keys,'telegram-id:'||(p->>'telegram_user_id')); END IF;
 IF p->>'telegram_username' ~ '^[A-Za-z0-9_]{5,32}$' THEN
  keys=array_append(keys,'telegram:'||lower(p->>'telegram_username'));tg='https://t.me/'||(p->>'telegram_username');
 END IF;
 IF NULLIF(p->>'linkedin_url','') IS NOT NULL THEN keys=array_append(keys,'linkedin:'||lower(regexp_replace(regexp_replace(p->>'linkedin_url','^https://(www\.)?',''),'/$',''))); END IF;
 IF cardinality(keys)=0 THEN keys=array_append(keys,'source:'||NEW."sourceLinkPrimaryLinkUrl"); END IF;
 SELECT array_agg(DISTINCT person_id) INTO matches FROM radar_review.person_identity WHERE identity_key=ANY(keys);
 IF cardinality(matches)>1 THEN RAISE EXCEPTION 'RADAR_PERSON_IDENTITY_CONFLICT'; END IF;
 pid=matches[1];
 IF pid IS NOT NULL AND p->>'telegram_user_id' ~ '^[0-9]+$' AND EXISTS(SELECT 1 FROM radar_review.person_identity WHERE person_id=pid AND identity_key LIKE 'telegram-id:%' AND identity_key<>'telegram-id:'||(p->>'telegram_user_id')) THEN RAISE EXCEPTION 'RADAR_PERSON_REUSED_USERNAME'; END IF;
 SELECT * INTO s FROM radar_review.person_source WHERE source_url=NEW."sourceLinkPrimaryLinkUrl" OR inbox_id=NEW.id LIMIT 1;
 IF s.inbox_id IS NOT NULL THEN
  IF s.source_url<>NEW."sourceLinkPrimaryLinkUrl" OR s.original_text<>NEW."summaryMarkdown" OR (pid IS NOT NULL AND pid<>s.person_id) THEN RAISE EXCEPTION 'RADAR_PERSON_SOURCE_CONFLICT'; END IF;
  pid=s.person_id;nid=s.note_id;tid=s.task_id;
 END IF;
 IF pid IS NULL THEN
  -- Existing non-Radar contact: only exact self-published profile URLs are usable.
  SELECT array_agg(DISTINCT id) INTO matches FROM workspace_radar.person WHERE "deletedAt" IS NULL AND ((tg IS NOT NULL AND lower("telegramPrimaryLinkUrl")=lower(tg)) OR (NULLIF(p->>'linkedin_url','') IS NOT NULL AND lower(regexp_replace("linkedinLinkPrimaryLinkUrl",'/$',''))=lower(regexp_replace(p->>'linkedin_url','/$',''))));
  IF cardinality(matches)>1 THEN RAISE EXCEPTION 'RADAR_PERSON_AMBIGUOUS_EXISTING'; END IF;
  pid=matches[1];
 END IF;
 pid=coalesce(pid,radar_review.person_uuid('radar-person:'||keys[1]));nid=coalesce(nid,radar_review.person_uuid('radar-note:'||NEW.id));tid=coalesce(tid,radar_review.person_uuid('radar-task:'||NEW.id));
 IF EXISTS(SELECT 1 FROM workspace_radar.person WHERE id=pid AND "deletedAt" IS NOT NULL) OR EXISTS(SELECT 1 FROM workspace_radar.note WHERE id=nid AND "deletedAt" IS NOT NULL) OR EXISTS(SELECT 1 FROM workspace_radar.task WHERE id=tid AND "deletedAt" IS NOT NULL) THEN RAISE EXCEPTION 'RADAR_PERSON_RECORD_IN_TRASH'; END IF;
 -- Company must have an exact verified domain, never just a name.
 company_claim=p->'company_identity';
 IF company_claim->>'status'='confirmed' AND company_claim->>'source_url'=NEW."sourceLinkPrimaryLinkUrl" AND NULLIF(company_claim->>'domain','') IS NOT NULL THEN
  SELECT array_agg(id) INTO matches FROM workspace_radar.company WHERE "deletedAt" IS NULL AND lower(regexp_replace(regexp_replace("domainNamePrimaryLinkUrl",'^https?://(www\.)?',''),'/$',''))=lower(company_claim->>'domain');
  IF cardinality(matches)=1 THEN cid=matches[1]; END IF;
 END IF;
 INSERT INTO workspace_radar.person AS r(id,"nameFirstName","nameLastName","jobTitle","emailsPrimaryEmail","linkedinLinkPrimaryLinkUrl","linkedinLinkPrimaryLinkLabel","telegramPrimaryLinkUrl","telegramPrimaryLinkLabel","companyId")
 VALUES(pid,p->>'first_name',p->>'last_name',nullif(p->>'job_title',''),nullif(p->>'email',''),nullif(regexp_replace(p->>'linkedin_url','/$',''),''),'LinkedIn',tg,CASE WHEN tg IS NOT NULL THEN '@'||(p->>'telegram_username') END,cid)
 ON CONFLICT(id) DO UPDATE SET
 "nameFirstName"=coalesce(nullif(r."nameFirstName",''),EXCLUDED."nameFirstName"),"nameLastName"=CASE WHEN lower(coalesce(r."nameFirstName",'')) IN ('',lower(EXCLUDED."nameFirstName")) THEN coalesce(nullif(r."nameLastName",''),EXCLUDED."nameLastName") ELSE r."nameLastName" END,
 "jobTitle"=coalesce(nullif(r."jobTitle",''),EXCLUDED."jobTitle"),"emailsPrimaryEmail"=coalesce(nullif(r."emailsPrimaryEmail",''),EXCLUDED."emailsPrimaryEmail"),
 "linkedinLinkPrimaryLinkUrl"=coalesce(nullif(r."linkedinLinkPrimaryLinkUrl",''),EXCLUDED."linkedinLinkPrimaryLinkUrl"),"linkedinLinkPrimaryLinkLabel"=coalesce(nullif(r."linkedinLinkPrimaryLinkLabel",''),EXCLUDED."linkedinLinkPrimaryLinkLabel"),
 "telegramPrimaryLinkUrl"=coalesce(nullif(r."telegramPrimaryLinkUrl",''),EXCLUDED."telegramPrimaryLinkUrl"),"telegramPrimaryLinkLabel"=coalesce(nullif(r."telegramPrimaryLinkLabel",''),EXCLUDED."telegramPrimaryLinkLabel"),"companyId"=coalesce(r."companyId",EXCLUDED."companyId");
 SELECT jsonb_build_object('job_title',"jobTitle",'email',"emailsPrimaryEmail",'linkedin_url',"linkedinLinkPrimaryLinkUrl") INTO actual FROM workspace_radar.person WHERE id=pid;
 FOREACH k IN ARRAY ARRAY['job_title','email','linkedin_url'] LOOP
 IF nullif(p->>k,'') IS NOT NULL AND nullif(actual->>k,'') IS NOT NULL AND regexp_replace(p->>k,'/$','')<>regexp_replace(actual->>k,'/$','') THEN conflicts=conflicts||E'\n'||k||': в People «'||(actual->>k)||'»; в новом источнике «'||(p->>k)||'». Сохранено прежнее поле.'; END IF;
 END LOOP;
 SELECT "companyId" INTO cid FROM workspace_radar.person WHERE id=pid;
 prose='Radar: '||NEW."candidateId"||E'\n\n## Почему интересен\n'||coalesce(NEW."whyFitMarkdown",'Не указано')||E'\n\n## Следующий шаг\n'||(a->>'title')||E'\nhttps://twenty.example.invalid/object/task/'||tid||E'\n\n## Автор и источник\n'||concat_ws(' ',p->>'first_name',p->>'last_name')||E'\n'||coalesce(NEW.source,'Не указан')||E'\n'||NEW."sourceLinkPrimaryLinkUrl"||E'\nДата: '||coalesce(NEW."publishedAt"::text,'Не указана')||E'\n\n## Подтверждённые сведения\n'||coalesce(NEW."knownConditionsMarkdown",'')||E'\nДолжность: '||coalesce(nullif(p->>'job_title',''),'Не указана')||E'\nLinkedIn: '||coalesce(nullif(p->>'linkedin_url',''),'Не указан')||E'\nEmail: '||coalesce(nullif(p->>'email',''),'Не указан')||E'\nTelegram: '||coalesce(tg,'Открыть автора через исходное сообщение')||E'\nКомпания: '||coalesce(nullif(p->>'company_name',''),'Не названа')||CASE WHEN cid IS NULL THEN ' — подтверждённая связь с Company отсутствует' ELSE E'\nhttps://twenty.example.invalid/object/company/'||cid END||E'\nПроверено: '||coalesce(p->>'checked_on','Не указано')||E'\nИсточник дополнений: '||coalesce(p->>'enrichment_source',NEW."sourceLinkPrimaryLinkUrl")||E'\nОграничения: '||coalesce(p->>'verification_limit','Сведения из исходника; актуальность отдельно не подтверждена')||E'\n\n## Твоё решение\nApprove — интерес, не подтверждённый спрос.\nКомментарий:\n'||coalesce(NEW."feedbackNoteMarkdown",'Без комментария')||E'\nИстория решения: https://twenty.example.invalid/object/projectInboxItem/'||NEW.id||E'\n\n## Гипотеза и неизвестное\n'||coalesce(NEW."whyFitMarkdown",'')||E'\n'||coalesce(NEW."unknownsMarkdown",'')||E'\n'||(a->>'questions')||E'\n\n## Расхождения\n'||coalesce(nullif(conflicts,''),'Не обнаружены по доступным полям')||E'\n\n## Риски\n'||coalesce(NEW."risksMarkdown",'')||E'\n\n## Оригинальный текст\n'||NEW."summaryMarkdown";
 task_prose='Radar: '||NEW."candidateId"||E'\n\n## Зачем обращаться\n'||coalesce(NEW."whyFitMarkdown",'Проверить гипотезу из исходника')||E'\n\n## Дословное основание\n'||(a->>'evidence_quote')||E'\n'||NEW."sourceLinkPrimaryLinkUrl"||E'\n\n## Что уточнить\n'||(a->>'questions')||E'\n\n## Полезный результат\n'||(a->>'useful_result')||E'\n\n## Черновик — не отправлен\n'||(a->>'draft')||E'\n\n## Полный контекст\nhttps://twenty.example.invalid/object/note/'||nid;
 INSERT INTO workspace_radar.note(id,title,"bodyV2Markdown","bodyV2Blocknote") VALUES(nid,'Radar — '||concat_ws(' ',p->>'first_name',p->>'last_name'),prose,radar_review.linked_blocks(prose)) ON CONFLICT(id) DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM workspace_radar.note WHERE id=nid AND position(NEW."summaryMarkdown" in "bodyV2Markdown")>0) THEN RAISE EXCEPTION 'RADAR_PERSON_NOTE_SOURCE_MISSING'; END IF;
 INSERT INTO workspace_radar.task(id,title,"bodyV2Markdown","bodyV2Blocknote",status) VALUES(tid,a->>'title',task_prose,radar_review.linked_blocks(task_prose),'TODO') ON CONFLICT(id) DO NOTHING;
 INSERT INTO workspace_radar."noteTarget"(id,"noteId","targetPersonId") VALUES(radar_review.person_uuid('radar-noteTarget:'||coalesce(s.inbox_id,NEW.id)),nid,pid) ON CONFLICT(id) DO NOTHING;
 INSERT INTO workspace_radar."taskTarget"(id,"taskId","targetPersonId") VALUES(radar_review.person_uuid('radar-taskTarget:'||coalesce(s.inbox_id,NEW.id)),tid,pid) ON CONFLICT(id) DO NOTHING;
 IF cid IS NOT NULL THEN
 INSERT INTO workspace_radar."taskTarget"(id,"taskId","targetCompanyId") VALUES(radar_review.person_uuid('radar-companyTaskTarget:'||coalesce(s.inbox_id,NEW.id)),tid,cid) ON CONFLICT(id) DO NOTHING;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM workspace_radar."noteTarget" WHERE "noteId"=nid AND "targetPersonId"=pid AND "deletedAt" IS NULL) OR NOT EXISTS(SELECT 1 FROM workspace_radar."taskTarget" WHERE "taskId"=tid AND "targetPersonId"=pid AND "deletedAt" IS NULL) THEN RAISE EXCEPTION 'RADAR_PERSON_RELATION_MISSING'; END IF;
 FOREACH k IN ARRAY keys LOOP INSERT INTO radar_review.person_identity VALUES(k,pid) ON CONFLICT(identity_key) DO NOTHING; END LOOP;
 INSERT INTO radar_review.person_source VALUES(NEW.id,NEW."sourceLinkPrimaryLinkUrl",NEW."summaryMarkdown",pid,nid,tid) ON CONFLICT DO NOTHING;
 NEW."personProfile"=p||jsonb_build_object('transfer_result',jsonb_build_object('person_id',pid,'note_id',nid,'task_id',tid));
 RETURN NEW;
END;$$;
CREATE OR REPLACE TRIGGER radar_person_transfer_guard BEFORE INSERT OR UPDATE ON workspace_radar."_projectInboxItem" FOR EACH ROW EXECUTE FUNCTION radar_review.transfer_person();
CREATE OR REPLACE FUNCTION radar_review.reject_person_id_collision() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
 IF EXISTS(SELECT 1 FROM workspace_radar."_projectInboxItem" WHERE id=NEW.id AND "candidateType"::text='PERSON') THEN RAISE EXCEPTION 'RADAR_PERSON_OLD_UI: Обнови страницу Twenty перед Approve'; END IF;
 RETURN NEW;
END;$guard$;
CREATE OR REPLACE TRIGGER radar_person_id_guard BEFORE INSERT ON workspace_radar.person FOR EACH ROW EXECUTE FUNCTION radar_review.reject_person_id_collision();
COMMIT;
