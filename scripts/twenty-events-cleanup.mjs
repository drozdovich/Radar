// Recoverable cleanup, restricted to synthetic event QA fixtures from this task.
import {sql} from './twenty-admin.mjs';
import {writeFileSync} from 'node:fs';
const result=sql(`BEGIN;
WITH cleaned AS (UPDATE workspace_radar."_projectInboxItem" SET "deletedAt"=now() WHERE "candidateId" LIKE 'radar-qa-events-%' AND "deletedAt" IS NULL RETURNING id) SELECT jsonb_build_object('qaInboxSoftDeleted',count(*)) FROM cleaned;
WITH cleaned AS (UPDATE workspace_radar."_radarEvent" e SET "deletedAt"=now() WHERE "deletedAt" IS NULL AND jsonb_array_length(COALESCE("sourceRecords",'[]'::jsonb))>0 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(e."sourceRecords") s WHERE s->>'candidateId' NOT LIKE 'radar-qa-events-%') RETURNING id) SELECT jsonb_build_object('qaEventsSoftDeleted',count(*)) FROM cleaned;
COMMIT;`);
writeFileSync('.radar/events-2026-09-14/qa-cleanup.json',JSON.stringify({recoverable:true,output:result}));console.log(result);
